"""Offline integrity verifier for the frozen Apophis Moon/Venus v1.1 matrix."""

from __future__ import annotations

import argparse
from bisect import bisect_right
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from orbit_baselines import State, norm, subtract
from run_eda import load_json, parse_horizons
from run_physics_baselines import errors, state_from_row


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _immutable_json(path: Path, value: Any) -> None:
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != value:
            raise RuntimeError(f"immutable verification artifact differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _finite_state(values: Any) -> State:
    if not isinstance(values, list) or len(values) != 6 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)) for v in values):
        raise ValueError("trace state must contain six finite numeric values")
    return State(tuple(float(v) for v in values[:3]), tuple(float(v) for v in values[3:]))  # type: ignore[arg-type]


def _setting_key(setting: Any) -> str:
    return setting["label"] if isinstance(setting, dict) else str(setting)


def _hermite(trace: list[list[Any]], targets: list[float]) -> list[State]:
    if not trace:
        raise ValueError("empty accepted trace")
    times = [float(row[0]) for row in trace]
    states = [_finite_state(row[1]) for row in trace]
    if not all(math.isfinite(t) for t in times) or any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("accepted trace times are not finite and strictly increasing")
    output: list[State] = []
    for target in targets:
        if not math.isfinite(float(target)) or target < times[0] - 1e-12 or target > times[-1] + 1e-12:
            raise ValueError("Hermite target outside finite accepted trace")
        i = min(len(times) - 2, max(0, bisect_right(times, target) - 1))
        if target >= times[-1]:
            output.append(states[-1])
            continue
        if target == times[i]:
            output.append(states[i])
            continue
        h = times[i + 1] - times[i]
        u = (target - times[i]) / h
        u2, u3 = u * u, u * u * u
        h00, h10, h01, h11 = 2*u3 - 3*u2 + 1, u3 - 2*u2 + u, -2*u3 + 3*u2, u3 - u2
        y0, y1 = states[i], states[i + 1]
        position = tuple(h00*y0.position[k] + h10*h*y0.velocity[k] + h01*y1.position[k] + h11*h*y1.velocity[k] for k in range(3))
        velocity = tuple((6*u2 - 6*u)*y0.position[k]/h + (3*u2 - 4*u + 1)*y0.velocity[k] + (-6*u2 + 6*u)*y1.position[k]/h + (3*u2 - 2*u)*y1.velocity[k] for k in range(3))
        output.append(State(position, velocity))
    return output


def _state_shift(left: list[State], right: list[State], au_km: float, day_s: float) -> dict[str, float]:
    if len(left) != len(right):
        raise ValueError("paired traces have different lengths")
    p = [norm(subtract(a.position, b.position)) * au_km for a, b in zip(left, right)]
    v = [norm(subtract(a.velocity, b.velocity)) * au_km * 1000.0 / day_s for a, b in zip(left, right)]
    return {"max_position_shift_km": max(p), "final_position_shift_km": p[-1], "max_velocity_shift_m_s": max(v), "final_velocity_shift_m_s": v[-1]}


def _summary(pred: list[State], truth: list[State], au_km: float, day_s: float) -> dict[str, float]:
    if len(pred) != len(truth):
        raise ValueError("prediction/reference length mismatch")
    positions = [errors(a, b, au_km, day_s)[0] for a, b in zip(pred, truth)]
    velocities = [errors(a, b, au_km, day_s)[1] for a, b in zip(pred, truth)]
    return {"max_grid_position_error_km": max(positions), "final_position_error_km": positions[-1], "max_grid_velocity_error_m_s": max(velocities), "final_velocity_error_m_s": velocities[-1]}


def _assert_close(actual: float, expected: float, tolerance: float, label: str) -> None:
    if not math.isfinite(actual) or not math.isfinite(expected) or abs(actual - expected) > tolerance:
        raise ValueError(f"{label} differs: {actual!r} versus {expected!r}")


def _verify_manifests(root: Path) -> dict[str, int]:
    records = []
    paths = set()
    for path in sorted((root / "data/checksums").glob("*manifest.json")):
        document = load_json(path)
        for record in document.get("files", []):
            if not isinstance(record, dict) or "path" not in record or "sha256" not in record or "bytes" not in record:
                raise ValueError(f"manifest record incomplete: {path}")
            raw = root / record["path"]
            paths.add(raw.resolve())
            if not raw.is_file() or _sha(raw).lower() != str(record["sha256"]).lower() or raw.stat().st_size != int(record["bytes"]):
                raise ValueError(f"manifest hash/size mismatch: {record['path']}")
            ignored = subprocess.run(["git", "check-ignore", "--no-index", "--quiet", record["path"]], cwd=root, check=False).returncode == 0
            if not ignored:
                raise ValueError(f"manifest raw path is not gitignored: {record['path']}")
            records.append((str(record["sha256"]).lower(), int(record["bytes"])))
    unique = len(set(records))
    if unique != 129 or len(paths) != 129:
        raise ValueError(f"expected 129 unique manifest SHA/size pairs, found {unique}")
    return {"manifest_records": len(records), "manifest_unique_sha_size": unique, "unique_raw_paths":len(paths)}


def _verify_run(root: Path, row: dict, old_rows: list[dict], dense_rows: list[dict], au_km: float, day_s: float, baseline: dict[str, float] | None) -> dict[str, Any]:
    if row["requested_samples"] != 797 or row["dense_samples"] != 2305:
        raise ValueError(f"wrong sample lengths for {row.get('arm')}:{row.get('solver')}:{row.get('setting')}")
    solver = row["solver"]
    expected_basis = "relative_days_since_start" if solver == "rk4" else "absolute_jd_tdb"
    if row.get("accepted_time_basis") != expected_basis:
        raise ValueError("native accepted time basis mismatch")
    start = float(row["start_jd_tdb"])
    if start != old_rows[0]["epoch_jd_tdb"] or _finite_state(row["requested_states"][0]) != state_from_row(old_rows[0]):
        raise ValueError("Forecast start or initial state differs from frozen teacher")
    target_abs = [r["epoch_jd_tdb"] for r in old_rows]
    requested_targets = [t - start for t in target_abs] if solver == "rk4" else target_abs
    dense_targets_abs = [r["epoch_jd_tdb"] for r in dense_rows]
    dense_targets = [t - start for t in dense_targets_abs] if solver == "rk4" else dense_targets_abs
    accepted = row.get("accepted_endpoints")
    if not isinstance(accepted, list):
        raise ValueError("accepted endpoints missing")
    trace = [[float(item[0]), list(item[1])] for item in accepted]
    origin = 0.0 if solver == "rk4" else start
    if trace[0][0] != origin:
        raise ValueError("accepted trace origin mismatch")
    if _finite_state(trace[0][1]) != _finite_state(row["requested_states"][0]):
        raise ValueError("accepted/requested initial state mismatch")
    if any(not math.isfinite(t) for t, _ in trace) or any(b[0] <= a[0] for a, b in zip(trace, trace[1:])):
        raise ValueError("accepted trace is not finite and strictly increasing")
    accepted_steps = len(trace) - 1
    counts = row.get("counts", {})
    if solver == "rk4" and (counts.get("rk4_steps") != accepted_steps or counts.get("force_evaluations") != accepted_steps * 4):
        raise ValueError("RK4 count does not match accepted endpoints")
    if solver == "dopri54" and counts.get("accepted_steps") != accepted_steps:
        raise ValueError("DP count does not match accepted endpoints")
    requested = [_finite_state(s) for s in row["requested_states"]]
    dense = [_finite_state(s) for s in row["dense_states"]]
    requested_check = _hermite(trace, requested_targets)
    dense_check = _hermite(trace, dense_targets)
    for actual, expected in zip(requested, requested_check):
        shift = _state_shift([actual], [expected], au_km, day_s)
        if shift["max_position_shift_km"] > 1e-7 or shift["max_velocity_shift_m_s"] > 1e-7:
            raise ValueError("requested state is inconsistent with native-basis Hermite trace")
    for actual, expected in zip(dense, dense_check):
        shift = _state_shift([actual], [expected], au_km, day_s)
        if shift["max_position_shift_km"] > 1e-7 or shift["max_velocity_shift_m_s"] > 1e-7:
            raise ValueError("dense state is inconsistent with native-basis Hermite trace")
    truth = [state_from_row(r) for r in old_rows]
    dense_truth = [state_from_row(r) for r in dense_rows]
    recomputed_primary = _summary(requested, truth, au_km, day_s)
    recomputed_secondary = _summary(dense, dense_truth, au_km, day_s)
    for name, got in (("summary_old_grid", recomputed_primary), ("summary_dense", recomputed_secondary)):
        saved = row.get(name)
        if not isinstance(saved, dict):
            raise ValueError(f"missing {name}")
        for key, value in got.items():
            _assert_close(float(saved[key]), value, 1e-10 if key.endswith("km") else 1e-7, f"{row['arm']} {name} {key}")
    if baseline is not None:
        for key, value in baseline.items():
            _assert_close(recomputed_primary[key], value, 1e-8, f"baseline {row['solver']}:{row['setting']} {key}")
    return {"accepted_endpoints": accepted_steps, "requested": len(requested), "dense": len(dense)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    root = args.root.resolve()
    config_path = root / "configs/apophis_moon_venus_v1_1.json"
    result_path = root / "outputs/apophis_moon_venus/v1_1/apophis_moon_venus_results.json"
    freeze_path = root / "outputs/apophis_moon_venus/v1_1/freeze.json"
    result = load_json(result_path)
    freeze = load_json(freeze_path)
    if result.get("fingerprint") != freeze.get("fingerprint") or result.get("source_sha256") != freeze.get("source_sha256"):
        raise ValueError("result/freeze fingerprint or source hash mismatch")
    for relative, digest in freeze["source_sha256"].items():
        if relative == "__runtime__":
            continue
        path = root / relative
        if not path.is_file() or _sha(path) != digest:
            raise ValueError(f"frozen source hash mismatch: {relative}")
    runtime = freeze["runtime"]
    runtime_for_hash = {"python_executable": runtime["executable"], "python_version": runtime["version"], "platform": runtime["platform"]}
    runtime_digest = hashlib.sha256(json.dumps(runtime_for_hash, sort_keys=True).encode()).hexdigest()
    if freeze["source_sha256"].get("__runtime__") != runtime_digest:
        raise ValueError("runtime fingerprint mismatch")
    raw_hashes={k:v for k,v in freeze["source_sha256"].items() if k!="__runtime__"}
    expected_fingerprint=hashlib.sha256(json.dumps({"hashes":raw_hashes,"runtime":runtime_for_hash},sort_keys=True).encode()).hexdigest()
    if expected_fingerprint!=freeze["fingerprint"]:
        raise ValueError("Combined source/runtime fingerprint mismatch")
    if sys.executable != runtime["executable"] or sys.version != runtime["version"] or __import__("platform").platform() != runtime["platform"]:
        raise ValueError("current runtime differs from frozen runtime")
    manifest_stats = _verify_manifests(root)
    config = load_json(config_path)
    from run_apophis_solver_audit import _target_rows
    data = load_json(root / "configs/eda_pilot_6.json")
    event = load_json(root / "configs/eda_apophis_2029_refinement.json")
    old_rows, _ = _target_rows(root, data, event, 365)
    new_rows = parse_horizons(root / config["raw_directory"] / "asteroid_99942.json", "99942", "Apophis")[1]
    if len(old_rows) != 797 or len(new_rows) != 2305:
        raise ValueError("teacher grid length mismatch")
    constants = data["constants"]
    baseline_doc = load_json(root / "outputs/apophis_solver_audit/stage3/apophis_solver_audit.json")
    baseline_rows = {}
    for item in next(c for c in baseline_doc["cases"] if c["case_id"] == "apophis_2029_long365d")["solver_comparison"]:
        if item["id"].startswith("rk4:"):
            key = ("rk4", item["id"].split(":", 1)[1])
        elif item["id"].startswith("dopri54:") and item["id"].split(":", 1)[1] in config["dp_tolerance_labels"]:
            key = ("dopri54", item["id"].split(":", 1)[1])
        else:
            continue
        baseline_rows[key] = {"max_grid_position_error_km": item["max_grid_position_error_km"], "final_position_error_km": item["final_position_error_km"], "max_grid_velocity_error_m_s": item["max_grid_velocity_error_m_s"], "final_velocity_error_m_s": item["final_velocity_error_m_s"]}
    runs = result.get("runs")
    if not isinstance(runs, list) or len(runs) != 22:
        raise ValueError("matrix must contain exactly 22 runs")
    keys = {(r["arm"], r["solver"], _setting_key(r["setting"])) for r in runs}
    if len(keys) != 22:
        raise ValueError("run keys are not unique")
    expected_keys={(a["id"],"dopri54",t) for a in config["arms"] for t in config["dp_tolerance_labels"]}
    expected_keys.update((a,"rk4",str(s)) for a in config["rk4_arms"] for s in config["rk4_scales"])
    if keys!=expected_keys:
        raise ValueError("Matrix keys differ from declared design")
    checkpoints = root / "outputs/apophis_moon_venus/v1_1/checkpoints"
    checkpoint_files = sorted(checkpoints.glob("*.json"))
    if len(checkpoint_files) != 22:
        raise ValueError(f"expected 22 frozen checkpoints, found {len(checkpoint_files)}")
    run_by_key = {(r["arm"], r["solver"], _setting_key(r["setting"])): r for r in runs}
    checkpoint_hashes = {}
    for checkpoint in checkpoint_files:
        saved = load_json(checkpoint)
        if saved.get("fingerprint") != freeze["fingerprint"]:
            raise ValueError(f"checkpoint fingerprint mismatch: {checkpoint.name}")
        item = saved.get("result", {})
        key = (item.get("arm"), item.get("solver"), _setting_key(item.get("setting")))
        if key not in run_by_key or item != run_by_key[key]:
            raise ValueError(f"checkpoint/result mismatch: {checkpoint.name}")
        checkpoint_hashes[checkpoint.name] = _sha(checkpoint)
    summaries = []
    for row in runs:
        baseline = baseline_rows.get((row["solver"], _setting_key(row["setting"]))) if row["arm"] == "old" else None
        summaries.append(_verify_run(root, row, old_rows, new_rows, constants["au_km"], constants["day_s"], baseline))
    paired = result.get("paired_shifts", {})
    if [len(paired.get(k,[])) for k in ("to_old_same_solver","within_arm","selected_arm_pairs")] != [22,11,8]:
        raise ValueError("Paired matrix coverage changed")
    lookup = run_by_key
    for entry in paired.get("to_old_same_solver", []):
        key = (entry["arm"], entry["solver"], _setting_key(entry["setting"]))
        old = lookup[("old", entry["solver"], _setting_key(entry["setting"]))]
        expected = _state_shift(_state_list(lookup[key]["requested_states"]), _state_list(old["requested_states"]), constants["au_km"], constants["day_s"])
        if entry["shift"] != expected:
            raise ValueError("paired shift to old mismatch")
    for entry in paired.get("within_arm", []):
        setting_a, setting_b = (("tight", "tighter") if entry["kind"] == "dp_tight_to_tighter" else ("0.5", "0.25"))
        a = lookup[(entry["arm"], "dopri54" if entry["kind"].startswith("dp") else "rk4", setting_a)]
        b = lookup[(entry["arm"], "dopri54" if entry["kind"].startswith("dp") else "rk4", setting_b)]
        expected = _state_shift(_state_list(a["requested_states"]), _state_list(b["requested_states"]), constants["au_km"], constants["day_s"])
        if entry["shift"] != expected:
            raise ValueError("within-arm shift mismatch")
    for entry in paired.get("selected_arm_pairs", []):
        a = lookup[(entry["left"], entry["solver"], entry["setting"])]
        b = lookup[(entry["right"], entry["solver"], entry["setting"])]
        expected = _state_shift(_state_list(a["requested_states"]), _state_list(b["requested_states"]), constants["au_km"], constants["day_s"])
        if entry["shift"] != expected:
            raise ValueError("selected arm shift mismatch")
    previous = {}
    for path in sorted((root / "outputs/apophis_moon_venus/checkpoints").glob("*.json")):
        previous[path.name] = _sha(path)
    verification = {"schema_version": 2, "result": str(result_path.relative_to(root)), "result_sha256":_sha(result_path),"verifier_sha256":_sha(Path(__file__)),"error_state_pairs":sum(s["requested"]+s["dense"] for s in summaries),"error_values_recomputed":2*sum(s["requested"]+s["dense"] for s in summaries),"fingerprint": freeze["fingerprint"], "runtime_sha256": freeze["source_sha256"]["__runtime__"], "run_count": len(runs), "unique_run_keys": len(keys), "checkpoint_count": len(checkpoint_files), "checkpoint_sha256": checkpoint_hashes, "manifest": manifest_stats, "teacher_roles_verified": result.get("teacher_roles"), "baseline_rows_verified": sum(r["arm"]=="old" for r in runs), "paired_entries_verified": sum(len(paired.get(k, [])) for k in ("to_old_same_solver", "within_arm", "selected_arm_pairs")), "previous_v1_checkpoint_sha256": previous}
    _immutable_json(root / "outputs/apophis_moon_venus/v1_1/verification_v2.json", verification)
    print(json.dumps(verification, indent=2))
    return 0


def _state_list(rows: list[list[float]]) -> list[State]:
    return [_finite_state(row) for row in rows]


if __name__ == "__main__":
    raise SystemExit(main())

"""Offline verifier for the frozen Apophis weak-force/shape matrix.

Only saved states, raw teacher tables, and frozen provenance are read.  The
shape context is constructed for the force-bound calculation, but no solver or
forecast is called by this verifier.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any

from orbit_baselines import State, norm, subtract
from run_eda import load_json
from run_physics_baselines import state_from_row

import verify_apophis_reference_time as reference_verifier


SUMMARY_FIELDS = reference_verifier.SUMMARY_FIELDS


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _write_immutable(path: Path, value: Any) -> None:
    if path.exists():
        if load_json(path) != value:
            raise RuntimeError(f"immutable artifact differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _state(value: Any) -> State:
    return reference_verifier._state(value)


def _difference(left: State, right: State) -> State:
    return State(tuple(a - b for a, b in zip(left.position, right.position)), tuple(a - b for a, b in zip(left.velocity, right.velocity)))


def _shift(left: list[State], right: list[State], au_km: float, day_s: float) -> dict[str, float]:
    return reference_verifier._shift(left, right, au_km, day_s)


def _manifest_inventory(root: Path) -> dict[str, Any]:
    manifests = sorted((root / "data/checksums").glob("*manifest.json"))
    records: list[tuple[str, str, int]] = []
    for manifest_path in manifests:
        manifest = load_json(manifest_path)
        entries = manifest.get("files")
        if entries is None:
            entries = manifest.get("downloads")
        if not isinstance(entries, list):
            raise ValueError(f"manifest has no files/downloads list: {manifest_path.name}")
        for item in entries:
            if not isinstance(item, dict) or not all(key in item for key in ("path", "sha256", "bytes")):
                raise ValueError(f"incomplete manifest record: {manifest_path.name}")
            name = str(item["path"])
            raw = _record_path(root, name)
            if not raw.is_file() or _sha(raw).lower() != str(item["sha256"]).lower() or raw.stat().st_size != int(item["bytes"]):
                raise ValueError(f"manifest raw hash/size mismatch: {name}")
            relative = raw.relative_to(root).as_posix() if raw.is_absolute() and raw.is_relative_to(root) else name
            tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "--", relative], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0
            if tracked:
                raise ValueError(f"manifest raw file is tracked: {relative}")
            if subprocess.run(["git", "check-ignore", "--no-index", "--quiet", relative], cwd=root, check=False).returncode != 0:
                raise ValueError(f"manifest raw file is not ignored: {relative}")
            records.append((relative, str(item["sha256"]).lower(), int(item["bytes"])))
    paths = {path for path, _, _ in records}
    if len(manifests) != 13 or len(paths) != 137:
        raise ValueError(f"manifest inventory mismatch: {len(manifests)} manifests, {len(paths)} unique paths")
    return {"manifest_count": len(manifests), "manifest_records": len(records), "unique_raw_paths": len(paths), "raw_sha256_size_records": len(set(records[i][1:] for i in range(len(records))))}


def _check_freeze(root: Path, result: dict[str, Any], freeze_path: Path) -> dict[str, Any]:
    freeze = load_json(freeze_path)
    if result.get("fingerprint") != freeze.get("fingerprint") or result.get("freeze_sha256") != _sha(freeze_path):
        raise ValueError("matrix/freeze fingerprint mismatch")
    for name, digest in freeze.get("hashes", {}).items():
        path = _record_path(root, name)
        if not path.is_file() or _sha(path) != digest:
            raise ValueError(f"freeze hash mismatch: {name}")
    runtime = {"executable": sys.executable, "version": sys.version, "platform": platform.platform()}
    if freeze.get("runtime") != runtime:
        raise ValueError("current runtime differs from frozen runtime")
    payload = {"hashes": freeze["hashes"], "runtime": freeze["runtime"]}
    expected = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    if expected != freeze.get("fingerprint"):
        raise ValueError("freeze fingerprint recomputation mismatch")
    return freeze


def _teacher_data(root: Path, previous: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, Any]]:
    config = load_json(root / "configs/apophis_shape_weak_force.json")
    base = load_json(root / "configs/apophis_solver_audit.json")
    data = load_json(root / base["data_config"])
    validation = load_json(root / "data/processed/apophis_reference_time/validation.json")
    if validation != previous.get("input_validation"):
        raise ValueError("reference input validation differs from previous matrix")
    paths = dict(validation["paths"])
    paths["long_repeat"] = "data/raw/apophis_reference_time/asteroid_99942_long_repeat.json"
    references = {name: __import__("run_eda", fromlist=["parse_horizons"]).parse_horizons(_record_path(root, path), "99942", "Apophis")[1] for name, path in paths.items()}
    event = load_json(root / base["event_config"])
    audit = __import__("run_apophis_solver_audit", fromlist=["_target_rows"])
    annual, _ = audit._target_rows(root, data, event, 365)
    return annual, references, data


def _expected_keys(config: dict[str, Any]) -> set[str]:
    result = set()
    for arm in config["geopotential_arms"]:
        for source in config["initial_sources"]:
            for label in config["dp_labels"]:
                result.add(f"{arm}__{source}__{label}")
    for arm in config["shape_arms"]:
        for label in config["dp_labels"]:
            result.add(f"{arm}__{config['shape_initial_source']}__{label}")
    return result


def _check_baselines(records: list[dict[str, Any]], previous: dict[str, Any]) -> tuple[int, int]:
    old_by_key = {row["key"]: row for row in previous["records"]}
    checked = 0
    sphere_checked = 0
    for row in records:
        if row["arm"] == "baseline":
            previous_key = f"relative_calendar_knots__dopri54__{row['setting']['label']}__{row['initial_source']}"
            old = old_by_key.get(previous_key)
            if old is None or row["requested_states"] != old["requested_states"] or row["requested_times_relative_days"] != old["requested_times_relative_days"]:
                raise ValueError(f"baseline does not exactly reproduce previous matrix: {row['key']}")
            checked += 1
        elif row["arm"] == "shape_sphere":
            baseline = next(item for item in records if item["arm"] == "baseline" and item["initial_source"] == row["initial_source"] and item["setting"] == row["setting"])
            if row["requested_states"] != baseline["requested_states"]:
                raise ValueError(f"sphere control differs from baseline: {row['key']}")
            sphere_checked += 1
    if checked != 4 or sphere_checked != 2:
        raise ValueError(f"baseline controls checked: {checked}, sphere controls: {sphere_checked}")
    return checked, sphere_checked


def _compute_shape_bound(ctx: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    trace_s = sum(ctx["S"][i][i] for i in range(3))
    peak_time, peak_bound = 0.0, 0.0
    minimum_distance = math.inf
    for time, values in zip(ctx["times"], record["requested_states"]):
        position = values[:3]
        bound = 3.0 * trace_s * ctx["mu"] / norm(position) ** 4
        for body in ctx["planets"]:
            distance = norm(subtract(position, body.ephemeris.state_at(time).position))
            minimum_distance = min(minimum_distance, distance)
            bound += 3.0 * trace_s * body.mu_au3_d2 / distance**4
        if bound > peak_bound:
            peak_time, peak_bound = time, bound
    return {
        "max_sampled_acceleration_bound_m_s2": peak_bound * ctx["au"] * 1000.0 / ctx["day"]**2,
        "peak_relative_day": peak_time,
        "minimum_sampled_point_source_distance_km": minimum_distance * ctx["au"],
        "radius_over_minimum_distance": ctx["moments"]["max_radius_from_centroid_km"] / (minimum_distance * ctx["au"]),
        "scope": "orientation-independent quadrupole force bound on baseline output grid; not a propagated error bound",
    }


def _analysis(records: list[dict[str, Any]], au: float, day_s: float) -> dict[str, Any]:
    by_key = {row["key"]: row for row in records}
    table = []
    for row in records:
        refs = row["reference_comparisons"]
        table.append({"key": row["key"], "arm": row["arm"], "initial_source": row["initial_source"], "setting": row["setting"]["label"],
                      "old_daily": refs["old_daily"], "long_repeat": refs["long_repeat"], "annual_daily": refs["annual_daily"]})
    per_arm_tolerance = []
    effects = []
    for arm in sorted({row["arm"] for row in records}):
        for source in sorted({row["initial_source"] for row in records if row["arm"] == arm}):
            tight = by_key[f"{arm}__{source}__tighter"]
            extreme = by_key[f"{arm}__{source}__extreme"]
            per_arm_tolerance.append({"arm": arm, "initial_source": source, "shift": _shift([_state(s) for s in tight["requested_states"]], [_state(s) for s in extreme["requested_states"]], au, day_s)})
    for row in records:
        if row["arm"] == "baseline":
            continue
        for label in ("tighter", "extreme"):
            baseline = by_key[f"baseline__{row['initial_source']}__{label}"]
            if row["setting"]["label"] != label:
                continue
            effects.append({"arm": row["arm"], "initial_source": row["initial_source"], "setting": label,
                            "shift_vs_baseline": _shift([_state(s) for s in row["requested_states"]], [_state(s) for s in baseline["requested_states"]], au, day_s)})
    stability = []
    for arm in sorted({row["arm"] for row in records if row["arm"] != "baseline"}):
        for source in sorted({row["initial_source"] for row in records if row["arm"] == arm}):
            tighter = next(item["shift_vs_baseline"] for item in effects if item["arm"] == arm and item["initial_source"] == source and item["setting"] == "tighter")
            extreme = next(item["shift_vs_baseline"] for item in effects if item["arm"] == arm and item["initial_source"] == source and item["setting"] == "extreme")
            tighter_row = by_key[f"{arm}__{source}__tighter"]
            extreme_row = by_key[f"{arm}__{source}__extreme"]
            base_tighter = by_key[f"baseline__{source}__tighter"]
            base_extreme = by_key[f"baseline__{source}__extreme"]
            tighter_effect_trace = [_difference(_state(a), _state(b)) for a, b in zip(tighter_row["requested_states"], base_tighter["requested_states"])]
            extreme_effect_trace = [_difference(_state(a), _state(b)) for a, b in zip(extreme_row["requested_states"], base_extreme["requested_states"])]
            stability.append({"arm": arm, "initial_source": source, "tighter_effect": tighter, "extreme_effect": extreme,
                              "summary_difference": {field: extreme[field] - tighter[field] for field in tighter},
                              "vector_effect_shift": _shift(tighter_effect_trace, extreme_effect_trace, au, day_s)})
    return {"schema_version": 1, "reference_error_table": table, "per_arm_dp_tolerance_shift": per_arm_tolerance,
            "arm_vs_baseline_shifts": effects, "force_effect_stability": stability}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    root = args.root.resolve()
    output_dir = root / "outputs/apophis_shape_weak_force"
    matrix_path, freeze_path = output_dir / "matrix.json", output_dir / "freeze.json"
    matrix = load_json(matrix_path)
    freeze = _check_freeze(root, matrix, freeze_path)
    manifest = _manifest_inventory(root)
    config = load_json(root / "configs/apophis_shape_weak_force.json")
    previous = load_json(root / config["previous_matrix"])
    annual, references, data = _teacher_data(root, previous)
    records = matrix.get("records")
    if not isinstance(records, list) or len(records) != 24:
        raise ValueError("matrix must contain exactly 24 records")
    if {row.get("key") for row in records} != _expected_keys(config):
        raise ValueError("matrix keys differ from frozen config")
    if set(config["geopotential_arms"]) != {"baseline", "j3", "j4", "j3j4"} or set(config["shape_arms"]) != {"shape_xyz", "shape_yzx", "shape_zxy", "shape_sphere"}:
        raise ValueError("unexpected frozen arm configuration")
    if len(annual) != 797 or set(references) != {"annual_daily", "annual_hourly", "short_daily", "short_hourly", "old_daily", "old_refined", "recent_refined", "long_repeat"}:
        raise ValueError("reference table set is not the frozen eight-table set")
    base_tolerances = {item["label"]: item for item in load_json(root / "configs/apophis_solver_audit.json")["dopri54_tolerances"]}
    for row in records:
        label = row.get("setting", {}).get("label") if isinstance(row.get("setting"), dict) else None
        expected_key = f"{row.get('arm')}__{row.get('initial_source')}__{label}"
        if row.get("key") != expected_key:
            raise ValueError(f"noncanonical run key: {row.get('key')}")
        if row.get("solver") != "dopri54" or row.get("mode") != "relative_calendar_knots":
            raise ValueError(f"solver/mode mismatch: {row.get('key')}")
        if label not in base_tolerances or row.get("setting") != base_tolerances[label]:
            raise ValueError(f"DP setting differs from frozen baseline config: {row.get('key')}")
        stats = row.get("solver_stats")
        if not isinstance(stats, dict) or stats.get("tolerance") != row["setting"]:
            raise ValueError(f"saved solver tolerance mismatch: {row.get('key')}")
    if any(row.get("fingerprint") != freeze["fingerprint"] for row in records):
        raise ValueError("record fingerprint differs from freeze")
    initial_sources = {"old": state_from_row(annual[0]), "annual_hourly": state_from_row(references["annual_hourly"][0])}
    au, day_s = float(data["constants"]["au_km"]), float(data["constants"]["day_s"])
    counts = {"accepted_endpoints": 0, "primary_points": 0, "reference_points": 0, "reference_tables": 0}
    for row in records:
        # This frozen utility validates native traces, exact requested endpoints,
        # initial six-state, and all eight reference summary metrics.
        reference_verifier._verify_record(row, annual, references, initial_sources, au, day_s)
        counts["accepted_endpoints"] += len(row["accepted_endpoints"])
        counts["primary_points"] += len(annual)
        counts["reference_points"] += sum(int(row["reference_comparisons"][name]["samples"]) for name in references)
        counts["reference_tables"] += len(references)
    checked_baselines, checked_spheres = _check_baselines(records, previous)
    checkpoints = sorted((output_dir / "checkpoints").glob("*.json"))
    if len(checkpoints) != 24:
        raise ValueError(f"checkpoint count mismatch: {len(checkpoints)}")
    by_key = {row["key"]: row for row in records}
    checkpoint_hashes = {}
    for path in checkpoints:
        if path.stem not in by_key or load_json(path) != by_key[path.stem]:
            raise ValueError(f"checkpoint differs from matrix: {path.name}")
        checkpoint_hashes[path.name] = _sha(path)
    saved_pairs = matrix.get("paired_shifts")
    if not isinstance(saved_pairs, list):
        raise ValueError("paired shifts missing")
    expected_pairs = []
    for index, left in enumerate(records):
        for right in records[index + 1:]:
            if left["initial_source"] == right["initial_source"] and (left["arm"] == right["arm"] or left["setting"] == right["setting"]):
                expected_pairs.append({"left": left["key"], "right": right["key"], **_shift([_state(s) for s in left["requested_states"]], [_state(s) for s in right["requested_states"]], au, day_s)})
    if saved_pairs != expected_pairs or len(expected_pairs) != 80:
        raise ValueError(f"paired shift mismatch: saved {len(saved_pairs)}, expected {len(expected_pairs)}")
    shape_context_module = __import__("run_apophis_shape_weak_force", fromlist=["context"])
    context = shape_context_module.context(root, freeze=False)
    bound_expected = _compute_shape_bound(context, by_key["baseline__annual_hourly__extreme"])
    if matrix.get("shape_force_bound") != bound_expected:
        raise ValueError("shape force bound mismatch")
    analysis = _analysis(records, au, day_s)
    analysis.update({"matrix_sha256": _sha(matrix_path), "verifier_sha256": _sha(root / "src/verify_apophis_shape_weak_force.py"), "fingerprint": freeze["fingerprint"]})
    _write_immutable(output_dir / "analysis.json", analysis)
    verification = {"schema_version": 2, "matrix": str(matrix_path.relative_to(root)), "matrix_sha256": _sha(matrix_path),
                    "verifier_sha256": _sha(root / "src/verify_apophis_shape_weak_force.py"),
                    "reference_verifier_sha256": _sha(root / "src/verify_apophis_reference_time.py"), "freeze_sha256": _sha(freeze_path),
                    "fingerprint": freeze["fingerprint"], "run_count": len(records), "unique_run_keys": len(by_key),
                    "paired_shift_count": len(expected_pairs), "paired_scalar_count": len(expected_pairs) * len(annual) * 2,
                    "accepted_endpoint_count": counts["accepted_endpoints"], "primary_rows": len(annual),
                    "reference_tables": len(references), "reference_comparison_count": counts["reference_tables"],
                    "scalar_error_count": (counts["primary_points"] + counts["reference_points"]) * 2,
                    "checkpoint_count": len(checkpoints), "checkpoint_sha256": checkpoint_hashes, "manifest": manifest,
                    "baseline_reproductions": checked_baselines, "sphere_controls": checked_spheres,
                    "shape_force_bound_verified": True}
    _write_immutable(output_dir / "verification.json", verification)
    print(json.dumps(verification, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

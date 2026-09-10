"""Offline verifier for the frozen Apophis DE441/SPK interpolation audit."""
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

from de441_subset import DE441Subset
from relative_time_dynamics import RelativeEphemerisInterpolator
from run_eda import load_json
from run_physics_baselines import state_from_row
import verify_apophis_reference_time as rv
import verify_precise_propagation as pv


ARMS = ("baseline", "same_knots", "hourly", "earth_moon_direct", "other_major_direct", "all_direct")
LABELS = ("extreme", "ultra", "rk4_fine")
NATIVE_BASIS = "uniform_inertial_q=rb-rSun0-t*vSun0,w=vb-vSun0"
OUTPUT_BASIS = "heliocentric_ICRF_geometric_AU_AU-per-day"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _write_immutable(path: Path, value: Any) -> None:
    if path.exists():
        if load_json(path) != value:
            raise RuntimeError(f"immutable artifact differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _check_freeze(root: Path, matrix: dict[str, Any], freeze_path: Path) -> dict[str, Any]:
    freeze = load_json(freeze_path)
    if matrix.get("fingerprint") != freeze.get("fingerprint") or matrix.get("freeze_sha256") != _sha(freeze_path):
        raise ValueError("SPK matrix/freeze provenance mismatch")
    hashes, frozen_runtime = freeze.get("hashes"), freeze.get("runtime")
    if not isinstance(hashes, dict) or not isinstance(frozen_runtime, dict):
        raise ValueError("SPK freeze lacks hashes/runtime")
    for name, digest in hashes.items():
        path = _path(root, str(name))
        if not path.is_file() or _sha(path) != str(digest):
            raise ValueError(f"SPK frozen hash mismatch: {name}")
    runtime = {"executable": sys.executable, "version": sys.version, "platform": platform.platform()}
    if runtime != frozen_runtime:
        raise ValueError("SPK runtime differs from freeze")
    payload = {"hashes": hashes, "runtime": frozen_runtime}
    if hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest() != freeze.get("fingerprint"):
        raise ValueError("SPK freeze fingerprint recomputation mismatch")
    return freeze


def _manifest_inventory(root: Path) -> dict[str, Any]:
    manifests = sorted((root / "data/checksums").glob("*manifest.json"))
    records = []
    for manifest_path in manifests:
        document = load_json(manifest_path)
        entries = document.get("files") if document.get("files") is not None else document.get("downloads")
        if not isinstance(entries, list):
            raise ValueError(f"manifest lacks files/downloads: {manifest_path.name}")
        for item in entries:
            if not isinstance(item, dict) or not all(k in item for k in ("path", "sha256", "bytes")):
                raise ValueError(f"incomplete manifest record: {manifest_path.name}")
            name, digest, size = str(item["path"]), str(item["sha256"]).lower(), int(item["bytes"])
            path = _path(root, name)
            relative = path.relative_to(root).as_posix() if path.is_relative_to(root) else name
            if not path.is_file() or _sha(path) != digest or path.stat().st_size != size:
                raise ValueError(f"raw manifest mismatch: {name}")
            tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "--", relative], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            ignored = subprocess.run(["git", "check-ignore", "--no-index", "--quiet", "--", relative], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            if tracked.returncode == 0 or ignored.returncode != 0:
                raise ValueError(f"raw Git exclusion failed: {relative}")
            records.append((relative, digest, size, manifest_path.name))
    unique = {item[0] for item in records}
    if len(manifests) != 16 or len(unique) != 170:
        raise ValueError(f"expected 16 manifests/170 raw paths, got {len(manifests)}/{len(unique)}")
    return {"manifest_count": len(manifests), "manifest_records": len(records), "unique_raw_paths": len(unique),
            "raw_sha256_size_records": len({(item[1], item[2]) for item in records})}


def _teachers(root: Path):
    previous = load_json(root / "outputs/apophis_reference_time/matrix.json")
    annual, refs, data = pv._load_teachers(root, previous)
    if len(annual) != 797 or len(refs) != 8:
        raise ValueError("teacher set must be 797 rows and eight references")
    return annual, refs, data


def _expected_setting(root: Path, label: str) -> dict[str, Any]:
    config = load_json(root / "configs/apophis_eih.json")
    return next(item for item in config["solver_settings"] if item["label"] == label)


def _force_spec(arm: str) -> dict[str, Any]:
    return {"gr": "eih_sun", "solar_j2": False, "earth_pole": "fixed_j2000", "ephemeris": arm,
            "newton_sources": "Sun + 10 major + SB16", "sb16_interpolation": "unchanged"}


def _check_inputs(root: Path, config: dict[str, Any], freeze: dict[str, Any], backend: DE441Subset, ctx: dict[str, Any], output: Path) -> dict[str, Any]:
    inputs = load_json(output / "inputs.json")
    input_config = load_json(root / config["input_config"])
    manifest_path, index_path = root / input_config["manifest"], root / input_config["index"]
    if inputs.get("fingerprint") != freeze["fingerprint"] or inputs.get("input_manifest_sha256") != _sha(manifest_path) or inputs.get("input_index_sha256") != _sha(index_path):
        raise ValueError("SPK input provenance mismatch")
    if inputs.get("previous_matrix_sha256") != _sha(root / config["previous_matrix"]):
        raise ValueError("SPK previous matrix input SHA mismatch")
    expected = {"native_state_basis": NATIVE_BASIS, "reported_state_basis": OUTPUT_BASIS, "initial_source": "annual_hourly",
                "target_data_unchanged": True, "gr": "eih_sun", "solar_j2": False, "earth_pole": "fixed_j2000",
                "sb16_ephemerides_unchanged": True, "sun_origin_acceleration_subtraction": False,
                "rk4_step_selection_state_basis": OUTPUT_BASIS}
    if any(inputs.get(k) != v for k, v in expected.items()):
        raise ValueError("SPK inputs metadata mismatch")
    manifest = load_json(manifest_path)
    if manifest.get("complete") is not True or manifest.get("design_sha256") != _sha(root / input_config["design"]):
        raise ValueError("SPK raw manifest is incomplete or design hash differs")
    index = load_json(index_path)
    if index.get("manifest_sha256") != _sha(manifest_path) or index.get("endian") not in ("<", ">") or index.get("nd") != 2 or index.get("ni") != 6:
        raise ValueError("SPK index provenance/format mismatch")
    if {int(item.get("target", -1)) for item in index.get("segments", [])} != {int(x) for x in input_config["target_ids"]}:
        raise ValueError("SPK index target set differs from frozen input design")
    if any(item.get("type") != 2 or item.get("frame") != 1 or float(item.get("start_et", 0)) > float(index["start_et"]) or float(item.get("end_et", 0)) < float(index["stop_et"]) for item in index["segments"]):
        raise ValueError("SPK index segment type/frame/coverage mismatch")
    entries = manifest.get("files", [])
    if len(entries) != 30 or {item.get("name") for item in entries} != {str(x) for x in (['file_record', 'metadata'] + [f'{n}_{kind}' for n in input_config['target_ids'] for kind in ('directory', 'records')])}:
        raise ValueError("SPK manifest selected range inventory differs")
    if inputs.get("source_knot_epoch_counts") is None:
        raise ValueError("SPK source knot count provenance missing")
    expected_counts = {p.body_id: sum(-1 <= t <= 366 for t in p.ephemeris.epochs_days)
                       for p in ctx["planets"] + (ctx["pluto"],) if not p.body_id.startswith("sb:")}
    if inputs.get("source_knot_epoch_counts") != expected_counts:
        raise ValueError("SPK source knot counts differ from the frozen context")
    return {"input_manifest_sha256": _sha(manifest_path), "input_index_sha256": _sha(index_path), "manifest_records": len(entries),
            "index_segments": len(index.get("segments", [])), "input_config_sha256": _sha(root / config["input_config"])}


def _check_gate(output: Path, name: str, expected_fingerprint: str) -> dict[str, Any]:
    value = load_json(output / name)
    if value.get("fingerprint") != expected_fingerprint or not value.get("source_gate_passed", value.get("force_gate_passed", False)):
        raise ValueError(f"SPK gate failed or has wrong fingerprint: {name}")
    return value


def _check_gates_independently(root: Path, output: Path, ctx: dict[str, Any], backend: DE441Subset, config: dict[str, Any], previous: dict[str, Any]) -> None:
    from check_apophis_spk_inputs import check_ephemerides, check_forces
    saved_inputs = _check_gate(output, "input_checks.json", previous["fingerprint"])
    saved_forces = _check_gate(output, "force_checks.json", previous["fingerprint"])
    recomputed_inputs = check_ephemerides(ctx, backend, config)
    if recomputed_inputs != {k: v for k, v in saved_inputs.items() if k != "fingerprint"}:
        raise ValueError("saved SPK input gate differs from independent recomputation")
    baseline = next(row for row in previous["records"] if row.get("key") == "pn_sun__ultra")
    recomputed_forces = check_forces(ctx, baseline, config)
    if recomputed_forces != {k: v for k, v in saved_forces.items() if k != "fingerprint"}:
        raise ValueError("saved SPK force gate differs from independent recomputation")


def _verify_native(row: dict[str, Any], sun: Any, initial: Any, times: list[float]) -> dict[str, int]:
    if row.get("native_initial_state") != row.get("initial_state") or row.get("native_state_basis") != NATIVE_BASIS or row.get("reported_state_basis") != OUTPUT_BASIS:
        raise ValueError(f"native basis metadata mismatch: {row['key']}")
    native_initial = rv._state(row["native_initial_state"])
    if native_initial != initial:
        raise ValueError(f"native initial mismatch: {row['key']}")
    sun0 = sun.state_at(0.0)
    def convert(t: float, state: Any):
        sun_t = sun.state_at(t)
        ds = tuple((sun_t.position[i] - sun0.position[i]) - t * sun0.velocity[i] for i in range(3))
        dvs = tuple(sun_t.velocity[i] - sun0.velocity[i] for i in range(3))
        return type(initial)(tuple(state.position[i] - ds[i] for i in range(3)), tuple(state.velocity[i] - dvs[i] for i in range(3)))
    native_requested = row.get("native_solver_requested_states")
    if not isinstance(native_requested, list) or len(native_requested) != 797:
        raise ValueError(f"native requested length mismatch: {row['key']}")
    native_states = [rv._state(item) for item in native_requested]
    converted = [convert(t, state) for t, state in zip(times, native_states, strict=True)]
    if [_flat_state(s) for s in converted] != row.get("requested_states"):
        raise ValueError(f"native requested conversion mismatch: {row['key']}")
    native_end = row.get("native_solver_endpoints"); output_end = row.get("accepted_endpoints")
    if not isinstance(native_end, list) or not isinstance(output_end, list) or len(native_end) != len(output_end) or not native_end:
        raise ValueError(f"native accepted trace mismatch: {row['key']}")
    endpoint_map = {}
    last = None
    for nitem, oitem in zip(native_end, output_end, strict=True):
        if not isinstance(nitem, list) or len(nitem) != 2 or not isinstance(oitem, list) or len(oitem) != 2:
            raise ValueError(f"malformed native endpoint: {row['key']}")
        t = float(nitem[0])
        if not math.isfinite(t) or t != float(oitem[0]) or (last is not None and t <= last):
            raise ValueError(f"native endpoint times invalid: {row['key']}")
        last = t
        state = rv._state(nitem[1]); out = rv._state(oitem[1])
        if out != convert(t, state):
            raise ValueError(f"native endpoint conversion mismatch: {row['key']}")
        endpoint_map[t] = state
    if last != times[-1] or next(iter(endpoint_map)) != 0.0 or endpoint_map[0.0] != initial:
        raise ValueError(f"native endpoint bounds mismatch: {row['key']}")
    for t, state in zip(times, native_states, strict=True):
        if endpoint_map.get(t) != state:
            raise ValueError(f"requested knot is not native endpoint: {row['key']} {t}")
    return {"native_requested": len(native_states), "native_accepted": len(native_end)}


def _flat_state(state: Any) -> list[float]:
    return [*state.position, *state.velocity]


def _helper_row(row: dict[str, Any]) -> dict[str, Any]:
    if row.get("solver") != "rk4" or "rk4_steps" in row.get("solver_stats", {}):
        return row
    result = dict(row); result["solver_stats"] = {**row["solver_stats"], "rk4_steps": row["solver_stats"].get("steps")}; return result


def _verify_reuse(root: Path, row: dict[str, Any], previous: dict[str, Any], label: str, matrix_path: Path) -> None:
    source_key = f"pn_sun__{label}"
    source = next((r for r in previous["records"] if r.get("key") == source_key), None)
    reuse = row.get("reused_from")
    if source is None or not isinstance(reuse, dict) or reuse != {"matrix": str(matrix_path.relative_to(root)), "key": source_key, "sha256": _sha(matrix_path)}:
        raise ValueError(f"baseline reuse provenance mismatch: {row['key']}")
    ignored = {"key", "arm", "fingerprint", "force_specification", "reused_from"}
    if {k: v for k, v in row.items() if k not in ignored} != {k: v for k, v in source.items() if k not in ignored}:
        raise ValueError(f"baseline reused trace differs: {row['key']}")


def _shift(left: list[Any], right: list[Any], au: float, day_s: float) -> dict[str, float]:
    return rv._shift(left, right, au, day_s)


def _sun_provider(ctx: dict[str, Any], backend: DE441Subset, arm: str) -> Any:
    """Build the Sun provider independently of the runner's arm dispatcher."""
    old = ctx["sun_barycentric"]
    if arm in {"baseline", "earth_moon_direct"}:
        return old
    direct = backend.ephemeris(10, 0)
    if arm in {"other_major_direct", "all_direct"}:
        return direct
    if arm == "same_knots":
        times = tuple(t for t in old.epochs_days if -1.0 <= t <= 366.0)
    elif arm == "hourly":
        times = tuple(k / 24.0 for k in range(-24, 8785))
    else:
        raise ValueError(f"unknown SPK arm: {arm}")
    return RelativeEphemerisInterpolator(times, tuple(direct.state_at(t) for t in times))


def _analysis(records: list[dict[str, Any]], au: float, day_s: float, config: dict[str, Any], annual: list[dict[str, Any]], refs: dict[str, list[dict[str, Any]]], previous: list[dict[str, Any]]) -> dict[str, Any]:
    by = {r["key"]: r for r in records}
    annual_errors, diagnostics, dp, cross, effects = [], [], [], [], []
    for arm in ARMS:
        e, u, k = (by[f"{arm}__{label}"] for label in LABELS)
        es, us, ks = ([_state_any(s) for s in r["requested_states"]] for r in (e, u, k))
        ds = _shift(es, us, au, day_s); ds["arm"] = arm; ds["within_budget"] = ds["max_position_shift_km"] * 1000 <= float(config["cross_solver_budget_m"]); dp.append(ds)
        cs = _shift(us, ks, au, day_s); cs.update({"arm": arm, "criterion": "DP_ultra_vs_compensated_RK4", "within_budget": cs["max_position_shift_km"] * 1000 <= float(config["cross_solver_budget_m"])}); cross.append(cs)
        base_ultra = [_state_any(s) for s in by["baseline__ultra"]["requested_states"]]
        base_rk4 = [_state_any(s) for s in by["baseline__rk4_fine"]["requested_states"]]
        def vector_difference(left, right):
            return [_state_any([a - b for a, b in zip(_flat_state(x), _flat_state(y))]) for x, y in zip(left, right, strict=True)]
        ultra_effect, rk4_effect = vector_difference(us, base_ultra), vector_difference(ks, base_rk4)
        effect_difference = _shift(ultra_effect, rk4_effect, au, day_s)
        effects.append({"arm": arm, "ultra_effect": _shift(ultra_effect, [_state_any([0.0] * 6) for _ in ultra_effect], au, day_s),
                        "rk4_effect": _shift(rk4_effect, [_state_any([0.0] * 6) for _ in rk4_effect], au, day_s),
                        "effect_vector_solver_difference": effect_difference})
    for row in records:
        annual_errors.append({"key": row["key"], "annual_daily": row["reference_comparisons"]["annual_daily"]})
        pred = {item["epoch_tdb"]: _state_any(s) for item, s in zip(annual, row["requested_states"], strict=True)}
        days = {}
        for day in config["diagnostic_days"]:
            match = [x for x in refs["annual_daily"] if abs(float(x["epoch_jd_tdb"]) - float(annual[0]["epoch_jd_tdb"]) - float(day)) <= 1e-8]
            if len(match) != 1: raise ValueError(f"missing diagnostic day {day}: {row['key']}")
            p, v = __import__("run_physics_baselines", fromlist=["errors"]).errors(pred[match[0]["epoch_tdb"]], state_from_row(match[0]), au, day_s)
            days[str(day)] = {"position_error_km": p, "velocity_error_m_s": v}
        diagnostics.append({"key": row["key"], "errors": days})
    return {"schema_version": 1, "matched_annual_daily": annual_errors, "diagnostic_days": diagnostics,
            "dp_setting_differences": dp, "cross_solver_differences": cross, "effects_vs_baseline": effects,
            "cross_solver_budget_m": float(config["cross_solver_budget_m"]), "empirical_only": True}


def _state_any(value: Any):
    return rv._state(value)


def verify(root: Path) -> dict[str, Any]:
    root = root.resolve(); config = load_json(root / "configs/apophis_spk_audit.json")
    output = root / config["output_directory"]; matrix_path, freeze_path = output / "matrix.json", output / "freeze.json"
    matrix = load_json(matrix_path); freeze = _check_freeze(root, matrix, freeze_path)
    if matrix.get("input_checks_sha256") != _sha(output / "input_checks.json") or matrix.get("force_checks_sha256") != _sha(output / "force_checks.json"):
        raise ValueError("SPK matrix gate artifact SHA mismatch")
    inventory = _manifest_inventory(root)
    # This context is read-only once the preceding barycentric freeze/design exists.
    from run_apophis_eih_barycentric import context as prior_context
    ctx, _, _, _, _, _, _ = prior_context(root)
    backend = DE441Subset(root, ctx["au"], ctx["day"])
    input_provenance = _check_inputs(root, config, freeze, backend, ctx, output)
    annual, refs, data = _teachers(root); previous_matrix_path = root / config["previous_matrix"]
    previous = load_json(previous_matrix_path); previous_records = previous["records"]
    if len(previous_records) != 12: raise ValueError("previous barycentric matrix must have 12 records")
    from verify_apophis_eih_barycentric import _check_freeze as check_previous_freeze
    check_previous_freeze(root, previous, root / "outputs/apophis_eih_barycentric/freeze.json")
    _check_gates_independently(root, output, ctx, backend, config, {"records": previous_records, "fingerprint": freeze["fingerprint"]})
    expected = {f"{arm}__{label}" for arm in ARMS for label in LABELS}; records = matrix.get("records")
    if not isinstance(records, list) or len(records) != 18 or {r.get("key") for r in records} != expected: raise ValueError("SPK matrix keys/size mismatch")
    initial = {"annual_hourly": state_from_row(refs["annual_hourly"][0])}
    for row in records:
        key, arm, label = row.get("key"), row.get("arm"), str(row.get("key", "")).rsplit("__", 1)[-1]
        if key != f"{arm}__{label}" or arm not in ARMS or label not in LABELS or row.get("fingerprint") != freeze["fingerprint"]:
            raise ValueError(f"SPK record metadata mismatch: {key}")
        if row.get("force_specification") != _force_spec(arm) or row.get("mode") != "relative_calendar_knots" or row.get("accepted_time_basis") != "relative_days_since_start" or row.get("native_state_basis") != NATIVE_BASIS or row.get("reported_state_basis") != OUTPUT_BASIS or row.get("initial_source") != "annual_hourly" or row.get("sun_origin_acceleration_subtraction") is not False or row.get("rk4_step_selection_state_basis") != OUTPUT_BASIS:
            raise ValueError(f"SPK physics/frame metadata mismatch: {key}")
        if row.get("setting") != _expected_setting(root, label): raise ValueError(f"SPK solver setting mismatch: {key}")
        if row.get("solver") != ("rk4" if label == "rk4_fine" else "dopri54") or row.get("numerical_mode") != ("compensated" if label == "rk4_fine" else "precise_dp"):
            raise ValueError(f"SPK solver metadata mismatch: {key}")
        if not isinstance(row.get("solver_stats"), dict) or row["solver_stats"].get("compensated") is not True: raise ValueError(f"SPK compensated flag missing: {key}")
        times = rv._expected_times(row, annual)
        if row.get("requested_times_relative_days") != times: raise ValueError(f"SPK time axis mismatch: {key}")
        rv._verify_record(_helper_row(row), annual, refs, initial, float(data["constants"]["au_km"]), float(data["constants"]["day_s"]))
        sun = _sun_provider(ctx, backend, arm)
        _verify_native(row, sun, initial["annual_hourly"], times)
        if arm == "baseline": _verify_reuse(root, row, load_json(previous_matrix_path), label, previous_matrix_path)
    checkpoints = sorted((output / "checkpoints").glob("*.json"))
    if {p.stem for p in checkpoints} != expected: raise ValueError("SPK checkpoint set mismatch")
    by = {r["key"]: r for r in records}; checkpoint_hashes = {}
    for path in checkpoints:
        if load_json(path) != by[path.stem]: raise ValueError(f"SPK checkpoint differs: {path.name}")
        checkpoint_hashes[path.name] = _sha(path)
    au, day_s = float(data["constants"]["au_km"]), float(data["constants"]["day_s"])
    analysis = _analysis(records, au, day_s, {**load_json(root / "configs/apophis_eih.json"), **config}, annual, refs, previous_records)
    provenance = {"matrix_sha256": _sha(matrix_path), "freeze_sha256": _sha(freeze_path), "fingerprint": freeze["fingerprint"],
                  "verifier_sha256": _sha(root / "src/verify_apophis_spk_audit.py"), "input_provenance": input_provenance,
                  "reference_verifier_sha256": _sha(root / "src/verify_apophis_reference_time.py"),
                  "propagation_verifier_sha256": _sha(root / "src/verify_precise_propagation.py")}
    analysis["provenance"] = provenance; _write_immutable(output / "analysis.json", analysis)
    scalar_count = sum(2 * (len(annual) + sum(int(r["reference_comparisons"][n]["samples"]) for n in refs)) for r in records)
    verification = {"schema_version": 1, "matrix": str(matrix_path.relative_to(root)), **provenance,
                    "config_sha256": _sha(root / "configs/apophis_spk_audit.json"), "inputs_sha256": _sha(output / "inputs.json"),
                    "run_count": len(records), "checkpoint_count": len(checkpoints), "checkpoint_sha256": checkpoint_hashes,
                    "manifest": inventory, "primary_rows": len(annual), "reference_tables": len(refs),
                    "accepted_endpoint_count": sum(len(r["accepted_endpoints"]) for r in records), "scalar_error_count": scalar_count,
                    "paired_scalar_count": len(ARMS) * len(annual) * 2, "cross_solver_budget_m": config["cross_solver_budget_m"],
                    "cross_solver_within_budget": all(x["within_budget"] for x in analysis["cross_solver_differences"])}
    _write_immutable(output / "verification.json", verification); return verification


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--root", type=Path, default=Path(".")); args = parser.parse_args()
    print(json.dumps(verify(args.root), indent=2, ensure_ascii=False))

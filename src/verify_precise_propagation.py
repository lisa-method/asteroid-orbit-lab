"""Offline verifier for the frozen endpoint-consistent RK4 experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
from typing import Any

from orbit_baselines import State
from run_eda import load_json
from run_physics_baselines import state_from_row

import verify_apophis_reference_time as reference_verifier
import verify_apophis_shape_weak_force as shape_verifier


SUMMARY_FIELDS = reference_verifier.SUMMARY_FIELDS


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _state(value: Any) -> State:
    return reference_verifier._state(value)


def _shift(left: list[State], right: list[State], au_km: float, day_s: float) -> dict[str, float]:
    return reference_verifier._shift(left, right, au_km, day_s)


def _write_immutable(path: Path, value: Any) -> None:
    if path.exists():
        if load_json(path) != value:
            raise RuntimeError(f"immutable artifact differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _check_freeze(root: Path, matrix: dict[str, Any], freeze_path: Path) -> dict[str, Any]:
    freeze = load_json(freeze_path)
    if matrix.get("fingerprint") != freeze.get("fingerprint") or matrix.get("freeze_sha256") != _sha(freeze_path):
        raise ValueError("matrix/freeze fingerprint mismatch")
    for name, digest in freeze.get("hashes", {}).items():
        path = root / name if not Path(name).is_absolute() else Path(name)
        if not path.is_file() or _sha(path) != digest:
            raise ValueError(f"frozen hash mismatch: {name}")
    runtime = {"executable": __import__("sys").executable, "version": __import__("sys").version, "platform": platform.platform()}
    if freeze.get("runtime") != runtime:
        raise ValueError("current runtime differs from frozen runtime")
    payload = {"hashes": freeze["hashes"], "runtime": freeze["runtime"]}
    if hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest() != freeze.get("fingerprint"):
        raise ValueError("freeze fingerprint recomputation mismatch")
    return freeze


def _load_teachers(root: Path, previous: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, Any]]:
    validation = previous.get("input_validation")
    if not isinstance(validation, dict) or validation.get("complete") is not True:
        raise ValueError("previous reference validation is incomplete")
    if load_json(root / "data/processed/apophis_reference_time/validation.json") != validation:
        raise ValueError("previous reference validation differs from disk")
    paths = dict(validation["paths"])
    paths["long_repeat"] = "data/raw/apophis_reference_time/asteroid_99942_long_repeat.json"
    parse = __import__("run_eda", fromlist=["parse_horizons"]).parse_horizons
    references = {name: parse((root / path) if not Path(path).is_absolute() else Path(path), "99942", "Apophis")[1] for name, path in paths.items()}
    base = load_json(root / "configs/apophis_solver_audit.json")
    data = load_json(root / base["data_config"])
    event = load_json(root / base["event_config"])
    audit = __import__("run_apophis_solver_audit", fromlist=["_target_rows"])
    annual, _ = audit._target_rows(root, data, event, 365)
    return annual, references, data


def _expected_specs(config: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for mode, scales in config["old_baseline_modes"].items():
        keys.update(f"{mode}__baseline__old__{scale}" for scale in scales)
    for item in config["additional_compensated"]:
        keys.update(f"compensated__{item['arm']}__{item['initial_source']}__{scale}" for scale in item["scales"])
    for arm in config["dp_arms"]:
        for source in config["dp_initial_sources"]:
            for label in config["dp_labels"]:
                keys.add(f"dopri54__{arm}__{source}__{label}")
    return keys


def _validate_records(records: list[dict[str, Any]], config: dict[str, Any], base: dict[str, Any]) -> set[str]:
    expected = _expected_specs(config)
    keys = {str(row.get("key")) for row in records}
    if len(records) != 24 or keys != expected:
        raise ValueError("precise propagation records differ from frozen specification")
    tolerances = {item["label"]: item for item in base["dopri54_tolerances"]}
    for row in records:
        key = row["key"]
        if key != f"{row['numerical_mode']}__{row['arm']}__{row['initial_source']}__{row['setting']['label'] if isinstance(row.get('setting'), dict) else row['setting']}":
            raise ValueError(f"noncanonical record key: {key}")
        if row.get("mode") != "relative_calendar_knots" or row.get("accepted_time_basis") != "relative_days_since_start":
            raise ValueError(f"time mode mismatch: {key}")
        numerical_mode = row["numerical_mode"]
        if numerical_mode == "dopri54":
            if row.get("solver") != "dopri54" or row.get("setting") != tolerances.get(row["setting"].get("label")):
                raise ValueError(f"DP setting mismatch: {key}")
        elif numerical_mode in {"legacy", "endpoint", "compensated"}:
            if row.get("solver") != "rk4" or not isinstance(row.get("setting"), (int, float)) or isinstance(row["setting"], bool) or float(row["setting"]) <= 0.0:
                raise ValueError(f"RK4 setting mismatch: {key}")
        else:
            raise ValueError(f"unknown numerical mode: {key}")
    return keys


def _check_previous_traces(records: list[dict[str, Any]], previous_reference: dict[str, Any], previous_shape: dict[str, Any]) -> tuple[int, int]:
    reference_by_key = {row["key"]: row for row in previous_reference["records"]}
    shape_by_key = {row["key"]: row for row in previous_shape["records"]}
    legacy_checked = dp_checked = 0
    for row in records:
        if row["numerical_mode"] == "legacy":
            old = reference_by_key.get(f"relative_calendar_knots__rk4__{row['setting']}__{row['initial_source']}")
            if old is None or row["requested_states"] != old["requested_states"]:
                raise ValueError(f"legacy trace differs from previous reference matrix: {row['key']}")
            legacy_checked += 1
        elif row["numerical_mode"] == "dopri54":
            old = shape_by_key.get(f"{row['arm']}__{row['initial_source']}__{row['setting']['label']}")
            if old is None or row["requested_states"] != old["requested_states"]:
                raise ValueError(f"DP trace differs from previous shape matrix: {row['key']}")
            dp_checked += 1
    if legacy_checked != 3 or dp_checked != 8:
        raise ValueError(f"previous traces checked: legacy={legacy_checked}, DP={dp_checked}")
    return legacy_checked, dp_checked


def _expected_pairs(records: list[dict[str, Any]], au: float, day_s: float) -> list[dict[str, Any]]:
    pairs = []
    for index, left in enumerate(records):
        for right in records[index + 1:]:
            if left["initial_source"] == right["initial_source"] and left["arm"] == right["arm"]:
                pairs.append({"left": left["key"], "right": right["key"], **_shift([_state(s) for s in left["requested_states"]], [_state(s) for s in right["requested_states"]], au, day_s)})
    return pairs


def _analysis(records: list[dict[str, Any]], au: float, day_s: float) -> dict[str, Any]:
    by_key = {row["key"]: row for row in records}
    teacher_rows = []
    for row in records:
        refs = row["reference_comparisons"]
        matched = "old_daily" if row["initial_source"] == "old" else "annual_daily"
        teacher_rows.append({"key": row["key"], "numerical_mode": row["numerical_mode"], "arm": row["arm"], "initial_source": row["initial_source"], "setting": row["setting"], "matched_teacher": matched, "matched_error": refs[matched], "old_daily": refs["old_daily"], "long_repeat": refs["long_repeat"], "annual_daily": refs["annual_daily"]})
    rk4_dp = []
    for row in records:
        if row["solver"] != "rk4" or row["arm"] not in {"baseline", "j3j4"}:
            continue
        dp = by_key.get(f"dopri54__{row['arm']}__{row['initial_source']}__extreme")
        if dp is not None:
            rk4_dp.append({"rk4_key": row["key"], "dp_key": dp["key"], "shift": _shift([_state(s) for s in row["requested_states"]], [_state(s) for s in dp["requested_states"]], au, day_s)})
    adjacent = []
    for arm, source in sorted({(row["arm"], row["initial_source"]) for row in records if row["numerical_mode"] == "compensated"}):
        rows = sorted((row for row in records if row["numerical_mode"] == "compensated" and row["arm"] == arm and row["initial_source"] == source), key=lambda row: float(row["setting"]), reverse=True)
        for left, right in zip(rows, rows[1:]):
            adjacent.append({"left": left["key"], "right": right["key"], "shift": _shift([_state(s) for s in left["requested_states"]], [_state(s) for s in right["requested_states"]], au, day_s)})
    effects = []
    for source in ("old", "annual_hourly"):
        for setting in (0.25, 0.125):
            arm = by_key.get(f"compensated__j3j4__{source}__{setting}")
            baseline = by_key.get(f"compensated__baseline__{source}__{setting}")
            if arm and baseline:
                effects.append({"solver": "rk4", "initial_source": source, "setting": setting, "effect_shift": _shift([_state(s) for s in arm["requested_states"]], [_state(s) for s in baseline["requested_states"]], au, day_s)})
        for label in ("tighter", "extreme"):
            arm = by_key[f"dopri54__j3j4__{source}__{label}"]
            baseline = by_key[f"dopri54__baseline__{source}__{label}"]
            effects.append({"solver": "dopri54", "initial_source": source, "setting": label, "effect_shift": _shift([_state(s) for s in arm["requested_states"]], [_state(s) for s in baseline["requested_states"]], au, day_s)})
    criteria = []
    for source in ("old", "annual_hourly"):
        for arm in ("baseline", "j3j4"):
            scales = [0.125] + ([0.0625] if source == "old" and arm == "baseline" else [])
            dp = by_key[f"dopri54__{arm}__{source}__extreme"]
            for scale in scales:
                comp = by_key[f"compensated__{arm}__{source}__{scale}"]
                shift = _shift([_state(s) for s in comp["requested_states"]], [_state(s) for s in dp["requested_states"]], au, day_s)
                criteria.append({"arm": arm, "initial_source": source, "compensated_scale": scale, "dp_key": dp["key"], "max_position_difference_m": shift["max_position_shift_km"] * 1000.0, "passed": shift["max_position_shift_km"] * 1000.0 <= 1.0, "empirical_only": True})
    return {"schema_version": 1, "teacher_error_table": teacher_rows, "rk4_to_dp_extreme": rk4_dp, "compensated_adjacent_scale_convergence": adjacent, "j3j4_vector_effects": effects, "fine_compensated_vs_dp_extreme": criteria, "criterion_all_passed": all(item["passed"] for item in criteria)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    root = args.root.resolve()
    output = root / "outputs/precise_propagation"
    matrix_path, freeze_path = output / "matrix.json", output / "freeze.json"
    matrix = load_json(matrix_path)
    freeze = _check_freeze(root, matrix, freeze_path)
    manifest = shape_verifier._manifest_inventory(root)
    config = load_json(root / "configs/precise_propagation.json")
    base = load_json(root / "configs/apophis_solver_audit.json")
    previous_reference = load_json(root / "outputs/apophis_reference_time/matrix.json")
    previous_shape = load_json(root / "outputs/apophis_shape_weak_force/matrix.json")
    annual, references, data = _load_teachers(root, previous_reference)
    if len(annual) != 797 or set(references) != {"annual_daily", "annual_hourly", "short_daily", "short_hourly", "old_daily", "old_refined", "recent_refined", "long_repeat"}:
        raise ValueError("teacher set is not the frozen eight-table set")
    records = matrix.get("records")
    if not isinstance(records, list):
        raise ValueError("matrix records missing")
    keys = _validate_records(records, config, base)
    if any(row.get("fingerprint") != freeze["fingerprint"] for row in records):
        raise ValueError("record fingerprint differs from freeze")
    initial_sources = {"old": state_from_row(annual[0]), "annual_hourly": state_from_row(references["annual_hourly"][0])}
    au, day_s = float(data["constants"]["au_km"]), float(data["constants"]["day_s"])
    for row in records:
        reference_verifier._verify_record(row, annual, references, initial_sources, au, day_s)
    previous_counts = _check_previous_traces(records, previous_reference, previous_shape)
    checkpoints = sorted((output / "checkpoints").glob("*.json"))
    if len(checkpoints) != 24:
        raise ValueError(f"checkpoint count mismatch: {len(checkpoints)}")
    by_key = {row["key"]: row for row in records}
    checkpoint_hashes = {}
    for path in checkpoints:
        if path.stem not in by_key or load_json(path) != by_key[path.stem]:
            raise ValueError(f"checkpoint differs from matrix: {path.name}")
        checkpoint_hashes[path.name] = _sha(path)
    expected_pairs = _expected_pairs(records, au, day_s)
    if matrix.get("paired_shifts") != expected_pairs or len(expected_pairs) != 84:
        raise ValueError(f"paired shift mismatch: expected {len(expected_pairs)}")
    analysis = _analysis(records, au, day_s)
    verifier_path = root / "src/verify_precise_propagation.py"
    analysis.update({"matrix_sha256": _sha(matrix_path), "verifier_sha256": _sha(verifier_path), "reference_verifier_sha256": _sha(root / "src/verify_apophis_reference_time.py")})
    _write_immutable(output / "analysis.json", analysis)
    scalar_count = (len(records) * (len(annual) + sum(int(row["reference_comparisons"][name]["samples"]) for name in references))) * 2
    verification = {"schema_version": 1, "matrix": str(matrix_path.relative_to(root)), "matrix_sha256": _sha(matrix_path), "verifier_sha256": _sha(verifier_path), "reference_verifier_sha256": _sha(root / "src/verify_apophis_reference_time.py"), "shape_verifier_sha256": _sha(root / "src/verify_apophis_shape_weak_force.py"), "freeze_sha256": _sha(freeze_path), "fingerprint": freeze["fingerprint"], "run_count": len(records), "unique_run_keys": len(keys), "paired_shift_count": len(expected_pairs), "paired_scalar_count": len(expected_pairs) * len(annual) * 2, "scalar_error_count": scalar_count, "accepted_endpoint_count": sum(len(row["accepted_endpoints"]) for row in records), "primary_rows": len(annual), "reference_tables": len(references), "checkpoint_count": len(checkpoints), "checkpoint_sha256": checkpoint_hashes, "manifest": manifest, "previous_trace_checks": {"legacy": previous_counts[0], "dopri54": previous_counts[1]}, "criterion": analysis["fine_compensated_vs_dp_extreme"]}
    _write_immutable(output / "verification.json", verification)
    print(json.dumps(verification, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

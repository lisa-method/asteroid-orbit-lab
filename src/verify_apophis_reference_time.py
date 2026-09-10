"""Offline integrity and trace verifier for the frozen reference-time matrix."""

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
from relative_time_dynamics import relative_rows
from run_eda import load_json, parse_horizons
from run_physics_baselines import errors, state_from_row

SUMMARY_FIELDS = (
    "max_grid_position_error_km", "final_position_error_km",
    "max_grid_velocity_error_m_s", "final_velocity_error_m_s",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_immutable(path: Path, value: Any) -> None:
    if path.exists():
        if load_json(path) != value:
            raise RuntimeError(f"verification artifact differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _state(value: Any) -> State:
    if (not isinstance(value, list) or len(value) != 6 or any(
        not isinstance(component, (int, float)) or isinstance(component, bool) or not math.isfinite(float(component))
        for component in value
    )):
        raise ValueError("invalid finite six-state")
    return State(tuple(float(v) for v in value[:3]), tuple(float(v) for v in value[3:]))  # type: ignore[arg-type]


def _flat(state: State) -> list[float]:
    return [*state.position, *state.velocity]


def _shift(left: list[State], right: list[State], au_km: float, day_s: float) -> dict[str, float]:
    if len(left) != len(right) or not left:
        raise ValueError("paired traces have incompatible lengths")
    positions = [norm(subtract(a.position, b.position)) * au_km for a, b in zip(left, right)]
    velocities = [norm(subtract(a.velocity, b.velocity)) * au_km * 1000.0 / day_s for a, b in zip(left, right)]
    return {"max_position_shift_km": max(positions), "final_position_shift_km": positions[-1],
            "max_velocity_shift_m_s": max(velocities), "final_velocity_shift_m_s": velocities[-1]}


def _summary(predictions: list[State], truth: list[State], au_km: float, day_s: float) -> dict[str, float]:
    if len(predictions) != len(truth) or not predictions:
        raise ValueError("summary traces have incompatible lengths")
    position = [errors(a, b, au_km, day_s)[0] for a, b in zip(predictions, truth)]
    velocity = [errors(a, b, au_km, day_s)[1] for a, b in zip(predictions, truth)]
    return {"max_grid_position_error_km": max(position), "final_position_error_km": position[-1],
            "max_grid_velocity_error_m_s": max(velocity), "final_velocity_error_m_s": velocity[-1]}


def _assert_close(actual: float, expected: float, tolerance: float, label: str) -> None:
    if not math.isfinite(actual) or not math.isfinite(expected) or abs(actual - expected) > tolerance:
        raise ValueError(f"{label} mismatch: {actual!r} versus {expected!r}")


def _assert_summary(saved: Any, expected: dict[str, float], label: str, tolerance: float = 1e-10) -> None:
    if not isinstance(saved, dict) or set(saved) != set(SUMMARY_FIELDS):
        raise ValueError(f"{label} has wrong summary fields")
    for field in SUMMARY_FIELDS:
        _assert_close(float(saved[field]), expected[field], tolerance if field.endswith("km") else max(tolerance, 1e-7), f"{label} {field}")


def _record_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _manifest_inventory(root: Path) -> dict[str, Any]:
    all_records: list[tuple[str, str, int, str]] = []
    manifest_paths = sorted((root / "data/checksums").glob("*manifest.json"))
    for manifest_path in manifest_paths:
        manifest = load_json(manifest_path)
        entries = manifest.get("files")
        if entries is None:
            entries = manifest.get("downloads")
        if not isinstance(entries, list):
            raise ValueError(f"manifest has no files/downloads list: {manifest_path}")
        for record in entries:
            if not isinstance(record, dict) or not all(key in record for key in ("path", "sha256", "bytes")):
                raise ValueError(f"incomplete manifest record: {manifest_path}")
            raw_name = str(record["path"])
            raw = _record_path(root, raw_name)
            if not raw.is_file() or _sha(raw).lower() != str(record["sha256"]).lower() or raw.stat().st_size != int(record["bytes"]):
                raise ValueError(f"manifest raw hash/size mismatch: {raw_name}")
            relative = raw.relative_to(root).as_posix() if raw.is_absolute() and raw.is_relative_to(root) else raw_name
            if subprocess.run(["git", "ls-files", "--error-unmatch", "--", relative], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0:
                raise ValueError(f"manifest raw file is tracked by git: {relative}")
            if subprocess.run(["git", "check-ignore", "--no-index", "--quiet", relative], cwd=root, check=False).returncode != 0:
                raise ValueError(f"manifest raw file is not gitignored: {relative}")
            all_records.append((relative, str(record["sha256"]).lower(), int(record["bytes"]), manifest_path.name))
    unique_paths = {record[0] for record in all_records}
    if len(unique_paths) != 134:
        raise ValueError(f"expected 134 unique raw paths, found {len(unique_paths)}")
    return {"manifest_count": len(manifest_paths), "manifest_records": len(all_records),
            "unique_raw_paths": len(unique_paths), "raw_sha256_size_records": len(set((s, b) for _, s, b, _ in all_records))}


def _load_teacher_rows(root: Path, base: dict[str, Any], validation: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    if validation.get("complete") is not True:
        raise ValueError("reference input validation is incomplete")
    paths = dict(validation.get("paths", {}))
    paths["long_repeat"] = "data/raw/apophis_reference_time/asteroid_99942_long_repeat.json"
    references = {name: parse_horizons(_record_path(root, relative), "99942", "Apophis")[1] for name, relative in paths.items()}
    data = load_json(root / base["data_config"])
    event = load_json(root / base["event_config"])
    audit = __import__("run_apophis_solver_audit", fromlist=["_target_rows"])
    annual, _ = audit._target_rows(root, data, event, 365)
    return annual, references


def _expected_times(row: dict[str, Any], annual: list[dict[str, Any]]) -> list[float]:
    mode, origin = row["mode"], float(row["origin_jd_tdb"])
    if mode == "relative_calendar_knots":
        return [float(item["epoch_relative_days"]) for item in relative_rows(annual, origin, row["origin_calendar_tdb"], "calendar")]
    if mode in {"legacy", "relative_float_knots"}:
        return [float(item["epoch_jd_tdb"]) - origin for item in annual]
    raise ValueError(f"unknown mode: {mode}")


def _native_times(row: dict[str, Any], expected_relative: list[float]) -> list[float]:
    basis = row.get("accepted_time_basis")
    if basis == "absolute_jd_tdb":
        return [float(row["origin_jd_tdb"]) + t for t in expected_relative]
    if basis == "relative_days_since_start":
        return expected_relative
    raise ValueError(f"unknown accepted time basis: {basis}")


def _verify_native_trace(row: dict[str, Any], expected_relative: list[float], expected_initial: State) -> list[State]:
    accepted = row.get("accepted_endpoints")
    if not isinstance(accepted, list) or not accepted:
        raise ValueError(f"accepted endpoint trace missing: {row.get('key')}")
    times: list[float] = []
    states: list[State] = []
    for item in accepted:
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError("malformed accepted endpoint")
        time = float(item[0])
        if not math.isfinite(time):
            raise ValueError("accepted trace contains nonfinite time")
        times.append(time)
        states.append(_state(item[1]))
    if any(right <= left for left, right in zip(times, times[1:])):
        raise ValueError("accepted trace is not strictly increasing")
    basis = row["accepted_time_basis"]
    native_expected = _native_times(row, expected_relative)
    native_origin = float(row["origin_jd_tdb"]) if basis == "absolute_jd_tdb" else 0.0
    if times[0] != native_origin or times[-1] != native_expected[-1]:
        raise ValueError(f"accepted trace bounds mismatch: {row['key']}")
    if states[0] != expected_initial:
        raise ValueError(f"accepted trace initial state mismatch: {row['key']}")
    by_time: dict[float, State] = {}
    for time, state in zip(times, states):
        if time in by_time:
            raise ValueError("accepted trace has duplicate native time")
        by_time[time] = state
    requested = row.get("requested_states")
    if not isinstance(requested, list) or len(requested) != len(expected_relative) or len(requested) != 797:
        raise ValueError(f"requested state length mismatch: {row.get('key')}")
    for native_time, value in zip(native_expected, requested):
        accepted_state = by_time.get(native_time)
        if accepted_state is None or accepted_state != _state(value):
            raise ValueError(f"requested state is not the exact native endpoint: {row['key']} {native_time!r}")
    return states


def _verify_record(row: dict[str, Any], annual: list[dict[str, Any]], references: dict[str, list[dict[str, Any]]], initial_sources: dict[str, State], au_km: float, day_s: float) -> dict[str, int]:
    if not isinstance(row, dict) or not isinstance(row.get("key"), str):
        raise ValueError("malformed matrix record")
    source = row.get("initial_source")
    if source not in initial_sources:
        raise ValueError(f"unknown initial source: {source}")
    expected_basis = "absolute_jd_tdb" if row.get("mode") == "legacy" and row.get("solver") == "dopri54" else "relative_days_since_start"
    if row.get("accepted_time_basis") != expected_basis:
        raise ValueError(f"accepted time basis mismatch: {row['key']}")
    if float(row["origin_jd_tdb"]) != float(annual[0]["epoch_jd_tdb"]) or row.get("origin_calendar_tdb") != annual[0]["epoch_tdb"]:
        raise ValueError(f"origin mismatch: {row['key']}")
    initial = _state(row.get("initial_state"))
    if initial != initial_sources[source]:
        raise ValueError(f"initial source state mismatch: {row['key']}")
    expected_relative = _expected_times(row, annual)
    if row.get("requested_times_relative_days") != expected_relative or len(expected_relative) != 797:
        raise ValueError(f"requested time axis mismatch: {row['key']}")
    accepted_states = _verify_native_trace(row, expected_relative, initial)
    requested = [_state(value) for value in row["requested_states"]]
    if requested[0] != initial or any(not math.isfinite(x) for state in accepted_states for x in _flat(state)):
        raise ValueError(f"state finiteness/initial mismatch: {row['key']}")
    primary = _summary(requested, [state_from_row(item) for item in annual], au_km, day_s)
    _assert_summary(row.get("primary_old_grid"), primary, f"{row['key']} primary")
    predictions = {item["epoch_tdb"]: state for item, state in zip(annual, requested)}
    compared = row.get("reference_comparisons")
    if not isinstance(compared, dict) or set(compared) != set(references):
        raise ValueError(f"reference comparison key mismatch: {row['key']}")
    for name, reference in references.items():
        pairs = [(predictions[item["epoch_tdb"]], state_from_row(item)) for item in reference if item["epoch_tdb"] in predictions]
        if not pairs:
            raise ValueError(f"reference has no common timestamps: {row['key']} {name}")
        expected = _summary([pair[0] for pair in pairs], [pair[1] for pair in pairs], au_km, day_s)
        saved = compared[name]
        if not isinstance(saved, dict) or int(saved.get("samples", -1)) != len(pairs):
            raise ValueError(f"reference sample count mismatch: {row['key']} {name}")
        _assert_summary({field: saved.get(field) for field in SUMMARY_FIELDS}, expected, f"{row['key']} reference {name}")
    stats = row.get("solver_stats")
    if not isinstance(stats, dict):
        raise ValueError(f"solver stats missing: {row['key']}")
    expected_steps = len(accepted_states) - 1
    if row["solver"] == "rk4" and int(stats.get("rk4_steps", -1)) != expected_steps:
        raise ValueError(f"RK4 endpoint count mismatch: {row['key']}")
    if row["solver"] == "dopri54" and int(stats.get("accepted_steps", -1)) != expected_steps:
        raise ValueError(f"DP accepted count mismatch: {row['key']}")
    return {"references": len(compared), "primary_scalars": len(SUMMARY_FIELDS), "reference_scalars": len(compared) * len(SUMMARY_FIELDS)}


def _run_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    setting = row.get("setting")
    label = setting.get("label") if isinstance(setting, dict) else str(setting)
    return str(row.get("mode")), str(row.get("solver")), str(label), str(row.get("initial_source"))


def _verify_expected_runs(records: list[dict[str, Any]], config: dict[str, Any], tolerances: dict[str, dict[str, Any]]) -> set[tuple[str, str, str, str]]:
    if len(records) != 18:
        raise ValueError(f"matrix must contain exactly 18 records, found {len(records)}")
    keys = {_run_key(row) for row in records}
    if len(keys) != 18 or any(not row.get("fingerprint") for row in records):
        raise ValueError("matrix run keys or fingerprints are invalid")
    expected: set[tuple[str, str, str, str]] = set()
    for mode in ("legacy", *config["relative_modes"]):
        prefix = "legacy" if mode == "legacy" else "relative"
        expected.update((mode, "dopri54", label, "old") for label in config[prefix + "_dp"])
        expected.update((mode, "rk4", str(scale), "old") for scale in config[prefix + "_rk4"])
    expected.update(("relative_calendar_knots", "dopri54", label, "annual_hourly") for label in config["new_initial_dp"])
    if keys != expected:
        raise ValueError("matrix run key/settings/source set differs from config")
    for row in records:
        if row["solver"] == "dopri54":
            label = _run_key(row)[2]
            if row.get("setting") != tolerances.get(label):
                raise ValueError(f"DP tolerance setting mismatch: {row['key']}")
        elif not isinstance(row.get("setting"), (int, float)) or isinstance(row["setting"], bool) or float(row["setting"]) not in {0.5, 0.25, 0.125}:
            raise ValueError(f"RK4 setting mismatch: {row['key']}")
    return keys


def _expected_pairs(records: list[dict[str, Any]], au_km: float, day_s: float) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for index, left in enumerate(records):
        for right in records[index + 1:]:
            if ((left["mode"] == right["mode"] and left["initial_source"] == right["initial_source"]) or
                    (left["solver"] == right["solver"] and left["setting"] == right["setting"])):
                pairs.append({"left": left["key"], "right": right["key"], **_shift([_state(v) for v in left["requested_states"]], [ _state(v) for v in right["requested_states"]], au_km, day_s)})
    return pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    root = args.root.resolve()
    output_dir = root / "outputs/apophis_reference_time"
    result_path, freeze_path = output_dir / "matrix.json", output_dir / "freeze.json"
    result, freeze = load_json(result_path), load_json(freeze_path)
    if result.get("fingerprint") != freeze.get("fingerprint") or result.get("freeze_sha256") != _sha(freeze_path):
        raise ValueError("matrix/freeze fingerprint mismatch")
    for relative, digest in freeze.get("hashes", {}).items():
        path = _record_path(root, relative)
        if not path.is_file() or _sha(path) != digest:
            raise ValueError(f"frozen source hash mismatch: {relative}")
    runtime = {"executable": sys.executable, "version": sys.version, "platform": platform.platform()}
    if freeze.get("runtime") != runtime:
        raise ValueError("current runtime differs from frozen runtime")
    payload = {"hashes": freeze["hashes"], "runtime": freeze["runtime"]}
    if hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest() != freeze.get("fingerprint"):
        raise ValueError("freeze fingerprint recomputation mismatch")
    manifest_stats = _manifest_inventory(root)
    config = load_json(root / "configs/apophis_reference_time.json")
    base = load_json(root / config["baseline_config"])
    data = load_json(root / base["data_config"])
    validation_path = root / "data/processed/apophis_reference_time/validation.json"
    repeat_path = root / "data/processed/apophis_reference_time/repeat_validation.json"
    validation, repeat_validation = load_json(validation_path), load_json(repeat_path)
    if result.get("input_validation") != validation or result.get("repeat_validation") != repeat_validation:
        raise ValueError("matrix input/repeat validation differs from frozen disk")
    annual, references = _load_teacher_rows(root, base, validation)
    if len(annual) != 797 or set(references) != {"annual_daily", "annual_hourly", "short_daily", "short_hourly", "old_daily", "old_refined", "recent_refined", "long_repeat"}:
        raise ValueError("teacher row set is not the frozen expected set")
    records = result.get("records")
    if not isinstance(records, list):
        raise ValueError("matrix records missing")
    tolerances = {item["label"]: item for item in base["dopri54_tolerances"]}
    keys = _verify_expected_runs(records, config, tolerances)
    if any(row.get("fingerprint") != freeze["fingerprint"] for row in records):
        raise ValueError("record fingerprint differs from freeze fingerprint")
    initial_sources = {"old": state_from_row(annual[0]), "annual_hourly": state_from_row(references["annual_hourly"][0])}
    au_km, day_s = float(data["constants"]["au_km"]), float(data["constants"]["day_s"])
    checkpoint_files = sorted((output_dir / "checkpoints").glob("*.json"))
    if len(checkpoint_files) != len(records):
        raise ValueError(f"checkpoint count mismatch: {len(checkpoint_files)}")
    by_key = {row["key"]: row for row in records}
    checkpoint_hashes = {}
    for path in checkpoint_files:
        checkpoint = load_json(path)
        if checkpoint.get("key") not in by_key or checkpoint != by_key[checkpoint["key"]]:
            raise ValueError(f"checkpoint differs from matrix: {path.name}")
        checkpoint_hashes[path.name] = _sha(path)
    counts = {"primary_points": 0, "reference_points": 0, "primary_scalars": 0, "reference_scalars": 0, "references": 0, "accepted_endpoints": 0}
    for row in records:
        current = _verify_record(row, annual, references, initial_sources, au_km, day_s)
        counts["accepted_endpoints"] += len(row["accepted_endpoints"])
        counts["primary_points"] += len(annual)
        counts["reference_points"] += sum(int(row["reference_comparisons"][name]["samples"]) for name in references)
        for key in ("primary_scalars", "reference_scalars", "references"):
            counts[key] += current[key]
    previous = load_json(root / "outputs/apophis_moon_venus/v1_1/apophis_moon_venus_results.json")
    legacy_count = 0
    for row in records:
        if row["mode"] != "legacy":
            continue
        matches = [item for item in previous["runs"] if item["arm"] == "earthmoon_venus15m" and item["solver"] == row["solver"] and item["setting"] == row["setting"]]
        if len(matches) != 1:
            raise ValueError(f"legacy baseline match missing/ambiguous: {row['key']}")
        for field in SUMMARY_FIELDS:
            _assert_close(float(row["primary_old_grid"][field]), float(matches[0]["summary_old_grid"][field]), float(config["baseline_absolute_tolerance"]), f"legacy baseline {row['key']} {field}")
        legacy_count += 1
    if legacy_count != 4:
        raise ValueError("four legacy baselines were not checked")
    expected_pairs = _expected_pairs(records, au_km, day_s)
    if result.get("paired_shifts") != expected_pairs:
        raise ValueError("paired shifts differ from independent recomputation")
    if len(expected_pairs) != 56:
        raise ValueError(f"expected 56 paired shifts, found {len(expected_pairs)}")
    previous_checkpoints = {}
    for path in sorted((root / "outputs/apophis_moon_venus/checkpoints").glob("*.json")):
        item = load_json(path)
        previous_checkpoints[path.name] = {"sha256": _sha(path), "fingerprint": item.get("fingerprint")}
    verification = {
        "schema_version": 2, "matrix": str(result_path.relative_to(root)), "matrix_sha256": _sha(result_path),
        "verifier_sha256": _sha(root / "src/verify_apophis_reference_time.py"), "freeze_sha256": _sha(freeze_path),
        "fingerprint": freeze["fingerprint"], "run_count": len(records), "unique_run_keys": len(keys),
        "checkpoint_count": len(checkpoint_files), "checkpoint_sha256": checkpoint_hashes, "manifest": manifest_stats,
        "primary_rows": len(annual), "secondary_reference_tables": len(references),
        "accepted_endpoint_count": counts["accepted_endpoints"],
        "verified_reference_comparisons": counts["references"],
        "scalar_error_count": (counts["primary_points"] + counts["reference_points"]) * 2,
        "paired_scalar_count": len(expected_pairs) * len(records[0]["requested_states"]) * 2,
        "verified_paired_shifts": len(expected_pairs),
        "legacy_baselines_verified": legacy_count, "previous_v1_checkpoints": previous_checkpoints,
    }
    _write_immutable(output_dir / "verification.json", verification)
    print(json.dumps(verification, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

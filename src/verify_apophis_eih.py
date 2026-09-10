"""Offline integrity verifier for the frozen Apophis EIH/PPN audit."""

from __future__ import annotations

import argparse
import datetime as dt
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
from run_physics_baselines import errors, state_from_row
import verify_apophis_reference_time as reference_verifier
import verify_precise_propagation as propagation_verifier


SUMMARY_FIELDS = reference_verifier.SUMMARY_FIELDS
REFERENCE_NAMES = {"annual_daily", "annual_hourly", "short_daily", "short_hourly", "old_daily", "old_refined", "recent_refined", "long_repeat"}


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
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _record_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _check_freeze(root: Path, matrix: dict[str, Any], freeze_path: Path) -> dict[str, Any]:
    freeze = load_json(freeze_path)
    if matrix.get("fingerprint") != freeze.get("fingerprint") or matrix.get("freeze_sha256") != _sha(freeze_path):
        raise ValueError("matrix/freeze fingerprint mismatch")
    hashes, frozen_runtime = freeze.get("hashes"), freeze.get("runtime")
    if not isinstance(hashes, dict) or not isinstance(frozen_runtime, dict):
        raise ValueError("freeze lacks hashes/runtime")
    for relative, digest in hashes.items():
        path = _record_path(root, str(relative))
        if not path.is_file() or _sha(path) != str(digest):
            raise ValueError(f"frozen source hash mismatch: {relative}")
    runtime = {"executable": sys.executable, "version": sys.version, "platform": platform.platform()}
    if runtime != frozen_runtime:
        raise ValueError("current runtime differs from frozen runtime")
    payload = {"hashes": hashes, "runtime": frozen_runtime}
    if hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest() != freeze.get("fingerprint"):
        raise ValueError("freeze fingerprint recomputation mismatch")
    return freeze


def _manifest_inventory(root: Path, expected_paths: int = 140) -> dict[str, Any]:
    manifests = sorted((root / "data/checksums").glob("*manifest.json"))
    records: list[tuple[str, str, int, str]] = []
    for manifest_path in manifests:
        document = load_json(manifest_path)
        entries = document.get("files") if document.get("files") is not None else document.get("downloads")
        if not isinstance(entries, list):
            raise ValueError(f"manifest has no files/downloads list: {manifest_path.name}")
        for item in entries:
            if not isinstance(item, dict) or not all(key in item for key in ("path", "sha256", "bytes")):
                raise ValueError(f"incomplete manifest record: {manifest_path.name}")
            name, digest, size = str(item["path"]), str(item["sha256"]).lower(), int(item["bytes"])
            path = _record_path(root, name)
            relative = path.relative_to(root).as_posix() if path.is_relative_to(root) else name
            if not path.is_file() or _sha(path) != digest or path.stat().st_size != size:
                raise ValueError(f"manifest raw mismatch: {name}")
            if subprocess.run(["git", "ls-files", "--error-unmatch", "--", relative], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0:
                raise ValueError(f"manifest raw is tracked: {relative}")
            if subprocess.run(["git", "check-ignore", "--no-index", "--quiet", "--", relative], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode != 0:
                raise ValueError(f"manifest raw is not gitignored: {relative}")
            records.append((relative, digest, size, manifest_path.name))
    unique = {item[0] for item in records}
    if len(unique) != expected_paths:
        raise ValueError(f"expected {expected_paths} unique raw paths, found {len(unique)}")
    return {"manifest_count": len(manifests), "manifest_records": len(records), "unique_raw_paths": len(unique), "raw_sha256_size_records": len({(sha, size) for _, sha, size, _ in records}), "manifests": {name: sum(source == name for _, _, _, source in records) for name in sorted({source for _, _, _, source in records})}}


def _load_teachers(root: Path) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, Any]]:
    previous = load_json(root / "outputs/apophis_reference_time/matrix.json")
    annual, references, data = propagation_verifier._load_teachers(root, previous)
    if len(annual) != 797 or set(references) != REFERENCE_NAMES:
        raise ValueError("teacher rows are not the frozen 797/eight-reference set")
    return annual, references, data


def _check_inputs(root: Path, config: dict[str, Any], freeze: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    output = root / config["output_directory"]
    inputs = load_json(output / "inputs.json")
    input_config = load_json(root / config["input_config"])
    manifest_path = root / input_config["manifest"]
    if inputs.get("fingerprint") != freeze["fingerprint"] or inputs.get("manifest_sha256") != _sha(manifest_path):
        raise ValueError("EIH inputs fingerprint/manifest provenance mismatch")
    manifest = load_json(manifest_path)
    entries = manifest.get("files", [])
    if manifest.get("complete") is not True or manifest.get("design_sha256") != _sha(root / input_config["design"]):
        raise ValueError("EIH input manifest is incomplete or design hash differs")
    expected_queries = input_config["queries"]
    expected_raw = [f"{input_config['raw_directory']}/{query['name']}.json" for query in expected_queries]
    if [item.get("path") for item in entries] != expected_raw:
        raise ValueError("EIH input manifest paths differ from frozen queries")
    for item, query in zip(entries, expected_queries, strict=True):
        if item.get("target_id") != query["id"] or item.get("target_name") != query["target_name"] or item.get("rows") != query["rows"]:
            raise ValueError("EIH input manifest target metadata differs from config")
        query_config = {**input_config, "coordinates": {**input_config["coordinates"], "center": query["center"]}}
        from download_jpl_pilot import build_horizons_url
        if item.get("source_url") != build_horizons_url(query_config, query["id"], small_body=False):
            raise ValueError(f"EIH source URL differs from frozen query: {query['name']}")
        path = root / item["path"]
        header, rows = __import__("run_eda", fromlist=["parse_horizons"]).parse_horizons(path, query["id"], query["target_name"])
        expected_center = "Solar System Barycenter (0)" if query["center"] == "500@0" else "Sun (10)"
        if not header["center"].startswith(expected_center) or f"({query['id']})" not in header["target"] or header["reference_frame"] != "ICRF" or header["units"] != "AU-D" or header["output_type"] != "GEOMETRIC cartesian states" or not header["geometric"] or not header["tdb"] or len(rows) != int(query["rows"]):
            raise ValueError(f"EIH Horizons header/schema mismatch: {query['name']}")
        dates = [dt.datetime.fromisoformat(row["epoch_tdb"]) for row in rows]
        start = dt.datetime.fromisoformat(input_config["time_range"]["start"])
        stop = dt.datetime.fromisoformat(input_config["time_range"]["stop"])
        if dates[0] != start or dates[-1] != stop or any((right - left).total_seconds() != 3600 for left, right in zip(dates, dates[1:])):
            raise ValueError(f"EIH Horizons cadence/coverage mismatch: {query['name']}")
        if any(not math.isfinite(float(value)) for row in rows for value in (*row["r"], *row["v"], row["epoch_jd_tdb"])):
            raise ValueError(f"EIH Horizons contains nonfinite state: {query['name']}")
        if item.get("header") != header or item.get("coordinates") != query_config["coordinates"] or item.get("time_scale") != "TDB":
            raise ValueError(f"EIH manifest header metadata differs from parsed raw: {query['name']}")
    if inputs.get("raw_paths") != expected_raw:
        raise ValueError("inputs raw_paths differ from input manifest")
    if inputs.get("pn_source_ids") != ["10", "199", "299", "399", "301", "4", "5", "6", "7", "8", "9"]:
        raise ValueError("PN source ID inventory differs from contract")
    if inputs.get("initial_source") != "annual_hourly" or inputs.get("target_data_unchanged") is not True:
        raise ValueError("EIH input role provenance mismatch")
    if inputs.get("small_body_physics") != "16 Newtonian only" or "barycentric velocities" not in str(inputs.get("frame", "")) or "PN" not in str(inputs.get("frame", "")):
        raise ValueError("EIH frame/physics provenance is incomplete")
    solar = load_json(root / "outputs/apophis_gravity_conventions/inputs.json").get("solar_constants")
    if inputs.get("solar_j2") != solar:
        raise ValueError("EIH solar J2 provenance differs from gravity audit")
    return {"input_manifest_sha256": _sha(manifest_path), "input_raw_count": len(entries), "query_rows": [int(item["rows"]) for item in input_config["queries"]], "coordinates": input_config["coordinates"], "time_range": input_config["time_range"]}


def _expected_setting(setting: dict[str, Any], row: dict[str, Any]) -> None:
    if row.get("setting") != setting:
        raise ValueError(f"solver setting mismatch: {row.get('key')}")


def _check_metadata(row: dict[str, Any], arms: dict[str, dict[str, Any]], settings: dict[str, dict[str, Any]]) -> None:
    key = row.get("key")
    if not isinstance(key, str) or key != f"{row.get('arm')}__{key.rsplit('__', 1)[-1]}":
        raise ValueError(f"noncanonical EIH key: {key}")
    arm_id, label = row.get("arm"), key.rsplit("__", 1)[-1]
    if arm_id not in arms or label not in settings or row.get("force_specification") != arms[arm_id]:
        raise ValueError(f"EIH arm/force specification mismatch: {key}")
    mode = "precise_dp" if label in {"extreme", "ultra"} else "compensated"
    solver = "dopri54" if mode == "precise_dp" else "rk4"
    if row.get("initial_source") != "annual_hourly" or row.get("numerical_mode") != mode or row.get("solver") != solver or row.get("mode") != "relative_calendar_knots" or row.get("accepted_time_basis") != "relative_days_since_start":
        raise ValueError(f"EIH numerical metadata mismatch: {key}")
    _expected_setting(settings[label], row)
    stats = row.get("solver_stats")
    if not isinstance(stats, dict) or stats.get("compensated") is not True:
        raise ValueError(f"compensated solver flag missing: {key}")
    if solver == "rk4":
        steps = stats.get("rk4_steps", stats.get("steps"))
        if isinstance(steps, bool) or not isinstance(steps, (int, float)) or int(steps) != steps:
            raise ValueError(f"RK4 step count missing: {key}")


def _check_reuse(root: Path, row: dict[str, Any], previous_matrix: Path, source_key: str) -> None:
    reused = row.get("reused_from")
    if not isinstance(reused, dict) or set(reused) != {"matrix", "key", "sha256"} or reused["matrix"] != str(previous_matrix.relative_to(root)) or reused["key"] != source_key or reused["sha256"] != _sha(previous_matrix):
        raise ValueError(f"EIH reused provenance mismatch: {row['key']}")
    source_matrix = load_json(previous_matrix)
    source = next((item for item in source_matrix["records"] if item.get("key") == source_key), None)
    if not isinstance(source, dict):
        raise ValueError(f"missing reused source row: {source_key}")
    ignored = {"key", "arm", "fingerprint", "reused_from", "force_specification"}
    current = {k: v for k, v in row.items() if k not in ignored}
    original = {k: v for k, v in source.items() if k not in ignored}
    if current != original:
        raise ValueError(f"EIH baseline trace differs from reused source: {row['key']}")


def _diagnostics(row: dict[str, Any], annual: list[dict[str, Any]], refs: dict[str, list[dict[str, Any]]], au: float, day_s: float, days: list[Any]) -> dict[str, Any]:
    predicted = {item["epoch_tdb"]: _state(state) for item, state in zip(annual, row["requested_states"])}
    origin = float(annual[0]["epoch_jd_tdb"])
    result = {}
    for value in days:
        day = float(value)
        matches = [item for item in refs["annual_daily"] if abs(float(item["epoch_jd_tdb"]) - origin - day) <= 1e-8]
        if len(matches) != 1 or matches[0]["epoch_tdb"] not in predicted:
            raise ValueError(f"missing diagnostic day {day}: {row['key']}")
        p, v = errors(predicted[matches[0]["epoch_tdb"]], state_from_row(matches[0]), au, day_s)
        result[str(value)] = {"position_error_km": p, "velocity_error_m_s": v}
    start = result[str(days[0])]
    return {"errors": result, "growth": {day: {"position_error_growth_km": val["position_error_km"] - start["position_error_km"], "velocity_error_growth_m_s": val["velocity_error_m_s"] - start["velocity_error_m_s"]} for day, val in result.items()}}


def _effect(row: dict[str, Any], base: dict[str, Any]) -> list[State]:
    return [_state([a - b for a, b in zip(left, right)]) for left, right in zip(row["requested_states"], base["requested_states"])]


def _analysis(records: list[dict[str, Any]], annual: list[dict[str, Any]], refs: dict[str, list[dict[str, Any]]], au: float, day_s: float, config: dict[str, Any]) -> dict[str, Any]:
    by_key = {row["key"]: row for row in records}
    dp_differences, cross_solver, effects, matched = [], [], [], []
    budget = float(config["cross_solver_budget_m"])
    for arm in sorted({row["arm"] for row in records}):
        extreme, ultra, rk4 = (by_key[f"{arm}__{label}"] for label in ("extreme", "ultra", "rk4_fine"))
        dp_differences.append({"arm": arm, **_shift([_state(s) for s in extreme["requested_states"]], [_state(s) for s in ultra["requested_states"]], au, day_s)})
        shift = _shift([_state(s) for s in ultra["requested_states"]], [_state(s) for s in rk4["requested_states"]], au, day_s)
        cross_solver.append({"arm": arm, "dp_key": ultra["key"], "rk4_key": rk4["key"], **shift, "within_budget": shift["max_position_shift_km"] * 1000.0 <= budget, "criterion": "ultra_vs_rk4"})
        base = by_key["baseline__ultra"]
        base_rk = by_key["baseline__rk4_fine"]
        if arm != "baseline":
            comparator = by_key["baseline__ultra"] if arm == "pluto" else by_key["pluto__ultra"]
            comparator_rk = by_key["baseline__rk4_fine"] if arm == "pluto" else by_key["pluto__rk4_fine"]
            e_ultra, e_rk = _effect(ultra, comparator), _effect(rk4, comparator_rk)
            effects.append({"arm": arm, "reference_arm": "baseline" if arm == "pluto" else "pluto", "ultra_effect": _shift(e_ultra, [_state([0.0] * 6) for _ in e_ultra], au, day_s), "rk4_effect": _shift(e_rk, [_state([0.0] * 6) for _ in e_rk], au, day_s), "effect_vector_solver_difference": _shift(e_ultra, e_rk, au, day_s)})
        if arm in {"pn_sun_solar_j2", "pn_all_solar_j2"}:
            plain = "pn_sun" if arm.startswith("pn_sun") else "pn_all"
            no_j2_ultra = _effect(ultra, by_key[f"{plain}__ultra"])
            no_j2_rk = _effect(rk4, by_key[f"{plain}__rk4_fine"])
            effects.append({"arm": arm, "reference_arm": plain, "effect_kind": "solar_j2", "ultra_effect": _shift(no_j2_ultra, [_state([0.0] * 6) for _ in no_j2_ultra], au, day_s), "rk4_effect": _shift(no_j2_rk, [_state([0.0] * 6) for _ in no_j2_rk], au, day_s), "effect_vector_solver_difference": _shift(no_j2_ultra, no_j2_rk, au, day_s)})
    for row in records:
        matched.append({"key": row["key"], "annual_daily": row["reference_comparisons"]["annual_daily"], "diagnostic": _diagnostics(row, annual, refs, au, day_s, config["diagnostic_days"])})
    return {"schema_version": 1, "dp_setting_differences": dp_differences, "cross_solver_differences": cross_solver, "force_effect_vectors": effects, "matched_annual_daily": matched, "cross_solver_budget_m": budget, "cross_solver_criterion": [item for item in cross_solver if item["criterion"] == "ultra_vs_rk4"], "empirical_only": True}


def verify(root: Path) -> dict[str, Any]:
    root = root.resolve()
    config = load_json(root / "configs/apophis_eih.json")
    output = root / config["output_directory"]
    matrix_path, freeze_path = output / "matrix.json", output / "freeze.json"
    matrix = load_json(matrix_path)
    freeze = _check_freeze(root, matrix, freeze_path)
    manifest = _manifest_inventory(root, 140)
    annual, references, data = _load_teachers(root)
    input_provenance = _check_inputs(root, config, freeze, data)
    arms = {item["id"]: item for item in config["arms"]}
    settings = {item["label"]: item for item in config["solver_settings"]}
    expected = {f"{arm}__{label}" for arm in arms for label in settings}
    records = matrix.get("records")
    if not isinstance(records, list) or len(records) != 18 or {row.get("key") for row in records} != expected:
        raise ValueError("EIH matrix does not contain the exact 18 prescribed records")
    if any(row.get("fingerprint") != freeze["fingerprint"] for row in records):
        raise ValueError("EIH record fingerprint differs from freeze")
    initial = {"annual_hourly": state_from_row(references["annual_hourly"][0])}
    previous_matrix = root / config["previous_matrix"]
    for row in records:
        _check_metadata(row, arms, settings)
        helper_row = row
        if row["solver"] == "rk4" and "rk4_steps" not in row["solver_stats"]:
            helper_row = dict(row)
            helper_row["solver_stats"] = {**row["solver_stats"], "rk4_steps": row["solver_stats"].get("steps")}
        reference_verifier._verify_record(helper_row, annual, references, initial, float(data["constants"]["au_km"]), float(data["constants"]["day_s"]))
        if row["arm"] == "baseline":
            _check_reuse(root, row, previous_matrix, f"fixed_pole__{row['key'].rsplit('__', 1)[-1]}")
        elif "reused_from" in row:
            raise ValueError(f"nonbaseline EIH row unexpectedly reused: {row['key']}")
    checkpoints = sorted((output / "checkpoints").glob("*.json"))
    if {path.stem for path in checkpoints} != expected:
        raise ValueError("EIH checkpoint set differs from matrix")
    by_key = {row["key"]: row for row in records}
    checkpoint_hashes = {}
    for path in checkpoints:
        if load_json(path) != by_key[path.stem]:
            raise ValueError(f"checkpoint differs from matrix: {path.name}")
        checkpoint_hashes[path.name] = _sha(path)
    au, day_s = float(data["constants"]["au_km"]), float(data["constants"]["day_s"])
    analysis = _analysis(records, annual, references, au, day_s, config)
    analysis.update({"matrix_sha256": _sha(matrix_path), "verifier_sha256": _sha(root / "src/verify_apophis_eih.py"), "reference_verifier_sha256": _sha(root / "src/verify_apophis_reference_time.py"), "gravity_verifier_sha256": _sha(root / "src/verify_apophis_gravity_conventions.py"), "propagation_verifier_sha256": _sha(root / "src/verify_precise_propagation.py"), "input_provenance": input_provenance})
    _write_immutable(output / "analysis.json", analysis)
    scalar_error_count = sum(2 * (len(annual) + sum(int(row["reference_comparisons"][name]["samples"]) for name in references)) for row in records)
    verification = {"schema_version": 1, "matrix": str(matrix_path.relative_to(root)), "matrix_sha256": _sha(matrix_path), "inputs_sha256": _sha(output / "inputs.json"), "verifier_sha256": _sha(root / "src/verify_apophis_eih.py"), "reference_verifier_sha256": _sha(root / "src/verify_apophis_reference_time.py"), "gravity_verifier_sha256": _sha(root / "src/verify_apophis_gravity_conventions.py"), "propagation_verifier_sha256": _sha(root / "src/verify_precise_propagation.py"), "freeze_sha256": _sha(freeze_path), "fingerprint": freeze["fingerprint"], "run_count": len(records), "unique_run_keys": len(expected), "checkpoint_count": len(checkpoints), "checkpoint_sha256": checkpoint_hashes, "manifest": manifest, "input_provenance": input_provenance, "primary_rows": len(annual), "reference_tables": len(references), "accepted_endpoint_count": sum(len(row["accepted_endpoints"]) for row in records), "scalar_error_count": scalar_error_count, "paired_scalar_count": len(analysis["cross_solver_differences"]) * len(annual) * 2, "reused_baselines_verified": 3, "new_propagations_verified": 15, "cross_solver_budget_m": config["cross_solver_budget_m"]}
    _write_immutable(output / "verification.json", verification)
    return verification


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(verify(args.root), indent=2, ensure_ascii=False))

"""Verify the saved force-model v2 matrix without propagating any trajectory.

The verifier is deliberately an artifact audit.  It reconstructs the merged
daily/refined reference grids and interpolates the saved first-production and
fine accepted-endpoint traces, but never calls ``forecast_candidate_v2``.
It accepts only the complete full-sample post-hoc regression namespace; a
single-object smoke result is not a substitute for the 600-record matrix.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess
from collections.abc import Mapping
from typing import Any

from force_models_v2 import build_force_model_v2
from ng_inputs_v2 import load_ng_input
from orbit_baselines import State, norm, subtract
from planetary_dynamics import EphemerisInterpolator
from run_development_benchmark import (
    _find_initial,
    _reference_grid,
    _rel,
    load_development_context,
    load_object_rows,
)
from run_eda import load_json
from run_force_models_v2 import MODEL_IDS, _input_hashes, _models, _ng_path
from trajectory_metrics import trajectory_error_rows, summarize_error_rows


DEFAULT_CONFIG = "configs/force_models_v2.json"
EXPECTED_RAW_FILES = 104
EXPECTED_V1_SOURCE_HASHES = 96
SUMMARY_KEYS = (
    "sample_count",
    "endpoint_position_error_km",
    "max_position_error_km",
    "max_position_error_time_days",
    "endpoint_velocity_error_m_s",
    "max_velocity_error_m_s",
    "rtn_endpoint_position_error_km",
    "rtn_max_abs_position_error_km",
)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _close(left: Any, right: Any, label: str, *, rel_tol: float = 1e-12, abs_tol: float = 1e-8) -> None:
    if isinstance(left, bool) or isinstance(right, bool):
        if left != right:
            raise AssertionError(f"{label}: {left!r} != {right!r}")
        return
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if not math.isclose(float(left), float(right), rel_tol=rel_tol, abs_tol=abs_tol):
            raise AssertionError(f"{label}: {left!r} != {right!r}")
        return
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        if len(left) != len(right):
            raise AssertionError(f"{label}: lengths {len(left)} != {len(right)}")
        for index, (actual, expected) in enumerate(zip(left, right)):
            _close(actual, expected, f"{label}[{index}]", rel_tol=rel_tol, abs_tol=abs_tol)
        return
    if left != right:
        raise AssertionError(f"{label}: {left!r} != {right!r}")


def _path(root: Path, value: str | Path) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else root / candidate


def verify_manifest_files(root: Path) -> dict[str, Any]:
    """Verify every manifest entry, including bytes, SHA-256 and Git ignore."""

    manifest_dir = root / "data" / "checksums"
    manifests = sorted(manifest_dir.glob("*manifest.json"))
    if not manifests:
        raise FileNotFoundError(f"no checksum manifests under {manifest_dir}")
    by_path: dict[str, tuple[str, int]] = {}
    manifest_hashes: dict[str, str] = {}
    for manifest in manifests:
        manifest_hashes[_rel(root, manifest)] = _sha256(manifest)
        document = _read(manifest)
        files = document.get("files")
        if not isinstance(files, list):
            raise AssertionError(f"manifest files must be a list: {manifest}")
        for entry in files:
            if not isinstance(entry, Mapping) or not isinstance(entry.get("path"), str):
                raise AssertionError(f"invalid manifest entry in {manifest}")
            file_path = _path(root, entry["path"])
            relative = _rel(root, file_path)
            expected_sha = str(entry.get("sha256", ""))
            expected_bytes = int(entry.get("bytes", -1))
            previous = by_path.get(relative)
            if previous is not None and previous != (expected_sha, expected_bytes):
                raise AssertionError(f"conflicting manifest entries for {relative}")
            by_path[relative] = (expected_sha, expected_bytes)
    if len(by_path) != EXPECTED_RAW_FILES:
        raise AssertionError(f"expected {EXPECTED_RAW_FILES} unique raw files, found {len(by_path)}")
    for relative, (expected_sha, expected_bytes) in sorted(by_path.items()):
        file_path = root / relative
        if not file_path.is_file():
            raise AssertionError(f"manifest input is missing: {relative}")
        actual_bytes = file_path.stat().st_size
        actual_sha = _sha256(file_path)
        if actual_bytes != expected_bytes:
            raise AssertionError(f"byte count mismatch for {relative}: {actual_bytes} != {expected_bytes}")
        if actual_sha != expected_sha:
            raise AssertionError(f"SHA-256 mismatch for {relative}: {actual_sha} != {expected_sha}")
        ignored = subprocess.run(
            ["git", "check-ignore", "--quiet", "--", str(file_path)],
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode == 0
        if not ignored:
            raise AssertionError(f"raw input is not Git-ignored: {relative}")
    return {"manifests": len(manifests), "manifest_sha256": manifest_hashes, "raw_files": len(by_path), "raw_bytes": sum(size for _, size in by_path.values())}


def _verify_hash_map(root: Path, hashes: Mapping[str, str], label: str) -> int:
    if not isinstance(hashes, Mapping) or not hashes:
        raise AssertionError(f"{label} is empty")
    checked = 0
    for relative, expected in hashes.items():
        path = _path(root, str(relative))
        if not path.is_file():
            raise AssertionError(f"{label} input is missing: {relative}")
        actual = _sha256(path)
        if actual != expected:
            raise AssertionError(f"{label} SHA-256 mismatch for {relative}: {actual} != {expected}")
        checked += 1
    return checked


def _trace_interpolator(root: Path, path: str) -> tuple[EphemerisInterpolator, dict[str, Any]]:
    document = _read(_path(root, path))
    accepted = document.get("accepted_states")
    if not isinstance(accepted, list) or len(accepted) < 2:
        raise AssertionError(f"trace has too few accepted states: {path}")
    times = []
    states = []
    for index, item in enumerate(accepted):
        if not isinstance(item, Mapping) or not isinstance(item.get("state"), Mapping):
            raise AssertionError(f"invalid trace state {path}[{index}]")
        state = item["state"]
        times.append(float(item["time_days"]))
        states.append(State(tuple(float(value) for value in state["r"]), tuple(float(value) for value in state["v"])))
    if times[0] != 0.0 or any(right <= left for left, right in zip(times, times[1:])):
        raise AssertionError(f"trace times are not strictly increasing from zero: {path}")
    return EphemerisInterpolator(tuple(times), tuple(states)), document


def _verify_force_metadata(actual: Any, expected: Mapping[str, Any], label: str) -> None:
    if not isinstance(actual, Mapping):
        raise AssertionError(f"{label} is not a metadata mapping")
    if set(actual) != set(expected):
        raise AssertionError(f"{label} keys differ: {sorted(actual)} != {sorted(expected)}")
    for key in expected:
        _close(actual[key], expected[key], f"{label}.{key}")


def _verify_timings(record: Mapping[str, Any]) -> None:
    trials = record.get("runtime_trials_seconds")
    if not isinstance(trials, list) or len(trials) != 3:
        raise AssertionError(f"v2 timing prefix must have 3 trials: {record.get('case_id')}")
    for index, value in enumerate(trials):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)) or float(value) < 0.0:
            raise AssertionError(f"invalid timing {record.get('case_id')} trial {index}: {value!r}")
    median = statistics.median(trials)
    _close(record.get("runtime_median_seconds"), median, f"{record.get('case_id')}:runtime_median_seconds", rel_tol=1e-13, abs_tol=1e-12)


def _verify_record(
    root: Path,
    record: Mapping[str, Any],
    obj: Mapping[str, Any],
    model_id: str,
    reference_rows: list[dict[str, Any]],
    start_jd: float,
    production: EphemerisInterpolator,
    fine: EphemerisInterpolator,
    au_km: float,
    day_s: float,
    config: Mapping[str, Any],
    expected_force_metadata: Mapping[str, Any],
) -> int:
    horizon = float(record["horizon_days"])
    times, references = _reference_grid(reference_rows, start_jd, horizon)
    predicted = [production.state_at(time) for time in times]
    fine_states = [fine.state_at(time) for time in times]
    error_rows = trajectory_error_rows(times, predicted, references, au_km, day_s)
    summary = summarize_error_rows(error_rows)
    for key in SUMMARY_KEYS:
        _close(record.get(key), summary[key], f"{record['case_id']}.{model_id}.{key}")
    for index, (actual, expected) in enumerate(zip(record.get("error_rows", []), error_rows)):
        for key in ("time_days", "position_error_km", "velocity_error_m_s", "rtn_position_error_km", "rtn_velocity_error_m_s"):
            _close(actual.get(key), expected[key], f"{record['case_id']}.{model_id}.error_rows[{index}].{key}")
    if len(record.get("error_rows", [])) != len(error_rows):
        raise AssertionError(f"error row count mismatch: {record['case_id']}:{model_id}")
    numerical = max(norm(subtract(left.position, right.position)) * au_km for left, right in zip(predicted, fine_states))
    _close(record.get("numerical_difference_km"), numerical, f"{record['case_id']}.{model_id}.numerical_difference_km")
    tolerances = config.get("position_tolerances_km", config.get("tolerances", []))
    budget = config.get("numerical_budget_fraction", config.get("numeric_budget", 0.1))
    if isinstance(budget, Mapping):
        budget = budget.get("fraction", budget.get("fraction_of_tolerance", 0.1))
    expected_eligibility = {
        str(float(tolerance)): float(record["max_position_error_km"]) <= float(tolerance)
        and numerical <= float(budget) * float(tolerance)
        for tolerance in tolerances
    }
    if record.get("eligibility") != expected_eligibility:
        raise AssertionError(f"eligibility mismatch: {record['case_id']}:{model_id}")
    _verify_force_metadata(record.get("force_metadata"), expected_force_metadata, f"{record['case_id']}:{model_id}:force_metadata")
    _verify_timings(record)
    return len(times)


def verify(root: Path, config_path: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    config_path = (config_path or (root / DEFAULT_CONFIG)).resolve()
    config = load_json(config_path)
    result_path = root / str(config["output_directory"]) / "results.json"
    if not result_path.exists():
        raise FileNotFoundError(f"cannot run yet: full matrix result is missing: {result_path}")
    result = load_json(result_path)
    if result.get("scope") != "full_development30_posthoc" or not result.get("posthoc_regression"):
        raise AssertionError("verifier accepts only the full post-hoc development30 scope")
    if result.get("config") != config:
        raise AssertionError("saved matrix config differs from current force_models_v2 config")
    run_started_path = result_path.parent / "run_started.json"
    run_started = load_json(run_started_path)
    saved_runtime = result.get("provenance", {}).get("runtime_environment")
    if saved_runtime != run_started.get("runtime_environment"):
        raise AssertionError("saved result runtime environment differs from run_started.json")
    source_count = _verify_hash_map(root, result.get("provenance", {}).get("source_hashes", {}), "v2 source")
    required_sources = {"src/force_models_v2.py", "src/ng_inputs_v2.py", "src/run_force_models_v2.py", "src/earth_oblateness.py", "docs/FORCE_MODELS_V2_CONTRACT.md"}
    missing = required_sources.difference(result["provenance"]["source_hashes"])
    if missing:
        raise AssertionError(f"v2 source freeze misses required files: {sorted(missing)}")
    manifest_result = verify_manifest_files(root)

    sample = load_json(root / str(config["sample_path"]))
    objects = sample.get("objects")
    if not isinstance(objects, list) or len(objects) != 30:
        raise AssertionError("the full v2 matrix requires exactly 30 sample objects")
    models = _models(config)
    model_ids = [model["model_id"] for model in models]
    if tuple(model_ids) != MODEL_IDS:
        raise AssertionError(f"unexpected model order: {model_ids}")
    records = result.get("records")
    expected_records = {
        (str(obj["id"]), str(obj["start_date"]), float(horizon), model_id)
        for obj in objects for horizon in config["horizons_days"] for model_id in model_ids
    }
    indexed = {(str(row.get("object_id")), str(row.get("start_date")), float(row.get("horizon_days")), str(row.get("model_id"))): row for row in records or []}
    if len(indexed) != len(records or []) or set(indexed) != expected_records or len(indexed) != 600:
        raise AssertionError(f"expected 600 unique records, found {len(indexed)}")
    object_models = result.get("object_model_results")
    if not isinstance(object_models, list) or len(object_models) != 120:
        raise AssertionError(f"expected 120 object/model artifacts, found {len(object_models or [])}")
    by_object_model = {(str(item.get("object_id")), str(item.get("model_id"))): item for item in object_models}
    if len(by_object_model) != 120:
        raise AssertionError("object/model artifacts are not unique")

    data_config = load_json(root / str(config["data_config"]))
    constants = data_config["constants"]
    au_km, day_s = float(constants["au_km"]), float(constants["day_s"])
    context = load_development_context(root, config)
    raw_directory = root / str(config["raw_directory"])
    error_samples = 0
    trace_count = 0
    input_hashes_checked = 0
    for obj in objects:
        object_id = str(obj["id"])
        daily, reference_rows = load_object_rows(root, raw_directory, obj)
        start_jd, _ = _find_initial(daily, str(obj["start_date"]))
        for model in models:
            model_id = model["model_id"]
            item = by_object_model.get((object_id, model_id))
            if item is None or len(item.get("records", [])) != 5:
                raise AssertionError(f"missing object/model artifact: {object_id}:{model_id}")
            nested_records = item["records"]
            for nested in nested_records:
                key = (str(nested.get("object_id")), str(nested.get("start_date")), float(nested.get("horizon_days")), str(nested.get("model_id")))
                if indexed.get(key) != nested:
                    raise AssertionError(f"nested/flat record mismatch: {object_id}:{model_id}:{key[2]:g}")
            trace_hashes = item.get("trace_hashes")
            if not isinstance(trace_hashes, Mapping) or len(trace_hashes) != 2:
                raise AssertionError(f"expected firstprod/fine trace hashes: {object_id}:{model_id}")
            for relative, expected_hash in trace_hashes.items():
                path = _path(root, str(relative))
                if not path.is_file() or _sha256(path) != expected_hash:
                    raise AssertionError(f"trace hash mismatch: {relative}")
            trace_count += len(trace_hashes)
            trace_paths = item.get("trace_paths")
            if not isinstance(trace_paths, list) or set(trace_paths) != set(trace_hashes):
                raise AssertionError(f"trace path/hash mismatch: {object_id}:{model_id}")
            prod_path = next(path for path in trace_paths if path.endswith("_firstprod.json"))
            fine_path = next(path for path in trace_paths if path.endswith("_fine.json"))
            production, production_document = _trace_interpolator(root, prod_path)
            fine, fine_document = _trace_interpolator(root, fine_path)
            if production_document.get("model_id") != model_id or fine_document.get("model_id") != model_id:
                raise AssertionError(f"trace model mismatch: {object_id}:{model_id}")
            if float(item.get("determinism_max_daily_position_difference_km", -1.0)) != 0.0:
                raise AssertionError(f"non-zero production determinism marker: {object_id}:{model_id}")
            initial_state = _find_initial(daily, str(obj["start_date"]))[1]
            trace_initial = production.state_at(0.0)
            _close(trace_initial.position, initial_state.position, f"{object_id}:{model_id}:trace initial position")
            _close(trace_initial.velocity, initial_state.velocity, f"{object_id}:{model_id}:trace initial velocity")
            annual = float(max(config["horizons_days"]))
            if production.epochs_jd_tdb[-1] != annual or fine.epochs_jd_tdb[-1] != annual:
                raise AssertionError(f"production/fine trace does not reach {annual:g} days: {object_id}:{model_id}")
            production_steps = int(production_document.get("rk4_steps", -1))
            fine_steps = int(fine_document.get("rk4_steps", -1))
            if production_steps < 0 or fine_steps < production_steps:
                raise AssertionError(f"fine trace has fewer RK4 steps: {object_id}:{model_id}")
            if production_steps != int(item["records"][-1]["rk4_steps"]):
                raise AssertionError(f"annual RK4 step count mismatch: {object_id}:{model_id}")
            ng_path = _ng_path(root, config, obj)
            expected_inputs = _input_hashes(root, raw_directory, obj, context, ng_path)
            if item.get("input_hashes") != expected_inputs:
                raise AssertionError(f"object/model input hash mismatch: {object_id}:{model_id}")
            input_hashes_checked += len(expected_inputs)
            _, _, expected_force_metadata = build_force_model_v2(
                start_jd, model, context, config,
                ng=load_ng_input(ng_path) if ng_path is not None else None,
            )
            _verify_force_metadata(item.get("production_force_metadata"), expected_force_metadata, f"{object_id}:{model_id}:production_force_metadata")
            _verify_force_metadata(item.get("fine_force_metadata"), expected_force_metadata, f"{object_id}:{model_id}:fine_force_metadata")
            _verify_force_metadata(production_document.get("force_metadata"), expected_force_metadata, f"{object_id}:{model_id}:production trace force_metadata")
            _verify_force_metadata(fine_document.get("force_metadata"), expected_force_metadata, f"{object_id}:{model_id}:fine trace force_metadata")
            previous_steps = -1
            for horizon in config["horizons_days"]:
                key = (object_id, str(obj["start_date"]), float(horizon), model_id)
                row = indexed[key]
                if row.get("split") != obj.get("split") or row.get("evaluation_scope") != "engineering_regression_posthoc":
                    raise AssertionError(f"original split/scope mismatch: {row['case_id']}")
                if int(row.get("rk4_steps", -1)) <= previous_steps:
                    raise AssertionError(f"RK4 prefixes are not increasing: {row['case_id']}")
                previous_steps = int(row["rk4_steps"])
                error_samples += _verify_record(root, row, obj, model_id, reference_rows, start_jd, production, fine, au_km, day_s, config, expected_force_metadata)

    v1_train_path = root / "outputs" / "development30" / "train" / "results.json"
    v1_train = load_json(v1_train_path)
    v1_count = _verify_hash_map(root, v1_train.get("provenance", {}).get("source_sha256_at_start", {}), "v1 train source")
    if v1_count != EXPECTED_V1_SOURCE_HASHES:
        raise AssertionError(f"expected {EXPECTED_V1_SOURCE_HASHES} v1 source hashes, found {v1_count}")
    verification = {
        "schema_version": 1,
        "passed": True,
        "scope": "full_development30_posthoc",
        "matrix_sha256": _sha256(result_path),
        "matrix_records_verified": len(indexed),
        "object_model_artifacts_verified": len(by_object_model),
        "traces_verified": trace_count,
        "error_samples_recomputed": error_samples,
        "v2_source_hashes_checked": source_count,
        "v1_train_source_hashes_checked": v1_count,
        "input_hash_entries_checked": input_hashes_checked,
        "manifest": manifest_result,
        "verifier_source_sha256": _sha256(Path(__file__).resolve()),
    }
    output = root / str(config["output_directory"]) / "verification" / "verification.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(verification, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return verification


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--config", type=Path, default=Path(DEFAULT_CONFIG))
    args = parser.parse_args(argv)
    try:
        result = verify(args.root, args.config)
    except FileNotFoundError as exc:
        print(json.dumps({"status": "cannot_run_yet", "message": str(exc)}, ensure_ascii=False))
        return 0
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["verify", "verify_manifest_files", "main"]

"""Run the v2 force-model matrix on the frozen development30 sample.

This is explicitly a post-hoc engineering regression of the existing sample;
it is not a new independent validation.  The runner supplies only the initial
state, forecast-time context, horizon and requested model to
``forecast_candidate_v2``.  Daily and refined reference rows are used here
only by the external evaluation stage after propagation.

The expensive part is intentionally bounded: three annual production prefixes
and one fine annual prefix per object/model.  Metrics for all configured
horizons are interpolated from those prefixes.  Completed object/model pairs
are immutable resume units and their traces are never overwritten.
"""

from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time
from collections.abc import Mapping
from typing import Any

from orbit_baselines import State, norm, subtract
from force_models_v2 import ForceModelSpec
from run_development_benchmark import (
    _find_initial,
    _input_hashes as _frozen_input_hashes,
    _interpolate_endpoints,
    _reference_grid,
    _rel,
    _trace_json,
    load_development_context,
    load_object_rows,
    merge_rows,
    source_hashes as _frozen_source_hashes,
    validate_raw_manifest,
)
from run_eda import load_json
from trajectory_metrics import trajectory_error_rows, summarize_error_rows


MODEL_IDS = ("V2-B2", "V2-P", "V2-P-GR", "V2-P-GR-SB16")
DEFAULT_CONTRACT = "docs/FORCE_MODELS_V2_CONTRACT.md"
_RUNNER_RELATIVE = "src/run_force_models_v2.py"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    """Write JSON through a sibling temporary file and an atomic replace."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _runtime_environment() -> dict[str, str]:
    return {
        "python_version": sys.version,
        "python_implementation": sys.implementation.name,
        "platform": sys.platform,
        "platform_release": platform.release(),
        "machine": platform.machine(),
    }


def _model_defaults(model: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the exact public ForceModelSpec shape used by the propagator."""

    if not isinstance(model, Mapping):
        raise ValueError("Each v2 model declaration must be a mapping")
    spec = ForceModelSpec.from_mapping(model)
    model_id = spec.model_id
    if model_id not in MODEL_IDS:
        raise ValueError(f"unsupported v2 model {model_id!r}")
    return asdict(spec)


def _models(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    declarations = config.get("models")
    if not isinstance(declarations, list) or not declarations:
        raise ValueError("force_models_v2 config must contain a non-empty models list")
    models = [_model_defaults(item) for item in declarations]
    ids = [item["model_id"] for item in models]
    if len(ids) != len(set(ids)):
        raise ValueError("force_models_v2 models must have unique model_id values")
    if set(ids) != set(MODEL_IDS):
        raise ValueError(f"force_models_v2 requires exactly {MODEL_IDS}")
    return models


def eligible(record: Mapping[str, Any], tolerance_km: float, budget_fraction: float) -> bool:
    """Apply the frozen numerical eligibility gate to one measured record."""

    return (
        float(record["max_position_error_km"]) <= float(tolerance_km)
        and float(record["numerical_difference_km"]) <= float(budget_fraction) * float(tolerance_km)
    )


def _config_contract_path(root: Path, config: Mapping[str, Any]) -> Path:
    return root / str(config.get("contract_path", DEFAULT_CONTRACT))


def source_hashes(root: Path, config_path: Path, config: Mapping[str, Any]) -> dict[str, str]:
    """Hash the complete v2 code/config/input freeze used by a run.

    The old benchmark helper supplies the immutable raw-input and baseline
    hashes.  This function adds only the v2 modules, config and contract plus
    manifests consulted by the NG adapter; reports and tests remain outside
    the numerical source freeze.
    """

    sample_path = root / str(config["sample_path"])
    raw_directory = root / str(config["raw_directory"])
    # Start with the exact v1 frozen dependency/input set, then add only the
    # v2 modules/config/contract.  In particular, reports and tests are not a
    # part of the numerical source freeze.
    paths: set[Path] = set()
    try:
        paths.update(root / relative for relative in _frozen_source_hashes(root, config_path, sample_path, raw_directory))
    except (FileNotFoundError, ValueError):
        pass
    for relative in (_RUNNER_RELATIVE, "src/force_models_v2.py", "src/ng_inputs_v2.py", "src/earth_oblateness.py"):
        paths.add(root / relative)
    paths.add(config_path)
    paths.add(sample_path)
    contract = _config_contract_path(root, config)
    if contract.exists():
        paths.add(contract)
    # ng_inputs_v2 discovers timestamps from manifests; freeze every manifest
    # it could inspect, including manifests outside development30/raw.
    manifest_dir = root / "data" / "checksums"
    if manifest_dir.is_dir():
        paths.update(path for path in manifest_dir.glob("*manifest.json") if path.is_file())
    # Include a configured NG directory even if it sits outside the old raw
    # tree.  It is an immutable forecast-time input, not a reference source.
    for key in ("ng_input_path", "ng_directory", "ng_input_directory", "ng_inputs_directory"):
        value = config.get(key)
        if not value:
            continue
        candidate = root / str(value)
        if candidate.is_file():
            paths.add(candidate)
        elif candidate.is_dir():
            paths.update(item for item in candidate.rglob("*") if item.is_file())
    result = {}
    for path in sorted(paths):
        if not path.is_file():
            continue
        try:
            relative = _rel(root, path)
        except ValueError:
            relative = str(path.resolve())
        result[relative] = _sha256(path)
    return dict(sorted(result.items()))


def _ng_path(root: Path, config: Mapping[str, Any], obj: Mapping[str, Any]) -> Path | None:
    """Resolve the optional object NG input without reading reference states."""

    object_id = str(obj["id"])
    explicit = obj.get("ng_input_path")
    if explicit:
        path = root / str(explicit)
        return path if path.is_file() else None
    for key in ("ng_input_path", "ng_directory", "ng_input_directory", "ng_inputs_directory"):
        value = config.get(key)
        if not value:
            continue
        candidate = root / str(value)
        if candidate.is_file():
            return candidate
        if candidate.is_dir():
            for name in (f"object_{object_id}.json", f"asteroid_{object_id}.json", f"{object_id}.json"):
                path = candidate / name
                if path.is_file():
                    return path
    # Development30 keeps the Horizons asteroid response (including its
    # pre-$$SOE NG header) beside the daily state table.  This fallback lets
    # the adapter inspect that header while the forecast still receives only
    # the validated NGInput object.
    raw_asteroid = root / str(config.get("raw_directory", "")) / "asteroids" / f"asteroid_{object_id}_daily.json"
    if raw_asteroid.is_file():
        return raw_asteroid
    return None


def _load_ng(root: Path, config: Mapping[str, Any], obj: Mapping[str, Any]) -> tuple[Any | None, Path | None]:
    path = _ng_path(root, config, obj)
    if path is None:
        return None, None
    from ng_inputs_v2 import load_ng_input  # imported lazily for synthetic runner tests

    return load_ng_input(path), path


def _input_hashes(root: Path, raw_directory: Path, obj: Mapping[str, Any], context: Mapping[str, Any], ng_path: Path | None) -> dict[str, str]:
    result = dict(_frozen_input_hashes(root, raw_directory, obj, context))
    if ng_path is not None and ng_path.exists():
        result[f"ng:{_rel(root, ng_path)}"] = _sha256(ng_path)
    return dict(sorted(result.items()))


def _snapshot(meta: Mapping[str, Any], horizon: float) -> Mapping[str, Any]:
    snapshots = meta.get("snapshots")
    if not isinstance(snapshots, Mapping):
        raise ValueError("forecast_candidate_v2 metadata must contain snapshots")
    for key in (str(float(horizon)), str(horizon), f"{horizon:g}"):
        if key in snapshots:
            value = snapshots[key]
            if not isinstance(value, Mapping):
                raise ValueError(f"snapshot {key!r} must be a mapping")
            return value
    raise ValueError(f"forecast metadata has no snapshot for horizon {horizon:g}")


def _state_json(state: State) -> dict[str, list[float]]:
    return {"r": list(state.position), "v": list(state.velocity)}


def _trace_payload(meta: Mapping[str, Any]) -> dict[str, Any]:
    accepted = meta.get("accepted_states")
    if not isinstance(accepted, list):
        raise ValueError("forecast_candidate_v2 metadata must contain accepted_states")
    normalized = []
    for item in accepted:
        if not isinstance(item, Mapping) or "time_days" not in item or "state" not in item:
            raise ValueError("accepted_states entries need time_days and state")
        state = item["state"]
        if not isinstance(state, State):
            if isinstance(state, Mapping):
                state = State(tuple(state["r"]), tuple(state["v"]))
            else:
                raise ValueError("accepted state must be State or r/v mapping")
        normalized.append({"time_days": float(item["time_days"]), "state": state})
    trace = _trace_json(normalized)
    return {
        "trace_schema_version": 1,
        "model_id": meta.get("model_id"),
        "accepted_states": trace,
        "force_metadata": meta.get("force_metadata", {}),
        "runtime_seconds": float(meta.get("runtime_seconds", 0.0)),
        "rk4_steps": int(meta.get("rk4_steps", 0)),
    }


def _record_for_horizon(
    initial: State,
    start_jd: float,
    horizon: float,
    model: Mapping[str, Any],
    context: Mapping[str, Any],
    config: Mapping[str, Any],
    reference_rows: list[dict[str, Any]],
    production: tuple[list[State], Mapping[str, Any]],
    fine: tuple[list[State], Mapping[str, Any]],
    object_id: str,
    object_name: str,
    start_date: str,
    original_split: str,
) -> dict[str, Any]:
    reference_times, reference_states = _reference_grid(reference_rows, start_jd, horizon)
    p_at = _interpolate_endpoints(list(production[1]["accepted_states"]))
    f_at = _interpolate_endpoints(list(fine[1]["accepted_states"]))
    production_states = [p_at(t) for t in reference_times]
    fine_states = [f_at(t) for t in reference_times]
    errors = trajectory_error_rows(
        reference_times, production_states, reference_states,
        float(context["au_km"]), float(context["day_s"]),
    )
    summary = summarize_error_rows(errors)
    numerical_difference = max(
        norm(subtract(left.position, right.position)) * float(context["au_km"])
        for left, right in zip(production_states, fine_states)
    )
    snapshot = _snapshot(production[1], horizon)
    tolerances = config.get("tolerances", config.get("position_tolerances_km", []))
    budget = float(config.get("numeric_budget", config.get("numerical_budget_fraction", 0.1)))
    if isinstance(config.get("numeric_budget"), Mapping):
        budget = float(config["numeric_budget"].get("fraction", config["numeric_budget"].get("fraction_of_tolerance", 0.1)))
    eligibility = {str(float(tolerance)): eligible({**summary, "numerical_difference_km": numerical_difference}, float(tolerance), budget) for tolerance in tolerances}
    return {
        "case_id": f"{object_id}:{start_date}:{horizon:g}",
        "object_id": object_id,
        "object_name": object_name,
        "split": original_split,
        "evaluation_scope": "engineering_regression_posthoc",
        "start_date": start_date,
        "horizon_days": horizon,
        "model_id": model["model_id"],
        **summary,
        "numerical_difference_km": numerical_difference,
        "runtime_median_seconds": float(snapshot.get("runtime_seconds", production[1].get("runtime_seconds", 0.0))),
        "runtime_trials_seconds": [],
        "rk4_steps": int(snapshot.get("rk4_steps", production[1].get("rk4_steps", 0))),
        "force_evaluations": 4 * int(snapshot.get("rk4_steps", production[1].get("rk4_steps", 0))),
        "force_metadata": production[1].get("force_metadata", {}),
        "eligibility": eligibility,
        "error_rows": errors,
    }


def _checkpoint_path(out: Path, object_id: str, model_id: str) -> Path:
    safe_model = model_id.replace("+", "_").replace("/", "_")
    return out / "checkpoints" / f"object_{object_id}_{safe_model}.json"


def _trace_path(out: Path, object_id: str, model_id: str, kind: str) -> Path:
    safe_model = model_id.replace("+", "_").replace("/", "_")
    return out / "traces" / f"object_{object_id}_{safe_model}_{kind}.json"


def _load_completed(path: Path, expected: Mapping[str, Any], trace_paths: tuple[Path, Path]) -> dict[str, Any] | None:
    if not path.exists():
        return None
    saved = load_json(path)
    for key in ("source_hashes", "input_hashes", "runtime_environment", "scope"):
        if saved.get(key) != expected[key]:
            raise RuntimeError(f"resume freeze mismatch for checkpoint {path}: {key}")
    trace_hashes = saved.get("trace_hashes")
    if not isinstance(trace_hashes, Mapping):
        raise RuntimeError(f"checkpoint has no trace hashes: {path}")
    for trace in trace_paths:
        if not trace.exists():
            raise RuntimeError(f"checkpoint trace is missing: {trace}")
        if trace_hashes.get(trace.name) != _sha256(trace):
            raise RuntimeError(f"checkpoint trace hash mismatch: {trace}")
    result = saved.get("result")
    if not isinstance(result, dict):
        raise ValueError(f"checkpoint result is not a mapping: {path}")
    return result


def _verify_result_trace_hashes(root: Path, result: Mapping[str, Any]) -> None:
    """Verify final-result trace hashes before returning an immutable resume."""

    for item in result.get("object_model_results", []):
        if not isinstance(item, Mapping):
            raise RuntimeError("invalid object/model result in completed v2 result")
        trace_hashes = item.get("trace_hashes")
        if not isinstance(trace_hashes, Mapping):
            raise RuntimeError("completed v2 result has no trace hashes")
        for relative, expected_hash in trace_hashes.items():
            trace = root / str(relative)
            if not trace.is_file() or _sha256(trace) != expected_hash:
                raise RuntimeError(f"completed v2 trace hash mismatch: {trace}")


def _forecast_candidate_v2(initial: State, start_jd: float, horizon: float, model: Mapping[str, Any], context: Mapping[str, Any], config: Mapping[str, Any], step_scale: float, ng: Any | None) -> tuple[list[State], dict[str, Any]]:
    from force_models_v2 import forecast_candidate_v2

    return forecast_candidate_v2(initial, start_jd, horizon, model, context, config, step_scale, ng=ng)


def _run_object_model(
    root: Path,
    out: Path,
    config: Mapping[str, Any],
    context: Mapping[str, Any],
    obj: Mapping[str, Any],
    model: Mapping[str, Any],
    source_freeze: Mapping[str, str],
    scope: str,
    resume: bool,
) -> dict[str, Any]:
    raw_directory = root / str(config["raw_directory"])
    daily, reference_rows = load_object_rows(root, raw_directory, obj)
    start_date = str(obj["start_date"])
    start_jd, initial = _find_initial(daily, start_date)
    ng, ng_path = _load_ng(root, config, obj)
    input_freeze = _input_hashes(root, raw_directory, obj, context, ng_path)
    model_id = str(model["model_id"])
    traces = (_trace_path(out, str(obj["id"]), model_id, "firstprod"), _trace_path(out, str(obj["id"]), model_id, "fine"))
    expected = {"source_hashes": dict(source_freeze), "input_hashes": input_freeze, "runtime_environment": _runtime_environment(), "scope": scope}
    checkpoint = _checkpoint_path(out, str(obj["id"]), model_id)
    if resume:
        completed = _load_completed(checkpoint, expected, traces)
        if completed is not None:
            return completed
        orphaned = [str(path) for path in traces if path.exists()]
        if orphaned:
            raise RuntimeError(
                "orphan v2 trace exists without a completed checkpoint; "
                f"preserve and inspect before rerunning: {', '.join(orphaned)}"
            )
    elif checkpoint.exists():
        raise RuntimeError(f"completed checkpoint exists; use --resume: {checkpoint}")

    annual = float(max(config["horizons_days"]))
    production_runs: list[tuple[list[State], dict[str, Any]]] = []
    repeat_count = int(config.get("timing_repeats", config.get("3repeats", 3)))
    if repeat_count != 3:
        raise ValueError("v2 contract requires exactly three production timing repeats")
    step_scale = float(config.get("step_scale", 1.0))
    for repeat in range(3):
        predictions, metadata = _forecast_candidate_v2(initial, start_jd, annual, model, {**context, "start_jd": start_jd}, config, step_scale, ng)
        if production_runs and predictions != production_runs[0][0]:
            raise RuntimeError(f"non-deterministic production rollout for {obj['id']}:{model_id}")
        production_runs.append((predictions, metadata))
        print(json.dumps({"object_id": str(obj["id"]), "model_id": model_id, "repeat": repeat + 1, "horizon_days": annual}, sort_keys=True), flush=True)
    fine_scale = step_scale * float(config.get("sensitivity_scale_factor", config.get("fine_step_scale_factor", 0.5)))
    fine = _forecast_candidate_v2(initial, start_jd, annual, model, {**context, "start_jd": start_jd}, config, fine_scale, ng)

    first_meta = production_runs[0][1]
    first_trace = _trace_payload(first_meta)
    fine_trace = _trace_payload(fine[1])
    for path, payload in zip(traces, (first_trace, fine_trace)):
        if path.exists():
            raise RuntimeError(f"trace already exists and is not resumable: {path}")
        _atomic_json(path, payload)

    records = []
    for horizon_value in config["horizons_days"]:
        record = _record_for_horizon(initial, start_jd, float(horizon_value), model, context, config, reference_rows, production_runs[0], fine, str(obj["id"]), str(obj["name"]), start_date, str(obj.get("split", "unknown")))
        record["runtime_trials_seconds"] = [float(_snapshot(meta, float(horizon_value)).get("runtime_seconds", meta.get("runtime_seconds", 0.0))) for _, meta in production_runs]
        record["runtime_median_seconds"] = statistics.median(record["runtime_trials_seconds"])
        records.append(record)
    result = {
        "object_id": str(obj["id"]),
        "object_name": str(obj["name"]),
        "start_date": start_date,
        "model_id": model_id,
        "records": records,
        "trace_paths": [_rel(root, path) for path in traces],
        "production_force_metadata": first_meta.get("force_metadata", {}),
        "fine_force_metadata": fine[1].get("force_metadata", {}),
        "input_hashes": input_freeze,
        "determinism_max_daily_position_difference_km": 0.0,
    }
    checkpoint_trace_hashes = {path.name: _sha256(path) for path in traces}
    result["trace_hashes"] = {_rel(root, path): _sha256(path) for path in traces}
    _atomic_json(checkpoint, {**expected, "schema_version": 1, "trace_hashes": checkpoint_trace_hashes, "result": result})
    print(json.dumps({"completed": f"{obj['id']}:{model_id}", "horizons": config["horizons_days"]}, sort_keys=True), flush=True)
    return result


def _scope_output(root: Path, config: Mapping[str, Any], object_id: str | None) -> tuple[Path, str]:
    base = root / str(config["output_directory"])
    if object_id is None:
        return base, "full_development30_posthoc"
    return base / f"subset_object_{object_id}", f"subset_object:{object_id}"


def run(root: Path, config_path: Path, *, resume: bool = False, object_id: str | None = None) -> dict[str, Any]:
    """Run the v2 matrix for the full sample or an isolated object smoke scope."""

    root = root.resolve()
    config_path = config_path.resolve()
    config = load_json(config_path)
    models = _models(config)
    sample_path = root / str(config["sample_path"])
    raw_directory = root / str(config["raw_directory"])
    validate_raw_manifest(root)
    sample = load_json(sample_path)
    objects = list(sample.get("objects", []))
    if object_id is not None:
        objects = [obj for obj in objects if str(obj.get("id")) == str(object_id)]
        if not objects:
            raise ValueError(f"object_id {object_id!r} is absent from the frozen sample")
    if not objects:
        raise ValueError("sample contains no objects")
    context = load_development_context(root, config)
    frozen = source_hashes(root, config_path, config)
    out, scope = _scope_output(root, config, object_id)
    out.mkdir(parents=True, exist_ok=True)
    run_started = out / "run_started.json"
    start_record = {"schema_version": 1, "scope": scope, "config_sha256": _sha256(config_path), "source_hashes": frozen, "runtime_environment": _runtime_environment(), "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    if run_started.exists():
        prior = load_json(run_started)
        for key in ("scope", "config_sha256", "source_hashes", "runtime_environment"):
            if prior.get(key) != start_record[key]:
                raise RuntimeError(f"run-start freeze mismatch for {scope}: {key}")
    else:
        _atomic_json(run_started, start_record)
    result_path = out / "results.json"
    if result_path.exists():
        if not resume:
            raise RuntimeError(f"completed v2 result exists; use --resume: {result_path}")
        existing = load_json(result_path)
        provenance = existing.get("provenance", {})
        if (existing.get("scope") != scope or provenance.get("source_hashes") != frozen or
                provenance.get("runtime_environment") != start_record["runtime_environment"]):
            raise RuntimeError(f"completed v2 result freeze mismatch: {result_path}")
        _verify_result_trace_hashes(root, existing)
        return existing
    completed: list[dict[str, Any]] = []
    for obj in objects:
        for model in models:
            completed.append(_run_object_model(root, out, config, context, obj, model, frozen, scope, resume))
    records = [record for result in completed for record in result["records"]]
    result = {
        "schema_version": 1,
        "experiment": "force_models_v2",
        "posthoc_regression": True,
        "scope": scope,
        "config": config,
        "records": records,
        "object_model_results": completed,
        "provenance": {
            "source_hashes": frozen,
            "input_hashes_by_object_model": {
                f"{item['object_id']}:{item['model_id']}": item.get("input_hashes", {})
                for item in completed
            },
            "runtime_environment": _runtime_environment(),
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "scope": scope,
            "reference_grid": "daily+refined merged grid evaluated after forecast",
        },
    }
    _atomic_json(out / "results.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--config", type=Path, default=Path("configs/force_models_v2.json"))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--object-id", help="Bounded smoke namespace; output is isolated from the full matrix")
    args = parser.parse_args(argv)
    result = run(args.root, args.config, resume=args.resume, object_id=args.object_id)
    print(json.dumps({"scope": result["scope"], "records": len(result["records"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["MODEL_IDS", "eligible", "source_hashes", "run", "main"]

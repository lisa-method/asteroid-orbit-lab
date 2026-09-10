"""Frozen selector-v3 evaluation on the whole-object holdout24 sample.

This module is intentionally an orchestration layer.  Forecast functions only
receive an initial state, exogenous ephemerides, horizon and forecast-time NG;
reference rows are consumed after a rollout by the evaluator.  The command is
resumable and never replaces a completed checkpoint or trace.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
from collections.abc import Mapping
from typing import Any

from force_models_v2 import forecast_candidate_v2
from ng_inputs_v2 import load_ng_input
from orbit_baselines import State, norm, subtract
from prepare_selector_holdout24 import MANIFEST_PATH, RAW_DIRECTORY, SAMPLE_PATH, check_hashes
from run_development_benchmark import (
    _find_initial, _interpolate_endpoints, _reference_grid, load_development_context,
    load_object_rows,
)
from run_force_models_v2 import _record_for_horizon, _runtime_environment
from selection_v2 import choose_model_v2
from selector_v3 import choose_v3, features_v3
from encounter_screening import scan_body
from trajectory_metrics import summarize_error_rows, trajectory_error_rows


OUT = "outputs/selector_holdout24"
V2_CONFIG = "configs/force_models_v2.json"
FEATURE_CONFIG = "configs/development30.json"
CONTRACT = "docs/SELECTOR_V3_CONTRACT.md"
V3_MODEL = "outputs/selector_v3/model.json"
METHOD_FREEZE = "outputs/selector_v3/method_freeze.json"
V2_RULES = "outputs/force_models_v2/rules.json"
HORIZONS = (7.0, 30.0, 90.0, 180.0, 365.0)
TOLERANCES = (0.1, 1.0, 10.0)
METHODS = ("horizon_v2", "horizon_guard", "fixed_full", "tree_guard")
FULL_MODEL = "V2-P-GR-SB16"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode()


def immutable_json(path: Path, value: Any) -> None:
    payload = _json(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise RuntimeError(f"completed immutable artifact differs: {path}")
        return
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_bytes(payload)
    temporary.replace(path)


def runtime_environment() -> dict[str, str]:
    return {"python_version": sys.version, "python_implementation": sys.implementation.name,
            "platform": sys.platform, "platform_release": platform.release(),
            "machine": platform.machine()}


def state_json(state: State) -> dict[str, list[float]]:
    if not isinstance(state, State):
        raise TypeError("state must be State")
    return {"r": [float(x) for x in state.position], "v": [float(x) for x in state.velocity]}


def state_from_json(value: Mapping[str, Any]) -> State:
    if not isinstance(value, Mapping) or set(value) != {"r", "v"}:
        raise ValueError("state must contain exactly r and v")
    state = State(tuple(float(x) for x in value["r"]), tuple(float(x) for x in value["v"]))
    if len(state.position) != 3 or len(state.velocity) != 3 or any(not math.isfinite(x) for x in (*state.position, *state.velocity)):
        raise ValueError("state components must be finite three-vectors")
    return state


def trace_json(meta: Mapping[str, Any]) -> dict[str, Any]:
    accepted = meta.get("accepted_states")
    if not isinstance(accepted, list) or not accepted:
        raise ValueError("forecast metadata has no accepted_states")
    result = []
    previous = None
    for item in accepted:
        if not isinstance(item, Mapping):
            raise ValueError("malformed accepted state")
        t = float(item["time_days"])
        if not math.isfinite(t) or (previous is not None and t <= previous):
            raise ValueError("accepted times must be finite and strictly increasing")
        state = item["state"] if isinstance(item["state"], State) else state_from_json(item["state"])
        result.append({"time_days": t, "state": state_json(state)})
        previous = t
    if result[0]["time_days"] != 0.0:
        raise ValueError("accepted trace must start at zero")
    return {"trace_schema_version": 1, "accepted_states": result,
            "runtime_seconds": float(meta.get("runtime_seconds", 0.0)),
            "rk4_steps": int(meta.get("rk4_steps", 0)),
            "force_metadata": meta.get("force_metadata", {})}


def _trace_states(trace: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = trace.get("accepted_states")
    if not isinstance(values, list) or not values:
        raise ValueError("trace has no accepted states")
    result = []
    previous = None
    for item in values:
        if not isinstance(item, Mapping):
            raise ValueError("malformed trace point")
        t = float(item.get("time_days"))
        if not math.isfinite(t) or (previous is not None and t <= previous):
            raise ValueError("trace times are not finite/increasing")
        result.append({"time_days": t, "state": state_from_json(item["state"])})
        previous = t
    if result[0]["time_days"] != 0.0:
        raise ValueError("trace does not start at zero")
    return result


def _stored_traces(root: Path, case: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    paths = case.get("trace_paths")
    hashes = case.get("trace_hashes")
    if isinstance(paths, list) and len(paths) == 2:
        if not isinstance(hashes, Mapping):
            raise ValueError("trace paths require trace hashes")
        loaded = []
        for relative in paths:
            path = root / str(relative)
            if not path.is_file() or sha(path) != hashes.get(str(relative)):
                raise ValueError(f"trace is missing or hash mismatched: {path}")
            loaded.append(json.loads(path.read_text(encoding="utf-8")))
        return loaded[0], loaded[1]
    production, fine = case.get("production_trace"), case.get("fine_trace")
    if not isinstance(production, Mapping) or not isinstance(fine, Mapping):
        raise ValueError("case has no production/fine traces")
    return production, fine


def _v2_context_config(v2: Mapping[str, Any]) -> dict[str, Any]:
    config = dict(v2)
    # Selector raw data contains only asteroid references.  Existing hourly
    # planetary/SB16 inputs remain the frozen development30 exogenous context.
    config["raw_directory"] = "data/raw/development30"
    return config


def load_inputs(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    sample_path, manifest_path = root / SAMPLE_PATH, root / MANIFEST_PATH
    if not sample_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("selector holdout sample and manifest are required")
    sample, manifest = json.loads(sample_path.read_text()), json.loads(manifest_path.read_text())
    objects = sample.get("objects")
    if sample.get("schema_version") != 1 or not isinstance(objects, list) or len(objects) != 24 or len({str(o.get("id")) for o in objects}) != 24:
        raise ValueError("sample must contain 24 unique objects")
    if manifest.get("complete") is not True or manifest.get("sample_sha256") != sha(sample_path):
        raise ValueError("holdout manifest is incomplete or does not match sample")
    if {str(o["id"]) for o in objects} & set(map(str, sample["excluded_object_ids"])):
        raise ValueError("Inspected object leaked into holdout")
    if any(sum(o["stratum"] == stratum for o in objects) != 4 for stratum in sample["strata"]):
        raise ValueError("Require four objects per fixed stratum")
    files = manifest.get("files")
    if not isinstance(files, list) or len({str(x.get("path")) for x in files}) != len(files):
        raise ValueError("manifest has invalid/duplicate paths")
    for item in files:
        path = root / str(item["path"])
        if not path.is_file() or sha(path) != item.get("sha256") or path.stat().st_size != item.get("bytes"):
            raise ValueError(f"raw provenance mismatch: {path}")
    v2 = json.loads((root / V2_CONFIG).read_text())
    artifact = json.loads((root / V3_MODEL).read_text())
    rules = json.loads((root / V2_RULES).read_text())
    method = json.loads((root / METHOD_FREEZE).read_text())
    check_hashes(root, method.get("hashes"))
    if method["runtime"] != _runtime_environment():
        raise ValueError("Runtime differs from method freeze")
    method_sha = sha(root / METHOD_FREEZE)
    if sample.get("method_freeze_sha256") != method_sha or manifest.get("method_freeze_sha256") != method_sha:
        raise ValueError("sample/manifest method-freeze hash mismatch")
    return sample, manifest, v2, {"artifact": artifact, "rules": rules, "method_freeze": method}


def source_hashes(root: Path, sample: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, str]:
    method = json.loads((root / METHOD_FREEZE).read_text())
    check_hashes(root, method["hashes"])
    relative = [METHOD_FREEZE, SAMPLE_PATH, MANIFEST_PATH]
    relative += [str(item["path"]) for item in manifest["files"]]
    return {**method["hashes"], **{p: sha(root / p) for p in relative}}


def _model_by_id(config: Mapping[str, Any], model_id: str) -> dict[str, Any]:
    for model in config["models"]:
        if str(model["model_id"]) == model_id:
            return dict(model)
    raise ValueError(f"unknown selected model {model_id}")


def decide(method: str, feature: Mapping[str, Any], horizon: float, tolerance: float,
           config: Mapping[str, Any], rules: Mapping[str, Any], tree: Mapping[str, Any]) -> dict[str, Any]:
    if method not in METHODS:
        raise ValueError(f"unknown selector method: {method}")
    if method in ("horizon_v2", "horizon_guard"):
        decision = dict(choose_model_v2(horizon, tolerance, rules, config))
        decision["status"] = "no_candidate_predicted" if decision.get("fallback") else "empirical_predicted_feasible"
        strong = method == "horizon_guard" and feature.get("strong_encounter") is True
        decision["strong_encounter"] = strong
        if strong:
            decision.update(model_id=config["fallback_model_id"], fallback=True, status="strong_encounter_unvalidated")
            decision.pop("empirical_error_cap_km", None)
    elif method == "fixed_full":
        decision = {"model_id": config.get("fallback_model_id", FULL_MODEL), "fallback": False,
                    "status": "fixed_full", "strong_encounter": False}
    else:
        decision = dict(choose_v3(feature, horizon, tolerance, tree))
    decision.update(method=method, warning=decision["status"] in ("no_candidate_predicted", "strong_encounter_unvalidated"),
                    accuracy_guaranteed=False, conditional_on_initial_state=True, covariance_calibrated=False)
    return decision


def _feature_config(config: Mapping[str, Any]) -> dict[str, Any]:
    return {"default_step_days": float(config.get("default_step_days", 0.0625)),
            "step_scale": float(config.get("step_scale", 0.5))}


def forecast_online(
    initial: State, start_jd: float, horizon: float, tolerance: float, method: str,
    artifact_v3: Mapping[str, Any], artifact_v2: Mapping[str, Any], context: Mapping[str, Any],
    config: Mapping[str, Any], feature_config: Mapping[str, Any], ng: Any | None = None,
) -> tuple[list[State], dict[str, Any], dict[str, Any]]:
    """Run one complete forecast-time decision and rollout.

    ``fixed_full`` and the unguarded v2 rule do not pay for feature building.
    Both guarded methods build features inside this function, which is the
    path used for direct online timing.  No target rows or object identifier
    is accepted by this API.
    """
    if method not in METHODS:
        raise ValueError(f"unknown selector method: {method}")
    began = time.perf_counter()
    feature: Mapping[str, Any] = {"strong_encounter": False}
    if method in ("horizon_guard", "tree_guard"):
        feature = features_v3(initial, start_jd, horizon, dict(context), dict(feature_config), ng=ng)
    decision = decide(method, feature, horizon, tolerance, config, artifact_v2, artifact_v3)
    model = _model_by_id(config, decision["model_id"])
    prediction, metadata = forecast_candidate_v2(
        initial, start_jd, horizon, model, {**context, "start_jd": start_jd}, config,
        float(config["step_scale"]), ng=ng,
    )
    online = {"runtime_seconds": time.perf_counter() - began, "decision": decision,
              "feature_built": method in ("horizon_guard", "tree_guard"),
              "feature_runtime_seconds": feature.get("runtime_seconds", 0.0),
              "accuracy_guaranteed": False, "conditional_on_initial_state": True, "covariance_calibrated": False}
    return prediction, metadata, online


def _event_geometry(obj: Mapping[str, Any], reference: list[dict[str, Any]], start: float,
                    production_meta: Mapping[str, Any], fine_meta: Mapping[str, Any],
                    context: Mapping[str, Any]) -> dict[str, Any] | None:
    """Evaluate the selected event only on its known refined ±2-day window."""
    event = obj.get("event")
    if event is None:
        return None
    body_id = str(event["body_id"])
    body = next((candidate for candidate in context["planets"] if candidate.body_id == body_id), None)
    if body is None:
        raise ValueError(f"event body {body_id} is absent from exogenous context")
    centre = math.floor(float(event["jd"]) - 0.5) + 0.5  # midnight of the fixed refinement date
    reference_window = [row for row in reference if abs(float(row["epoch_jd_tdb"]) - centre) <= 2.0 + 2.0e-9]
    reference_window.sort(key=lambda row: float(row["epoch_jd_tdb"]))
    if len(reference_window) != 1153:
        raise ValueError(f"event reference window is incomplete: {obj['id']}")
    reference_times = [float(row["epoch_jd_tdb"]) - start for row in reference_window]
    reference_states = [State(tuple(row["r"]), tuple(row["v"])) for row in reference_window]

    def geometry(meta: Mapping[str, Any]) -> dict[str, Any]:
        native = list(meta["accepted_states"])
        left, right = reference_times[0], reference_times[-1]
        at = _interpolate_endpoints(native)
        middle = [item for item in native if left < item["time_days"] < right]
        times = [left] + [float(item["time_days"]) for item in middle] + [right]
        states = [at(left)] + [item["state"] for item in middle] + [at(right)]
        body_states = [body.ephemeris.state_at(start + t) for t in times]
        return scan_body(times, states, body_states, body, float(context["mu"]), float(context["au_km"]),
                         float(context["day_s"]), refine=True)

    predicted = geometry(production_meta)
    fine = geometry(fine_meta)
    body_reference_states = [body.ephemeris.state_at(start + t) for t in reference_times]
    reference_geometry = scan_body(reference_times, reference_states, body_reference_states, body,
                                   float(context["mu"]), float(context["au_km"]), float(context["day_s"]), refine=True)
    return {"event_body_id": body_id, "predicted": predicted, "fine": fine, "reference": reference_geometry,
            "errors": {"distance_km": abs(float(predicted["distance_km"]) - float(reference_geometry["distance_km"])),
                        "time_days": abs(float(predicted["time_days"]) - float(reference_geometry["time_days"]))},
            "production_fine_shift": {"distance_km": abs(float(predicted["distance_km"]) - float(fine["distance_km"])),
                                       "time_days": abs(float(predicted["time_days"]) - float(fine["time_days"]))},
            "reference_window_days": 2.0, "reference_nodes": len(reference_times),
            "reference_max_spacing_seconds": max(b-a for a,b in zip(reference_times,reference_times[1:])) * float(context["day_s"]),
            "reference_bounds_jd_tdb": [start+reference_times[0], start+reference_times[-1]],
            "basis": "accepted endpoints native times; evaluator reference refined nodes ±2 d"}


def _case(obj: Mapping[str, Any], model: Mapping[str, Any], horizon: float, context: Mapping[str, Any],
          config: Mapping[str, Any], reference: list[dict[str, Any]], start: float, initial: State,
          feature: Mapping[str, Any], production_meta: Mapping[str, Any], fine_meta: Mapping[str, Any]) -> dict[str, Any]:
    record = _record_for_horizon(initial, start, horizon, model, context, config, reference,
                                 ([], production_meta), ([], fine_meta), str(obj["id"]), str(obj["name"]),
                                 str(obj["start_date"]), "selector_holdout24")
    record["evaluation_scope"] = "frozen_selector_v3_new_holdout24"
    geometry = _event_geometry(obj, reference, start, production_meta, fine_meta, context) if horizon == max(HORIZONS) else None
    if geometry is not None:
        record["closest_geometry"] = geometry
    return {"schema_version": 1, "case_id": f"{obj['id']}__{model['model_id']}__{horizon:g}",
            "object_id": str(obj["id"]), "object_name": str(obj["name"]), "model_id": model["model_id"],
            "horizon_days": horizon, "start_jd_tdb": start, "initial_state": state_json(initial),
            "feature": {k: feature[k] for k in ("schema_version", "feature_names", "vector", "strong_encounter", "strong_encounter_rule", "ng", "geometry", "warnings")},
            "runtime_seconds": float(production_meta.get("runtime_seconds", 0.0)), "record": record}


def _run_pair(root: Path, obj: Mapping[str, Any], model: Mapping[str, Any], context: Mapping[str, Any],
              config: Mapping[str, Any], feature_config: Mapping[str, Any], feature_cache=None) -> tuple[list[dict[str, Any]], Mapping[str, Any], Mapping[str, Any]]:
    daily, reference = load_object_rows(root, root / RAW_DIRECTORY, obj)
    start, initial = _find_initial(daily, str(obj["start_date"]))
    ng = load_ng_input(root / RAW_DIRECTORY / "asteroids" / f"asteroid_{obj['id']}_daily.json")
    if feature_cache is None:
        feature_cache = {h: features_v3(initial, start, h, dict(context), dict(feature_config), ng=ng) for h in HORIZONS}
    annual = max(HORIZONS)
    production, production_meta = forecast_candidate_v2(initial, start, annual, model, {**context, "start_jd": start}, config, float(config["step_scale"]), ng=ng)
    fine, fine_meta = forecast_candidate_v2(initial, start, annual, model, {**context, "start_jd": start}, config, float(config["step_scale"]) * float(config.get("sensitivity_scale_factor", 0.5)), ng=ng)
    cases = [_case(obj, model, h, context, config, reference, start, initial, feature_cache[h], production_meta, fine_meta) for h in HORIZONS]
    return cases, production_meta, fine_meta


def _eligible(record: Mapping[str, Any], tolerance: float, fraction: float) -> bool:
    return float(record["max_position_error_km"]) <= tolerance and float(record["numerical_difference_km"]) <= fraction * tolerance


def _choice_rows(records: list[Mapping[str, Any]], sample: Mapping[str, Any], config: Mapping[str, Any], rules: Mapping[str, Any], tree: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_case = {(r["object_id"], r["model_id"], float(r["horizon_days"])): r for r in records}
    expected = {(str(o["id"]), m["model_id"], h) for o in sample["objects"] for m in config["models"] for h in HORIZONS}
    if len(records) != len(expected) or set(by_case) != expected:
        raise ValueError("Incomplete or duplicate physical candidate matrix")
    choices = []
    for obj in sample["objects"]:
        oid = str(obj["id"])
        for h in HORIZONS:
            rows = [by_case[(oid, str(model["model_id"]), h)] for model in config["models"]]
            feature = rows[0]["feature"]
            if any(r["feature"] != feature for r in rows):
                raise ValueError("Candidate records disagree on forecast features")
            for tol in TOLERANCES:
                for method in METHODS:
                    d = decide(method, feature, h, float(tol), config, rules, tree)
                    row_record = by_case[(oid, d["model_id"], h)]["record"]
                    any_candidate = any(_eligible(candidate["record"], float(tol), float(config["numerical_budget_fraction"])) for candidate in rows)
                    choices.append({"object_id": oid, "horizon_days": h, "method": method, "tolerance_km": tol,
                                    "model_id": d["model_id"], "selected_model_id": d["model_id"], "status": d["status"], "fallback": bool(d.get("fallback", False)),
                                    "warning": bool(d.get("warning", False)),
                                    "strong_encounter": bool(d.get("strong_encounter", False)),
                                    "any_candidate_eligible": any_candidate,
                                    "no_candidate_truth": not any_candidate,
                                    "no_candidate_flagged": (not any_candidate) and bool(d.get("warning", False)),
                                    "actual_eligible": _eligible(row_record, tol, float(config["numerical_budget_fraction"])),
                                    "max_position_error_km": row_record["max_position_error_km"],
                                    "numerical_difference_km": row_record["numerical_difference_km"]})
    summaries = []
    for tol in TOLERANCES:
        for method in METHODS:
            subset = [x for x in choices if x["tolerance_km"] == tol and x["method"] == method]
            warned = [x for x in subset if x["warning"]]
            summaries.append({"tolerance_km": tol, "method": method, "cases": len(subset),
                "eligible_cases": sum(x["actual_eligible"] for x in subset),
                "feasible_cases": sum(x["any_candidate_eligible"] for x in subset),
                "no_candidate_cases": sum(x["no_candidate_truth"] for x in subset),
                "avoidable_selection_misses": sum(x["any_candidate_eligible"] and not x["actual_eligible"] for x in subset),
                "warning_cases": len(warned), "strong_flagged": sum(x["strong_encounter"] for x in subset),
                "warning_vs_no_candidate": {"tp": sum(x["no_candidate_truth"] and x["warning"] for x in subset),
                    "fn": sum(x["no_candidate_truth"] and not x["warning"] for x in subset),
                    "fp": sum(not x["no_candidate_truth"] and x["warning"] for x in subset),
                    "tn": sum(not x["no_candidate_truth"] and not x["warning"] for x in subset)},
                "warning_on_eligible_prediction": sum(x["actual_eligible"] for x in warned),
                "unflagged_cases": len(subset)-len(warned),
                "unflagged_failures": sum(not x["actual_eligible"] and not x["warning"] for x in subset),
                "worst_position_error_km": max(x["max_position_error_km"] for x in subset)})
    return choices, summaries


def _provenance(root: Path, sample: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {"scope": "selector-v3 whole-object holdout24", "source_hashes": source_hashes(root, sample, manifest),
            # A resume fingerprint must be stable across invocations.  Wall
            # clock timestamps belong in result metadata, never in the hash
            # that authorizes reuse of a completed checkpoint.
            "runtime_environment": runtime_environment()}


def matrix(root: Path, *, resume: bool = False) -> dict[str, Any]:
    sample, manifest, config, artifacts = load_inputs(root)
    provenance = _provenance(root, sample, manifest)
    out = root / OUT
    marker = out / "run_started.json"
    marker_payload = {"schema_version": 1, "provenance": provenance}
    if marker.exists() and json.loads(marker.read_text()).get("provenance") != provenance:
        raise RuntimeError("run freeze differs from existing run_started.json")
    immutable_json(marker, marker_payload)
    completed_path = out / "matrix.json"
    if completed_path.exists():
        saved = json.loads(completed_path.read_text())
        if saved["provenance"] != provenance:
            raise ValueError("Completed matrix provenance mismatch")
        for row in saved["records"]:
            check_hashes(root, row["trace_hashes"])
        return saved
    context = load_development_context(root, _v2_context_config(config))
    feature_config = json.loads((root / FEATURE_CONFIG).read_text(encoding="utf-8"))
    records = []
    for obj in sample["objects"]:
        daily, _ = load_object_rows(root, root / RAW_DIRECTORY, obj)
        ng = load_ng_input(root / RAW_DIRECTORY / "asteroids" / f"asteroid_{obj['id']}_daily.json")
        start, initial = _find_initial(daily, str(obj["start_date"]))
        feature_path = out / "features" / f"object_{obj['id']}.json"
        if feature_path.exists():
            feature_record = json.loads(feature_path.read_text())
            if feature_record["provenance"] != provenance:
                raise ValueError("Feature cache provenance differs")
            feature_cache = {float(h): f for h, f in feature_record["features"].items()}
        else:
            feature_cache = {h: features_v3(initial, start, h, dict(context), dict(feature_config), ng=ng) for h in HORIZONS}
            immutable_json(feature_path, {"provenance": provenance, "features": feature_cache})
        for model in config["models"]:
            key = f"{obj['id']}__{model['model_id']}"
            checkpoint = out / "checkpoints" / f"{key}.json"
            if checkpoint.exists():
                saved = json.loads(checkpoint.read_text())
                if saved.get("provenance") != provenance or not isinstance(saved.get("cases"), list):
                    raise RuntimeError(f"checkpoint freeze mismatch: {checkpoint}")
                trace_hashes = saved.get("trace_hashes")
                if not isinstance(trace_hashes, Mapping):
                    raise RuntimeError(f"checkpoint has no trace hashes: {checkpoint}")
                for relative, expected_hash in trace_hashes.items():
                    path = root / str(relative)
                    if not path.is_file() or sha(path) != expected_hash:
                        raise RuntimeError(f"completed trace hash mismatch: {path}")
                records.extend(saved["cases"])
                continue
            cases, production_meta, fine_meta = _run_pair(root, obj, model, context, config, feature_config, feature_cache)
            trace_dir = out / "traces"
            safe = key.replace("+", "_").replace("/", "_")
            production_path, fine_path = trace_dir / f"{safe}_production.json", trace_dir / f"{safe}_fine.json"
            immutable_json(production_path, trace_json(production_meta))
            immutable_json(fine_path, trace_json(fine_meta))
            paths = [production_path.relative_to(root).as_posix(), fine_path.relative_to(root).as_posix()]
            hashes = {paths[0]: sha(production_path), paths[1]: sha(fine_path)}
            for case in cases:
                case["trace_paths"] = paths
                case["trace_hashes"] = hashes
            immutable_json(checkpoint, {"provenance": provenance, "cases": cases, "trace_hashes": hashes})
            records.extend(cases)
            print(json.dumps({"completed": key, "records": len(records)}, sort_keys=True), flush=True)
    expected = len(sample["objects"]) * len(config["models"]) * len(HORIZONS)
    if len(records) != expected:
        raise RuntimeError(f"expected {expected} records, got {len(records)}")
    choices, summaries = _choice_rows(records, sample, config, artifacts["rules"], artifacts["artifact"])
    result = {"schema_version": 1, "experiment": "selector_v3_holdout24", "provenance": provenance,
              "records": records, "choices": choices, "summaries": summaries,
              "shared_ephemeris_load_seconds": context["load_seconds"],
              "record_count": len(records), "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    immutable_json(out / "matrix.json", result)
    return result


def direct_cost(root: Path, *, resume: bool = False) -> dict[str, Any]:
    """Time complete causal calls, then independently check their actual predictions."""
    sample, manifest, config, artifacts = load_inputs(root)
    provenance = _provenance(root, sample, manifest)
    matrix_path = root / OUT / "matrix.json"
    matrix_result = json.loads(matrix_path.read_text())
    if matrix_result["provenance"] != provenance:
        raise ValueError("Complete frozen matrix required before timing")
    target = root / OUT / "direct_cost.json"
    if target.exists():
        old = json.loads(target.read_text())
        if old["provenance"] != provenance or old["matrix_sha256"] != sha(matrix_path):
            raise ValueError("Completed timing provenance mismatch")
        return old
    lookup = {(r["object_id"], r["horizon_days"], r["model_id"]): r for r in matrix_result["records"]}
    decisions = {(r["object_id"], r["horizon_days"], r["method"]): r
                 for r in matrix_result["choices"] if r["tolerance_km"] == 1.0}
    context = load_development_context(root, _v2_context_config(config))
    feature_config = json.loads((root / FEATURE_CONFIG).read_text())
    all_rows = []
    for obj in sample["objects"]:
        path = root / OUT / "cost_checkpoints" / f"object_{obj['id']}.json"
        if path.exists():
            saved = json.loads(path.read_text())
            if saved["provenance"] != provenance or saved["matrix_sha256"] != sha(matrix_path):
                raise ValueError("Timing checkpoint provenance differs")
            all_rows.extend(saved["timings"])
            continue
        daily, reference = load_object_rows(root, root / RAW_DIRECTORY, obj)
        start, initial = _find_initial(daily, obj["start_date"])
        ng = load_ng_input(root / RAW_DIRECTORY / "asteroids" / f"asteroid_{obj['id']}_daily.json")
        rows = []
        object_traces = {}
        for m in config["models"]:
            row = lookup[(obj["id"], 365.0, m["model_id"])]
            trace, _ = _stored_traces(root, row)
            object_traces[m["model_id"]] = _interpolate_endpoints(_trace_states(trace))
        for h in HORIZONS:
            times, reference_states = _reference_grid(reference, start, h)
            trials = {m: [] for m in METHODS}
            feature_trials = {m: [] for m in METHODS}
            entries, final_states = {}, {}
            for repeat in range(3):
                order = METHODS if repeat % 2 == 0 else tuple(reversed(METHODS))
                for method in order:
                    prediction, metadata, online = forecast_online(
                        initial, start, h, 1.0, method, artifacts["artifact"], artifacts["rules"],
                        context, config, feature_config, ng=ng)
                    trials[method].append(online["runtime_seconds"])
                    feature_trials[method].append(online["feature_runtime_seconds"])
                    decision = online["decision"]
                    expected_decision = decisions[(obj["id"], h, method)]
                    if any(decision[k] != expected_decision[k] for k in ("model_id", "status", "warning", "strong_encounter")):
                        raise ValueError("Online and saved decisions differ")
                    last = metadata["accepted_states"][-1]["state"]
                    if method in final_states and final_states[method] != last:
                        raise ValueError("Repeated forecast endpoints are not deterministic")
                    final_states[method] = last
                    if repeat == 0:
                        at = _interpolate_endpoints(metadata["accepted_states"])
                        actual_states = [at(t) for t in times]
                        annual_at = object_traces[decision["model_id"]]
                        shift = max(norm(subtract(a.position, annual_at(t).position)) * context["au_km"]
                                    for t, a in zip(times, actual_states, strict=True))
                        if shift > 1e-5:
                            raise ValueError(f"Direct forecast differs from annual prefix: {obj['id']} {h} {method}: {shift} km")
                        measured = summarize_error_rows(trajectory_error_rows(times, actual_states, reference_states,
                                                       context["au_km"], context["day_s"]))
                        expected = lookup[(obj["id"], h, decision["model_id"])]["record"]
                        if any(abs(measured[k] - expected[k]) > 1e-5 for k in ("max_position_error_km", "max_velocity_error_m_s")):
                            raise ValueError("Actual online error differs from matrix evaluation")
                        entries[method] = {"object_id": obj["id"], "horizon_days": h, "method": method,
                            "tolerance_km": 1.0, "model_id": decision["model_id"], "status": decision["status"],
                            "warning": decision["warning"], "actual_eligible": _eligible(expected, 1.0, config["numerical_budget_fraction"]),
                            "max_position_error_km": measured["max_position_error_km"],
                            "max_velocity_error_m_s": measured["max_velocity_error_m_s"],
                            "max_direct_vs_annual_prefix_km": shift}
                    del prediction, metadata, online
            for method in METHODS:
                rows.append({**entries[method], "runtime_trials_seconds": trials[method],
                    "runtime_median_seconds": statistics.median(trials[method]),
                    "feature_runtime_trials_seconds": feature_trials[method],
                    "includes": ["inference", "force_construction", "rollout"] +
                                (["features"] if method in ("tree_guard", "horizon_guard") else [])})
            print(json.dumps({"timing_object": obj["id"], "horizon_completed": h}), flush=True)
        immutable_json(path, {"provenance": provenance, "matrix_sha256": sha(matrix_path), "timings": rows})
        all_rows.extend(rows)
    totals = {m: {"cases": sum(r["method"] == m for r in all_rows),
                  "eligible_cases": sum(r["method"] == m and r["actual_eligible"] for r in all_rows),
                  "total_seconds": sum(r["runtime_median_seconds"] for r in all_rows if r["method"] == m)} for m in METHODS}
    result = {"schema_version": 1, "provenance": provenance, "timings": all_rows, "full_cost": totals,
              "rows": len(all_rows), "matrix_sha256": sha(matrix_path), "shared_ephemeris_load_seconds": context["load_seconds"],
              "timing_scope": "120 cases x 4 methods x 3 alternating repeats; resident inputs; evaluator and cold loading excluded"}
    immutable_json(target, result)
    return result


def verify(root: Path) -> dict[str, Any]:
    sample, manifest, config, artifacts = load_inputs(root)
    matrix_path = root / OUT / "matrix.json"
    result = json.loads(matrix_path.read_text())
    provenance = _provenance(root, sample, manifest)
    if result["provenance"] != provenance or result["record_count"] != 480:
        raise ValueError("Matrix provenance or count differs")
    context = load_development_context(root, _v2_context_config(config))
    choices, summaries = _choice_rows(result["records"], sample, config, artifacts["rules"], artifacts["artifact"])
    if choices != result["choices"] or summaries != result["summaries"]:
        raise ValueError("Decisions do not reproduce")
    objects = {o["id"]: o for o in sample["objects"]}
    inputs = {}
    traces = None
    previous_paths = None
    samples = geometry_count = 0
    unique_paths = set()
    normal = lambda value: json.loads(json.dumps(value))
    for row in result["records"]:
        oid, h = row["object_id"], row["horizon_days"]
        if oid not in inputs:
            daily, reference = load_object_rows(root, root / RAW_DIRECTORY, objects[oid])
            start, initial = _find_initial(daily, objects[oid]["start_date"])
            inputs[oid] = reference, start, initial
        reference, start, initial = inputs[oid]
        if row["initial_state"] != state_json(initial) or row["start_jd_tdb"] != start:
            raise ValueError("Forecast initial input differs")
        if row["trace_paths"] != previous_paths:
            raw_traces = _stored_traces(root, row)
            points = [_trace_states(t) for t in raw_traces]
            for pt in points:
                if pt[0]["state"] != initial or pt[-1]["time_days"] != 365.0:
                    raise ValueError("Trace does not match initial state/annual endpoint")
            traces = [_interpolate_endpoints(pt) for pt in points]
            previous_paths = row["trace_paths"]
            unique_paths.update(previous_paths)
        times, reference_states = _reference_grid(reference, start, h)
        pred, fine = [[at(t) for t in times] for at in traces]
        errors = trajectory_error_rows(times, pred, reference_states, context["au_km"], context["day_s"])
        summary = normal(summarize_error_rows(errors))
        numerical = max(norm(subtract(a.position, b.position)) * context["au_km"] for a,b in zip(pred,fine,strict=True))
        stored = row["record"]
        if any(stored[k] != v for k,v in summary.items()) or normal(errors) != stored["error_rows"]:
            raise ValueError("Stored position/velocity/RTN error rows do not reproduce")
        if numerical != stored["numerical_difference_km"]:
            raise ValueError("Step comparison does not reproduce")
        for tol in TOLERANCES:
            if stored["eligibility"][str(tol)] != _eligible(stored,tol,config["numerical_budget_fraction"]):
                raise ValueError("Eligibility differs")
        if h == 365.0 and objects[oid]["event"] is not None:
            geom = _event_geometry(objects[oid],reference,start,{"accepted_states":points[0]},
                                   {"accepted_states":points[1]},context)
            if normal(geom) != stored["closest_geometry"]:
                raise ValueError("Closest-approach metrics do not reproduce")
            geometry_count += 1
        samples += len(errors)
    if len(unique_paths) != 192 or geometry_count != 64:
        raise ValueError("Incomplete physical traces or 16-event/four-model geometry")
    timing_path = root / OUT / "direct_cost.json"
    timing = json.loads(timing_path.read_text())
    if timing["provenance"] != provenance or timing["matrix_sha256"] != sha(matrix_path):
        raise ValueError("Timing provenance mismatch")
    expected = {(o["id"], h, m) for o in sample["objects"] for h in HORIZONS for m in METHODS}
    found = {(r["object_id"], r["horizon_days"], r["method"]) for r in timing["timings"]}
    if len(timing["timings"]) != 480 or found != expected:
        raise ValueError("Incomplete or duplicate timing cases")
    choice_map = {(r["object_id"],r["horizon_days"],r["method"]):r for r in choices if r["tolerance_km"]==1.0}
    for row in timing["timings"]:
        trials = row["runtime_trials_seconds"]
        if len(trials) != 3 or any(not math.isfinite(v) or v <= 0 for v in trials) or statistics.median(trials) != row["runtime_median_seconds"]:
            raise ValueError("Invalid actual timing")
        decision = choice_map[(row["object_id"],row["horizon_days"],row["method"])]
        if any(row[k] != decision[k] for k in ("model_id","status","warning","actual_eligible")):
            raise ValueError("Timing used a different selection")
    totals = {m:{"cases":sum(r["method"]==m for r in timing["timings"]),
                 "eligible_cases":sum(r["method"]==m and r["actual_eligible"] for r in timing["timings"]),
                 "total_seconds":sum(r["runtime_median_seconds"] for r in timing["timings"] if r["method"]==m)} for m in METHODS}
    if totals != timing["full_cost"]:
        raise ValueError("Timing totals do not reproduce")
    raw = [str(r["path"]) for r in manifest["files"]]
    check = subprocess.run(["git","check-ignore","--stdin"],cwd=root,input="\n".join(raw)+"\n",text=True,capture_output=True)
    tracked = subprocess.run(["git","ls-files"],cwd=root,text=True,capture_output=True)
    if set(check.stdout.splitlines()) != set(raw) or set(raw) & set(tracked.stdout.splitlines()):
        raise ValueError("Raw data are not excluded from Git")
    summary = {"passed":True,"records":480,"choices":len(choices),"error_rows":samples,"physical_traces":len(unique_paths),
               "event_geometry_records":geometry_count,"direct_timing_rows":len(timing["timings"]),
               "raw_files_checked":len(raw),"matrix_sha256":sha(matrix_path),"timing_sha256":sha(timing_path),
               "verifier_source_sha256":sha(Path(__file__))}
    immutable_json(root/OUT/"verification.json",summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("matrix", "cost", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    fn = {"matrix": matrix, "cost": direct_cost, "verify": verify}[args.phase]
    value = fn(args.root.resolve(), resume=args.resume) if args.phase != "verify" else fn(args.root.resolve())
    print(json.dumps({k: value[k] for k in ("record_count", "rows", "passed", "records") if k in value}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["METHODS", "HORIZONS", "TOLERANCES", "decide", "immutable_json", "matrix", "direct_cost", "verify"]

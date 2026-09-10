"""Train-only horizon baseline for the explicitly versioned v2 force family."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
import statistics
import time

from force_models_v2 import ForceModelSpec, forecast_candidate_v2


def config_digest(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _positive(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be positive and finite")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return value


def fit_horizon_rule_v2(records, config):
    """Fit caps/costs from complete train records; no validation label is allowed.

    The prior validation cohort has been inspected, so subsequent evaluation on
    it is explicitly a replay. Caps are empirical, not error guarantees.
    """
    models = [ForceModelSpec.from_mapping(item).model_id for item in config["models"]]
    if len(set(models)) != len(models) or not models:
        raise ValueError("model catalogue must contain unique candidates")
    if config.get("fallback_model_id") not in models:
        raise ValueError("an explicit fallback_model_id from the catalogue is required")
    horizons = {_positive(value, "horizon") for value in config["horizons_days"]}
    budget = _positive(config["numerical_budget_fraction"], "numerical budget")
    tasks = defaultdict(set)
    object_horizons = defaultdict(set)
    errors, costs = defaultdict(list), defaultdict(list)
    for row in records:
        if "original_split" in row and "split" in row and row["original_split"] != row["split"]:
            raise ValueError("conflicting split labels")
        if row.get("original_split", row.get("split")) != "train":
            raise ValueError("only training records may be fitted")
        object_id, horizon, model_id = str(row["object_id"]), float(row["horizon_days"]), row["model_id"]
        if horizon not in horizons or model_id not in models:
            raise ValueError("unknown model or horizon in training record")
        key = (object_id, horizon)
        if model_id in tasks[key]:
            raise ValueError("duplicate training object/horizon/model")
        tasks[key].add(model_id)
        object_horizons[object_id].add(horizon)
        error, step, cost = (float(row[name]) for name in
                             ("max_position_error_km", "numerical_difference_km", "runtime_median_seconds"))
        if any(not math.isfinite(value) or value < 0 for value in (error, step, cost)):
            raise ValueError("training metrics must be finite and nonnegative")
        errors[(model_id, horizon)].append(max(error, step / budget))
        costs[(model_id, horizon)].append(cost)
    if not tasks or any(value != set(models) for value in tasks.values()):
        raise ValueError("every training case must have all candidate records")
    if any(value != horizons for value in object_horizons.values()):
        raise ValueError("every training object must cover every configured horizon")
    return {
        "schema_version": 2,
        "method": "horizon_rule_v2",
        "config_digest": config_digest(config),
        "training_object_ids": sorted(object_horizons),
        "models": models,
        "horizons_days": sorted(horizons),
        "effective_error_caps_km": {model: {str(float(h)): max(errors[(model, h)]) for h in sorted(horizons)} for model in models},
        "median_cost_seconds": {model: {str(float(h)): statistics.median(costs[(model, h)]) for h in sorted(horizons)} for model in models},
        "fallback_model_id": config["fallback_model_id"],
        "accuracy_guaranteed": False,
        "evaluation_scope": "development replay; fresh holdout required",
    }


def choose_model_v2(horizon_days, tolerance_km, artifact, config):
    """Choose once, without object ID, target future states, event labels or errors."""
    horizon = _positive(horizon_days, "horizon_days")
    tolerance = _positive(tolerance_km, "tolerance_km")
    if artifact.get("schema_version") != 2 or artifact.get("method") != "horizon_rule_v2":
        raise ValueError("a calibrated v2 horizon artifact is required")
    if artifact["config_digest"] != config_digest(config):
        raise ValueError("rule/config mismatch: recalibrate after changing force or numerical settings")
    if horizon not in artifact["horizons_days"]:
        raise ValueError("horizon not calibrated; interpolation/extrapolation is not supported")
    key = str(float(horizon))
    caps = {model: artifact["effective_error_caps_km"][model][key] for model in artifact["models"]}
    feasible = [model for model, error in caps.items() if error <= tolerance]
    chosen = min(feasible, key=lambda model: (artifact["median_cost_seconds"][model][key], model)) if feasible else artifact["fallback_model_id"]
    return {"model_id": chosen, "fallback": not feasible, "empirical_error_cap_km": caps[chosen],
            "accuracy_guaranteed": False, "method": "horizon_rule_v2"}


def forecast_with_selection_v2(initial, start_jd, horizon_days, tolerance_km,
                               artifact, context, config, *, ng=None):
    """Full online nominal forecast including inference, force construction and rollout."""
    started = time.perf_counter()
    decision = choose_model_v2(horizon_days, tolerance_km, artifact, config)
    inference_end = time.perf_counter()
    model = next(item for item in config["models"] if item["model_id"] == decision["model_id"])
    predictions, metadata = forecast_candidate_v2(initial, start_jd, horizon_days, model, context,
                                                   config, config["step_scale"], ng=ng)
    elapsed = time.perf_counter() - started
    return predictions, metadata, {
        "decision": decision,
        "runtime_seconds": elapsed,
        "inference_seconds": inference_end - started,
        "propagation_seconds": elapsed - (inference_end - started),
        "force_metadata": metadata["force_metadata"],
        "accuracy_guaranteed": False,
    }

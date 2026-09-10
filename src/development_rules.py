"""Small, forecast-time rules for selecting an orbit propagation model.

The rules in this module are deliberately simple baselines for the first
operational selector.  Fitting is an offline operation over training labels
and forecast-time feature rows.  Selection consumes only a requested horizon,
position tolerance, and causal force-proxy features.

The fitted quantities are empirical safeguards, not physical error bounds.
They should be evaluated on future objects and event windows before being used
as a production policy.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping
from typing import Any


MODEL_IDS = ("B2", "B3", "B3+GR", "B3+GR+SB16")
STRONGEST_MODEL_ID = MODEL_IDS[-1]
_LABEL_FIELDS = (
    "case_id",
    "object_id",
    "horizon_days",
    "split",
    "model_id",
    "max_position_error_km",
    "numerical_difference_km",
    "runtime_median_seconds",
)
_FEATURE_FIELDS = ("case_id", "object_id", "horizon_days", "split", "features", "runtime_median_seconds")
_PROXY_FIELDS = ("planet_proxy_km", "gr_proxy_km", "small_body_proxy_km")
_FORBIDDEN_FORECAST_KEYS = {
    "case_id",
    "object_id",
    "horizon_days",
    "split",
    "model_id",
    "max_position_error_km",
    "numerical_difference_km",
    "runtime_median_seconds",
    "label",
    "target",
    "reference",
}


def _horizon_key(value: float) -> str:
    """Return the stable JSON key used for fitted per-horizon mappings."""

    return format(float(value), "g")


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _rows(rows: Iterable[Mapping[str, Any]], name: str) -> list[Mapping[str, Any]]:
    if isinstance(rows, (str, bytes)):
        raise TypeError(f"{name} must be an iterable of dictionaries")
    try:
        materialized = list(rows)
    except TypeError as exc:
        raise TypeError(f"{name} must be an iterable of dictionaries") from exc
    for index, row in enumerate(materialized):
        if not isinstance(row, Mapping):
            raise TypeError(f"{name}[{index}] must be a dictionary")
    if not materialized:
        raise ValueError(f"{name} must not be empty")
    return materialized


def _id(row: Mapping[str, Any], field: str, index: int, source: str) -> Any:
    if field not in row or row[field] is None:
        raise ValueError(f"{source}[{index}] is missing {field}")
    value = row[field]
    try:
        hash(value)
    except TypeError as exc:
        raise ValueError(f"{source}[{index}].{field} must be hashable") from exc
    return value


def _horizon(row: Mapping[str, Any], index: int, source: str) -> float:
    value = _finite(row.get("horizon_days"), f"{source}[{index}].horizon_days")
    if value <= 0.0:
        raise ValueError(f"{source}[{index}].horizon_days must be positive")
    return value


def _feature_values(row: Mapping[str, Any], index: int, source: str) -> dict[str, float]:
    raw = row.get("features")
    if not isinstance(raw, Mapping):
        raise ValueError(f"{source}[{index}].features must be a dictionary")
    values: dict[str, float] = {}
    for field in _PROXY_FIELDS:
        if field not in raw:
            raise ValueError(f"{source}[{index}].features is missing {field}")
        values[field] = _finite(raw[field], f"{source}[{index}].features.{field}")
        if values[field] < 0.0:
            raise ValueError(f"{source}[{index}].features.{field} must be nonnegative")
    return values


def _task_key(row: Mapping[str, Any], index: int, source: str) -> tuple[Any, Any, float]:
    return (_id(row, "case_id", index, source), _id(row, "object_id", index, source), _horizon(row, index, source))


def _effective_error(row: Mapping[str, Any], index: int, budget_fraction: float) -> float:
    error = _finite(row.get("max_position_error_km"), f"records[{index}].max_position_error_km")
    difference = _finite(row.get("numerical_difference_km"), f"records[{index}].numerical_difference_km")
    if error < 0.0 or difference < 0.0:
        raise ValueError("label errors must be nonnegative")
    return max(error, difference / budget_fraction)


def _check_required(row: Mapping[str, Any], fields: tuple[str, ...], index: int, source: str) -> None:
    missing = [field for field in fields if field not in row]
    if missing:
        raise ValueError(f"{source}[{index}] is missing {', '.join(missing)}")


def fit_rules(
    records: Iterable[Mapping[str, Any]],
    feature_rows: Iterable[Mapping[str, Any]],
    numerical_budget_fraction: float = 0.1,
) -> dict[str, Any]:
    """Fit the horizon and force-proxy rules using training rows only.

    ``records`` contains one labelled row per ``case_id``, object, horizon and
    candidate model.  ``feature_rows`` contains one causal feature row per
    case/object/horizon.  Every four-model task must be complete so the cost
    ordering and strongest-model fallback are well-defined.
    """

    budget = _finite(numerical_budget_fraction, "numerical_budget_fraction")
    if budget <= 0.0:
        raise ValueError("numerical_budget_fraction must be positive")
    labels = _rows(records, "records")
    features = _rows(feature_rows, "feature_rows")

    label_map: dict[tuple[Any, Any, float, str], tuple[float, float]] = {}
    feature_map: dict[tuple[Any, Any, float], dict[str, float]] = {}
    runtimes: dict[tuple[float, str], list[float]] = {}
    effective_by_model_horizon: dict[tuple[str, float], list[float]] = {}

    for index, row in enumerate(labels):
        _check_required(row, _LABEL_FIELDS, index, "records")
        if row["split"] != "train":
            raise ValueError("fit_rules accepts training rows only; records contains non-train input")
        model_id = row["model_id"]
        if model_id not in MODEL_IDS:
            raise ValueError(f"records[{index}].model_id must be one of {MODEL_IDS}")
        task = _task_key(row, index, "records")
        key = (*task, model_id)
        if key in label_map:
            raise ValueError(f"duplicate label row for {key!r}")
        effective = _effective_error(row, index, budget)
        runtime = _finite(row["runtime_median_seconds"], f"records[{index}].runtime_median_seconds")
        if runtime < 0.0:
            raise ValueError("runtime_median_seconds must be nonnegative")
        label_map[key] = (effective, runtime)
        effective_by_model_horizon.setdefault((model_id, task[2]), []).append(effective)
        runtimes.setdefault((task[2], model_id), []).append(runtime)

    for index, row in enumerate(features):
        _check_required(row, _FEATURE_FIELDS, index, "feature_rows")
        if row["split"] != "train":
            raise ValueError("fit_rules accepts training rows only; feature_rows contains non-train input")
        task = _task_key(row, index, "feature_rows")
        if task in feature_map:
            raise ValueError(f"duplicate feature row for {task!r}")
        feature_map[task] = _feature_values(row, index, "feature_rows")

    label_tasks = {(case, obj, horizon) for case, obj, horizon, _model in label_map}
    feature_tasks = set(feature_map)
    if label_tasks != feature_tasks:
        missing_features = sorted(label_tasks - feature_tasks, key=repr)
        missing_labels = sorted(feature_tasks - label_tasks, key=repr)
        raise ValueError(f"label/feature task mismatch: missing_features={missing_features!r}, missing_labels={missing_labels!r}")
    for task in sorted(label_tasks, key=repr):
        absent = [model_id for model_id in MODEL_IDS if (*task, model_id) not in label_map]
        if absent:
            raise ValueError(f"task {task!r} is missing model labels: {absent!r}")

    horizons = sorted({task[2] for task in feature_tasks})
    caps: dict[str, dict[str, float]] = {
        model_id: {
            _horizon_key(horizon): max(effective_by_model_horizon[(model_id, horizon)])
            for horizon in horizons
        }
        for model_id in MODEL_IDS
    }
    median_costs = {
        model_id: {
            _horizon_key(horizon): statistics.median(runtimes[(horizon, model_id)])
            for horizon in horizons
        }
        for model_id in MODEL_IDS
    }

    proxy_by_model = {
        "B2": ("planet_proxy_km", "gr_proxy_km", "small_body_proxy_km"),
        "B3": ("gr_proxy_km", "small_body_proxy_km"),
        "B3+GR": ("small_body_proxy_km",),
        "B3+GR+SB16": (),
    }
    calibration: dict[str, float | None] = {}
    for model_id in MODEL_IDS:
        # The strongest candidate has no missing-force proxy.  Its operational
        # prediction is the empirical horizon cap below, so there is no useful
        # global proxy coefficient to fit for it.
        if model_id == STRONGEST_MODEL_ID:
            calibration[model_id] = None
            continue
        ratios = []
        for task, proxy_values in feature_map.items():
            effective = label_map[(*task, model_id)][0]
            proxy = sum(proxy_values[field] for field in proxy_by_model[model_id])
            ratios.append(effective / max(proxy, 1e-6))
        calibration[model_id] = max(1.0, max(ratios))

    methodology = {
        "effective_error": "max(max_position_error_km, numerical_difference_km / numerical_budget_fraction)",
        "horizon_rule": "training maximum effective error per candidate and horizon",
        "physics_rule": "calibrated sum of causal missing-force proxies",
        "calibration": "per-model maximum training effective_error / max(proxy_km, 1e-6), floored at 1",
        "cost_order": "median training runtime per candidate and horizon",
        "limitations": (
            "Empirical caps and proxy calibrations are conservative training summaries, "
            "not error guarantees. Validation, object, event and final-test evaluation "
            "must be performed before operational use."
        ),
    }
    return {
        "artifact_version": 1,
        "model_ids": list(MODEL_IDS),
        "strongest_model_id": STRONGEST_MODEL_ID,
        "horizons_days": horizons,
        "numerical_budget_fraction": budget,
        "horizon_caps_km": caps,
        "empirical_horizon_caps_km": caps,
        "calibration_factors": calibration,
        "median_runtime_seconds": median_costs,
        "training_median_runtime_seconds": median_costs,
        "physics_proxy_map": {model_id: list(fields) for model_id, fields in proxy_by_model.items()},
        "methodology": methodology,
    }


def _artifact_horizon(artifact: Mapping[str, Any], horizon_days: float) -> float:
    horizon = _finite(horizon_days, "horizon_days")
    if horizon <= 0.0:
        raise ValueError("horizon_days must be positive")
    known = tuple(float(value) for value in artifact.get("horizons_days", ()))
    if horizon not in known:
        raise ValueError(f"horizon_days={horizon:g} is absent from the fitted training horizons")
    return horizon


def _horizon_value(mapping: Mapping[Any, Any], horizon: float, name: str) -> Any:
    """Read a fitted horizon value before or after JSON serialization."""

    key = _horizon_key(horizon)
    if key in mapping:
        return mapping[key]
    # Accept pre-serialization artifacts made by an earlier development
    # version, while always emitting canonical string keys from fit_rules.
    if horizon in mapping:
        return mapping[horizon]
    raise ValueError(f"artifact is missing {name} for horizon_days={horizon:g}")


def _validate_features(features: Mapping[str, Any]) -> dict[str, float]:
    if not isinstance(features, Mapping):
        raise TypeError("features must be a dictionary of forecast-time scalar features")
    forbidden = sorted(_FORBIDDEN_FORECAST_KEYS.intersection(features), key=str)
    if forbidden:
        raise ValueError(f"selection features cannot contain labels, split or identity metadata: {forbidden!r}")
    values = {}
    for field in _PROXY_FIELDS:
        if field not in features:
            raise ValueError(f"selection features is missing {field}")
        values[field] = _finite(features[field], f"features.{field}")
        if values[field] < 0.0:
            raise ValueError(f"features.{field} must be nonnegative")
    return values


def choose_model(
    artifact: Mapping[str, Any],
    method: str,
    horizon_days: float,
    tolerance_km: float,
    features: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Choose the cheapest forecast-time candidate under a fitted rule.

    ``method`` is ``"horizon_rule"`` or ``"physics_rule"``.  The latter uses
    only the three force-proxy scalars in ``features``.  If no candidate is
    predicted eligible, the strongest candidate is returned with
    ``fallback=True`` and ``predicted_eligible=False``.
    """

    if not isinstance(artifact, Mapping):
        raise TypeError("artifact must be a fitted rule dictionary")
    if method not in {"horizon_rule", "physics_rule"}:
        raise ValueError("method must be 'horizon_rule' or 'physics_rule'")
    horizon = _artifact_horizon(artifact, horizon_days)
    tolerance = _finite(tolerance_km, "tolerance_km")
    if tolerance <= 0.0:
        raise ValueError("tolerance_km must be positive")
    model_ids = tuple(artifact.get("model_ids", MODEL_IDS))
    if model_ids != MODEL_IDS:
        raise ValueError("artifact model ladder does not match the supported candidates")
    caps = artifact.get("horizon_caps_km")
    costs = artifact.get("median_runtime_seconds")
    if not isinstance(caps, Mapping) or not isinstance(costs, Mapping):
        raise ValueError("artifact is missing fitted caps or runtime costs")

    if method == "horizon_rule":
        predictions = {
            model_id: float(_horizon_value(caps[model_id], horizon, f"horizon cap for {model_id}"))
            for model_id in MODEL_IDS
        }
    else:
        if features is None:
            raise ValueError("physics_rule requires forecast-time features")
        values = _validate_features(features)
        proxy_map = artifact.get("physics_proxy_map")
        calibration = artifact.get("calibration_factors")
        if not isinstance(proxy_map, Mapping) or not isinstance(calibration, Mapping):
            raise ValueError("artifact is missing physics calibration")
        predictions = {}
        for model_id in MODEL_IDS:
            if model_id == STRONGEST_MODEL_ID:
                predictions[model_id] = float(_horizon_value(caps[model_id], horizon, f"horizon cap for {model_id}"))
            else:
                proxy = sum(values[field] for field in proxy_map[model_id])
                factor = calibration[model_id]
                if factor is None:
                    raise ValueError(f"artifact is missing calibration for {model_id}")
                predictions[model_id] = float(factor) * max(proxy, 1e-6)

    eligible = {model_id: predictions[model_id] <= tolerance for model_id in MODEL_IDS}
    ordered = sorted(
        MODEL_IDS,
        key=lambda model_id: (float(_horizon_value(costs[model_id], horizon, f"runtime cost for {model_id}")), MODEL_IDS.index(model_id)),
    )
    selected = next((model_id for model_id in ordered if eligible[model_id]), STRONGEST_MODEL_ID)
    fallback = not any(eligible.values())
    return {
        "model_id": selected,
        "method": method,
        "horizon_days": horizon,
        "tolerance_km": tolerance,
        "predicted_error_km": predictions[selected],
        "predicted_eligible": not fallback,
        "fallback": fallback,
        "candidate_predictions_km": predictions,
        "candidate_eligible": eligible,
        "candidate_runtime_seconds": {
            model_id: float(_horizon_value(costs[model_id], horizon, f"runtime cost for {model_id}"))
            for model_id in MODEL_IDS
        },
    }


__all__ = ["MODEL_IDS", "STRONGEST_MODEL_ID", "fit_rules", "choose_model"]

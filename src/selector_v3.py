"""Forecast-time features and a train-only empirical selector.

The feature builder calls the frozen development feature routine for the
causal B2 geometry/proxies.  The selector is an empirical diagnostic: its
calibration margins are not accuracy guarantees and no object identifier is
accepted by the prediction path.
"""

from __future__ import annotations

from collections import defaultdict
import math
import statistics
import time
from collections.abc import Mapping, Sequence
from typing import Any

from development_features import build_features
from ng_inputs_v2 import NGInput
from orbit_baselines import State, norm
from selector_tree import fit_tree, predict_tree


FEATURE_NAMES = (
    "log10_horizon_days",
    "log10_initial_radius_au",
    "initial_eccentricity",
    "log10_planet_proxy_km",
    "log10_gr_proxy_km",
    "log10_small_body_proxy_km",
    "log10_min_planet_distance_over_hill",
    "log10_max_scattering_strength",
    "log10_ng_amplitude_au_d2",
)
_LOG_FLOOR = 1.0e-12
_NG_FLOOR = 1.0e-30


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _positive(value: object, name: str) -> float:
    result = _finite(value, name)
    if result <= 0.0:
        raise ValueError(f"{name} must be positive")
    return result


def _log_floor(value: object, name: str, floor: float = _LOG_FLOOR) -> float:
    result = _finite(value, name)
    if result < 0.0:
        raise ValueError(f"{name} must be non-negative")
    return math.log10(max(result, floor))


def _state(value: object) -> State:
    if not isinstance(value, State):
        raise TypeError("initial must be an orbit_baselines.State")
    if len(value.position) != 3 or len(value.velocity) != 3:
        raise ValueError("initial must contain position and velocity vectors")
    if any(not math.isfinite(float(component)) for component in (*value.position, *value.velocity)):
        raise ValueError("initial state must be finite")
    return State(tuple(float(component) for component in value.position),
                 tuple(float(component) for component in value.velocity))


def _eccentricity(initial: State, mu: float) -> float:
    radius = norm(initial.position)
    if radius <= 0.0:
        raise ValueError("initial position must be nonzero")
    speed_squared = math.fsum(component * component for component in initial.velocity)
    rv = math.fsum(a * b for a, b in zip(initial.position, initial.velocity, strict=True))
    factor = speed_squared - mu / radius
    vector = tuple((factor * initial.position[index] - rv * initial.velocity[index]) / mu for index in range(3))
    eccentricity = math.sqrt(math.fsum(component * component for component in vector))
    if not math.isfinite(eccentricity):
        raise ValueError("initial eccentricity is not finite")
    return eccentricity


def _body_map(context: Mapping[str, Any]) -> dict[str, Any]:
    planets = context.get("planets")
    if isinstance(planets, (str, bytes)) or not isinstance(planets, Sequence) or not planets:
        raise ValueError("context.planets must contain at least one perturber")
    result: dict[str, Any] = {}
    for body in planets:
        body_id = getattr(body, "body_id", None)
        if not isinstance(body_id, str) or body_id in result:
            raise ValueError("planet body identifiers must be unique strings")
        result[body_id] = body
    return result


def _geometry_metrics(
    base: Mapping[str, Any], context: Mapping[str, Any], start_jd: float
) -> tuple[float, float, bool, dict[str, dict[str, Any]], list[str]]:
    geometry = base.get("per_body", base.get("geometry"))
    if not isinstance(geometry, Mapping) or not geometry:
        raise ValueError("build_features returned no per-body geometry")
    bodies = _body_map(context)
    au_km = _positive(context.get("au_km"), "context.au_km")
    day_s = _positive(context.get("day_s"), "context.day_s")
    mu_sun = _positive(context.get("mu"), "context.mu")
    earth = bodies.get("399")
    moon = bodies.get("301")
    earth_mu = _positive(getattr(earth, "mu_au3_d2", None), "Earth mu") if earth is not None else None
    warnings: list[str] = []
    rows: dict[str, dict[str, Any]] = {}
    ratios: list[float] = []
    strengths: list[float] = []
    strong = False
    for body_id, raw in geometry.items():
        if body_id not in bodies or not isinstance(raw, Mapping):
            raise ValueError(f"geometry has unknown or malformed body {body_id!r}")
        body = bodies[body_id]
        distance = _positive(raw.get("distance_au"), f"geometry[{body_id}].distance_au")
        relative_speed_km_s = _finite(raw.get("relative_speed_km_s"), f"geometry[{body_id}].relative_speed_km_s")
        if relative_speed_km_s < 0.0:
            raise ValueError(f"geometry[{body_id}] relative speed must be non-negative")
        relative_speed_au_d = relative_speed_km_s * day_s / au_km
        if relative_speed_au_d == 0.0:
            strength = math.inf if getattr(body, "mu_au3_d2", 0.0) > 0.0 else 0.0
        else:
            strength = _positive(getattr(body, "mu_au3_d2", None), f"mu for {body_id}") / (distance * relative_speed_au_d**2)
        if not math.isfinite(strength):
            warnings.append(f"zero relative speed for body {body_id}; scattering strength is unbounded")

        time_days = _finite(raw.get("time_days"), f"geometry[{body_id}].time_days")
        body_state = body.ephemeris.state_at(start_jd + time_days)
        if body_id == "301":
            # The Moon's heliocentric Hill proxy is not meaningful.  Use its
            # simultaneous Earth-Moon distance and the Earth mass hierarchy.
            if earth is None or moon is None or earth_mu is None:
                raise ValueError("Moon geometry requires Earth and Moon perturbers")
            earth_state = earth.ephemeris.state_at(start_jd + time_days)
            earth_moon_distance = norm(tuple(a - b for a, b in zip(body_state.position, earth_state.position, strict=True)))
            hill = earth_moon_distance * (float(body.mu_au3_d2) / (3.0 * earth_mu)) ** (1.0 / 3.0)
            hill_basis = "Earth-Moon distance with muMoon/(3 muEarth)"
        else:
            heliocentric_radius = norm(body_state.position)
            if heliocentric_radius <= 0.0:
                raise ValueError(f"body {body_id} has zero heliocentric radius")
            hill = heliocentric_radius * (float(body.mu_au3_d2) / (3.0 * mu_sun)) ** (1.0 / 3.0)
            hill_basis = "heliocentric radius with muBody/(3 muSun)"
        if not math.isfinite(hill) or hill <= 0.0:
            raise ValueError(f"body {body_id} has invalid Hill radius")
        ratio = distance / hill
        ratios.append(ratio)
        strengths.append(strength)
        if ratio < 0.25 and strength > 0.01:
            strong = True
        rows[body_id] = {
            **dict(raw),
            "hill_radius_au": hill,
            "distance_over_hill": ratio,
            "scattering_strength": strength,
            "hill_basis": hill_basis,
            "relative_speed_au_per_day": relative_speed_au_d,
        }
    if not ratios or not strengths:
        raise ValueError("no valid body geometry")
    if any(not math.isfinite(value) for value in ratios):
        raise ValueError("distance/Hill ratio is not finite")
    max_strength = max(strengths)
    return min(ratios), max_strength, strong, rows, warnings


def _ng_metadata(ng: NGInput | None, start_jd: float) -> tuple[float, dict[str, Any], list[str]]:
    warnings: list[str] = []
    if ng is None:
        status = "not_provided"
    elif not isinstance(ng, NGInput):
        raise TypeError("ng must be an ng_inputs_v2.NGInput or None")
    else:
        status = ng.status(start_jd)
    if ng is not None and status == "available":
        assert ng.parameters is not None
        amplitude = math.fsum(abs(float(getattr(ng.parameters, name))) for name in ("a1_au_d2", "a2_au_d2", "a3_au_d2"))
        metadata = {
            "status": status, "applied": True, "source": ng.source,
            "source_sha256": ng.source_sha256, "available_from_jd_tdb": ng.available_from_jd_tdb,
            "parameters": {name: float(getattr(ng.parameters, name)) for name in ("a1_au_d2", "a2_au_d2", "a3_au_d2")},
            "amplitude_au_d2": amplitude,
        }
        return amplitude, metadata, warnings
    if status == "not_provided":
        warnings.append("non-gravitational parameters not provided; NG feature uses explicit zero")
    else:
        warnings.append(f"non-gravitational parameters unavailable at start ({status}); NG feature uses explicit zero")
    metadata = {
        "status": status, "applied": False, "source": None if ng is None else ng.source,
        "source_sha256": None if ng is None else ng.source_sha256,
        "available_from_jd_tdb": None if ng is None else ng.available_from_jd_tdb,
        "parameters": None, "amplitude_au_d2": 0.0,
    }
    return 0.0, metadata, warnings


def features_v3(
    initial: State,
    start_jd: float,
    horizon_days: float,
    context: dict,
    feature_config: dict,
    ng: NGInput | None = None,
) -> dict[str, Any]:
    """Build fixed causal features and geometry metadata for one forecast."""
    started = time.perf_counter()
    initial_state = _state(initial)
    start = _finite(start_jd, "start_jd")
    horizon = _positive(horizon_days, "horizon_days")
    if not isinstance(context, dict) or not isinstance(feature_config, dict):
        raise TypeError("context and feature_config must be dictionaries")
    base_started = time.perf_counter()
    base = build_features(initial_state, start, horizon, context, feature_config)
    base_runtime = time.perf_counter() - base_started
    if not isinstance(base, Mapping):
        raise ValueError("build_features must return a mapping")
    mu = _positive(context.get("mu"), "context.mu")
    radius = _positive(norm(initial_state.position), "initial radius")
    min_ratio, max_strength, strong, geometry, warnings = _geometry_metrics(base, context, start)
    ng_amplitude, ng_metadata, ng_warnings = _ng_metadata(ng, start)
    warnings.extend(ng_warnings)
    vector = [
        math.log10(horizon),
        math.log10(radius),
        _eccentricity(initial_state, mu),
        _log_floor(base.get("planet_proxy_km"), "planet_proxy_km"),
        _log_floor(base.get("gr_proxy_km"), "gr_proxy_km"),
        _log_floor(base.get("small_body_proxy_km"), "small_body_proxy_km"),
        _log_floor(min_ratio, "min_planet_distance_over_hill"),
        _log_floor(max_strength, "max_scattering_strength"),
        _log_floor(ng_amplitude, "ng_amplitude_au_d2", _NG_FLOOR),
    ]
    if not all(math.isfinite(value) for value in vector):
        raise ValueError("feature vector is not finite")
    return {
        "schema_version": 3,
        "feature_names": list(FEATURE_NAMES),
        "vector": vector,
        "horizon_days": horizon,
        "start_jd": start,
        "initial_radius_au": radius,
        "initial_eccentricity": vector[2],
        "geometry": geometry,
        "strong_encounter": strong,
        "strong_encounter_rule": "any distance_over_hill < 0.25 and scattering_strength > 0.01; heuristic, not a guarantee",
        "min_planet_distance_over_hill": min_ratio,
        "max_scattering_strength": max_strength,
        "ng": ng_metadata,
        "warnings": warnings,
        "base_feature_runtime_seconds": base_runtime,
        "runtime_seconds": time.perf_counter() - started,
        "base_features": dict(base),
        "accuracy_guaranteed": False,
        "conditional_on_initial_state": True,
        "covariance_calibrated": False,
    }


def _model_ids(models: Sequence[Any]) -> list[str]:
    if isinstance(models, (str, bytes)) or not isinstance(models, Sequence) or not models:
        raise ValueError("models must be a non-empty ordered sequence")
    result: list[str] = []
    for index, model in enumerate(models):
        identifier = model.get("model_id") if isinstance(model, Mapping) else model
        if not isinstance(identifier, str) or not identifier:
            raise ValueError(f"models[{index}] must have a non-empty string model_id")
        if identifier in result:
            raise ValueError("models must have unique IDs")
        result.append(identifier)
    return result


def _row_features(row: Mapping[str, Any], name: str) -> list[float]:
    values = row.get("features", row.get("vector"))
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence) or not values:
        raise ValueError(f"{name}.features must be a non-empty vector")
    result = [_finite(value, f"{name}.features[{index}]") for index, value in enumerate(values)]
    return result


def _check_rows(rows: Sequence[Mapping[str, Any]], models: Sequence[str], label: str) -> tuple[list[Mapping[str, Any]], set[str], int]:
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence) or not rows:
        raise ValueError(f"{label} must be a non-empty sequence")
    seen: set[tuple[str, float, str]] = set()
    objects: set[str] = set()
    dimension: int | None = None
    checked: list[Mapping[str, Any]] = []
    accepted_splits = {"calibration", "validation", "holdout"}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"{label}[{index}] must be a mapping")
        object_id = row.get("object_id")
        if not isinstance(object_id, str) or not object_id:
            raise ValueError(f"{label}[{index}].object_id must be a non-empty string")
        split = row.get("split", row.get("original_split"))
        if label == "training" and split is not None and split != "train":
            raise ValueError("training rows must be labelled train")
        if label == "calibration" and split is not None and split not in accepted_splits:
            raise ValueError("calibration rows have an invalid split label")
        horizon = _positive(row.get("horizon_days"), f"{label}[{index}].horizon_days")
        model_id = row.get("model_id")
        if not isinstance(model_id, str) or model_id not in models:
            raise ValueError(f"{label}[{index}] has an unknown model")
        key = (object_id, horizon, model_id)
        if key in seen:
            raise ValueError(f"duplicate {label} object/horizon/model row")
        seen.add(key)
        vector = _row_features(row, f"{label}[{index}]")
        if dimension is None:
            dimension = len(vector)
        elif len(vector) != dimension:
            raise ValueError("all selector feature vectors must have one dimension")
        for metric in ("max_position_error_km", "numerical_difference_km", "runtime_median_seconds"):
            value = _finite(row.get(metric), f"{label}[{index}].{metric}")
            if value < 0.0:
                raise ValueError(f"{label}[{index}].{metric} must be non-negative")
        objects.add(object_id)
        checked.append(row)
    assert dimension is not None
    return checked, objects, dimension


def fit_selector(
    training: list[dict],
    calibration: list[dict],
    models: list,
    numerical_fraction: float = 0.1,
) -> dict[str, Any]:
    """Fit one group-aware CART cap model per candidate from train rows."""
    model_ids = _model_ids(models)
    fraction = _positive(numerical_fraction, "numerical_fraction")
    train_rows, train_objects, dimension = _check_rows(training, model_ids, "training")
    cal_rows, calibration_objects, cal_dimension = _check_rows(calibration, model_ids, "calibration")
    if train_objects & calibration_objects:
        raise ValueError("training and calibration object IDs overlap")
    if cal_dimension != dimension:
        raise ValueError("training and calibration feature dimensions differ")
    trees: dict[str, dict] = {}
    margins: dict[str, float] = {}
    costs: dict[str, dict[str, float]] = {model: {} for model in model_ids}
    for model_id in model_ids:
        rows = [row for row in train_rows if row.get("model_id") == model_id]
        cal = [row for row in cal_rows if row.get("model_id") == model_id]
        if not rows or not cal:
            raise ValueError(f"every model needs train and calibration rows: {model_id}")
        targets = [math.log10(max(
            _finite(row["max_position_error_km"], "position error"),
            _finite(row["numerical_difference_km"], "numerical difference") / fraction,
            _LOG_FLOOR,
        )) for row in rows]
        trees[model_id] = fit_tree(
            [_row_features(row, f"training.{model_id}") for row in rows],
            targets,
            [str(row["object_id"]) for row in rows],
            max_depth=3,
            min_leaf_objects=4,
        )
        residuals = []
        for row in cal:
            actual = math.log10(max(
                _finite(row["max_position_error_km"], "calibration position error"),
                _finite(row["numerical_difference_km"], "calibration numerical difference") / fraction,
                _LOG_FLOOR,
            ))
            residuals.append(actual - predict_tree(trees[model_id], _row_features(row, f"calibration.{model_id}")))
        margins[model_id] = max(0.0, max(residuals))
        grouped_costs: defaultdict[float, list[float]] = defaultdict(list)
        for row in rows:
            grouped_costs[_positive(row["horizon_days"], "training horizon")].append(
                _finite(row["runtime_median_seconds"], "training runtime")
            )
        costs[model_id] = {str(float(horizon)): statistics.median(values) for horizon, values in sorted(grouped_costs.items())}
    horizons = sorted({float(row["horizon_days"]) for row in train_rows})
    if any(str(float(horizon)) not in costs[model_id] for model_id in model_ids for horizon in horizons):
        raise ValueError("every model must have a training cost at every horizon")
    return {
        "schema_version": 3,
        "method": "cart_selector_v3",
        "models": model_ids,
        "fallback_model_id": model_ids[-1],
        "feature_dimension": dimension,
        "feature_names": list(FEATURE_NAMES) if dimension == len(FEATURE_NAMES) else None,
        "numerical_fraction": fraction,
        "trees": trees,
        "calibration_margin_log10": margins,
        "median_cost_seconds": costs,
        "horizons_days": horizons,
        "training_object_ids": sorted(train_objects),
        "calibration_object_ids": sorted(calibration_objects),
        "training_row_count": len(train_rows),
        "calibration_row_count": len(cal_rows),
        "accuracy_guaranteed": False,
        "conditional_on_initial_state": True,
        "covariance_calibrated": False,
        "evaluation_scope": "train-only CART with empirical calibration margins; fresh whole-object holdout required",
    }


def _horizon_key(artifact: Mapping[str, Any], horizon: float) -> str:
    horizons = artifact.get("horizons_days")
    if not isinstance(horizons, Sequence):
        raise ValueError("selector artifact has no horizons")
    matches = [float(value) for value in horizons if _finite(value, "artifact horizon") == horizon]
    if len(matches) != 1:
        raise ValueError("horizon is absent from selector artifact")
    return str(float(horizon))


def choose_v3(
    feature_result: Mapping[str, Any],
    horizon: float,
    tolerance: float,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Choose the cheapest empirically feasible model without using an ID."""
    if not isinstance(feature_result, Mapping):
        raise TypeError("feature_result must be a mapping")
    if artifact.get("schema_version") != 3 or artifact.get("method") != "cart_selector_v3":
        raise ValueError("invalid selector v3 artifact")
    horizon_value = _positive(horizon, "horizon")
    tolerance_value = _positive(tolerance, "tolerance")
    key = _horizon_key(artifact, horizon_value)
    vector = feature_result.get("vector")
    if isinstance(vector, (str, bytes)) or not isinstance(vector, Sequence):
        raise ValueError("feature_result has no vector")
    dimension = artifact.get("feature_dimension")
    if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0 or len(vector) != dimension:
        raise ValueError("feature vector dimension differs from artifact")
    values = [_finite(value, f"feature_result.vector[{index}]") for index, value in enumerate(vector)]
    models = _model_ids(artifact.get("models"))
    trees = artifact.get("trees")
    margins = artifact.get("calibration_margin_log10")
    costs = artifact.get("median_cost_seconds")
    if not isinstance(trees, Mapping) or not isinstance(margins, Mapping) or not isinstance(costs, Mapping):
        raise ValueError("selector artifact lacks trees, margins or costs")
    caps: dict[str, float] = {}
    statuses: dict[str, str] = {}
    for model_id in models:
        if model_id not in trees or model_id not in margins or model_id not in costs:
            raise ValueError(f"selector artifact is incomplete for {model_id}")
        predicted_log = predict_tree(trees[model_id], values) + _finite(margins[model_id], f"margin {model_id}")
        try:
            cap = 10.0 ** predicted_log
        except OverflowError:
            cap = math.inf
        caps[model_id] = cap
        statuses[model_id] = "predicted_feasible" if cap <= tolerance_value else "predicted_infeasible"
    fallback = artifact.get("fallback_model_id", models[-1])
    if fallback not in models:
        raise ValueError("fallback model is not in model catalogue")
    strong = feature_result.get("strong_encounter") is True
    feasible = [model for model in models if caps[model] <= tolerance_value]
    if strong:
        chosen, status, used_fallback = fallback, "strong_encounter_unvalidated", True
    elif feasible:
        for model in feasible:
            value = costs[model].get(key)
            if value is None or not math.isfinite(float(value)) or float(value) < 0.0:
                raise ValueError(f"missing finite train cost for {model} at horizon {horizon_value}")
        chosen = min(feasible, key=lambda model: (costs[model].get(key), models.index(model)))
        status, used_fallback = "predicted_feasible", False
    else:
        chosen, status, used_fallback = fallback, "no_candidate_predicted", True
    chosen_cost = costs[chosen].get(key)
    if chosen_cost is None:
        raise ValueError(f"missing train cost for selected model {chosen}")
    return {
        "schema_version": 3,
        "method": "cart_selector_v3",
        "model_id": chosen,
        "horizon_days": horizon_value,
        "tolerance_km": tolerance_value,
        "predicted_error_caps_km": caps,
        "statuses": statuses,
        "status": status,
        "fallback": used_fallback,
        "strong_encounter": strong,
        "train_cost_seconds": float(chosen_cost),
        "accuracy_guaranteed": False,
        "conditional_on_initial_state": True,
        "covariance_calibrated": False,
    }


__all__ = ["FEATURE_NAMES", "choose_v3", "features_v3", "fit_selector"]

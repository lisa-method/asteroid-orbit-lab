"""Dependency-free metrics and offline model-selection diagnostics.

The trajectory metric functions use the project's canonical state units:
position in AU and velocity in AU/day.  Their returned errors are converted to
kilometres and metres/second respectively.  RTN components are defined from
the reference state at each epoch, so the decomposition remains an analysis
quantity even when a predicted trajectory has drifted substantially.

``offline_oracle`` is deliberately an *offline* lower-bound diagnostic.  It
uses the measured labels to choose a model and therefore must not be described
as an operational selector or as an achieved speedup.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any


Vector = tuple[float, float, float]
State = tuple[Vector, Vector]


def _finite_number(value: Any, name: str, *, nonnegative: bool = False) -> float:
    """Validate and return a real-valued finite scalar."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite real number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if nonnegative and result < 0.0:
        raise ValueError(f"{name} must be non-negative")
    return result


def _vector(value: Any, name: str) -> Vector:
    """Validate a finite three-component vector."""

    if isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must contain exactly three numeric components")
    try:
        components = tuple(value)
    except TypeError as exc:
        raise ValueError(f"{name} must contain exactly three numeric components") from exc
    if len(components) != 3:
        raise ValueError(f"{name} must contain exactly three numeric components")
    return tuple(
        _finite_number(component, f"{name}[{index}]")
        for index, component in enumerate(components)
    )  # type: ignore[return-value]


def _state(value: Any, name: str) -> State:
    """Normalize common trajectory-state representations.

    Accepted forms are ``(position, velocity)``, a flat six-component
    sequence, or a mapping with ``position``/``velocity`` (aliases ``r``/``v``
    are accepted for compact dynamics tables).
    """

    position: Any
    velocity: Any
    if isinstance(value, Mapping):
        if "position" in value and "velocity" in value:
            position, velocity = value["position"], value["velocity"]
        elif "r" in value and "v" in value:
            position, velocity = value["r"], value["v"]
        else:
            raise ValueError(
                f"{name} must contain position/velocity or r/v entries"
            )
    elif hasattr(value, "position") and hasattr(value, "velocity"):
        # This intentionally supports orbit_baselines.State without importing
        # that module (and therefore keeps this metrics module dependency-free).
        position, velocity = value.position, value.velocity
    else:
        if isinstance(value, (str, bytes)):
            raise ValueError(f"{name} must be a state pair or six-component sequence")
        try:
            components = tuple(value)
        except TypeError as exc:
            raise ValueError(
                f"{name} must be a state pair or six-component sequence"
            ) from exc
        if len(components) == 6:
            position, velocity = components[:3], components[3:]
        elif len(components) == 2:
            position, velocity = components
        else:
            raise ValueError(
                f"{name} must be a state pair or six-component sequence"
            )
    return _vector(position, f"{name}.position"), _vector(velocity, f"{name}.velocity")


def _cross(left: Vector, right: Vector) -> Vector:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _dot(left: Vector, right: Vector) -> float:
    return sum(a * b for a, b in zip(left, right))


def _subtract(left: Vector, right: Vector) -> Vector:
    return tuple(a - b for a, b in zip(left, right))  # type: ignore[return-value]


def _norm(vector: Vector) -> float:
    return math.sqrt(_dot(vector, vector))


def _scale(vector: Vector, factor: float) -> Vector:
    return tuple(component * factor for component in vector)  # type: ignore[return-value]


def _rtn_basis(reference_position: Vector, reference_velocity: Vector) -> tuple[Vector, Vector, Vector]:
    radius = _norm(reference_position)
    if radius == 0.0:
        raise ValueError("Reference position is degenerate: RTN radial basis is undefined")
    radial = _scale(reference_position, 1.0 / radius)
    angular_momentum = _cross(reference_position, reference_velocity)
    angular_momentum_norm = _norm(angular_momentum)
    if angular_momentum_norm == 0.0:
        raise ValueError(
            "Reference position and velocity are degenerate: RTN normal basis is undefined"
        )
    normal = _scale(angular_momentum, 1.0 / angular_momentum_norm)
    transverse = _cross(normal, radial)
    return radial, transverse, normal


def _project(vector: Vector, basis: tuple[Vector, Vector, Vector]) -> tuple[float, float, float]:
    return tuple(_dot(vector, axis) for axis in basis)  # type: ignore[return-value]


def trajectory_error_rows(
    times_days: Iterable[float],
    predictions: Iterable[Any],
    references: Iterable[Any],
    au_km: float,
    day_s: float,
) -> list[dict[str, Any]]:
    """Return per-epoch Cartesian norms and reference-frame RTN errors.

    ``predictions`` and ``references`` are paired sampled states.  Position
    residuals are converted from AU to km and velocity residuals from AU/day
    to m/s.  The velocity RTN values are the Cartesian ``dv`` projected onto
    the reference RTN basis; they are not derivatives of rotating RTN
    coordinates.
    """

    au_km_value = _finite_number(au_km, "au_km")
    day_s_value = _finite_number(day_s, "day_s")
    if au_km_value <= 0.0:
        raise ValueError("au_km must be positive")
    if day_s_value <= 0.0:
        raise ValueError("day_s must be positive")

    try:
        times = tuple(_finite_number(time, "time_days") for time in times_days)
    except TypeError as exc:
        raise ValueError("times_days must be a finite iterable") from exc
    if not times:
        raise ValueError("At least one trajectory epoch is required")
    if any(right <= left for left, right in zip(times, times[1:])):
        raise ValueError("times_days must be strictly increasing")

    try:
        prediction_states = tuple(_state(value, f"predictions[{index}]") for index, value in enumerate(predictions))
        reference_states = tuple(_state(value, f"references[{index}]") for index, value in enumerate(references))
    except TypeError as exc:
        raise ValueError("predictions and references must be finite iterables of states") from exc
    if len(prediction_states) != len(times) or len(reference_states) != len(times):
        raise ValueError(
            "times_days, predictions, and references must have matching non-empty lengths"
        )

    velocity_scale = au_km_value * 1000.0 / day_s_value
    rows: list[dict[str, Any]] = []
    for time, predicted, reference in zip(times, prediction_states, reference_states):
        reference_position, reference_velocity = reference
        basis = _rtn_basis(reference_position, reference_velocity)
        position_error_au = _subtract(predicted[0], reference_position)
        velocity_error_au_day = _subtract(predicted[1], reference_velocity)
        position_error_km = _scale(position_error_au, au_km_value)
        velocity_error_m_s = _scale(velocity_error_au_day, velocity_scale)
        rows.append(
            {
                "time_days": time,
                "position_error_km": _norm(position_error_km),
                "velocity_error_m_s": _norm(velocity_error_m_s),
                "rtn_position_error_km": _project(position_error_km, basis),
                "rtn_velocity_error_m_s": _project(velocity_error_m_s, basis),
            }
        )
    return rows


def _metric_row(row: Mapping[str, Any], index: int) -> tuple[float, float, tuple[float, float, float]]:
    if not isinstance(row, Mapping):
        raise ValueError(f"rows[{index}] must be a mapping")
    time = _finite_number(row.get("time_days"), f"rows[{index}].time_days")
    position = _finite_number(
        row.get("position_error_km"), f"rows[{index}].position_error_km", nonnegative=True
    )
    velocity = _finite_number(
        row.get("velocity_error_m_s"), f"rows[{index}].velocity_error_m_s", nonnegative=True
    )
    rtn_value = row.get("rtn_position_error_km")
    rtn_position = _vector(rtn_value, f"rows[{index}].rtn_position_error_km")
    for component_index, component in enumerate(rtn_position):
        _finite_number(component, f"rows[{index}].rtn_position_error_km[{component_index}]")
    _vector(row.get("rtn_velocity_error_m_s"), f"rows[{index}].rtn_velocity_error_m_s")
    return time, position, rtn_position


def summarize_error_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize sampled trajectory errors with endpoint and worst-case values."""

    try:
        materialized = tuple(rows)
    except TypeError as exc:
        raise ValueError("rows must be a non-empty iterable of mappings") from exc
    if not materialized:
        raise ValueError("At least one error row is required")

    parsed = tuple(_metric_row(row, index) for index, row in enumerate(materialized))
    if any(right[0] <= left[0] for left, right in zip(parsed, parsed[1:])):
        raise ValueError("rows must be strictly increasing in time_days")
    position_errors = tuple(item[1] for item in parsed)
    velocity_errors = tuple(
        _finite_number(materialized[index].get("velocity_error_m_s"), f"rows[{index}].velocity_error_m_s", nonnegative=True)
        for index in range(len(materialized))
    )
    max_position = max(position_errors)
    max_position_index = position_errors.index(max_position)
    max_velocity = max(velocity_errors)
    endpoint_rtn = parsed[-1][2]
    max_abs_rtn = tuple(
        max(abs(item[2][component_index]) for item in parsed)
        for component_index in range(3)
    )
    return {
        "sample_count": len(parsed),
        "endpoint_position_error_km": position_errors[-1],
        "max_position_error_km": max_position,
        "max_position_error_time_days": parsed[max_position_index][0],
        "endpoint_velocity_error_m_s": velocity_errors[-1],
        "max_velocity_error_m_s": max_velocity,
        "rtn_endpoint_position_error_km": endpoint_rtn,
        "rtn_max_abs_position_error_km": max_abs_rtn,
    }


def _record_items(records: Any) -> list[tuple[Any, Any, Mapping[str, Any]]]:
    """Normalize supported flat, tuple-keyed, or nested oracle records."""

    items: list[tuple[Any, Any, Mapping[str, Any]]] = []
    if isinstance(records, Mapping):
        # A single record is accepted for useful validation errors, although
        # the oracle still rejects it because a model set cannot be inferred.
        if "case_id" in records or "model_id" in records:
            iterable: Iterable[Any] = (records,)
        else:
            for case_id, case_records in records.items():
                if isinstance(case_records, Mapping):
                    # Nested shape: case_id -> model_id -> metric mapping.
                    for model_id, metric_record in case_records.items():
                        if not isinstance(metric_record, Mapping):
                            raise ValueError("Each oracle record must be a mapping of metrics")
                        items.append((case_id, model_id, metric_record))
                else:
                    raise ValueError(
                        "Mapping records must be keyed by (case_id, model_id) or nested case/model mappings"
                    )
            return items
    else:
        try:
            iterable = iter(records)
        except TypeError as exc:
            raise ValueError("records must be an iterable or mapping of oracle records") from exc

    for index, record in enumerate(iterable):
        if not isinstance(record, Mapping):
            raise ValueError(f"records[{index}] must be a mapping")
        if "case_id" not in record or "model_id" not in record:
            # Tuple-keyed entries are handled below only through a mapping;
            # flat entries must carry explicit IDs.
            raise ValueError(f"records[{index}] must contain case_id and model_id")
        items.append((record["case_id"], record["model_id"], record))
    if isinstance(records, Mapping):
        return items

    # ``records`` may also be a mapping whose keys are exactly (case, model).
    # This branch is reached only when the mapping was not identified as a
    # nested map above; retain it for compatibility with dict-based runners.
    return items


def _normalize_oracle_records(records: Any) -> list[tuple[str, str, Mapping[str, Any]]]:
    """Normalize all documented record forms, including tuple-keyed mappings."""

    if isinstance(records, Mapping) and "case_id" not in records and "model_id" not in records:
        keys = tuple(records.keys())
        if keys and all(isinstance(key, tuple) and len(key) == 2 for key in keys):
            items = []
            for key, value in records.items():
                if not isinstance(value, Mapping):
                    raise ValueError("Each tuple-keyed oracle record must be a metrics mapping")
                items.append((key[0], key[1], value))
        else:
            items = _record_items(records)
    else:
        items = _record_items(records)
    normalized: list[tuple[str, str, Mapping[str, Any]]] = []
    for case_id, model_id, record in items:
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("case_id must be a non-empty string")
        if not isinstance(model_id, str) or not model_id:
            raise ValueError("model_id must be a non-empty string")
        normalized.append((case_id, model_id, record))
    return normalized


def offline_oracle(
    records: Any,
    position_tolerance_km: float,
    numerical_budget_fraction: float = 0.1,
    cost_key: str = "runtime_median_seconds",
) -> dict[str, Any]:
    """Choose the cheapest feasible candidate per case using offline labels.

    Feasibility requires both ``max_position_error_km <= position_tolerance_km``
    and ``numerical_difference_km <= numerical_budget_fraction *
    position_tolerance_km``.  Every case must contain the same model set.  The
    returned ``selections`` mapping contains ``None`` for an infeasible case;
    no fallback is silently inserted.
    """

    tolerance = _finite_number(position_tolerance_km, "position_tolerance_km", nonnegative=True)
    budget_fraction = _finite_number(
        numerical_budget_fraction, "numerical_budget_fraction", nonnegative=True
    )
    if budget_fraction > 1.0:
        raise ValueError("numerical_budget_fraction must be at most 1")
    if not isinstance(cost_key, str) or not cost_key:
        raise ValueError("cost_key must be a non-empty string")

    normalized = _normalize_oracle_records(records)
    if not normalized:
        raise ValueError("At least one oracle record is required")
    seen: set[tuple[str, str]] = set()
    data: dict[str, dict[str, dict[str, float | bool]]] = {}
    for case_id, model_id, record in normalized:
        pair = (case_id, model_id)
        if pair in seen:
            raise ValueError(f"Duplicate oracle record for case_id={case_id!r}, model_id={model_id!r}")
        seen.add(pair)
        max_error = _finite_number(
            record.get("max_position_error_km"),
            f"record[{case_id!r},{model_id!r}].max_position_error_km",
            nonnegative=True,
        )
        numerical_difference = _finite_number(
            record.get("numerical_difference_km"),
            f"record[{case_id!r},{model_id!r}].numerical_difference_km",
            nonnegative=True,
        )
        cost = _finite_number(
            record.get(cost_key),
            f"record[{case_id!r},{model_id!r}].{cost_key}",
            nonnegative=True,
        )
        feasible = max_error <= tolerance and numerical_difference <= budget_fraction * tolerance
        data.setdefault(case_id, {})[model_id] = {
            "max_error": max_error,
            "numerical_difference": numerical_difference,
            "cost": cost,
            "feasible": feasible,
        }

    model_sets = {frozenset(models) for models in (case_data.keys() for case_data in data.values())}
    if len(model_sets) != 1:
        raise ValueError("Every case must contain the same model set")
    model_ids = tuple(sorted(next(iter(model_sets))))
    case_ids = tuple(sorted(data))

    selections: dict[str, str | None] = {}
    selected_records: dict[str, dict[str, Any] | None] = {}
    selected_count = 0
    oracle_total_runtime = 0.0
    for case_id in case_ids:
        feasible_models = [
            model_id for model_id in model_ids if bool(data[case_id][model_id]["feasible"])
        ]
        if not feasible_models:
            selections[case_id] = None
            selected_records[case_id] = None
            continue
        model_id = min(feasible_models, key=lambda candidate: (float(data[case_id][candidate]["cost"]), candidate))
        selected_count += 1
        selections[case_id] = model_id
        oracle_total_runtime += float(data[case_id][model_id]["cost"])
        selected_records[case_id] = {
            "model_id": model_id,
            "cost": float(data[case_id][model_id]["cost"]),
            "max_position_error_km": float(data[case_id][model_id]["max_error"]),
            "numerical_difference_km": float(data[case_id][model_id]["numerical_difference"]),
        }

    feasible_case_count = sum(
        any(bool(data[case_id][model_id]["feasible"]) for model_id in model_ids)
        for case_id in case_ids
    )
    infeasible_case_count = len(case_ids) - feasible_case_count
    fixed_model_summaries: dict[str, dict[str, Any]] = {}
    selection_counts_by_model = {
        model_id: sum(1 for selected in selections.values() if selected == model_id)
        for model_id in model_ids
    }
    for model_id in model_ids:
        model_rows = [data[case_id][model_id] for case_id in case_ids]
        model_feasible = [row for row in model_rows if bool(row["feasible"])]
        total_cost = sum(float(row["cost"]) for row in model_rows)
        feasible_cost = sum(float(row["cost"]) for row in model_feasible)
        model_feasible_cases = tuple(
            case_id
            for case_id in case_ids
            if bool(data[case_id][model_id]["feasible"])
        )
        oracle_cost_on_model_feasible_cases = sum(
            float(selected_records[case_id]["cost"])
            for case_id in model_feasible_cases
            if selected_records[case_id] is not None
        )
        oracle_to_fixed_cost_ratio = (
            oracle_cost_on_model_feasible_cases / feasible_cost
            if feasible_cost > 0.0
            else None
        )
        fixed_model_summaries[model_id] = {
            "case_count": len(model_rows),
            "feasible_case_count": len(model_feasible),
            "infeasible_case_count": len(model_rows) - len(model_feasible),
            "failure_count": len(model_rows) - len(model_feasible),
            "total_cost_seconds": total_cost,
            "total_cost_on_feasible_cases_seconds": feasible_cost,
            "oracle_cost_on_model_feasible_cases_seconds": oracle_cost_on_model_feasible_cases,
            "oracle_to_fixed_cost_ratio": oracle_to_fixed_cost_ratio,
        }

    paired_cost_totals: list[dict[str, Any]] = []
    for index, model_a in enumerate(model_ids):
        for model_b in model_ids[index + 1 :]:
            common_cases = tuple(
                case_id
                for case_id in case_ids
                if bool(data[case_id][model_a]["feasible"])
                and bool(data[case_id][model_b]["feasible"])
            )
            cost_a = sum(float(data[case_id][model_a]["cost"]) for case_id in common_cases)
            cost_b = sum(float(data[case_id][model_b]["cost"]) for case_id in common_cases)
            paired_cost_totals.append(
                {
                    "model_a": model_a,
                    "model_b": model_b,
                    "common_feasible_case_count": len(common_cases),
                    "model_a_cost_total_seconds": cost_a,
                    "model_b_cost_total_seconds": cost_b,
                }
            )

    return {
        "selections": selections,
        "selected_count": selected_count,
        "selected_case_count": selected_count,
        "selection_counts_by_model": selection_counts_by_model,
        "case_count": len(case_ids),
        "feasible_case_count": feasible_case_count,
        "infeasible_case_count": infeasible_case_count,
        "oracle_total_runtime_seconds": oracle_total_runtime,
        "oracle_total_cost_seconds": oracle_total_runtime,
        "selected_records": selected_records,
        "fixed_model_summaries": fixed_model_summaries,
        "paired_cost_totals": paired_cost_totals,
        "position_tolerance_km": tolerance,
        "numerical_budget_fraction": budget_fraction,
        "cost_key": cost_key,
        "operational_speedup_claim": False,
    }

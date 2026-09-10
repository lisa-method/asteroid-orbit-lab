"""Analyze the frozen Apophis joint orbit/non-gravitational probes.

The runner owns propagation and persistence.  This module is deliberately a
pure, in-memory analysis layer: it takes its 34 validated records, forms
offsets before unit conversion, and returns ordinary JSON-compatible values.
Position, velocity, and A1/A2 units are kept explicit in every covariance
summary; no mixed-unit eigenanalysis is performed.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from covariance_tools import jacobi_eigh_3x3


_STATE_SIZE = 6
_AUGMENTED_SIZE = 8
_COLUMNS = tuple(range(8))
_SIGNS = (-1, 1)
_SQRT8 = math.sqrt(8.0)


def _number(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _state(value: Sequence[object], name: str) -> tuple[float, ...]:
    if isinstance(value, (str, bytes)) or len(value) != _STATE_SIZE:
        raise ValueError(f"{name} must contain six components")
    return tuple(_number(item, f"{name}[{i}]") for i, item in enumerate(value))


def _ng(value: Mapping[str, object], name: str) -> tuple[float, float]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return (_number(value["a1_au_d2"], f"{name}.a1_au_d2"),
            _number(value["a2_au_d2"], f"{name}.a2_au_d2"))


def _norm(values: Sequence[float]) -> float:
    return math.sqrt(math.fsum(value * value for value in values))


def _matrix(rows: int, columns: int, values: Sequence[Sequence[float]] | None = None) -> list[list[float]]:
    if values is None:
        return [[0.0] * columns for _ in range(rows)]
    if len(values) != rows or any(len(row) != columns for row in values):
        raise ValueError("matrix shape mismatch")
    return [[_number(value, "matrix component") for value in row] for row in values]


def _symmetrize(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    size = len(matrix)
    return [[(matrix[i][j] + matrix[j][i]) / 2.0 for j in range(size)] for i in range(size)]


def _covariance(offsets: Sequence[Sequence[float]], weights: Sequence[float]) -> tuple[list[float], list[list[float]]]:
    if not offsets or len(offsets) != len(weights):
        raise ValueError("offsets and weights must be non-empty and equally sized")
    dimension = len(offsets[0])
    if dimension == 0 or any(len(row) != dimension for row in offsets):
        raise ValueError("offsets must be rectangular")
    weight_sum = math.fsum(weights)
    if weight_sum <= 0.0 or not math.isfinite(weight_sum):
        raise ValueError("weights must have a positive finite sum")
    normalized = [weight / weight_sum for weight in weights]
    mean = [math.fsum(weight * row[i] for row, weight in zip(offsets, normalized, strict=True))
            for i in range(dimension)]
    covariance = [[0.0] * dimension for _ in range(dimension)]
    for i in range(dimension):
        for j in range(i + 1):
            value = math.fsum(weight * (row[i] - mean[i]) * (row[j] - mean[j])
                              for row, weight in zip(offsets, normalized, strict=True))
            covariance[i][j] = value
            covariance[j][i] = value
    return mean, covariance


def _linear_covariance(basis: Sequence[Sequence[float]]) -> list[list[float]]:
    rows = len(basis)
    columns = len(basis[0]) if basis else 0
    if not rows or not columns or any(len(row) != columns for row in basis):
        raise ValueError("basis must be non-empty and rectangular")
    covariance = [[0.0] * rows for _ in range(rows)]
    for i in range(rows):
        for j in range(i + 1):
            value = math.fsum(basis[i][column] * basis[j][column] for column in range(columns))
            covariance[i][j] = value
            covariance[j][i] = value
    return covariance


def _frobenius(matrix: Sequence[Sequence[float]]) -> float:
    return math.sqrt(math.fsum(value * value for row in matrix for value in row))


def _block(matrix: Sequence[Sequence[float]], start: int, size: int = 3) -> list[list[float]]:
    return [[matrix[start + i][start + j] for j in range(size)] for i in range(size)]


def _row_block(matrix: Sequence[Sequence[float]], start: int, size: int = 3) -> list[list[float]]:
    """Select state rows while retaining every sigma-column."""
    return [list(matrix[start + i]) for i in range(size)]


def _relative_frobenius(left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]) -> float | None:
    numerator = _frobenius([[a - b for a, b in zip(row_a, row_b, strict=True)]
                            for row_a, row_b in zip(left, right, strict=True)])
    denominator = _frobenius(right)
    if denominator == 0.0:
        return 0.0 if numerator == 0.0 else None
    return numerator / denominator


def _scale_matrix(matrix: Sequence[Sequence[float]], factors: Sequence[float]) -> list[list[float]]:
    return [[matrix[i][j] * factors[i] * factors[j] for j in range(len(factors))]
            for i in range(len(factors))]


def _si_state_covariance(matrix: Sequence[Sequence[float]], au_km: float, day_seconds: float) -> list[list[float]]:
    position = au_km * 1000.0
    velocity = position / day_seconds
    return _scale_matrix(matrix, (position, position, position, velocity, velocity, velocity))


def _principal_position(matrix: Sequence[Sequence[float]], au_km: float) -> dict[str, Any]:
    result = jacobi_eigh_3x3(matrix)
    values = list(result.eigenvalues)
    negative = [value for value in values if value < 0.0]
    position_factor = au_km * 1000.0
    output: dict[str, Any] = {
        "eigenvalues_au2": values,
        "eigenvectors_columns": [list(matrix_column) for matrix_column in zip(*result.eigenvectors)],
        "eigen_residual_relative": result.residual,
        "jacobi_sweeps": result.sweeps,
        "unresolved_negative_eigenvalues": bool(negative),
        "negative_eigenvalue_count": len(negative),
        "sigma_axes_m": None,
        "sigma_sqrt_trace_m": None,
        "sigma_axis_rms_m": None,
    }
    if not negative:
        output["sigma_axes_m"] = [math.sqrt(value * position_factor * position_factor) for value in values]
        output["sigma_sqrt_trace_m"] = math.sqrt(math.fsum(value for value in values)) * position_factor
        output["sigma_axis_rms_m"] = math.sqrt(math.fsum(value for value in values) / 3.0) * position_factor
    return output


def _record_index(records: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Mapping[str, Any]], list[float]]:
    if len(records) != 34:
        raise ValueError("covariance analysis requires exactly 34 records")
    by_id: dict[str, Mapping[str, Any]] = {}
    times: list[float] | None = None
    for row in records:
        if not isinstance(row, Mapping) or not isinstance(row.get("id"), str) or row["id"] in by_id:
            raise ValueError("records require unique string ids")
        samples = row.get("samples")
        if not isinstance(samples, list) or not samples:
            raise ValueError(f"record {row['id']} has no samples")
        row_times: list[float] = []
        for index, sample in enumerate(samples):
            if not isinstance(sample, Mapping):
                raise ValueError("sample must be an object")
            row_times.append(_number(sample["day"], f"{row['id']}.samples[{index}].day"))
            _state(sample["state"], f"{row['id']}.samples[{index}].state")
        if any(right <= left for left, right in zip(row_times, row_times[1:])):
            raise ValueError(f"record {row['id']} sample days must increase")
        if times is None:
            times = row_times
        elif row_times != times:
            raise ValueError("all covariance records must use one shared time grid")
        _ng(row["ng"], f"{row['id']}.ng")
        by_id[row["id"]] = row
    assert times is not None
    return by_id, times


def _validate_probe_matrix(by_id: Mapping[str, Mapping[str, Any]], config: Mapping[str, Any]) -> None:
    nominal_ids = {"nominal_extreme", "nominal_ultra"}
    if not nominal_ids.issubset(by_id):
        raise ValueError("nominal_extreme and nominal_ultra records are required")
    scales = tuple(_number(value, "sigma scale") for value in config.get("sigma_scales", (0.25, 1.0)))
    if scales != (0.25, 1.0):
        raise ValueError("the covariance contract requires sigma scales 0.25 and 1.0")
    expected: set[str] = set(nominal_ids)
    for scale in scales:
        for column in _COLUMNS:
            for sign in _SIGNS:
                identifier = f"probe_s{scale:g}_j{column}_{'plus' if sign > 0 else 'minus'}".replace(".", "p")
                expected.add(identifier)
                row = by_id.get(identifier)
                if row is None or row.get("kind") != "probe":
                    raise ValueError(f"missing probe record {identifier}")
                if row.get("scale") != scale or row.get("column") != column or row.get("sign") != sign:
                    raise ValueError(f"probe identity fields disagree for {identifier}")
    if set(by_id) != expected:
        raise ValueError("record ids do not match the fixed nominal/probe matrix")
    for identifier in nominal_ids:
        if by_id[identifier].get("kind") != "nominal":
            raise ValueError(f"{identifier} must be nominal")


def _probe_offsets(row: Mapping[str, Any], nominal: Mapping[str, Any], index: int) -> tuple[float, ...]:
    probe_state = _state(row["samples"][index]["state"], f"{row['id']}.state")
    nominal_state = _state(nominal["samples"][index]["state"], "nominal state")
    probe_ng = _ng(row["ng"], f"{row['id']}.ng")
    nominal_ng = _ng(nominal["ng"], "nominal.ng")
    # Subtract in native units first; this avoids converting AU-scale states to
    # metres before forming the tiny perturbation signal.
    return tuple(probe_state[i] - nominal_state[i] for i in range(6)) + tuple(
        probe_ng[i] - nominal_ng[i] for i in range(2))


def _basis_for_scale(by_id: Mapping[str, Mapping[str, Any]], nominal: Mapping[str, Any], index: int,
                     scale: float) -> tuple[list[list[float]], list[list[float]], list[list[float]]]:
    state_basis = [[0.0] * 8 for _ in range(6)]
    augmented_basis = [[0.0] * 8 for _ in range(8)]
    offsets: list[list[float]] = []
    for column in _COLUMNS:
        prefix = f"probe_s{scale:g}_j{column}_".replace(".", "p")
        plus = _probe_offsets(by_id[prefix + "plus"], nominal, index)
        minus = _probe_offsets(by_id[prefix + "minus"], nominal, index)
        denominator = 2.0 * _SQRT8 * scale
        difference = [math.fsum((plus[i], -minus[i])) / denominator for i in range(8)]
        for i in range(6):
            state_basis[i][column] = difference[i]
        for i in range(8):
            augmented_basis[i][column] = difference[i]
        offsets.extend((list(plus), list(minus)))
    return state_basis, augmented_basis, offsets


def _covariance_summary(matrix: Sequence[Sequence[float]], au_km: float, day_seconds: float) -> dict[str, Any]:
    si = _si_state_covariance(matrix, au_km, day_seconds)
    return {
        "native": [list(row) for row in matrix],
        "si": [list(row) for row in si],
        "units": ["AU", "AU", "AU", "AU/day", "AU/day", "AU/day"],
        "si_units": ["m", "m", "m", "m/s", "m/s", "m/s"],
        "position_block_native_units": "AU^2",
        "velocity_block_native_units": "(AU/day)^2",
        "cross_position_velocity_native_units": "AU^2/day",
    }


def _augmented_covariance_summary(matrix: Sequence[Sequence[float]]) -> dict[str, Any]:
    return {
        "native": [list(row) for row in matrix],
        "units": ["AU", "AU", "AU", "AU/day", "AU/day", "AU/day", "AU/day^2", "AU/day^2"],
        "matrix_coordinate_units": "row_i*row_j units from units list",
    }


def _diagnostic(
    by_id: Mapping[str, Mapping[str, Any]], nominal: Mapping[str, Any], index: int,
    day: float, tau: float, au_km: float, day_seconds: float,
    covariance_budget: float, mean_shift_budget: float, dispersion_thresholds_km: Sequence[float],
) -> dict[str, Any]:
    small_state, small_augmented, _ = _basis_for_scale(by_id, nominal, index, 0.25)
    full_state, full_augmented, full_offsets = _basis_for_scale(by_id, nominal, index, 1.0)
    small_offsets = []
    for column in _COLUMNS:
        small_offsets.extend((list(_probe_offsets(by_id[f"probe_s0p25_j{column}_plus"], nominal, index)),
                              list(_probe_offsets(by_id[f"probe_s0p25_j{column}_minus"], nominal, index))))
    weights = [1.0 / 16.0] * 16
    full_mean, full_cov_augmented = _covariance(full_offsets, weights)
    small_mean, small_cov_augmented = _covariance(small_offsets, weights)
    normalized_small_cov_augmented = [[value / (0.25 * 0.25) for value in row] for row in small_cov_augmented]
    linear_cov_state = _linear_covariance(small_state)
    linear_cov_augmented = _linear_covariance(small_augmented)
    normalized_small_cov_state = [row[:6] for row in normalized_small_cov_augmented[:6]]
    full_cov_state = [row[:6] for row in full_cov_augmented[:6]]
    diagnostic: dict[str, Any] = {
        "forecast_day": day, "tau_since_covariance_epoch": tau,
        "state_basis_small": [list(row) for row in small_state],
        "state_basis_full": [list(row) for row in full_state],
        "augmented_basis_small": [list(row) for row in small_augmented],
        "augmented_basis_full": [list(row) for row in full_augmented],
        "basis_units": ["AU", "AU", "AU", "AU/day", "AU/day", "AU/day"],
        "augmented_basis_units": ["AU", "AU", "AU", "AU/day", "AU/day", "AU/day", "AU/day^2", "AU/day^2"],
        "linear_covariance": _covariance_summary(linear_cov_state, au_km, day_seconds),
        "full_scale_cubature_covariance": _covariance_summary(full_cov_state, au_km, day_seconds),
        "normalized_small_scale_cubature_covariance": _covariance_summary(normalized_small_cov_state, au_km, day_seconds),
        "linear_augmented_covariance": _augmented_covariance_summary(linear_cov_augmented),
        "full_augmented_cubature_covariance": _augmented_covariance_summary(full_cov_augmented),
        "normalized_small_augmented_cubature_covariance": _augmented_covariance_summary(normalized_small_cov_augmented),
        "relative_frobenius": {
            "full_vs_linear_position": _relative_frobenius(_block(full_cov_state, 0), _block(linear_cov_state, 0)),
            "full_vs_linear_velocity": _relative_frobenius(_block(full_cov_state, 3), _block(linear_cov_state, 3)),
            "normalized_small_vs_linear_position": _relative_frobenius(_block(normalized_small_cov_state, 0), _block(linear_cov_state, 0)),
            "normalized_small_vs_linear_velocity": _relative_frobenius(_block(normalized_small_cov_state, 3), _block(linear_cov_state, 3)),
        },
        "B_scale_relative_difference": {
            # Only like-unit blocks are compared.  A six/eight dimensional
            # aggregate would mix AU, AU/day, and AU/day^2 components.
            "position": _relative_frobenius(_row_block(full_state, 0), _row_block(small_state, 0)),
            "velocity": _relative_frobenius(_row_block(full_state, 3), _row_block(small_state, 3)),
        },
        "covariance_relative_budget": covariance_budget,
    }
    # Short conventional names make the numerical contract easy to consume;
    # the descriptive covariance names above remain the source of truth.
    diagnostic.update({
        "B_small": diagnostic["state_basis_small"],
        "B_full": diagnostic["state_basis_full"],
        "C_linear": diagnostic["linear_covariance"],
        "C_full": diagnostic["full_scale_cubature_covariance"],
        "C_small_normalized": diagnostic["normalized_small_scale_cubature_covariance"],
    })
    diagnostic["weighted_mean_offset"] = {
        "state_native": list(full_mean[:6]), "state_units": ["AU", "AU", "AU", "AU/day", "AU/day", "AU/day"],
        "ng_native": list(full_mean[6:]), "ng_units": ["AU/day^2", "AU/day^2"],
        "state_si": list(_state_si(full_mean[:6], au_km, day_seconds)),
    }
    for name, covariance in (("linear", linear_cov_state), ("full_scale", full_cov_state),
                             ("normalized_small_scale", normalized_small_cov_state)):
        diagnostic.setdefault("principal_position", {})[name] = _principal_position(_block(covariance, 0), au_km)
    major_sigma = diagnostic["principal_position"]["linear"]["sigma_axes_m"]
    major_sigma = major_sigma[0] if major_sigma else None
    shift = _norm(diagnostic["weighted_mean_offset"]["state_si"][:3])
    diagnostic["mean_shift"] = {
        "position_norm_m": shift,
        "relative_to_linear_position_sigma": (shift / major_sigma if major_sigma not in (None, 0.0) else None),
        "budget": mean_shift_budget,
        "passes": (shift / major_sigma <= mean_shift_budget if major_sigma not in (None, 0.0) else None),
        "interpretation": "nonlinear/numerical diagnostic; not a Gaussian-tail or accuracy bound",
    }
    relative_values = diagnostic["relative_frobenius"]
    diagnostic["covariance_relative_passes"] = all(
        value is not None and value <= covariance_budget for value in relative_values.values())
    diagnostic["formal_dispersion_flags"] = {
        flavor: {
            f"above_{threshold:g}_km": (
                result["sigma_axes_m"] is not None and result["sigma_axes_m"][0] > threshold * 1000.0
            ) for threshold in dispersion_thresholds_km
        } for flavor, result in diagnostic["principal_position"].items()
    }
    diagnostic["sigma_units"] = "position sigma axes and sqrt(trace) in m; velocity kept separate in m/s"
    return diagnostic


def _state_si(state: Sequence[float], au_km: float, day_seconds: float) -> tuple[float, ...]:
    factors = (au_km * 1000.0,) * 3 + (au_km * 1000.0 / day_seconds,) * 3
    return tuple(value * factor for value, factor in zip(state, factors, strict=True))


def _nominal_comparison(extreme: Mapping[str, Any], ultra: Mapping[str, Any], au_km: float,
                        day_seconds: float, criterion_m: float = 1.0) -> dict[str, Any]:
    extreme_samples, ultra_samples = extreme["samples"], ultra["samples"]
    if len(extreme_samples) != len(ultra_samples):
        raise ValueError("nominal DP traces have different lengths")
    position_differences: list[float] = []
    velocity_differences: list[float] = []
    for left, right in zip(extreme_samples, ultra_samples, strict=True):
        if _number(left["day"], "nominal day") != _number(right["day"], "nominal day"):
            raise ValueError("nominal DP traces have different time grids")
        delta = tuple(a - b for a, b in zip(_state(left["state"], "extreme state"),
                                             _state(right["state"], "ultra state"), strict=True))
        si = _state_si(delta, au_km, day_seconds)
        position_differences.append(_norm(si[:3]))
        velocity_differences.append(_norm(si[3:]))
    return {
        "samples": len(position_differences),
        "max_position_difference_m": max(position_differences),
        "final_position_difference_m": position_differences[-1],
        "max_velocity_difference_m_s": max(velocity_differences),
        "final_velocity_difference_m_s": velocity_differences[-1],
        "position_units": "m", "velocity_units": "m/s",
        "criterion_m": criterion_m,
        "within_criterion": max(position_differences) <= criterion_m,
        "scope": "nominal numerical comparison on the full covariance-epoch grid; not probe uncertainty",
    }


def analyze_records(
    records: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    au_km: float,
    day_seconds: float,
) -> dict[str, Any]:
    """Return diagnostics for all 34 nominal and signed cubature records.

    ``records`` use days since the covariance epoch and states in AU/AU-day.
    ``config['forecast_diagnostic_days']`` are days after the forecast epoch;
    they are translated to the shared covariance-relative time grid internally.
    """

    if not isinstance(config, Mapping):
        raise TypeError("config must be a mapping")
    au = _number(au_km, "au_km")
    day = _number(day_seconds, "day_seconds")
    if au <= 0.0 or day <= 0.0:
        raise ValueError("au_km and day_seconds must be positive")
    by_id, times = _record_index(records)
    _validate_probe_matrix(by_id, config)
    nominal = by_id["nominal_ultra"]
    covariance_budget = _number(config.get("linear_covariance_relative_budget", 0.01),
                                "linear_covariance_relative_budget")
    mean_shift_budget = _number(config.get("mean_shift_in_major_sigma_budget", 0.01),
                                "mean_shift_in_major_sigma_budget")
    dispersion_thresholds = tuple(_number(value, "formal dispersion threshold")
                                  for value in config.get("formal_dispersion_thresholds_km", (0.1, 1.0, 10.0)))
    if covariance_budget < 0.0 or mean_shift_budget < 0.0 or any(value < 0.0 for value in dispersion_thresholds):
        raise ValueError("analysis thresholds must be non-negative")
    offset_days = _number(config["forecast_epoch_jd_tdb"], "forecast_epoch_jd_tdb") - _number(
        config["covariance_epoch_jd_tdb"], "covariance_epoch_jd_tdb")
    forecast_days = config.get("forecast_diagnostic_days")
    if not isinstance(forecast_days, list) or not forecast_days:
        raise ValueError("forecast_diagnostic_days must be a non-empty list")
    diagnostics: list[dict[str, Any]] = []
    for raw_day in forecast_days:
        forecast_day = _number(raw_day, "forecast diagnostic day")
        tau = offset_days + forecast_day
        matches = [index for index, value in enumerate(times) if value == tau]
        if len(matches) != 1:
            raise ValueError(f"diagnostic tau {tau:g} is absent from the shared covariance grid")
        diagnostics.append(_diagnostic(by_id, nominal, matches[0], forecast_day, tau, au, day,
                                        covariance_budget, mean_shift_budget, dispersion_thresholds))
    result = {
        "schema_version": 1,
        "record_count": len(records),
        "forecast_epoch_jd_tdb": _number(config["forecast_epoch_jd_tdb"], "forecast_epoch_jd_tdb"),
        "covariance_epoch_jd_tdb": _number(config["covariance_epoch_jd_tdb"], "covariance_epoch_jd_tdb"),
        "forecast_origin_offset_days": offset_days,
        "diagnostic_days": diagnostics,
        "nominal_dp_comparison": _nominal_comparison(
            by_id["nominal_extreme"], nominal, au, day,
            _number(config.get("nominal_dp_comparison_budget_m", 1.0), "nominal_dp_comparison_budget_m")),
        "units": {"position": "AU", "velocity": "AU/day", "ng": "AU/day^2",
                  "position_si": "m", "velocity_si": "m/s", "ng_si": "AU/day^2"},
        "scope": "post-hoc joint orbit/NG cubature diagnostics; not covariance calibration, impact probability, or validation",
    }
    origin = next(item for item in diagnostics if item["forecast_day"] == 0.0)
    origin_tau = origin["tau_since_covariance_epoch"]
    origin_index = next(index for index, value in enumerate(times) if value == origin_tau)
    result["forecast_origin_joint_covariance"] = {
        "forecast_day": 0.0,
        "tau_since_covariance_epoch": origin_tau,
        "nominal_state": list(_state(nominal["samples"][origin_index]["state"], "nominal origin state")),
        "nominal_ng": {
            "a1_au_d2": _ng(nominal["ng"], "nominal.ng")[0],
            "a2_au_d2": _ng(nominal["ng"], "nominal.ng")[1],
        },
        "parameter_labels": list(config.get("labels", ("e", "q", "tp", "node", "peri", "i", "A1", "A2"))),
        "coordinate_units": ["AU", "AU", "AU", "AU/day", "AU/day", "AU/day", "AU/day^2", "AU/day^2"],
        "linear_state": origin["linear_covariance"],
        "full_scale_state": origin["full_scale_cubature_covariance"],
        "normalized_small_scale_state": origin["normalized_small_scale_cubature_covariance"],
        "linear_augmented": origin["linear_augmented_covariance"],
        "full_scale_augmented": origin["full_augmented_cubature_covariance"],
        "normalized_small_scale_augmented": origin["normalized_small_augmented_cubature_covariance"],
        "principal_position": origin["principal_position"],
        "units": result["units"],
    }
    return result


__all__ = ["analyze_records"]

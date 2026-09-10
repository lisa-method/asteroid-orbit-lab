"""Continuous closest-approach geometry for one relative-state segment.

The endpoint states are relative states (for example, asteroid minus Earth)
with positions in AU and velocities in AU/day.  Position is represented by the
cubic Hermite interpolant implied by the two endpoint states.  The stationary
points of its squared distance are the roots of the degree-five polynomial
``r(u) dot dr/du``.

Only the standard library is used here.  In particular, roots are isolated by
recursively isolating the roots of the derivative and then bisecting every
sign-changing interval.  This also finds stationary roots that do not change
sign, which is important for a global minimum between widely spaced samples.
"""

from __future__ import annotations

import math

from orbit_baselines import State


_COEFFICIENT_TOLERANCE = 2.0e-14
_ROOT_VALUE_TOLERANCE = 2.0e-13
_ROOT_U_TOLERANCE = 2.0e-14
_TIE_ULP_FACTOR = 128.0
_TIE_ROOT_ERROR_FACTOR = 16.0


def _finite_vector(value: object, label: str) -> tuple[float, float, float]:
    """Validate and convert one three-dimensional state vector."""

    try:
        components = tuple(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain three finite numbers") from exc
    if len(components) != 3:
        raise ValueError(f"{label} must contain three finite numbers")
    try:
        converted = tuple(float(component) for component in components)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must contain three finite numbers") from exc
    if not all(math.isfinite(component) for component in converted):
        raise ValueError(f"{label} must contain three finite numbers")
    return converted  # type: ignore[return-value]


def _validated_state(state: object, label: str) -> State:
    if not isinstance(state, State):
        raise ValueError(f"{label} must be an orbit_baselines.State")
    return State(
        _finite_vector(state.position, f"{label}.position"),
        _finite_vector(state.velocity, f"{label}.velocity"),
    )


def _trim_and_normalize(coefficients: tuple[float, ...]) -> tuple[float, ...]:
    """Trim negligible leading degree terms and normalize coefficient scale."""

    scale = max((abs(value) for value in coefficients), default=0.0)
    if scale == 0.0:
        return (0.0,)
    normalized = [value / scale for value in coefficients]
    while len(normalized) > 1 and abs(normalized[-1]) <= _COEFFICIENT_TOLERANCE:
        normalized.pop()
    return tuple(normalized)


def _polynomial_value(coefficients: tuple[float, ...], argument: float) -> float:
    value = 0.0
    for coefficient in reversed(coefficients):
        value = value * argument + coefficient
    return value


def _deduplicate_roots(roots: list[float]) -> list[float]:
    result: list[float] = []
    for root in sorted(roots):
        root = min(1.0, max(0.0, root))
        if not result or root - result[-1] > 4.0 * _ROOT_U_TOLERANCE:
            result.append(root)
        elif root < result[-1]:
            result[-1] = root
    return result


def _bisect_root(
    coefficients: tuple[float, ...],
    left: float,
    right: float,
    left_value: float,
    right_value: float,
) -> float:
    """Bisect a bracket whose finite endpoint values have opposite signs."""

    for _ in range(100):
        midpoint = left + 0.5 * (right - left)
        if midpoint == left or midpoint == right:
            break
        midpoint_value = _polynomial_value(coefficients, midpoint)
        if midpoint_value == 0.0 or right - left <= _ROOT_U_TOLERANCE:
            return midpoint
        if (left_value < 0.0) == (midpoint_value < 0.0):
            left, left_value = midpoint, midpoint_value
        else:
            right, right_value = midpoint, midpoint_value
    return left + 0.5 * (right - left)


def _real_roots_unit_interval(coefficients: tuple[float, ...]) -> list[float]:
    """Return all real roots in [0, 1] using derivative recursion.

    Critical points from the derivative partition the interval into pieces on
    which a polynomial can have at most one simple root.  Values at critical
    points are checked explicitly, so even-multiplicity (touching) roots are
    retained.  The zero polynomial has a continuum of roots; callers already
    test both endpoints, and the earliest endpoint is the deterministic choice.
    """

    polynomial = _trim_and_normalize(coefficients)
    degree = len(polynomial) - 1
    if degree <= 0:
        return []
    if degree == 1:
        constant, linear = polynomial
        root = -constant / linear
        if -_ROOT_U_TOLERANCE <= root <= 1.0 + _ROOT_U_TOLERANCE:
            return [min(1.0, max(0.0, root))]
        return []

    # A strict constant-term bound is a useful safe shortcut for the many
    # ordinary segments whose separation derivative cannot vanish in [0, 1].
    if abs(polynomial[0]) > sum(abs(value) for value in polynomial[1:]):
        return []

    derivative = tuple(index * polynomial[index] for index in range(1, len(polynomial)))
    critical_points = _real_roots_unit_interval(derivative)
    partition = _deduplicate_roots([0.0, 1.0, *critical_points])
    values = [_polynomial_value(polynomial, point) for point in partition]
    roots: list[float] = []

    for point, value in zip(partition, values):
        if abs(value) <= _ROOT_VALUE_TOLERANCE:
            roots.append(point)

    for left, right, left_value, right_value in zip(
        partition, partition[1:], values, values[1:]
    ):
        if left_value * right_value < 0.0:
            roots.append(_bisect_root(polynomial, left, right, left_value, right_value))
    return _deduplicate_roots(roots)


def _hermite_position_coefficients(left: State, right: State, duration: float) -> tuple[tuple[float, ...], ...]:
    """Return ascending-power cubic coefficients for x, y, and z."""

    position_coefficients = []
    for p0, v0, p1, v1 in zip(
        left.position, left.velocity, right.position, right.velocity
    ):
        scaled_v0 = duration * v0
        scaled_v1 = duration * v1
        coefficients = (
            p0,
            scaled_v0,
            -3.0 * p0 - 2.0 * scaled_v0 + 3.0 * p1 - scaled_v1,
            2.0 * p0 + scaled_v0 - 2.0 * p1 + scaled_v1,
        )
        if not all(math.isfinite(value) for value in coefficients):
            raise ValueError("Hermite interpolant is not finite")
        position_coefficients.append(coefficients)
    return tuple(position_coefficients)


def _interpolate(
    left: State,
    right: State,
    position_coefficients: tuple[tuple[float, ...], ...],
    duration: float,
    fraction: float,
) -> State:
    if fraction <= 0.0:
        return left
    if fraction >= 1.0:
        return right

    positions = []
    velocities = []
    for coefficients in position_coefficients:
        position = _polynomial_value(coefficients, fraction)
        derivative = _polynomial_value(
            (coefficients[1], 2.0 * coefficients[2], 3.0 * coefficients[3]),
            fraction,
        )
        positions.append(position)
        velocities.append(derivative / duration)
    return State(tuple(positions), tuple(velocities))  # type: ignore[arg-type]


def segment_closest_approach(
    left: State, right: State, duration_days: float
) -> tuple[float, State]:
    """Find the global minimum separation on one cubic-Hermite segment.

    Parameters
    ----------
    left, right:
        Relative endpoint states.  Their positions are in AU and velocities in
        AU/day, as in :class:`orbit_baselines.State`.
    duration_days:
        Positive duration between the endpoint states.  The returned fraction
        ``u`` is measured from the left endpoint, so the physical time is
        ``u * duration_days``.
    """

    validated_left = _validated_state(left, "left")
    validated_right = _validated_state(right, "right")
    try:
        duration = float(duration_days)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("duration_days must be finite and positive") from exc
    if not math.isfinite(duration) or duration <= 0.0:
        raise ValueError("duration_days must be finite and positive")

    position_coefficients = _hermite_position_coefficients(
        validated_left, validated_right, duration
    )
    derivative_coefficients = tuple(
        (
            coefficients[1],
            2.0 * coefficients[2],
            3.0 * coefficients[3],
        )
        for coefficients in position_coefficients
    )

    # Dot the cubic position with its quadratic derivative.  Coefficients are
    # accumulated in ascending powers, yielding degree at most five.
    stationary_polynomial = [0.0] * 6
    for position, derivative in zip(position_coefficients, derivative_coefficients):
        for position_power, position_value in enumerate(position):
            for derivative_power, derivative_value in enumerate(derivative):
                stationary_polynomial[position_power + derivative_power] += (
                    position_value * derivative_value
                )
    if not all(math.isfinite(value) for value in stationary_polynomial):
        raise ValueError("Closest-approach polynomial is not finite")

    # Root isolation stops after a small interval in u.  The resulting
    # position uncertainty is proportional to the largest Hermite derivative;
    # retain equal minima within that scale and normal floating-point error.
    position_evaluation_scale = sum(
        abs(value) for coefficients in position_coefficients for value in coefficients
    )
    derivative_evaluation_scale = sum(
        abs(value) for coefficients in derivative_coefficients for value in coefficients
    )
    position_error_floor = (
        _ROOT_U_TOLERANCE * derivative_evaluation_scale
        + 8.0 * math.ulp(1.0) * position_evaluation_scale
    )

    candidates = _deduplicate_roots(
        [0.0, 1.0, *_real_roots_unit_interval(tuple(stationary_polynomial))]
    )
    best_fraction = 0.0
    best_state = validated_left
    best_distance_squared = math.inf

    for fraction in candidates:
        state = _interpolate(
            validated_left,
            validated_right,
            position_coefficients,
            duration,
            fraction,
        )
        distance_squared = sum(component * component for component in state.position)
        if not math.isfinite(distance_squared):
            raise ValueError("Interpolated state is not finite")
        if not all(
            math.isfinite(value)
            for value in (*state.position, *state.velocity)
        ):
            raise ValueError("Interpolated state is not finite")
        if not math.isfinite(best_distance_squared):
            best_fraction = fraction
            best_state = state
            best_distance_squared = distance_squared
            continue
        scale = max(abs(best_distance_squared), abs(distance_squared))
        tie_tolerance = (
            _TIE_ULP_FACTOR * math.ulp(1.0) * scale
            + _TIE_ROOT_ERROR_FACTOR * position_error_floor * position_error_floor
        )
        tied = abs(distance_squared - best_distance_squared) <= tie_tolerance
        if distance_squared < best_distance_squared and not tied:
            best_fraction = fraction
            best_state = state
            best_distance_squared = distance_squared
        elif tied and fraction < best_fraction:
            best_fraction = fraction
            best_state = state
            best_distance_squared = distance_squared

    return best_fraction, best_state


__all__ = ["segment_closest_approach"]

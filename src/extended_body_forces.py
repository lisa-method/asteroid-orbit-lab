"""Small, explicit extensions to point-mass body forces.

The functions here return only corrections to the usual point-mass
acceleration.  Positions, radii, and second moments use the caller's
consistent length units (the project convention is AU); ``mu`` has matching
length cubed per time squared units.
"""

from __future__ import annotations

import math
from typing import Sequence

from orbit_baselines import Vector, norm


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _vector(value: Sequence[float], name: str) -> Vector:
    if len(value) != 3:
        raise ValueError(f"{name} must contain three components")
    result = tuple(_finite(component, name) for component in value)
    return result  # type: ignore[return-value]


def _legendre_with_derivative(degree: int, cosine: float) -> tuple[float, float]:
    if degree == 2:
        return (0.5 * (3.0 * cosine * cosine - 1.0), 3.0 * cosine)
    if degree == 3:
        return (0.5 * (5.0 * cosine**3 - 3.0 * cosine), 0.5 * (15.0 * cosine * cosine - 3.0))
    if degree == 4:
        return ((35.0 * cosine**4 - 30.0 * cosine * cosine + 3.0) / 8.0, (35.0 * cosine**3 - 15.0 * cosine) / 2.0)
    raise ValueError("degree must be 2, 3, or 4")


def zonal_acceleration(
    displacement: Sequence[float],
    pole: Sequence[float],
    mu: float,
    reference_radius: float,
    degree: int,
    jn: float,
) -> Vector:
    """Return one unnormalised zonal ``Jn`` acceleration correction.

    ``displacement`` is target relative to the body's centre and ``pole`` is
    its symmetry axis.  The supplied axis is normalized internally.  Only
    degrees 2, 3, and 4 are accepted.  The formula is

    ``mu*Jn*R**n/r**(n+3) * (((n+1)*Pn+s*Pn')*rvec - r*Pn'*polehat)``.
    """

    vector = _vector(displacement, "displacement")
    axis = _vector(pole, "pole")
    mu_value = _finite(mu, "mu")
    radius_scale = _finite(reference_radius, "reference_radius")
    coefficient = _finite(jn, "jn")
    if mu_value <= 0.0:
        raise ValueError("mu must be positive")
    if radius_scale <= 0.0:
        raise ValueError("reference_radius must be positive")
    if not isinstance(degree, int) or isinstance(degree, bool) or degree not in (2, 3, 4):
        raise ValueError("degree must be 2, 3, or 4")
    distance = norm(vector)
    axis_norm = norm(axis)
    if distance == 0.0 or not math.isfinite(distance):
        raise ValueError("displacement must be nonzero and finite")
    if axis_norm == 0.0 or not math.isfinite(axis_norm):
        raise ValueError("pole must be nonzero and finite")
    unit_axis = tuple(component / axis_norm for component in axis)
    s = sum(component * direction for component, direction in zip(vector, unit_axis)) / distance
    polynomial, derivative = _legendre_with_derivative(degree, s)
    factor = mu_value * coefficient * radius_scale**degree / distance ** (degree + 3)
    radial = (degree + 1.0) * polynomial + s * derivative
    return tuple(
        factor * (radial * component - distance * derivative * direction)
        for component, direction in zip(vector, unit_axis)
    )  # type: ignore[return-value]


def _second_moment(value: Sequence[Sequence[float]]) -> tuple[tuple[float, float, float], ...]:
    if len(value) != 3 or any(len(row) != 3 for row in value):
        raise ValueError("second_moment must be a 3x3 matrix")
    matrix = tuple(tuple(_finite(component, "second_moment") for component in row) for row in value)
    scale = max(max(abs(component) for component in row) for row in matrix)
    tolerance = 1.0e-12 * max(scale, 1.0e-30)
    for i in range(3):
        for j in range(i):
            if abs(matrix[i][j] - matrix[j][i]) > tolerance:
                raise ValueError("second_moment must be symmetric")
    determinant_tolerance = 1.0e-12 * max(scale, 1.0e-30) ** 2
    for i in range(3):
        if matrix[i][i] < -tolerance:
            raise ValueError("second_moment must be positive semidefinite")
    for i, j in ((0, 1), (0, 2), (1, 2)):
        if matrix[i][i] * matrix[j][j] - matrix[i][j] ** 2 < -determinant_tolerance:
            raise ValueError("second_moment must be positive semidefinite")
    determinant = (
        matrix[0][0] * (matrix[1][1] * matrix[2][2] - matrix[1][2] ** 2)
        - matrix[0][1] * (matrix[0][1] * matrix[2][2] - matrix[1][2] * matrix[0][2])
        + matrix[0][2] * (matrix[0][1] * matrix[1][2] - matrix[1][1] * matrix[0][2])
    )
    if determinant < -1.0e-12 * max(scale, 1.0e-30) ** 3:
        raise ValueError("second_moment must be positive semidefinite")
    return matrix  # type: ignore[return-value]


def extended_body_quadrupole_acceleration(
    displacement: Sequence[float],
    mu: float,
    second_moment: Sequence[Sequence[float]],
) -> Vector:
    """Return the leading finite-size correction for a test body.

    ``second_moment`` is the mass-normalized symmetric PSD matrix
    ``S=<delta delta^T>``.  This is a centre-of-mass acceleration correction
    for an extended test body in a point source field; it is neither the asteroid's self-gravity
    nor a torque model.
    """

    vector = _vector(displacement, "displacement")
    mu_value = _finite(mu, "mu")
    if mu_value <= 0.0:
        raise ValueError("mu must be positive")
    matrix = _second_moment(second_moment)
    distance = norm(vector)
    if distance == 0.0 or not math.isfinite(distance):
        raise ValueError("displacement must be nonzero and finite")
    if (matrix[0][1] == matrix[0][2] == matrix[1][2] == 0.0 and
            matrix[0][0] == matrix[1][1] == matrix[2][2]):
        return (0.0, 0.0, 0.0)
    s_times_r = tuple(sum(matrix[i][j] * vector[j] for j in range(3)) for i in range(3))
    trace = sum(matrix[i][i] for i in range(3))
    quadratic = sum(vector[i] * s_times_r[i] for i in range(3))
    radial_factor = 5.0 * quadratic / (distance * distance)
    factor = 3.0 * mu_value / (2.0 * distance**5)
    return tuple(factor * (2.0 * s_times_r[i] + trace * vector[i] - radial_factor * vector[i]) for i in range(3))  # type: ignore[return-value]


__all__ = ["extended_body_quadrupole_acceleration", "zonal_acceleration"]

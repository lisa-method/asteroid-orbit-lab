"""Independent Decimal audit terms for a massless target.

The PPN expression is Holman et al. (2023), Eq. (2), with ``beta=gamma=1``.
This module intentionally does not import the project's relativistic force
implementation.  Coordinates of the target and massive sources may share an
arbitrary spatial translation; velocities are the supplied barycentric
velocities.  Returned components are :class:`decimal.Decimal` values so a
caller can compare terms without first losing the exact binary float inputs.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any


_ZERO = Decimal(0)
_ONE = Decimal(1)
_TWO = Decimal(2)
_THREE = Decimal(3)
_FOUR = Decimal(4)
_FIVE = Decimal(5)

Vector = tuple[Decimal, Decimal, Decimal]
Terms = dict[str, Vector]


def _decimal(value: object, name: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        if isinstance(value, Decimal):
            result = value
        elif isinstance(value, float):
            result = Decimal.from_float(value)
        elif isinstance(value, int):
            result = Decimal(value)
        else:
            result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError(f"{name} must be finite" + (" and positive" if positive else ""))
    return result


def _vector(value: Iterable[object], name: str) -> Vector:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must have three finite components")
    try:
        values = tuple(_decimal(x, f"{name}[{i}]") for i, x in enumerate(value))
    except TypeError as exc:
        raise ValueError(f"{name} must have three finite components") from exc
    if len(values) != 3:
        raise ValueError(f"{name} must have three finite components")
    return values  # type: ignore[return-value]


def _add(a: Vector, b: Vector) -> Vector:
    return tuple(x + y for x, y in zip(a, b))  # type: ignore[return-value]


def _sub(a: Vector, b: Vector) -> Vector:
    return tuple(x - y for x, y in zip(a, b))  # type: ignore[return-value]


def _scale(a: Vector, factor: Decimal) -> Vector:
    return tuple(factor * x for x in a)  # type: ignore[return-value]


def _dot(a: Vector, b: Vector) -> Decimal:
    return sum((x * y for x, y in zip(a, b)), _ZERO)


def _cross(a: Vector, b: Vector) -> Vector:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a: Vector) -> Decimal:
    return _dot(a, a).sqrt()


def _zero() -> Vector:
    return (_ZERO, _ZERO, _ZERO)


def _mapping_value(mapping: Mapping[str, Any], *names: str, default: object = None) -> object:
    for name in names:
        if name in mapping:
            return mapping[name]
    return default


def _j2(displacement: Vector, *, mu: object, radius: object, j2: object, pole: Iterable[object]) -> Vector:
    """Return the direct zonal J2 correction in AU/day²."""
    m = _decimal(mu, "J2 mu", positive=True)
    radius_scale = _decimal(radius, "J2 radius", positive=True)
    coefficient_j2 = _decimal(j2, "J2 coefficient")
    axis = _vector(pole, "J2 pole")
    r2 = _dot(displacement, displacement)
    if r2 <= 0:
        raise ValueError("J2 displacement must be nonzero")
    r = r2.sqrt()
    axis_norm = _norm(axis)
    if axis_norm == 0:
        raise ValueError("J2 pole must be nonzero")
    unit = _scale(axis, _ONE / axis_norm)
    projection = _dot(displacement, unit)
    coefficient = _THREE / _TWO * m * coefficient_j2 * radius_scale**2 / r**5
    factor = _FIVE * (projection / r) ** 2 - _ONE
    return _scale(_sub(_scale(displacement, factor), _scale(unit, _TWO * projection)), coefficient)


def _param(mapping: Mapping[str, Any] | object, *names: str, default: object = None) -> object:
    if isinstance(mapping, Mapping):
        return _mapping_value(mapping, *names, default=default)
    for name in names:
        if hasattr(mapping, name):
            return getattr(mapping, name)
    return default


def _weak_ng(position: Vector, velocity: Vector, ng: Mapping[str, Any] | object) -> Vector:
    a1 = _decimal(_param(ng, "a1_au_d2", "A1", "a1", default=0), "NG A1")
    a2 = _decimal(_param(ng, "a2_au_d2", "A2", "a2", default=0), "NG A2")
    a3 = _decimal(_param(ng, "a3_au_d2", "A3", "a3", default=0), "NG A3")
    r0 = _decimal(_param(ng, "r0_au", "R0", "r0", default=1), "NG r0", positive=True)
    alpha = _decimal(_param(ng, "alpha", default=1), "NG alpha", positive=True)
    m = _decimal(_param(ng, "exponent_m", "m", default=2), "NG m")
    n = _decimal(_param(ng, "exponent_n", "n", default=0), "NG n")
    k = _decimal(_param(ng, "exponent_k", "k", default=0), "NG k")
    radius = _norm(position)
    if radius == 0:
        raise ValueError("NG position must be nonzero")
    radial = _scale(position, _ONE / radius)
    angular = _cross(position, velocity)
    angular_norm = _norm(angular)
    if angular_norm == 0 and (a1 != 0 or a2 != 0 or a3 != 0):
        raise ValueError("NG RTN basis is undefined for zero angular momentum")
    if angular_norm == 0:
        return _zero()
    normal = _scale(angular, _ONE / angular_norm)
    transverse = _cross(normal, radial)
    ratio = radius / r0
    law = alpha * ratio ** (-m) * (_ONE + ratio**n) ** (-k)
    return _scale(_add(_add(_scale(radial, a1), _scale(transverse, a2)), _scale(normal, a3)), law)


def decimal_force_terms(
    r: Iterable[object],
    v: Iterable[object],
    source_mus: Sequence[object],
    source_positions: Sequence[Iterable[object]],
    source_velocities: Sequence[Iterable[object]],
    c: object,
    outer_indices: Iterable[int] | None = None,
    *,
    major_indices: Iterable[int] | None = None,
    sun_index: int = 0,
    earth_j2: Mapping[str, Any] | object | None = None,
    earth_index: int | None = None,
    solar_j2: Mapping[str, Any] | object | None = None,
    ng: Mapping[str, Any] | object | None = None,
) -> Terms:
    """Return independently summed Newtonian, PPN, J2 and NG vectors.

    ``source_mus``, positions and velocities are in one common translated
    barycentric coordinate system.  ``major_indices`` explicitly identifies
    the Sun and major bodies included in PPN ``U_i``, ``U_j`` and source
    accelerations; omitted means every source.  All sources still contribute
    to ``newton``.  ``outer_indices`` explicitly selects a subset of
    ``major_indices``; omitted means every major source.  ``sun_index`` is used
    to form the heliocentric NG and solar-J2 displacement/velocity.
    ``earth_j2`` is direct J2
    with ``position`` or ``earth_index`` identifying Earth and a fixed-z pole
    by default.  ``solar_j2`` is direct J2 about the coordinate origin and
    requires the same ``mu``, ``radius_au`` and ``j2`` fields.  NG is evaluated
    from the target's heliocentric ``r,v`` using A1/A2/A3 RTN coefficients.
    """
    target_r, target_v = _vector(r, "r"), _vector(v, "v")
    try:
        mus = tuple(_decimal(x, f"source_mus[{i}]", positive=True) for i, x in enumerate(source_mus))
        positions = tuple(_vector(x, f"source_positions[{i}]") for i, x in enumerate(source_positions))
        velocities = tuple(_vector(x, f"source_velocities[{i}]") for i, x in enumerate(source_velocities))
    except TypeError as exc:
        raise ValueError("source arrays must be finite sequences") from exc
    count = len(mus)
    if not count or len(positions) != count or len(velocities) != count:
        raise ValueError("source arrays must be nonempty and have equal lengths")
    if isinstance(sun_index, bool) or not isinstance(sun_index, int) or not 0 <= sun_index < count:
        raise ValueError("sun_index must be an in-range source index")
    if major_indices is None:
        major = tuple(range(count))
    else:
        try:
            major = tuple(major_indices)
        except TypeError as exc:
            raise ValueError("major_indices must contain integers") from exc
        if any(isinstance(i, bool) or not isinstance(i, int) or i < 0 or i >= count for i in major) or len(set(major)) != len(major) or not major:
            raise ValueError("major_indices must contain unique in-range integers")
    if sun_index not in major:
        raise ValueError("sun_index must be a major source")
    speed_c = _decimal(c, "c", positive=True)
    if outer_indices is None:
        selected = major
    else:
        try:
            selected = tuple(outer_indices)
        except TypeError as exc:
            raise ValueError("outer_indices must contain integers") from exc
        if any(isinstance(i, bool) or not isinstance(i, int) or i < 0 or i >= count for i in selected) or len(set(selected)) != len(selected) or any(i not in major for i in selected):
            raise ValueError("outer_indices must be a unique subset of major_indices")

    with localcontext() as context:
        context.prec = 50
        newton = _zero()
        distances: list[list[Decimal]] = [[_ZERO] * count for _ in range(count)]
        source_accels: list[Vector] = [_zero() for _ in range(count)]
        for i in major:
            acceleration = _zero()
            for j in major:
                if i == j:
                    continue
                delta = _sub(positions[j], positions[i])
                d = _norm(delta)
                if d == 0:
                    raise ValueError("coincident source positions")
                distances[i][j] = d
                acceleration = _add(acceleration, _scale(delta, mus[j] / d**3))
            source_accels[i] = acceleration
        for j in range(count):
            delta = _sub(positions[j], target_r)
            d = _norm(delta)
            if d == 0:
                raise ValueError("target coincides with source")
            newton = _add(newton, _scale(delta, mus[j] / d**3))

        ui = sum((mus[j] / _norm(_sub(positions[j], target_r)) for j in major), _ZERO)
        inv_c2 = _ONE / speed_c**2
        ppn_position = _zero()
        ppn_velocity = _zero()
        ppn_source = _zero()
        target_speed2 = _dot(target_v, target_v)
        for j in selected:
            delta = _sub(positions[j], target_r)
            d = _norm(delta)
            source_v = velocities[j]
            relative = _sub(target_r, positions[j])
            uj = sum((mus[k] / distances[j][k] for k in major if k != j), _ZERO)
            bracket = (-_FOUR * ui - uj + target_speed2 + _TWO * _dot(source_v, source_v)
                       - _FOUR * _dot(target_v, source_v)
                       - (Decimal(3) / _TWO) * (_dot(relative, source_v) / d) ** 2
                       + _ONE / _TWO * _dot(delta, source_accels[j]))
            coefficient = mus[j] / d**3 * inv_c2
            ppn_position = _add(ppn_position, _scale(delta, coefficient * bracket))
            ppn_velocity = _add(ppn_velocity, _scale(_sub(target_v, source_v), coefficient * _dot(relative, _sub(_scale(target_v, _FOUR), _scale(source_v, Decimal(3))))))
            ppn_source = _add(ppn_source, _scale(source_accels[j], Decimal(7) / _TWO * mus[j] / d * inv_c2))

        sun_position, sun_velocity = positions[sun_index], velocities[sun_index]
        relative_position = _sub(target_r, sun_position)
        relative_velocity = _sub(target_v, sun_velocity)
        earth_term = _zero()
        if earth_j2 is not None:
            earth_pos_value = _param(earth_j2, "position", "earth_position", default=None)
            index_value = _param(earth_j2, "source_index", "earth_index", default=earth_index)
            if earth_pos_value is None:
                if index_value is None or isinstance(index_value, bool) or not isinstance(index_value, int) or not 0 <= index_value < count:
                    raise ValueError("earth_j2 requires position or earth_index")
                earth_pos = positions[index_value]
            else:
                earth_pos = _vector(earth_pos_value, "earth_j2 position")
            pole = _param(earth_j2, "pole", default=(0, 0, 1))
            earth_term = _j2(_sub(target_r, earth_pos), mu=_param(earth_j2, "mu", "mu_au3_d2"), radius=_param(earth_j2, "radius_au", "reference_radius_au"), j2=_param(earth_j2, "j2"), pole=pole)
        solar_term = _zero()
        if solar_j2 is not None:
            solar_term = _j2(relative_position, mu=_param(solar_j2, "mu", "mu_sun"), radius=_param(solar_j2, "radius_au", "reference_radius_au"), j2=_param(solar_j2, "j2"), pole=_param(solar_j2, "pole", default=(0, 0, 1)))
        ng_term = _weak_ng(relative_position, relative_velocity, ng) if ng is not None else _zero()
        total = _zero()
        for term in (newton, ppn_position, ppn_velocity, ppn_source, earth_term, solar_term, ng_term):
            total = _add(total, term)
        return {"newton": newton, "ppn_position": ppn_position, "ppn_velocity": ppn_velocity,
                "ppn_source_acceleration": ppn_source, "earth_j2": earth_term,
                "solar_j2": solar_term, "ng": ng_term, "total": total}


__all__ = ["decimal_force_terms", "Vector", "Terms"]

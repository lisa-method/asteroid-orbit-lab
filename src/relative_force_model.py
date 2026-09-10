"""Generic relative-time force composition for a causal target rollout.

The ephemerides supplied to :func:`build_relative_force_model` are already
relative-time ``Perturber`` objects, as produced by
``relative_time_dynamics.relative_perturbers``.  No target identity, reference
trajectory, or object-specific routing is used.  The no-J3/J4 path delegates
to the frozen ``build_relative_force`` arithmetic exactly.

J3/J4 use the conventional unnormalised relation ``Jn = -sqrt(2n+1) Cn0``
and the supplied secular rates in Julian years.  Earth pole and rate handling
follow the project's published IAU/IERS-compatible approximation; this is a
force-model option, not an accuracy guarantee.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from extended_body_forces import zonal_acceleration
from ng_inputs_v2 import NGInput
from orbit_baselines import Acceleration, Vector, add, subtract
from planetary_dynamics import (
    NonGravitationalParameters,
    Perturber,
    combine_accelerations,
)
from relative_time_dynamics import build_relative_force


_J2000_JD_TDB = 2451545.0
_DAYS_PER_JULIAN_YEAR = 365.25


def _finite(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result) or (positive and result <= 0.0):
        suffix = " and positive" if positive else ""
        raise ValueError(f"{name} must be finite{suffix}")
    return result


def _config_number(config: Mapping[str, Any], key: str, *, positive: bool = False) -> float:
    if key not in config:
        raise ValueError(f"configuration requires {key}")
    return _finite(config[key], key, positive=positive)


def _au_value(config: Mapping[str, Any], direct_key: str, km_key: str, name: str) -> float:
    """Read an explicit AU value, or convert km with explicit ``au_km``."""
    if direct_key in config:
        return _config_number(config, direct_key, positive=True)
    if km_key not in config or "au_km" not in config:
        raise ValueError(f"configuration requires {direct_key} (or {km_key} with au_km)")
    kilometers = _config_number(config, km_key, positive=True)
    au_km = _config_number(config, "au_km", positive=True)
    return _finite(kilometers / au_km, name, positive=True)


def _mu_value(config: Mapping[str, Any]) -> float:
    if "mu_au3_d2" in config:
        return _config_number(config, "mu_au3_d2", positive=True)
    if "mu_km3_s2" not in config or "au_km" not in config or "day_s" not in config:
        raise ValueError("zonal configuration requires mu_au3_d2 or explicit km/s conversion fields")
    mu_km3_s2 = _config_number(config, "mu_km3_s2", positive=True)
    au_km = _config_number(config, "au_km", positive=True)
    day_s = _config_number(config, "day_s", positive=True)
    return _finite(mu_km3_s2 * day_s**2 / au_km**3, "mu_au3_d2", positive=True)


def _enabled_degrees(enabled: Iterable[int | str] | Mapping[str, Any] | None, enabled_j3: bool, enabled_j4: bool) -> tuple[int, ...]:
    if type(enabled_j3) is not bool or type(enabled_j4) is not bool:
        raise ValueError("enabled_j3 and enabled_j4 must be boolean")
    degrees: set[int] = set()
    items = () if enabled is None else (enabled.keys() if isinstance(enabled, Mapping) else enabled)
    for item in items:
        if isinstance(enabled, Mapping):
            flag = enabled[item]
            if type(flag) is not bool:
                raise ValueError("enabled mapping values must be boolean")
            if not flag:
                continue
        if isinstance(item, bool):
            raise ValueError("enabled zonal degrees must be 3 or 4")
        if isinstance(item, str):
            token = item.strip().upper()
            if token.startswith("J"):
                token = token[1:]
            try:
                numeric = float(token)
            except (TypeError, ValueError) as exc:
                raise ValueError("enabled zonal degrees must be 3 or 4") from exc
        else:
            try:
                numeric = float(item)
            except (TypeError, ValueError) as exc:
                raise ValueError("enabled zonal degrees must be 3 or 4") from exc
        if not math.isfinite(numeric) or numeric != math.trunc(numeric):
            raise ValueError("enabled zonal degrees must be integral 3 or 4")
        degree = int(numeric)
        if degree not in (3, 4):
            raise ValueError("enabled zonal degrees must be 3 or 4")
        degrees.add(degree)
    if enabled_j3:
        degrees.add(3)
    if enabled_j4:
        degrees.add(4)
    return tuple(sorted(degrees))


def _old_iau_pole(origin_jd_tdb: float, time_days: float) -> Vector:
    """Match the relative audit's explicit origin-then-local-time grouping."""
    centuries = ((origin_jd_tdb - _J2000_JD_TDB) + time_days) / 36525.0
    right_ascension = math.radians(-0.641 * centuries)
    declination = math.radians(90.0 - 0.557 * centuries)
    cosine = math.cos(declination)
    return (
        cosine * math.cos(right_ascension),
        cosine * math.sin(right_ascension),
        math.sin(declination),
    )


def _validate_perturbers(planets: Sequence[Perturber]) -> tuple[Perturber, ...]:
    result = tuple(planets)
    if not result:
        raise ValueError("relative force requires at least Earth body_id 399")
    if any(not isinstance(body, Perturber) for body in result):
        raise TypeError("planets must contain Perturber instances")
    ids = [body.body_id for body in result]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate perturber would double-count its gravity")
    for body in result:
        _finite(body.mu_au3_d2, f"mu of {body.body_id}", positive=True)
    if "399" not in ids:
        raise ValueError("relative force requires Earth body_id 399")
    return result


def build_relative_force_model(
    origin_jd_tdb: float,
    planets: Sequence[Perturber],
    mu_sun_au3_d2: float,
    speed_of_light_au_d: float,
    ng: NGInput | None,
    earth_j2: Mapping[str, Any],
    earth_zonals: Mapping[str, Any] | None = None,
    *,
    enabled: Iterable[int | str] | Mapping[str, Any] | None = None,
    enabled_j3: bool = False,
    enabled_j4: bool = False,
    ng_policy: str = "available",
) -> tuple[Acceleration, dict[str, Any]]:
    """Build a generic relative-time Sun/planet/Earth-zonal force.

    ``planets`` must contain already-relative ``Perturber`` ephemerides.  The
    mandatory Earth J2 configuration accepts ``reference_radius_au`` and
    ``j2``; alternatively an explicit ``reference_radius_km`` plus ``au_km``
    may be supplied.  Zonal configuration values are explicit: ``mu_au3_d2``
    (or ``mu_km3_s2`` with ``au_km`` and ``day_s``), radius, ``c30_j2000``,
    ``c40_j2000``, and their ``*_rate_per_julian_year`` fields when J3/J4 is
    enabled.  No values are read from disk or silently defaulted.

    ``ng_policy`` is ``"available"`` (apply only when the forecast-origin
    gate says available), ``"off"``, or ``"required"``.  The returned
    metadata is JSON-serializable and explicitly records limitations.
    """
    origin = _finite(origin_jd_tdb, "origin_jd_tdb")
    mu_sun = _finite(mu_sun_au3_d2, "mu_sun_au3_d2", positive=True)
    speed = _finite(speed_of_light_au_d, "speed_of_light_au_d", positive=True)
    bodies = _validate_perturbers(planets)
    if not isinstance(earth_j2, Mapping):
        raise TypeError("earth_j2 must be a mapping")
    if earth_zonals is not None and not isinstance(earth_zonals, Mapping):
        raise TypeError("earth_zonals must be a mapping or None")
    if ng_policy not in {"available", "off", "required"}:
        raise ValueError("ng_policy must be available, off or required")
    if ng is not None and not isinstance(ng, NGInput):
        raise TypeError("ng must be a validated NGInput or None")
    ng_input = ng if ng is not None else NGInput()

    # The relative builder's frozen J2 baseline uses AU radius and IAU pole.
    # Conversion from km is allowed only when the caller supplies au_km.
    j2_radius = _au_value(earth_j2, "reference_radius_au", "reference_radius_km", "reference_radius_au")
    j2_value = _config_number(earth_j2, "j2")
    pole_model = earth_j2.get("pole_model", "iau")
    if pole_model != "iau":
        raise ValueError("relative baseline uses the frozen IAU mean-pole arithmetic")

    status = ng_input.status(origin)
    if ng_policy == "required" and status != "available":
        raise ValueError(f"NG parameters required but {status}")
    ng_applied = ng_policy != "off" and status == "available"
    parameters: NonGravitationalParameters | None = ng_input.parameters if ng_applied else None

    # This exact call is intentional: with no optional J3/J4, the callback is
    # the frozen relative_time_dynamics implementation byte-for-byte in its
    # arithmetic and term ordering.
    baseline = build_relative_force(origin, bodies, mu_sun, speed, parameters, j2_radius, j2_value)
    degrees = _enabled_degrees(enabled, enabled_j3, enabled_j4)
    if degrees and earth_zonals is None:
        raise ValueError("earth_zonals configuration is required when J3/J4 is enabled")

    zonal_config: dict[str, Any] = {}
    if degrees:
        assert earth_zonals is not None
        zonal_config = dict(earth_zonals)
        zonal_radius = _au_value(zonal_config, "reference_radius_au", "reference_radius_km", "zonal_reference_radius_au")
        zonal_mu = _mu_value(zonal_config)
        for degree in degrees:
            _config_number(zonal_config, f"c{degree}0_j2000")
            _config_number(zonal_config, f"c{degree}0_rate_per_julian_year")
    else:
        zonal_radius = zonal_mu = None

    earth = next(body for body in bodies if body.body_id == "399")

    def zonal_term(time_days: float, position: Vector, _velocity: Vector) -> Vector:
        if not degrees:
            return (0.0, 0.0, 0.0)
        time = _finite(time_days, "time_days")
        earth_position = earth.ephemeris.state_at(time).position
        pole = _old_iau_pole(origin, time)
        julian_years = ((origin - _J2000_JD_TDB) + time) / _DAYS_PER_JULIAN_YEAR
        total: Vector = (0.0, 0.0, 0.0)
        assert zonal_radius is not None and zonal_mu is not None
        for degree in degrees:
            c_name = f"c{degree}0_j2000"
            rate_name = f"c{degree}0_rate_per_julian_year"
            cn0 = _finite(zonal_config[c_name], c_name) + _finite(zonal_config[rate_name], rate_name) * julian_years
            jn = -math.sqrt(2.0 * degree + 1.0) * cn0
            direct = zonal_acceleration(
                tuple(position[i] - earth_position[i] for i in range(3)),
                pole, zonal_mu, zonal_radius, degree, jn,
            )
            indirect = zonal_acceleration(
                tuple(-value for value in earth_position),
                pole, zonal_mu, zonal_radius, degree, jn,
            )
            total = add(total, subtract(direct, indirect))
        return total

    force = combine_accelerations(baseline, zonal_term) if degrees else baseline
    applied_terms = ["solar_monopole", "planetary_monopoles", "solar_schwarzschild", "earth_j2"]
    if ng_applied:
        applied_terms.append("non_gravitational")
    applied_terms.extend(f"earth_j{degree}" for degree in degrees)
    limitations = ["accuracy_guaranteed_false"]
    if degrees:
        limitations.append("tdb_iers_scaling_approx")
    if status != "available":
        limitations.append(f"ng_{status}")
    elif ng_applied and ng_input.sigma_au_d2 is None:
        limitations.append("ng_uncertainty_unknown")
    metadata = {
        "applied_forces": list(applied_terms),
        "applied_terms": list(applied_terms),
        "perturber_ids": [body.body_id for body in bodies],
        "small_body_ids": [body.body_id for body in bodies if str(body.body_id).startswith("sb:")],
        "small_body_monopoles_applied": any(str(body.body_id).startswith("sb:") for body in bodies),
        "ng_policy": ng_policy,
        "ng_status": status,
        "ng_applied": ng_applied,
        "ng_availability": {
            "status": status,
            "source": ng_input.source,
            "source_sha256": ng_input.source_sha256,
            "available_from_jd_tdb": ng_input.available_from_jd_tdb,
            "basis": ng_input.availability_basis,
            "solution_date": ng_input.solution_date,
            "sigma_au_d2": list(ng_input.sigma_au_d2) if ng_applied and ng_input.sigma_au_d2 is not None else None,
        },
        "ng_source": ng_input.source,
        "ng_source_sha256": ng_input.source_sha256,
        "earth_j2": {"j2": j2_value, "reference_radius_au": j2_radius, "pole_model": "iau"},
        "earth_zonals": {
            "enabled_degrees": list(degrees),
            "reference_radius_au": zonal_radius,
            "mu_au3_d2": zonal_mu,
            "time_rate_basis": "TDB Julian years from J2000; IERS-compatible approximation",
        },
        "tdb_iers_scaling_approx": bool(degrees),
        "tdb_iers_scaling_approximation": bool(degrees),
        "uncertainty_propagated": False,
        "accuracy_guaranteed": False,
        "limitations": limitations,
        "limits": list(limitations),
    }
    return force, metadata


__all__ = ["build_relative_force_model"]

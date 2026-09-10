"""Barycentric 1PN force in a linearly Sun-moving integration frame.

This builder addresses a frame audit rather than changing the frozen
heliocentric wrappers.  The integrated coordinates are
``q = r_b - r_sun(0) - t*v_sun(0)`` and ``w = v_b - v_sun(0)``.  Their frame
origin has zero acceleration, so the callback returns direct barycentric
Newtonian and EIH accelerations without subtracting a model Sun acceleration.
The returned converter maps ``(q, w)`` back to the heliocentric state used by
the existing project APIs.

Source velocities remain barycentric.  Source positions use Sun ``(0, 0, 0)``
and heliocentric planet positions; all EIH position terms are differences, so
this common translation is equivalent to using barycentric positions.
The EIH term itself is the independent implementation in
``relativistic_eih.py``.  No target identity or future target state is used.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from earth_oblateness import j2_acceleration
from orbit_baselines import State, Vector
from planetary_dynamics import Perturber, non_gravitational_acceleration, solar_schwarzschild_acceleration
from relativistic_eih import eih_correction, prepare_sources


_FIXED_J2000_POLE: Vector = (0.0, 0.0, 1.0)


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _vector(value: Vector, name: str) -> Vector:
    try:
        components = tuple(value)
    except TypeError as exc:
        raise ValueError(f"{name} must contain three finite components") from exc
    if len(components) != 3 or any(isinstance(component, bool) or not math.isfinite(float(component)) for component in components):
        raise ValueError(f"{name} must contain three finite components")
    return tuple(float(component) for component in components)  # type: ignore[return-value]


def _sub(left: Vector, right: Vector) -> Vector:
    return tuple(a - b for a, b in zip(left, right))  # type: ignore[return-value]


def _add(left: Vector, right: Vector) -> Vector:
    return tuple(a + b for a, b in zip(left, right))  # type: ignore[return-value]


def _scale(vector: Vector, factor: float) -> Vector:
    return tuple(component * factor for component in vector)  # type: ignore[return-value]


def _norm(vector: Vector) -> float:
    return math.sqrt(sum(component * component for component in vector))


def _solar_j2_term(mu_sun: float, solar_j2: Mapping[str, object] | None):
    if solar_j2 is None:
        return None
    if not isinstance(solar_j2, Mapping):
        raise TypeError("solar_j2 must be a mapping or None")
    required = ("j2", "radius_au", "pole_ra_deg", "pole_dec_deg")
    if any(key not in solar_j2 for key in required):
        raise ValueError("solar_j2 requires j2, radius_au, pole_ra_deg and pole_dec_deg")
    values = {key: _finite(solar_j2[key], f"solar_j2[{key}]") for key in required}
    if values["radius_au"] <= 0.0:
        raise ValueError("solar_j2 radius_au must be positive")
    ra = math.radians(values["pole_ra_deg"])
    dec = math.radians(values["pole_dec_deg"])
    pole = (math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra), math.sin(dec))

    def acceleration(position: Vector) -> Vector:
        return j2_acceleration(position, pole, mu_sun, values["radius_au"], values["j2"])

    return acceleration


def build_barycentric_eih(
    origin,
    planets: Sequence[Perturber],
    mu_sun,
    c,
    ng,
    earth_radius,
    earth_j2,
    sun_barycentric_ephemeris,
    *,
    gr="eih_all",
    solar_j2=None,
):
    """Build a direct barycentric EIH force and heliocentric converter.

    ``planets`` are heliocentric relative-time ephemerides, while
    ``sun_barycentric_ephemeris`` supplies the Sun's barycentric state.  The
    callback has the standard ``(time_days, q, w)`` signature and returns
    ``q''``.  ``to_heliocentric(time, State(q, w))`` applies the Sun ephemeris
    offset.  ``gr`` may be ``"eih_sun"`` or ``"eih_all"``; the optional
    ``"schwarzschild"`` mode preserves a direct solar-only legacy diagnostic.
    """

    if gr not in {"schwarzschild", "eih_sun", "eih_all"}:
        raise ValueError("gr must be schwarzschild, eih_sun or eih_all")
    origin_value = _finite(origin, "origin")
    mu_sun_value = _finite(mu_sun, "mu_sun",)
    c_value = _finite(c, "c")
    if mu_sun_value <= 0.0 or c_value <= 0.0:
        raise ValueError("mu_sun and c must be positive")
    if not isinstance(planets, Sequence):
        planets = tuple(planets)
    bodies = tuple(planets)
    if not bodies or any(not isinstance(body, Perturber) for body in bodies):
        raise TypeError("planets must contain Perturber instances")
    ids = [body.body_id for body in bodies]
    if len(set(ids)) != len(ids) or "10" in ids or "399" not in ids:
        raise ValueError("unique perturbers with Earth body_id 399 and no duplicated Sun are required")
    if not callable(getattr(sun_barycentric_ephemeris, "state_at", None)):
        raise TypeError("sun_barycentric_ephemeris must provide state_at(time)")
    initial_sun = sun_barycentric_ephemeris.state_at(0.0)
    sun_position_0 = _vector(initial_sun.position, "initial Sun position")
    sun_velocity_0 = _vector(initial_sun.velocity, "initial Sun velocity")
    major = tuple(body for body in bodies if not body.body_id.startswith("sb:"))
    masses = (mu_sun_value, *(float(body.mu_au3_d2) for body in major))
    if any(not math.isfinite(mu) or mu <= 0.0 for mu in masses):
        raise ValueError("all EIH source gravitational parameters must be finite and positive")
    earth = next(body for body in bodies if body.body_id == "399")
    if not math.isfinite(float(earth_radius)) or float(earth_radius) <= 0.0 or not math.isfinite(float(earth_j2)):
        raise ValueError("invalid Earth J2 constants")
    solar_term = _solar_j2_term(mu_sun_value, solar_j2)
    ng_term = non_gravitational_acceleration(ng) if ng is not None else None
    ss_term = solar_schwarzschild_acceleration(mu_sun_value, c_value) if gr == "schwarzschild" else None
    cached_time = None
    cached_data = None
    sun_cached_time = 0.0
    sun_cached_state = initial_sun

    def sun_at(time: float):
        nonlocal sun_cached_time, sun_cached_state
        if time != sun_cached_time:
            sun_cached_state = sun_barycentric_ephemeris.state_at(time)
            sun_cached_time = time
        return sun_cached_state

    def prepared_at(time: float):
        nonlocal cached_time, cached_data
        if time == cached_time:
            return cached_data
        sun_state = sun_at(time)
        body_states = tuple(body.ephemeris.state_at(time) for body in bodies)
        major_states = tuple(state for body, state in zip(bodies, body_states)
                             if not body.body_id.startswith('sb:'))
        # A common zero translation retains stable heliocentric differences.
        source_positions = ((0.0, 0.0, 0.0),) + tuple(item.position for item in major_states)
        source_velocities = (_vector(sun_state.velocity, "Sun barycentric velocity"),) + tuple(
            _add(_vector(sun_state.velocity, "Sun barycentric velocity"), _vector(item.velocity, "planet heliocentric velocity"))
            for item in major_states
        )
        prepared = prepare_sources(masses, source_positions, source_velocities)
        cached_time = time
        cached_data = (sun_state, body_states, prepared)
        return cached_data

    def to_heliocentric(time, state: State) -> State:
        local_time = _finite(time, "time")
        if not isinstance(state, State):
            raise TypeError("state must be orbit_baselines.State")
        q = _vector(state.position, "q")
        w = _vector(state.velocity, "w")
        sun_state = sun_at(local_time)
        sun_position = _vector(sun_state.position, "Sun barycentric position")
        sun_velocity = _vector(sun_state.velocity, "Sun barycentric velocity")
        ds = _sub(_sub(sun_position, sun_position_0), _scale(sun_velocity_0, local_time))
        dvs = _sub(sun_velocity, sun_velocity_0)
        return State(_sub(q, ds), _sub(w, dvs))

    def force(time, q, w):
        local_time = _finite(time, "time")
        q_value = _vector(q, "q")
        w_value = _vector(w, "w")
        sun_state, body_states, prepared = prepared_at(local_time)
        ds = _sub(_sub(_vector(sun_state.position, "Sun barycentric position"), sun_position_0), _scale(sun_velocity_0, local_time))
        dvs = _sub(_vector(sun_state.velocity, "Sun barycentric velocity"), sun_velocity_0)
        rh = _sub(q_value, ds)
        vh = _sub(w_value, dvs)
        radius = _norm(rh)
        if radius == 0.0:
            raise ValueError("target is at the Sun and Newtonian force is singular")
        total = _scale(rh, -mu_sun_value / radius**3)
        for body, body_state in zip(bodies, body_states):
            displacement = _sub(body_state.position, rh)
            distance = _norm(displacement)
            if distance == 0.0:
                raise ValueError(f"target coincides with perturber {body.body_id}")
            total = _add(total, _scale(displacement, body.mu_au3_d2 / distance**3))
        target_bary_velocity = _add(w_value, sun_velocity_0)
        if gr == "schwarzschild":
            total = _add(total, ss_term(local_time, rh, vh))
        else:
            pn = eih_correction(
                rh,
                target_bary_velocity,
                prepared,
                c_value,
                outer_indices=(0,) if gr == "eih_sun" else None,
            )
            total = _add(total, pn)
        if ng_term is not None:
            total = _add(total, ng_term(local_time, rh, vh))
        earth_displacement = _sub(rh, body_states[ids.index('399')].position)
        total = _add(total, j2_acceleration(earth_displacement, _FIXED_J2000_POLE, earth.mu_au3_d2, float(earth_radius), float(earth_j2)))
        if solar_term is not None:
            total = _add(total, solar_term(rh))
        return total

    return force, to_heliocentric


__all__ = ["build_barycentric_eih"]

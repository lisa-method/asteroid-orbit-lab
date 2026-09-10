"""Forecast-time development features for choosing a propagation model.

The public :func:`build_features` function deliberately accepts only an
initial test-particle state, a forecast interval, exogenous ephemerides and
numerical constants.  It does not accept reference states, event metadata,
object identifiers or split labels.

The trajectory used here is B2 (Sun-only two-body RK4).  Planetary, solar GR
and small-body accelerations are evaluated on that B2 trajectory as cheap
diagnostics.  For an acceleration term ``a(t)`` the reported proxy is

    ``AU_km * integral_0^H (H - t) * ||a(t)|| dt``.

The integral is evaluated with a trapezoidal rule on daily B2 nodes (with an
exact final ``H`` node).  Planet ``a(t)`` is the heliocentric direct-minus-
indirect acceleration.  The combined planet proxy uses the norm of the vector
sum of all planet terms; per-body proxies use each term separately.  These
quantities ignore the state transition and are therefore diagnostics, not
trajectory-error bounds or encounter guarantees.
"""

from __future__ import annotations

import math
import time
from typing import Iterable

from encounter_screening import scan_body, uniform_times
from orbit_baselines import (
    State,
    add,
    norm,
    propagate_variable_step,
    scale,
    subtract,
    two_body_acceleration,
)
from planetary_dynamics import (
    Perturber,
    encounter_aware_step_selector,
    solar_schwarzschild_acceleration,
)


_ZERO: tuple[float, float, float] = (0.0, 0.0, 0.0)


def _finite(value: object, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _validate_state(state: State) -> None:
    for component in (*state.position, *state.velocity):
        _finite(component, "initial state")
    if norm(state.position) == 0.0:
        raise ValueError("initial position must be nonzero")


def _heliocentric_perturbation(body: Perturber, asteroid_position: tuple[float, float, float], epoch_jd: float) -> tuple[float, float, float]:
    """Return one body's direct-minus-indirect heliocentric acceleration."""

    body_state = body.ephemeris.state_at(epoch_jd)
    mu = _finite(body.mu_au3_d2, f"mu for {body.body_id}")
    if mu == 0.0:
        return _ZERO
    displacement = subtract(body_state.position, asteroid_position)
    distance = norm(displacement)
    body_radius = norm(body_state.position)
    if distance == 0.0 or body_radius == 0.0:
        raise ValueError(f"singular heliocentric perturbation for body {body.body_id}")
    direct = scale(displacement, mu / distance**3)
    indirect = scale(body_state.position, -mu / body_radius**3)
    return add(direct, indirect)


def _weighted_trapezoid(times: list[float], values: list[float], horizon_days: float, au_km: float) -> float:
    """Integrate ``(H-t)*value`` on the supplied causal time grid."""

    if len(times) != len(values) or len(times) < 2:
        raise ValueError("at least two matching quadrature nodes are required")
    total = 0.0
    for left_t, right_t, left_value, right_value in zip(times, times[1:], values, values[1:]):
        width = right_t - left_t
        left_weight = (horizon_days - left_t) * left_value
        right_weight = (horizon_days - right_t) * right_value
        total += 0.5 * width * (left_weight + right_weight)
    return total * au_km


def _max_ratio(accelerations: Iterable[tuple[float, float, float]], solar_values: list[float]) -> float:
    ratios = []
    for acceleration, solar in zip(accelerations, solar_values):
        ratios.append(norm(acceleration) / solar if solar > 0.0 else 0.0)
    return max(ratios, default=0.0)


def build_features(
    initial: State,
    start_jd: float,
    horizon_days: float,
    context: dict,
    config: dict,
) -> dict:
    """Build causal B2 geometry and force-proxy features for one horizon.

    ``context`` follows the existing model-sufficiency loader contract and
    contains ``mu``, ``au_km``, ``day_s``, ``c_au_d``, ``planets`` and
    ``small``.  ``planets`` and ``small`` are tuples of :class:`Perturber`.
    ``config`` supplies ``default_step_days`` and ``step_scale``.  Every
    ephemeris query is restricted to ``[start_jd, start_jd + horizon_days]``;
    no future state beyond the requested horizon is used as a feature.
    """

    started = time.perf_counter()
    if not isinstance(initial, State):
        raise TypeError("initial must be an orbit_baselines.State")
    _validate_state(initial)
    start_jd = _finite(start_jd, "start_jd")
    horizon_days = _finite(horizon_days, "horizon_days")
    if horizon_days <= 0.0:
        raise ValueError("horizon_days must be positive")
    if not isinstance(context, dict) or not isinstance(config, dict):
        raise TypeError("context and config must be dictionaries")

    try:
        mu = _finite(context["mu"], "context.mu")
        au_km = _finite(context["au_km"], "context.au_km")
        day_s = _finite(context["day_s"], "context.day_s")
        c_au_d = _finite(context["c_au_d"], "context.c_au_d")
        planets = tuple(context["planets"])
        small = tuple(context["small"])
    except KeyError as exc:
        raise ValueError(f"context is missing {exc.args[0]!r}") from exc
    except TypeError as exc:
        raise ValueError("context planets and small must be iterable") from exc
    for value, label in ((mu, "context.mu"), (au_km, "context.au_km"), (day_s, "context.day_s"), (c_au_d, "context.c_au_d")):
        if value <= 0.0:
            raise ValueError(f"{label} must be positive")
    default_step_days = _finite(config.get("default_step_days", 0.0625), "config.default_step_days")
    step_scale = _finite(config.get("step_scale", 1.0), "config.step_scale")
    if default_step_days <= 0.0 or step_scale <= 0.0:
        raise ValueError("step sizes must be positive")
    if len({body.body_id for body in planets}) != len(planets):
        raise ValueError("planet body_id values must be unique")
    if len({body.body_id for body in small}) != len(small):
        raise ValueError("small-body body_id values must be unique")
    if {body.body_id for body in planets} & {body.body_id for body in small}:
        raise ValueError("planet and small-body body_id values must be disjoint")
    for body in (*planets, *small):
        try:
            body_mu = _finite(body.mu_au3_d2, f"mu for {body.body_id}")
        except AttributeError as exc:
            raise TypeError("context bodies must be Perturber-like objects") from exc
        if body_mu < 0.0:
            raise ValueError(f"mu for {body.body_id} must be nonnegative")

    stop_jd = start_jd + horizon_days
    for body in (*planets, *small):
        try:
            covered = body.ephemeris.epochs_jd_tdb[0] <= start_jd <= stop_jd <= body.ephemeris.epochs_jd_tdb[-1]
        except AttributeError as exc:
            raise TypeError("context bodies must be Perturber-like objects") from exc
        if not covered:
            raise ValueError(f"ephemeris for body {body.body_id} does not cover forecast window")

    times = uniform_times(horizon_days, 1.0)
    selector = encounter_aware_step_selector((), start_jd, default_step_days=default_step_days, scale_factor=step_scale)
    propagation_started = time.perf_counter()
    predictions, steps = propagate_variable_step(
        initial,
        times,
        two_body_acceleration(mu),
        selector,
    )
    propagation_seconds = time.perf_counter() - propagation_started

    geometry_started = time.perf_counter()
    per_body: dict[str, dict] = {}
    for body in planets:
        planet_states = [body.ephemeris.state_at(start_jd + t) for t in times]
        per_body[body.body_id] = scan_body(
            times,
            predictions,
            planet_states,
            body,
            mu,
            au_km,
            day_s,
            refine=True,
        )
    geometry_seconds = time.perf_counter() - geometry_started

    quadrature_started = time.perf_counter()
    solar_magnitudes = [mu / norm(state.position) ** 2 for state in predictions]
    planet_terms: dict[str, list[tuple[float, float, float]]] = {body.body_id: [] for body in planets}
    combined_planet_terms: list[tuple[float, float, float]] = []
    for t, state in zip(times, predictions):
        combined = _ZERO
        for body in planets:
            term = _heliocentric_perturbation(body, state.position, start_jd + t)
            planet_terms[body.body_id].append(term)
            combined = add(combined, term)
        combined_planet_terms.append(combined)

    gr_acceleration = solar_schwarzschild_acceleration(mu, c_au_d)
    gr_terms = [gr_acceleration(t, state.position, state.velocity) for t, state in zip(times, predictions)]
    small_terms: list[tuple[float, float, float]] = []
    for t, state in zip(times, predictions):
        combined = _ZERO
        for body in small:
            combined = add(combined, _heliocentric_perturbation(body, state.position, start_jd + t))
        small_terms.append(combined)

    planet_proxy_by_body = {
        body_id: _weighted_trapezoid(times, [norm(term) for term in terms], horizon_days, au_km)
        for body_id, terms in planet_terms.items()
    }
    planet_proxy_km = _weighted_trapezoid(times, [norm(term) for term in combined_planet_terms], horizon_days, au_km)
    gr_proxy_km = _weighted_trapezoid(times, [norm(term) for term in gr_terms], horizon_days, au_km)
    small_body_proxy_km = _weighted_trapezoid(times, [norm(term) for term in small_terms], horizon_days, au_km)
    max_eta_by_body = {
        body_id: _max_ratio(terms, solar_magnitudes) for body_id, terms in planet_terms.items()
    }
    combined_planet_max_eta = _max_ratio(combined_planet_terms, solar_magnitudes)
    min_solar_index = min(range(len(predictions)), key=lambda index: norm(predictions[index].position))
    min_solar_distance_au = norm(predictions[min_solar_index].position)
    quadrature_seconds = time.perf_counter() - quadrature_started
    # Add max eta and the integrated diagnostic beside the existing scan_body
    # geometry.  Keep scan_body's Moon rho semantics unchanged (None for body
    # id 301, whose heliocentric Hill proxy is not meaningful here).
    for body_id, geometry in per_body.items():
        geometry["max_eta_helio"] = max_eta_by_body[body_id]
        geometry["planet_proxy_km"] = planet_proxy_by_body[body_id]

    caveats = (
        "B2 is a Sun-only recursive RK4 forecast; planets, GR and SB16 are diagnostic terms evaluated on its path.",
        "The weighted acceleration proxies ignore state transition and are not error bounds or encounter guarantees.",
        "Planet minima use daily-node cubic Hermite geometry; min_solar_distance_au and max eta are sampled daily extrema, not continuous extrema.",
        "Daily sampling and B2 drift can miss or mislocate behavior during strong encounters.",
    )
    result = {
        "horizon_days": horizon_days,
        "min_solar_distance_au": min_solar_distance_au,
        "min_solar_time_days": times[min_solar_index],
        "planet_proxy_km": planet_proxy_km,
        "combined_planet_proxy_km": planet_proxy_km,
        "gr_proxy_km": gr_proxy_km,
        "small_body_proxy_km": small_body_proxy_km,
        "max_eta_by_body": max_eta_by_body,
        "planet_max_eta": combined_planet_max_eta,
        "combined_planet_max_eta": combined_planet_max_eta,
        "per_body": per_body,
        "geometry": per_body,
        "planet_count": len(planets),
        "small_body_count": len(small),
        "daily_sample_count": len(times),
        "steps": steps,
        "rk4_steps": steps,
        "runtime_seconds": 0.0,
        "propagation_seconds": propagation_seconds,
        "geometry_seconds": geometry_seconds,
        "quadrature_seconds": quadrature_seconds,
        "proxy_formula": "AU_km * trapezoid_integral[(H-t) * ||a(t)|| dt] on daily B2 nodes; planet combined term is norm(sum_i a_i(t))",
        "scientific_caveats": caveats,
    }
    # Include per-body augmentation and result assembly in the reported total.
    result["runtime_seconds"] = time.perf_counter() - started
    return result

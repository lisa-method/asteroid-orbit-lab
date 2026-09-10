"""Forecast-time encounter features from recursive B2 or restricted B3 rollouts.

No function here accepts reference asteroid rows, event dates, model-sufficiency
labels or fitted parameters. Relative geometry uses simultaneous states.
"""

from __future__ import annotations

import math
import time
from bisect import bisect_right

from encounter_geometry import segment_closest_approach
from orbit_baselines import State, add, norm, propagate_variable_step, scale, subtract, two_body_acceleration
from planetary_dynamics import (
    EphemerisInterpolator,
    Perturber,
    encounter_aware_step_selector,
    restricted_n_body_acceleration,
)


def uniform_times(horizon_days: float, cadence_days: float) -> list[float]:
    if not all(math.isfinite(x) and x > 0 for x in (horizon_days, cadence_days)):
        raise ValueError("Horizon and cadence must be finite and positive")
    count = math.floor(horizon_days / cadence_days)
    times = [i * cadence_days for i in range(count + 1)]
    if times[-1] < horizon_days:
        times.append(horizon_days)
    return times


def _relative(asteroid: State, planet: State) -> State:
    return State(subtract(asteroid.position, planet.position), subtract(asteroid.velocity, planet.velocity))


def scan_body(times_days: list[float], asteroid_states: list[State], planet_states: list[State],
              body: Perturber, mu_sun: float, au_km: float, day_s: float, *, refine: bool) -> dict:
    """Constrained-window minimum of the sampled or Hermite relative trajectory.

The caller supplies a common knot grid. A refined result is a minimum of that
interpolant, not a guarantee for the true dynamics between the samples.
"""
    if len(times_days) < 2 or len(times_days) != len(asteroid_states) or len(times_days) != len(planet_states):
        raise ValueError("At least two matching times and states are required")
    if not all(math.isfinite(t) for t in times_days) or any(b <= a for a, b in zip(times_days, times_days[1:])):
        raise ValueError("Times must be finite and strictly increasing")
    relative = [_relative(a, p) for a, p in zip(asteroid_states, planet_states)]
    index = min(range(len(relative)), key=lambda i: norm(relative[i].position))
    best_time, best_state = times_days[index], relative[index]
    best_distance = norm(best_state.position)
    if refine:
        for i, (left, right) in enumerate(zip(relative, relative[1:])):
            duration = times_days[i+1] - times_days[i]
            fraction, candidate = segment_closest_approach(left, right, duration)
            distance = norm(candidate.position)
            epoch = times_days[i] + fraction * duration
            if distance < best_distance or (distance == best_distance and epoch < best_time):
                best_time, best_state, best_distance = epoch, candidate, distance
    # The same common-grid cubic is used for the planet and relative trajectory.
    planet = EphemerisInterpolator(tuple(times_days), tuple(planet_states)).state_at(best_time)
    asteroid_position = add(planet.position, best_state.position)
    solar_radius, planet_radius = norm(asteroid_position), norm(planet.position)
    eta = None
    if best_distance > 0 and solar_radius > 0 and planet_radius > 0:
        direct = scale(best_state.position, -body.mu_au3_d2 / best_distance**3)
        indirect = scale(planet.position, -body.mu_au3_d2 / planet_radius**3)
        eta = norm(add(direct, indirect)) / (mu_sun / solar_radius**2)
    # Heliocentric Hill proxy is not assigned to the Earth-bound Moon.
    hill = planet_radius * (body.mu_au3_d2 / (3 * mu_sun))**(1/3) if body.body_id != "301" else None
    bracket = min(len(times_days)-2, bisect_right(times_days, best_time)-1)
    return {"body_id": body.body_id, "body_name": body.name,
            "time_days": best_time, "distance_au": best_distance, "distance_km": best_distance * au_km,
            "relative_speed_km_s": norm(best_state.velocity) * au_km / day_s,
            "eta_helio_at_closest": eta,
            "rho_sun_proxy_at_closest": best_distance / hill if hill else None,
            "boundary_minimum": best_time == times_days[0] or best_time == times_days[-1],
            "zero_separation": best_distance == 0,
            "bracket_gap_days_at_minimum": times_days[bracket+1] - times_days[bracket],
            "sample_count": len(times_days), "max_knot_gap_days": max(b-a for a,b in zip(times_days,times_days[1:]))}


def forecast_encounters(initial: State, start_jd: float, horizon_days: float,
                        perturbers: tuple[Perturber, ...], mu_sun: float, *,
                        cadence_days: float, refine: bool, default_step_days: float,
                        step_scale: float, au_km: float, day_s: float,
                        force_model: str = "B2", sampling: str = "uniform",
                        refinement_distance_au: float = 0.01) -> dict:
    """Forecast simultaneous asteroid--perturber minima from forecast inputs.

    ``B2`` is the historical solar two-body rollout.  ``B3`` adds the
    heliocentric restricted N-body terms from the supplied ephemerides.
    ``encounter_steps`` augments the nominal output grid with accepted RK4
    endpoints near any supplied perturber.  The distance threshold is a guard
    for retaining candidate knots; it is not a rigorous guarantee that every
    close approach is resolved.
    """
    started = time.perf_counter()
    for value in (*initial.position, *initial.velocity, start_jd, mu_sun, au_km, day_s, default_step_days, step_scale):
        if not math.isfinite(value):
            raise ValueError("Forecast inputs must be finite")
    if min(mu_sun, au_km, day_s, default_step_days, step_scale) <= 0 or norm(initial.position) == 0:
        raise ValueError("Positive constants and a nonzero initial radius are required")
    if force_model not in ("B2", "B3"):
        raise ValueError("force_model must be 'B2' or 'B3'")
    if sampling not in ("uniform", "encounter_steps"):
        raise ValueError("sampling must be 'uniform' or 'encounter_steps'")
    try:
        threshold_is_valid = math.isfinite(refinement_distance_au)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("refinement_distance_au must be finite and positive") from exc
    if not threshold_is_valid or refinement_distance_au <= 0.0:
        raise ValueError("refinement_distance_au must be finite and positive")
    if len({p.body_id for p in perturbers}) != len(perturbers) or not perturbers:
        raise ValueError("Provide a non-empty set of unique perturbers")
    times = uniform_times(horizon_days, cadence_days)
    nominal_output_samples = len(times)
    for body in perturbers:
        if not math.isfinite(body.mu_au3_d2) or body.mu_au3_d2 <= 0:
            raise ValueError("Perturber mass parameters must be finite and positive")
        if not body.ephemeris.epochs_jd_tdb[0] <= start_jd < start_jd + horizon_days <= body.ephemeris.epochs_jd_tdb[-1]:
            raise ValueError("Planetary ephemeris does not cover the forecast window")
    force_perturbers = perturbers if force_model == "B3" else ()
    acceleration = (
        restricted_n_body_acceleration(mu_sun, perturbers, start_jd)
        if force_model == "B3"
        else two_body_acceleration(mu_sun)
    )
    selector = encounter_aware_step_selector(
        force_perturbers,
        start_jd,
        default_step_days=default_step_days,
        scale_factor=step_scale,
    )

    # A dictionary makes coincident step/output endpoints unique while the
    # final sorted order keeps the merged knot grid deterministic.
    retained_steps: dict[float, State] = {}

    if sampling == "encounter_steps":
        def retain_nearby_step(
            left_time: float,
            left_state: State,
            right_time: float,
            right_state: State,
        ) -> None:
            left_planets = tuple(
                body.ephemeris.state_at(start_jd + left_time).position
                for body in perturbers
            )
            right_planets = tuple(
                body.ephemeris.state_at(start_jd + right_time).position
                for body in perturbers
            )
            near_left = any(
                norm(subtract(left_state.position, planet_position)) <= refinement_distance_au
                for planet_position in left_planets
            )
            near_right = any(
                norm(subtract(right_state.position, planet_position)) <= refinement_distance_au
                for planet_position in right_planets
            )
            if near_left or near_right:
                retained_steps[left_time] = left_state
                retained_steps[right_time] = right_state

        predictions, steps = propagate_variable_step(
            initial,
            times,
            acceleration,
            selector,
            on_step=retain_nearby_step,
        )
        merged = {time: state for time, state in zip(times, predictions)}
        merged.update(retained_steps)
        times = sorted(merged)
        predictions = [merged[time] for time in times]
    else:
        predictions, steps = propagate_variable_step(initial, times, acceleration, selector)
    propagation_seconds = time.perf_counter() - started
    minima = []
    for body in perturbers:
        planets = [body.ephemeris.state_at(start_jd + t) for t in times]
        minima.append(scan_body(times, predictions, planets, body, mu_sun, au_km, day_s, refine=refine))
    total = time.perf_counter() - started
    return {"minima": minima, "runtime_seconds": total, "propagation_seconds": propagation_seconds,
            "screening_seconds": total - propagation_seconds, "rk4_steps": steps,
            "force_evaluations": 4 * steps, "output_samples": len(times),
            "nominal_output_samples": nominal_output_samples,
            "sampling": sampling, "force_model": force_model}

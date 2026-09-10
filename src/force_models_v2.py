"""Composable forecast-time forces; the development30 v1 implementation is frozen.

Model names are labels only. Physical switches and available input parameters
determine the acceleration used throughout a rollout. Reference trajectories,
object identity and encounter catalogue labels are not inputs to this API.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
import math
import time
from typing import Any, Mapping

from earth_oblateness import earth_j2_acceleration
from encounter_screening import uniform_times
from ng_inputs_v2 import NGInput
from orbit_baselines import State, propagate_variable_step
from planetary_dynamics import (
    combine_accelerations, encounter_aware_step_selector,
    non_gravitational_acceleration, restricted_n_body_acceleration,
    solar_schwarzschild_acceleration,
)


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(f"{name} must be finite" + (" and positive" if positive else ""))
    return result


@dataclass(frozen=True)
class ForceModelSpec:
    model_id: str
    planets: bool
    solar_gr: bool
    small_bodies: bool
    earth_j2: bool
    non_grav: str

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise ValueError("model_id must be a nonempty label")
        for name in ("planets", "solar_gr", "small_bodies", "earth_j2"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be an explicit boolean")
        if self.non_grav not in ("off", "if_available", "required"):
            raise ValueError("non_grav must be off, if_available or required")
        if self.earth_j2 and not self.planets:
            raise ValueError("Earth J2 requires the planetary monopole model")

    @classmethod
    def from_mapping(cls, model: Mapping[str, Any]) -> ForceModelSpec:
        expected = {field.name for field in fields(cls)}
        if set(model) != expected:
            raise ValueError(f"model fields must be exactly {sorted(expected)}")
        return cls(**dict(model))


def build_force_model_v2(
    start_jd: float, model: Mapping[str, Any] | ForceModelSpec,
    context: Mapping[str, Any], config: Mapping[str, Any], *, ng: NGInput | None = None,
):
    """Return a force callback, encounter-step bodies, and provenance metadata."""
    start = _number(start_jd, "start_jd")
    spec = model if isinstance(model, ForceModelSpec) else ForceModelSpec.from_mapping(model)
    if ng is not None and not isinstance(ng, NGInput):
        raise TypeError("ng must be a validated NGInput")
    ng_input = ng if ng is not None else NGInput()
    mu = _number(context["mu"], "mu", positive=True)
    bodies = tuple(context["planets"]) if spec.planets else ()
    if spec.small_bodies:
        bodies += tuple(context["small"])
    if len({body.body_id for body in bodies}) != len(bodies):
        raise ValueError("duplicate perturber would double-count its gravity")
    for body in bodies:
        _number(body.mu_au3_d2, f"mu of {body.body_id}", positive=True)
    terms = [restricted_n_body_acceleration(mu, bodies, start)]
    applied = ["solar_monopole"]
    if spec.planets:
        applied.append("planetary_monopoles")
    if spec.small_bodies:
        applied.append("small_body_monopoles")
    if spec.solar_gr:
        terms.append(solar_schwarzschild_acceleration(
            mu, _number(context["c_au_d"], "c_au_d", positive=True)))
        applied.append("solar_schwarzschild")
    if spec.earth_j2:
        earth = next((body for body in bodies if body.body_id == "399"), None)
        if earth is None:
            raise ValueError("Earth J2 requires Earth centre body 399")
        constants = config["earth_j2"]
        radius = _number(constants["reference_radius_km"], "Earth radius", positive=True)
        au = _number(context["au_km"], "au_km", positive=True)
        terms.append(earth_j2_acceleration(
            earth, start, radius / au, _number(constants["j2"], "Earth J2", positive=True),
            pole_model=constants["pole_model"]))
        applied.append("earth_j2")
    status = ng_input.status(start)
    if spec.non_grav == "required" and status != "available":
        raise ValueError(f"NG parameters required but {status}")
    ng_applied = spec.non_grav != "off" and status == "available"
    if ng_applied:
        terms.append(non_gravitational_acceleration(ng_input.parameters))
        applied.append("non_gravitational")
    limitations = []
    if status != "available":
        limitations.append(f"ng_{status}")
    elif ng_applied and ng_input.sigma_au_d2 is None:
        limitations.append("ng_uncertainty_unknown")
    return combine_accelerations(*terms), bodies, {
        "model_id": spec.model_id,
        "applied_terms": applied,
        "perturber_ids": [body.body_id for body in bodies],
        "ng_policy": spec.non_grav,
        "ng_status": status,
        "ng_applied": ng_applied,
        "ng_source": ng_input.source,
        "ng_source_sha256": ng_input.source_sha256,
        "ng_availability_basis": ng_input.availability_basis,
        "ng_available_from_jd_tdb": ng_input.available_from_jd_tdb,
        "ng_sigma_au_d2": list(ng_input.sigma_au_d2) if ng_applied and ng_input.sigma_au_d2 is not None else None,
        "uncertainty_propagated": False,
        "accuracy_guaranteed": False,
        "limitations": limitations,
    }


def forecast_candidate_v2(
    initial: State, start_jd: float, horizon_days: float,
    model: Mapping[str, Any] | ForceModelSpec, context: Mapping[str, Any],
    config: Mapping[str, Any], step_scale: float, *, ng: NGInput | None = None,
) -> tuple[list[State], dict[str, Any]]:
    """Propagate one fixed force specification from causal inputs only.

Every RK4 stage evaluates all enabled terms at the predicted state. Availability
is decided once at the forecast start, including for future rollout epochs.
Uncertainty metadata is retained; this API returns a nominal trajectory.
"""
    started = time.perf_counter()
    if not isinstance(initial, State):
        raise TypeError("initial must be an orbit_baselines.State")
    for vector in (initial.position, initial.velocity):
        if len(vector) != 3:
            raise ValueError("initial position and velocity must have three components")
        for component in vector:
            _number(component, "initial state component")
    start = _number(start_jd, "start_jd")
    horizon = _number(horizon_days, "horizon_days", positive=True)
    scale = _number(step_scale, "step_scale", positive=True)
    default_step = _number(config["default_step_days"], "default_step_days", positive=True)
    acceleration, bodies, force_metadata = build_force_model_v2(start, model, context, config, ng=ng)
    for body in bodies:
        epochs = body.ephemeris.epochs_jd_tdb
        if not epochs[0] <= start < start + horizon <= epochs[-1]:
            raise ValueError(f"ephemeris for {body.body_id} does not cover forecast")
    selector = encounter_aware_step_selector(bodies, start, default_step_days=default_step, scale_factor=scale)
    targets = uniform_times(horizon, 1.0)
    accepted = {0.0: initial}
    snapshots: dict[str, dict[str, Any]] = {}

    def on_step(left_time, left_state, right_time, right_state):
        accepted[float(left_time)] = left_state
        accepted[float(right_time)] = right_state

    def on_output(epoch, _state, steps):
        snapshots[str(float(epoch))] = {"runtime_seconds": time.perf_counter() - started, "rk4_steps": int(steps)}

    predictions, steps = propagate_variable_step(initial, targets, acceleration, selector,
                                                  on_output=on_output, on_step=on_step)
    return predictions, {
        "horizon_days": horizon,
        "daily_times_days": targets,
        "snapshots": snapshots,
        "accepted_states": [{"time_days": t, "state": accepted[t]} for t in sorted(accepted)],
        "rk4_steps": steps,
        "runtime_seconds": time.perf_counter() - started,
        "model_id": force_metadata["model_id"],
        "step_scale": scale,
        "force_metadata": force_metadata,
    }

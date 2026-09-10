"""Causal daily forecast API using the shared relative-time force model.

The contract is Sun-centred ICRF, TDB, AU, AU/day, and AU/day**2.  Planet
ephemerides must already be relative to ``origin_jd_tdb`` in days.  This module
accepts no reference states and has no object-identity routing or selector.
It is an API only; callers choose and record all numerical settings explicitly.
"""

from __future__ import annotations

from dataclasses import asdict
import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from independent_rk import integrate_dopri54
from ng_inputs_v2 import NGInput
from orbit_baselines import State
from planetary_dynamics import Perturber
from precise_rk4 import advance as advance_rk4
from planetary_dynamics import encounter_aware_step_selector
from relative_force_model import build_relative_force_model
from relative_time_dynamics import RelativeEphemerisInterpolator


def _finite(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(number) or (positive and number <= 0.0):
        raise ValueError(f"{name} must be finite" + (" and positive" if positive else ""))
    return number


def _state(value: State, name: str = "initial") -> State:
    if not isinstance(value, State):
        raise TypeError(f"{name} must be an orbit_baselines.State")
    if len(value.position) != 3 or len(value.velocity) != 3:
        raise ValueError(f"{name} must contain six finite components")
    values = (*value.position, *value.velocity)
    if any(isinstance(component, bool) or not math.isfinite(float(component)) for component in values):
        raise ValueError(f"{name} must contain six finite components")
    return State(tuple(float(component) for component in value.position), tuple(float(component) for component in value.velocity))  # type: ignore[arg-type]


def _daily_times(horizon_days: float) -> tuple[float, ...]:
    whole_days = int(math.floor(horizon_days))
    values = [float(day) for day in range(0, whole_days + 1)]
    if values[-1] != horizon_days:
        values.append(float(horizon_days))
    return tuple(values)


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _validate_planets(planets: Sequence[Perturber], horizon_days: float) -> tuple[Perturber, ...]:
    bodies = tuple(planets)
    if not bodies:
        raise ValueError("planets must contain Earth and all enabled relative perturbers")
    ids = [body.body_id for body in bodies if isinstance(body, Perturber)]
    if len(ids) != len(bodies):
        raise TypeError("planets must contain Perturber instances")
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate relative perturber body_id")
    for body in bodies:
        if not isinstance(body.ephemeris, RelativeEphemerisInterpolator):
            raise TypeError("planets must use RelativeEphemerisInterpolator ephemerides")
        if body.ephemeris.epochs_days[0] > 0.0 or body.ephemeris.epochs_days[-1] < horizon_days:
            raise ValueError(f"relative ephemeris for {body.body_id} does not cover forecast")
    if "399" not in ids:
        raise ValueError("relative forecast requires Earth body_id 399 for the mandatory J2 baseline")
    return bodies


def _settings(config: Mapping[str, Any], solver: str) -> Mapping[str, Any]:
    candidate = config.get(solver)
    if isinstance(candidate, Mapping):
        return candidate
    # A flat solver-only config is useful for small callers, while force
    # configuration remains explicit in its own ``earth_*`` mappings.
    required = {"rtol", "atol_position", "atol_velocity", "max_step"} if solver == "dopri54" else {"default_step_days", "step_scale"}
    if required.issubset(config):
        return config
    raise ValueError(f"config must provide explicit {solver} numerical settings")


def _flat(state: State) -> list[float]:
    return [*state.position, *state.velocity]


def forecast_precise(
    initial: State,
    origin_jd_tdb: float,
    horizon_days: float,
    planets: Sequence[Perturber],
    constants: Mapping[str, Any],
    config: Mapping[str, Any],
    ng: NGInput | None = None,
    *,
    solver: str = "dopri54",
    enabled: Iterable[int | str] | Mapping[str, Any] | None = None,
    enabled_j3: bool | None = None,
    enabled_j4: bool | None = None,
) -> tuple[list[State], dict[str, Any]]:
    """Forecast daily states and retain native accepted-endpoint traces.

    ``constants`` must explicitly contain ``mu_sun_au3_d2`` and
    ``speed_of_light_au_d``.  ``config`` must contain the explicit mandatory
    ``earth_j2`` mapping, optional ``earth_zonals`` mapping, and solver
    settings either under ``dopri54``/``compensated_rk4`` or as a flat mapping.
    DP tolerances and RK4 step size are never selected from a hidden default.
    """
    state = _state(initial)
    origin = _finite(origin_jd_tdb, "origin_jd_tdb")
    horizon = _finite(horizon_days, "horizon_days", positive=True)
    if solver not in {"dopri54", "compensated_rk4"}:
        raise ValueError("solver must be dopri54 or compensated_rk4")
    if not isinstance(constants, Mapping) or not isinstance(config, Mapping):
        raise TypeError("constants and config must be mappings")
    if "mu_sun_au3_d2" not in constants or "speed_of_light_au_d" not in constants:
        raise ValueError("constants require explicit mu_sun_au3_d2 and speed_of_light_au_d")
    if "earth_j2" not in config or not isinstance(config["earth_j2"], Mapping):
        raise ValueError("config requires explicit earth_j2 mapping")
    bodies = _validate_planets(planets, horizon)
    targets = _daily_times(horizon)
    config_enabled = config.get("enabled")
    if enabled is not None and config_enabled is not None and enabled != config_enabled:
        raise ValueError("enabled argument conflicts with config['enabled']")
    resolved_enabled = enabled if enabled is not None else config_enabled
    resolved_flags: dict[str, bool] = {}
    for name, argument in (("enabled_j3", enabled_j3), ("enabled_j4", enabled_j4)):
        configured = config.get(name)
        if argument is not None:
            if type(argument) is not bool:
                raise ValueError(f"{name} must be boolean or None")
            if configured is not None and configured != argument:
                raise ValueError(f"{name} argument conflicts with config['{name}']")
            resolved_flags[name] = argument
        elif configured is None:
            resolved_flags[name] = False
        else:
            if type(configured) is not bool:
                raise ValueError(f"config['{name}'] must be boolean")
            resolved_flags[name] = configured
    force, force_metadata = build_relative_force_model(
        origin,
        bodies,
        _finite(constants["mu_sun_au3_d2"], "mu_sun_au3_d2", positive=True),
        _finite(constants["speed_of_light_au_d"], "speed_of_light_au_d", positive=True),
        ng,
        config["earth_j2"],
        config.get("earth_zonals"),
        enabled=resolved_enabled,
        enabled_j3=resolved_flags["enabled_j3"],
        enabled_j4=resolved_flags["enabled_j4"],
        ng_policy=str(config.get("ng_policy", "available")),
    )
    settings = dict(_settings(config, solver))
    native_trace: list[list[Any]] = []
    if solver == "dopri54":
        required = ("rtol", "atol_position", "atol_velocity", "max_step")
        for key in required:
            if key not in settings:
                raise ValueError(f"dopri54 settings require {key}")
        position_atol = _finite(settings["atol_position"], "atol_position")
        velocity_atol = _finite(settings["atol_velocity"], "atol_velocity")
        relative_tol = _finite(settings["rtol"], "rtol")
        minimum_step = _finite(settings.get("min_step", 1.0e-10), "min_step", positive=True)
        maximum_step = _finite(settings["max_step"], "max_step", positive=True)
        max_steps = _positive_int(settings.get("max_steps", 1_000_000), "max_steps")
        raw_breakpoints = tuple(_finite(value, "breakpoint") for value in settings.get("breakpoints", ()))
        predictions = [state]
        native_trace = [[0.0, _flat(state)]]
        current = state
        segment_start = 0.0
        aggregate: dict[str, Any] = {
            "status": "finished", "accepted_steps": 0, "rejected_steps": 0,
            "attempted_steps": 0, "function_evaluations": 0,
            "min_accepted_step": math.inf, "max_accepted_step": 0.0,
            "final_step": 0.0, "max_error_norm": 0.0,
            "breakpoints": raw_breakpoints, "epoch": 0.0, "final_time": horizon,
            "segments": 0,
        }
        # A separate call per daily interval keeps every requested daily state
        # an exact endpoint while preserving the solver's local-time arithmetic.
        for target in targets[1:]:
            duration = target - segment_start
            local_breakpoints = tuple(value - segment_start for value in raw_breakpoints if segment_start < value < target)
            base_time = segment_start
            remaining_budget = max_steps - int(aggregate["attempted_steps"])
            if remaining_budget <= 0:
                raise RuntimeError("maximum DP step budget exceeded")
            result = integrate_dopri54(
                lambda local, position, velocity, base=base_time: force(base + local, position, velocity),
                0.0,
                _flat(current),
                (duration,),
                atol_position=position_atol,
                atol_velocity=velocity_atol,
                rtol=relative_tol,
                min_step=minimum_step,
                max_step=maximum_step,
                max_steps=remaining_budget,
                breakpoints=local_breakpoints,
            )
            sample = result.samples[-1][1]
            current = State(tuple(sample[:3]), tuple(sample[3:]))  # type: ignore[arg-type]
            predictions.append(current)
            native_trace.extend([[base_time + float(time_days), list(values)] for time_days, values in result.accepted_endpoints[1:]])
            for key in ("accepted_steps", "rejected_steps", "attempted_steps", "function_evaluations"):
                aggregate[key] += int(result.stats[key])
            aggregate["min_accepted_step"] = min(aggregate["min_accepted_step"], float(result.stats["min_accepted_step"]))
            aggregate["max_accepted_step"] = max(aggregate["max_accepted_step"], float(result.stats["max_accepted_step"]))
            aggregate["max_error_norm"] = max(aggregate["max_error_norm"], float(result.stats["max_error_norm"]))
            aggregate["final_step"] = float(result.stats["final_step"])
            aggregate["segments"] += 1
            segment_start = target
        if aggregate["accepted_steps"] == 0:
            aggregate["min_accepted_step"] = 0.0
        solver_stats = aggregate
    else:
        if "default_step_days" not in settings or "step_scale" not in settings:
            raise ValueError("compensated_rk4 settings require default_step_days and step_scale")
        default_step_days = _finite(settings["default_step_days"], "default_step_days", positive=True)
        step_scale = _finite(settings["step_scale"], "step_scale", positive=True)
        max_steps = _positive_int(settings.get("max_steps", 1_000_000), "max_steps")
        native_trace = [[0.0, _flat(state)]]

        def on_step(_left_time: float, _left_state: State, right_time: float, right_state: State) -> None:
            native_trace.append([float(right_time), _flat(right_state)])

        predictions, stats = advance_rk4(
            state,
            targets,
            force,
            encounter_aware_step_selector(bodies, 0.0, default_step_days=default_step_days, scale_factor=step_scale),
            compensated=True,
            on_step=on_step,
            max_steps=max_steps,
        )
        solver_stats = asdict(stats)
        settings["compensated"] = True
        settings["default_step_days"] = default_step_days
        settings["step_scale"] = step_scale

    metadata = {
        "schema_version": 1,
        "solver": solver,
        "origin_jd_tdb": origin,
        "horizon_days": horizon,
        "daily_times_days": list(targets),
        "daily_epochs_jd_tdb": [origin + time_days for time_days in targets],
        "accepted_time_basis": "relative_days_since_origin",
        "native_trace": native_trace,
        "accepted_endpoints": native_trace,
        "solver_stats": solver_stats,
        "numerical_settings": settings,
        "force_metadata": force_metadata,
        "frame_contract": {
            "center": "Sun",
            "frame": "ICRF",
            "time_scale": "TDB",
            "position_unit": "AU",
            "velocity_unit": "AU/day",
            "acceleration_unit": "AU/day^2",
            "planet_ephemerides": "relative days from origin_jd_tdb",
        },
        "accuracy_guaranteed": False,
        "reference_states_used": False,
        "selector_or_retraining_used": False,
    }
    return predictions, metadata


__all__ = ["forecast_precise"]

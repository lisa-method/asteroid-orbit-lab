"""Endpoint-consistent RK4 integration on a relative time axis.

This is an independent implementation for numerical diagnostics.  States use
the project's six-component ``State`` type; time is in relative days and the
acceleration callback receives ``(time_days, position, velocity)``.  No
absolute epoch arithmetic is performed here.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Iterable

from orbit_baselines import Acceleration, State, Vector, add, scale


StepSelector = Callable[[float, State], float]
StepObserver = Callable[[float, State, float, State], None]


@dataclass(frozen=True)
class RK4Meta:
    """Diagnostics for one ``advance`` call."""

    steps: int
    selected_step_sum_days: float
    actual_step_sum_days: float
    sum_step_mismatch_days: float
    absolute_step_mismatch_sum_days: float
    max_step_mismatch_days: float
    final_time_days: float
    compensated: bool


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


def _state(value: State, name: str = "state") -> State:
    if not isinstance(value, State):
        raise ValueError(f"{name} must be a State")
    if len(value.position) != 3 or len(value.velocity) != 3:
        raise ValueError(f"{name} must contain six components")
    components = (*value.position, *value.velocity)
    if any(not isinstance(component, (int, float)) or isinstance(component, bool) or not math.isfinite(float(component)) for component in components):
        raise ValueError(f"{name} must contain six finite components")
    return State(tuple(float(v) for v in value.position), tuple(float(v) for v in value.velocity))  # type: ignore[arg-type]


def _acceleration(value: object) -> Vector:
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError("acceleration must return three finite components")
    if any(not isinstance(component, (int, float)) or isinstance(component, bool) or not math.isfinite(float(component)) for component in value):
        raise ValueError("acceleration must return three finite components")
    return tuple(float(component) for component in value)  # type: ignore[return-value]


def _derivative(time: float, state: State, acceleration: Acceleration) -> State:
    return State(state.velocity, _acceleration(acceleration(time, state.position, state.velocity)))


def _state_add_scaled(state: State, derivative: State, factor: float) -> State:
    return State(add(state.position, scale(derivative.position, factor)), add(state.velocity, scale(derivative.velocity, factor)))


def rk4_increment(
    state: State,
    time_days: float,
    step_days: float,
    acceleration: Acceleration,
    *,
    right_time_days: float | None = None,
) -> State:
    """Return the RK4 state increment without changing ``state``."""

    _state(state)
    time = _finite(time_days, "time_days")
    step = _finite(step_days, "step_days")
    if step <= 0.0:
        raise ValueError("step_days must be positive")
    right_time = time + step if right_time_days is None else _finite(right_time_days, "right_time_days")
    if not math.isfinite(right_time) or right_time <= time:
        raise ValueError("right_time_days must be after time_days")
    if right_time_days is not None and step != right_time - time:
        raise ValueError("step_days must equal the represented endpoint difference")
    k1 = _derivative(time, state, acceleration)
    k2 = _derivative(time + 0.5 * step, _state_add_scaled(state, k1, 0.5 * step), acceleration)
    k3 = _derivative(time + 0.5 * step, _state_add_scaled(state, k2, 0.5 * step), acceleration)
    k4 = _derivative(right_time, _state_add_scaled(state, k3, step), acceleration)
    position = scale(add(add(k1.position, scale(k2.position, 2.0)), add(scale(k3.position, 2.0), k4.position)), step / 6.0)
    velocity = scale(add(add(k1.velocity, scale(k2.velocity, 2.0)), add(scale(k3.velocity, 2.0), k4.velocity)), step / 6.0)
    return State(position, velocity)


def _kahan_add(state: State, increment: State, compensation: list[float]) -> State:
    values = [*state.position, *state.velocity]
    delta = [*increment.position, *increment.velocity]
    updated: list[float] = []
    for index, (current, amount) in enumerate(zip(values, delta)):
        corrected = amount - compensation[index]
        total = current + corrected
        compensation[index] = (total - current) - corrected
        updated.append(total)
    return State(tuple(updated[:3]), tuple(updated[3:]))  # type: ignore[arg-type]


def advance(
    initial_state: State,
    target_times_days: Iterable[float],
    acceleration: Acceleration,
    step_selector: StepSelector,
    *,
    compensated: bool = False,
    on_step: StepObserver | None = None,
    max_steps: int = 1_000_000,
) -> tuple[list[State], RK4Meta]:
    """Advance through strictly increasing relative-time output targets.

    The selector is called causally at each accepted left endpoint.  Every
    accepted step uses ``right_t = min(left_t + selected_step, target_t)``;
    when a target is consumed, ``right_t`` is the target object itself so the
    observer and output state share an exact endpoint.  ``compensated`` keeps a
    six-component Kahan accumulator between accepted increments.
    """

    state = _state(initial_state, "initial_state")
    targets = [_finite(value, "target_time_days") for value in target_times_days]
    if not targets:
        raise ValueError("at least one target time is required")
    if targets[0] < 0.0 or any(value < 0.0 for value in targets):
        raise ValueError("target times must be nonnegative")
    if any(right <= left for left, right in zip(targets, targets[1:])):
        raise ValueError("target times must be strictly increasing")
    if not callable(acceleration) or not callable(step_selector):
        raise ValueError("acceleration and step_selector must be callable")
    if not isinstance(compensated, bool):
        raise ValueError("compensated must be boolean")
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps <= 0:
        raise ValueError("max_steps must be a positive integer")
    current_time = 0.0
    compensation = [0.0] * 6
    predictions: list[State] = []
    steps = 0
    selected_steps = []
    actual_steps = []
    mismatches = []
    mismatch_max = 0.0
    for target in targets:
        while current_time < target:
            if steps >= max_steps:
                raise RuntimeError("maximum RK4 step budget exceeded")
            selected = _finite(step_selector(current_time, state), "selected_step_days")
            if selected <= 0.0:
                raise ValueError("step selector must return a finite positive step")
            remaining = target - current_time
            step = min(selected, remaining)
            right_time = target if step == remaining else current_time + step
            if not math.isfinite(right_time) or right_time <= current_time:
                raise ValueError("RK4 step does not advance time")
            left_time, left_state = current_time, state
            increment = rk4_increment(state, left_time, right_time - left_time, acceleration, right_time_days=right_time)
            state = _kahan_add(state, increment, compensation) if compensated else _state_add_scaled(state, State(increment.position, increment.velocity), 1.0)
            state = _state(state)
            selected_or_remaining = min(selected, remaining)
            mismatch = (right_time - left_time) - selected_or_remaining
            selected_steps.append(selected_or_remaining)
            actual_steps.append(right_time - left_time)
            mismatches.append(mismatch)
            mismatch_max = max(mismatch_max, abs(mismatch))
            current_time = right_time
            steps += 1
            if on_step is not None:
                on_step(left_time, left_state, right_time, state)
        predictions.append(state)
    return predictions, RK4Meta(steps, math.fsum(selected_steps), math.fsum(actual_steps),
        math.fsum(mismatches), math.fsum(abs(v) for v in mismatches), mismatch_max, current_time, compensated)


__all__ = ["RK4Meta", "advance", "rk4_increment"]

"""Dependency-free trajectory propagation and central-law identification.

The module deliberately uses explicit state and unit conventions. Positions are
in AU, velocities in AU/day, times in days, and accelerations in AU/day^2.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Iterable


Vector = tuple[float, float, float]
Acceleration = Callable[[float, Vector, Vector], Vector]


@dataclass(frozen=True)
class State:
    position: Vector
    velocity: Vector


StepSelector = Callable[[float, State], float]
StepObserver = Callable[[float, State, float, State], None]


@dataclass(frozen=True)
class TrajectoryWindow:
    object_id: str
    initial_state: State
    target_times_days: tuple[float, ...]
    target_states: tuple[State, ...]

    def __post_init__(self) -> None:
        if not self.target_times_days:
            raise ValueError("A trajectory window needs at least one target time")
        if len(self.target_times_days) != len(self.target_states):
            raise ValueError("Target times and states must have equal length")
        if any(time <= 0.0 for time in self.target_times_days):
            raise ValueError("Target times must be positive")
        if any(right <= left for left, right in zip(self.target_times_days, self.target_times_days[1:])):
            raise ValueError("Target times must be strictly increasing")


@dataclass(frozen=True)
class CentralLawFit:
    reference_acceleration_au_d2: float
    exponent: float
    loss: float
    evaluations: int


def add(left: Vector, right: Vector) -> Vector:
    return tuple(a + b for a, b in zip(left, right))  # type: ignore[return-value]


def subtract(left: Vector, right: Vector) -> Vector:
    return tuple(a - b for a, b in zip(left, right))  # type: ignore[return-value]


def scale(vector: Vector, factor: float) -> Vector:
    return tuple(component * factor for component in vector)  # type: ignore[return-value]


def norm(vector: Vector) -> float:
    return math.sqrt(sum(component * component for component in vector))


def cross(left: Vector, right: Vector) -> Vector:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def constant_velocity(initial_state: State, target_times_days: Iterable[float]) -> list[State]:
    predictions = []
    for time in target_times_days:
        if time < 0.0:
            raise ValueError("Backward propagation is not supported")
        predictions.append(
            State(
                add(initial_state.position, scale(initial_state.velocity, time)),
                initial_state.velocity,
            )
        )
    return predictions


def central_acceleration(
    reference_acceleration_au_d2: float,
    exponent: float,
    *,
    reference_radius_au: float = 1.0,
) -> Acceleration:
    """Return a radial law with a dimensionally stable reference amplitude.

    The magnitude is ``a_ref * (r / r_ref) ** (-n)``. At ``n = 2`` and
    ``r_ref = 1 AU``, ``mu = a_ref * r_ref**2``.
    """

    if reference_acceleration_au_d2 <= 0.0:
        raise ValueError("Reference acceleration must be positive")
    if reference_radius_au <= 0.0:
        raise ValueError("Reference radius must be positive")

    def acceleration(_time: float, position: Vector, _velocity: Vector) -> Vector:
        radius = norm(position)
        if radius == 0.0:
            raise ValueError("Central acceleration is singular at the origin")
        magnitude = reference_acceleration_au_d2 * (radius / reference_radius_au) ** (-exponent)
        return scale(position, -magnitude / radius)

    return acceleration


def two_body_acceleration(mu_au3_d2: float) -> Acceleration:
    return central_acceleration(mu_au3_d2, 2.0)


def _state_derivative(time: float, state: State, acceleration: Acceleration) -> State:
    return State(state.velocity, acceleration(time, state.position, state.velocity))


def _state_add_scaled(state: State, derivative: State, factor: float) -> State:
    return State(
        add(state.position, scale(derivative.position, factor)),
        add(state.velocity, scale(derivative.velocity, factor)),
    )


def rk4_step(state: State, time: float, step_days: float, acceleration: Acceleration) -> State:
    if step_days <= 0.0:
        raise ValueError("RK4 step must be positive")
    k1 = _state_derivative(time, state, acceleration)
    k2 = _state_derivative(
        time + 0.5 * step_days,
        _state_add_scaled(state, k1, 0.5 * step_days),
        acceleration,
    )
    k3 = _state_derivative(
        time + 0.5 * step_days,
        _state_add_scaled(state, k2, 0.5 * step_days),
        acceleration,
    )
    k4 = _state_derivative(
        time + step_days,
        _state_add_scaled(state, k3, step_days),
        acceleration,
    )
    position_increment = scale(
        add(add(k1.position, scale(k2.position, 2.0)), add(scale(k3.position, 2.0), k4.position)),
        step_days / 6.0,
    )
    velocity_increment = scale(
        add(add(k1.velocity, scale(k2.velocity, 2.0)), add(scale(k3.velocity, 2.0), k4.velocity)),
        step_days / 6.0,
    )
    return State(add(state.position, position_increment), add(state.velocity, velocity_increment))


def propagate(
    initial_state: State,
    target_times_days: Iterable[float],
    acceleration: Acceleration,
    *,
    max_step_days: float = 0.25,
) -> list[State]:
    targets = list(target_times_days)
    if max_step_days <= 0.0:
        raise ValueError("max_step_days must be positive")
    if any(time < 0.0 for time in targets):
        raise ValueError("Backward propagation is not supported")
    if any(right < left for left, right in zip(targets, targets[1:])):
        raise ValueError("Target times must be sorted")

    state = initial_state
    current_time = 0.0
    predictions = []
    for target_time in targets:
        duration = target_time - current_time
        if duration > 0.0:
            steps = max(1, math.ceil(duration / max_step_days))
            step = duration / steps
            for _ in range(steps):
                state = rk4_step(state, current_time, step, acceleration)
                current_time += step
            current_time = target_time
        predictions.append(state)
    return predictions


def propagate_variable_step(
    initial_state: State,
    target_times_days: Iterable[float],
    acceleration: Acceleration,
    step_selector: StepSelector,
    *,
    on_output: Callable[[float, State, int], None] | None = None,
    on_step: StepObserver | None = None,
) -> tuple[list[State], int]:
    """Propagate with deterministic state-dependent RK4 steps.

    ``on_output`` observes requested target times.  ``on_step`` observes each
    accepted RK4 step and receives ``(left_time, left_state, right_time,
    right_state)``.  The latter is deliberately independent of output knots,
    so an observer can retain integration endpoints without changing the
    propagated trajectory or the existing output callback behavior.
    """

    targets = list(target_times_days)
    if any(time < 0.0 for time in targets):
        raise ValueError("Backward propagation is not supported")
    if any(right < left for left, right in zip(targets, targets[1:])):
        raise ValueError("Target times must be sorted")
    state = initial_state
    current_time = 0.0
    predictions = []
    steps = 0
    for target_time in targets:
        while current_time < target_time:
            selected_step = step_selector(current_time, state)
            if not math.isfinite(selected_step) or selected_step <= 0.0:
                raise ValueError("Step selector must return a finite positive step")
            remaining = target_time - current_time
            step = min(selected_step, remaining)
            left_time, left_state = current_time, state
            next_state = rk4_step(state, current_time, step, acceleration)
            # Keep the historical floating-point time accumulation for the
            # numerical state path.  Observers still receive an exact target
            # time when this accepted step consumes the final remainder.
            current_time += step
            if current_time <= left_time:
                raise ValueError("RK4 step does not advance time")
            state = next_state
            steps += 1
            if on_step is not None:
                right_time = target_time if step == remaining else current_time
                on_step(left_time, left_state, right_time, state)
        current_time = target_time
        predictions.append(state)
        if on_output is not None:
            on_output(target_time, state, steps)
    return predictions, steps


def specific_energy(state: State, mu_au3_d2: float) -> float:
    return 0.5 * norm(state.velocity) ** 2 - mu_au3_d2 / norm(state.position)


def specific_angular_momentum(state: State) -> float:
    return norm(cross(state.position, state.velocity))


def trajectory_loss(
    windows: Iterable[TrajectoryWindow],
    reference_acceleration_au_d2: float,
    exponent: float,
    *,
    max_step_days: float,
) -> float:
    total = 0.0
    count = 0
    acceleration = central_acceleration(reference_acceleration_au_d2, exponent)
    for window in windows:
        predictions = propagate(
            window.initial_state,
            window.target_times_days,
            acceleration,
            max_step_days=max_step_days,
        )
        for predicted, target in zip(predictions, window.target_states):
            position_scale = max(norm(target.position), 0.1)
            velocity_scale = max(norm(target.velocity), 1.0e-4)
            position_relative = norm(subtract(predicted.position, target.position)) / position_scale
            velocity_relative = norm(subtract(predicted.velocity, target.velocity)) / velocity_scale
            total += position_relative**2 + velocity_relative**2
            count += 2
    if count == 0:
        raise ValueError("No observations supplied to trajectory loss")
    return total / count


def fit_central_law(
    windows: Iterable[TrajectoryWindow],
    *,
    reference_acceleration_bounds: tuple[float, float] = (1.0e-5, 1.0e-3),
    exponent_bounds: tuple[float, float] = (1.0, 3.0),
    max_step_days: float = 0.25,
    iterations: int = 18,
) -> CentralLawFit:
    """Fit ``a_ref`` and ``n`` with bounded deterministic Nelder-Mead."""

    windows = tuple(windows)
    lower_a, upper_a = reference_acceleration_bounds
    lower_n, upper_n = exponent_bounds
    if not windows:
        raise ValueError("At least one trajectory window is required")
    if not (0.0 < lower_a < upper_a and lower_n < upper_n):
        raise ValueError("Invalid fit bounds")
    if iterations <= 0:
        raise ValueError("iterations must be positive")

    lower_log_a = math.log(lower_a)
    upper_log_a = math.log(upper_a)
    cache: dict[tuple[float, float], float] = {}

    def clamp(point: tuple[float, float]) -> tuple[float, float]:
        return (
            min(upper_log_a, max(lower_log_a, point[0])),
            min(upper_n, max(lower_n, point[1])),
        )

    def objective(point: tuple[float, float]) -> float:
        candidate_log_a, candidate_n = clamp(point)
        key = (candidate_log_a, candidate_n)
        if key not in cache:
            cache[key] = trajectory_loss(
                windows,
                math.exp(candidate_log_a),
                candidate_n,
                max_step_days=max_step_days,
            )
        return cache[key]

    center = (
        0.5 * (lower_log_a + upper_log_a),
        0.5 * (lower_n + upper_n),
    )
    simplex = [
        center,
        clamp((center[0] + 0.25 * (upper_log_a - lower_log_a), center[1])),
        clamp((center[0], center[1] + 0.25 * (upper_n - lower_n))),
    ]

    for _ in range(iterations):
        ranked = sorted((objective(point), point) for point in simplex)
        (best_loss, best), (second_loss, second), (worst_loss, worst) = ranked
        centroid = ((best[0] + second[0]) / 2.0, (best[1] + second[1]) / 2.0)
        reflected = clamp(
            (centroid[0] + (centroid[0] - worst[0]), centroid[1] + (centroid[1] - worst[1]))
        )
        reflected_loss = objective(reflected)
        if best_loss <= reflected_loss < second_loss:
            simplex = [best, second, reflected]
        elif reflected_loss < best_loss:
            expanded = clamp(
                (
                    centroid[0] + 2.0 * (reflected[0] - centroid[0]),
                    centroid[1] + 2.0 * (reflected[1] - centroid[1]),
                )
            )
            simplex = [best, second, expanded if objective(expanded) < reflected_loss else reflected]
        else:
            if reflected_loss < worst_loss:
                contracted = clamp(
                    (
                        centroid[0] + 0.5 * (reflected[0] - centroid[0]),
                        centroid[1] + 0.5 * (reflected[1] - centroid[1]),
                    )
                )
            else:
                contracted = clamp(
                    (
                        centroid[0] + 0.5 * (worst[0] - centroid[0]),
                        centroid[1] + 0.5 * (worst[1] - centroid[1]),
                    )
                )
            if objective(contracted) < min(reflected_loss, worst_loss):
                simplex = [best, second, contracted]
            else:
                simplex = [
                    best,
                    clamp(((best[0] + second[0]) / 2.0, (best[1] + second[1]) / 2.0)),
                    clamp(((best[0] + worst[0]) / 2.0, (best[1] + worst[1]) / 2.0)),
                ]

    best_loss, best = min((objective(point), point) for point in simplex)
    return CentralLawFit(math.exp(best[0]), best[1], best_loss, len(cache))

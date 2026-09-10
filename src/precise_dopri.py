"""Endpoint-consistent, compensated relative-time Dormand--Prince 5(4).

Versioned numerical extension of independent_rk.py; frozen original is intact.
The same tableau/controller are used (SciPy RK45 algorithm reference in the
original module). This variant requires epoch=0; embed an absolute physical
origin in the force callback. State increments use compensated addition,
stage/error weighted sums use math.fsum, and h is the representable endpoint
difference. Compensation is committed only on accepted steps. FSAL is
computed at the actual compensated high-order endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Iterable, Mapping


State6 = tuple[float, float, float, float, float, float]
Sample = tuple[float, State6]
Acceleration = Callable[[float, tuple[float, float, float], tuple[float, float, float]], Iterable[float]]


# Dormand--Prince 5(4) tableau used by SciPy's RK45.  The final row B is the
# fifth-order solution and E is (fifth - fourth), as in SciPy's implementation.
_C = (0.0, 1.0 / 5.0, 3.0 / 10.0, 4.0 / 5.0, 8.0 / 9.0, 1.0, 1.0)
_A = (
    (),
    (1.0 / 5.0,),
    (3.0 / 40.0, 9.0 / 40.0),
    (44.0 / 45.0, -56.0 / 15.0, 32.0 / 9.0),
    (19372.0 / 6561.0, -25360.0 / 2187.0, 64448.0 / 6561.0, -212.0 / 729.0),
    (9017.0 / 3168.0, -355.0 / 33.0, 46732.0 / 5247.0, 49.0 / 176.0, -5103.0 / 18656.0),
    (35.0 / 384.0, 0.0, 500.0 / 1113.0, 125.0 / 192.0, -2187.0 / 6784.0, 11.0 / 84.0),
)
_B = (35.0 / 384.0, 0.0, 500.0 / 1113.0, 125.0 / 192.0, -2187.0 / 6784.0, 11.0 / 84.0, 0.0)
_E = (71.0 / 57600.0, 0.0, -71.0 / 16695.0, 71.0 / 1920.0, -17253.0 / 339200.0, 22.0 / 525.0, -1.0 / 40.0)


@dataclass(frozen=True)
class IntegrationResult:
    """Saved output samples, every accepted endpoint, and audit statistics."""

    samples: tuple[Sample, ...]
    accepted_endpoints: tuple[Sample, ...]
    stats: Mapping[str, object]


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _state(value: Iterable[float], name: str = "state") -> State6:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must contain six finite numbers")
    try:
        result = tuple(_finite(component, f"{name}[{index}]") for index, component in enumerate(value))
    except TypeError as exc:
        raise ValueError(f"{name} must contain six finite numbers") from exc
    if len(result) != 6:
        raise ValueError(f"{name} must contain six finite numbers")
    return result  # type: ignore[return-value]


def _add_scaled(state: State6, derivatives: tuple[State6, ...], coefficients: tuple[float, ...], step: float) -> State6:
    return tuple(
        state[index] + step * sum(coefficient * derivative[index] for coefficient, derivative in zip(coefficients, derivatives))
        for index in range(6)
    )  # type: ignore[return-value]


def _rhs(acceleration: Acceleration, absolute_time: float, state: State6) -> State6:
    position = (state[0], state[1], state[2])
    velocity = (state[3], state[4], state[5])
    try:
        raw_values = tuple(acceleration(absolute_time, position, velocity))
    except TypeError as exc:
        raise ValueError("acceleration must return three finite components") from exc
    if len(raw_values) != 3:
        raise ValueError("acceleration must return three finite components")
    values = []
    for index, value in enumerate(raw_values):
        try:
            values.append(_finite(value, f"acceleration[{index}]"))
        except ValueError as exc:
            raise FloatingPointError("acceleration returned a non-finite component") from exc
    return (state[3], state[4], state[5], values[0], values[1], values[2])


def _error_norm(error: State6, old: State6, new: State6, atol_position: float, atol_velocity: float, rtol: float) -> float:
    scales = tuple(
        (atol_position if index < 3 else atol_velocity) + rtol * max(abs(old[index]), abs(new[index]))
        for index in range(6)
    )
    normalized = []
    for index, scale in enumerate(scales):
        if scale == 0.0:
            normalized.append(0.0 if error[index] == 0.0 else math.inf)
        else:
            normalized.append(abs(error[index]) / scale)
    return max(normalized)


def _finite_nonnegative(value: object, name: str) -> float:
    result = _finite(value, name)
    if result < 0.0:
        raise ValueError(f"{name} must be non-negative")
    return result


def integrate_precise_dopri54(
    acceleration: Acceleration,
    epoch: float,
    state: Iterable[float],
    output_times: Iterable[float],
    *,
    atol_position: float = 1e-12,
    atol_velocity: float = 1e-12,
    rtol: float = 1e-9,
    min_step: float = 1e-10,
    max_step: float = 0.25,
    max_steps: int = 1_000_000,
    breakpoints: Iterable[float] = (),
) -> IntegrationResult:
    """Integrate with compensated DP5(4), requiring relative-time epoch zero.

    ``acceleration`` is called as ``acceleration(absolute_time, position,
    velocity)`` and must return a finite three-vector.  ``output_times`` and
    ``breakpoints`` are absolute times in the same units as ``epoch``.  Output
    times may include ``epoch`` and must be non-decreasing; all must be at or
    after ``epoch``.  Breakpoints are optional hard boundaries: no accepted
    step crosses one, even when ``max_step`` would permit it.

    The error scale is componentwise: position components use
    ``atol_position + rtol * max(|y_old|, |y_new|)``, and velocity components use
    the analogous ``atol_velocity`` scale.  The normalized error is the maximum
    over the six components (an infinity norm).  The Dormand--Prince tableau
    and safety/factor controller bounds follow the SciPy RK45 reference; this
    implementation uses the explicit max-component norm above.  ``min_step``
    is a guard; a final target/breakpoint distance shorter than it is allowed
    so exact requested endpoints remain reachable.
    """

    if not callable(acceleration):
        raise TypeError("acceleration must be callable")
    epoch_value = _finite(epoch, "epoch")
    if epoch_value != 0.0:
        raise ValueError("precise DP requires relative-time epoch zero")
    initial = _state(state)
    position_atol = _finite_nonnegative(atol_position, "atol_position")
    velocity_atol = _finite_nonnegative(atol_velocity, "atol_velocity")
    relative_tol = _finite_nonnegative(rtol, "rtol")
    if position_atol == 0.0 and velocity_atol == 0.0 and relative_tol == 0.0:
        raise ValueError("at least one error tolerance must be positive")
    minimum_step = _finite(min_step, "min_step")
    maximum_step = _finite(max_step, "max_step")
    if minimum_step <= 0.0 or maximum_step <= 0.0 or minimum_step > maximum_step:
        raise ValueError("min_step and max_step must be positive with min_step <= max_step")
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps <= 0:
        raise ValueError("max_steps must be a positive integer")

    targets = tuple(_finite(value, f"output_times[{index}]") for index, value in enumerate(output_times))
    if not targets:
        raise ValueError("output_times must contain at least one time")
    if any(value < epoch_value for value in targets):
        raise ValueError("output_times must be at or after epoch")
    if any(right < left for left, right in zip(targets, targets[1:])):
        raise ValueError("output_times must be sorted")
    final_time = targets[-1]
    raw_breakpoints = tuple(_finite(value, f"breakpoints[{index}]") for index, value in enumerate(breakpoints))
    if any(right <= left for left, right in zip(raw_breakpoints, raw_breakpoints[1:])):
        raise ValueError("breakpoints must be strictly increasing")
    constrained = tuple(value for value in raw_breakpoints if epoch_value < value < final_time)

    # Validate the initial force before entering the loop, and retain its
    # derivative as the first DP stage for the first attempted step.
    first_derivative = _rhs(acceleration, epoch_value, initial)
    function_evaluations = 1
    local_time = 0.0
    current = initial
    compensation = (0.0,) * 6
    max_step_mismatch = 0.0
    accepted: list[Sample] = [(epoch_value, initial)]
    samples: list[Sample] = []
    target_index = 0
    breakpoint_index = 0
    accepted_steps = 0
    rejected_steps = 0
    last_attempt_rejected = False
    attempted_steps = 0
    min_accepted = math.inf
    max_accepted = 0.0
    max_error = 0.0
    step = min(maximum_step, max(minimum_step, (final_time - epoch_value) / 100.0 or maximum_step))
    safety = 0.9
    min_factor = 0.2
    max_factor = 10.0

    def emit_targets() -> None:
        nonlocal target_index
        absolute_time = epoch_value + local_time
        while target_index < len(targets) and targets[target_index] == absolute_time:
            samples.append((targets[target_index], current))
            target_index += 1

    emit_targets()
    while target_index < len(targets):
        next_target_local = targets[target_index] - epoch_value
        while breakpoint_index < len(constrained) and constrained[breakpoint_index] <= epoch_value + local_time:
            breakpoint_index += 1
        next_boundary = next_target_local
        if breakpoint_index < len(constrained):
            next_boundary = min(next_boundary, constrained[breakpoint_index] - epoch_value)
        remaining = next_boundary - local_time
        if remaining < 0.0 or not math.isfinite(remaining):
            raise RuntimeError("internal local-time boundary error")
        if remaining == 0.0:
            # A breakpoint is an endpoint of the next segment, so move past it
            # without evaluating a zero-length step.
            breakpoint_index += 1
            continue
        step = min(step, maximum_step, remaining)
        if step <= 0.0:
            raise RuntimeError("non-positive integration step")
        if step < minimum_step and remaining > minimum_step * (1.0 + 1e-14):
            raise RuntimeError("required step is below min_step")
        if local_time + step == local_time:
            raise RuntimeError("local-time step stagnation")
        attempted_steps += 1
        if attempted_steps > max_steps:
            raise RuntimeError("maximum integration step budget exceeded")

        # Round time once, then use precisely that interval for every stage.
        reaches_boundary = step == remaining
        right_time = next_boundary if reaches_boundary else local_time + step
        actual_step = right_time - local_time
        max_step_mismatch = max(max_step_mismatch, abs(actual_step - step))
        step = actual_step
        derivatives: list[State6] = [first_derivative]
        for stage in range(1, 6):
            stage_state = tuple(math.fsum((
                current[index], -compensation[index],
                step * math.fsum(coefficient * derivative[index]
                    for coefficient, derivative in zip(_A[stage], derivatives))
            )) for index in range(6))
            stage_time = right_time if _C[stage] == 1.0 else local_time + _C[stage] * step
            derivatives.append(_rhs(acceleration, stage_time, stage_state))
        increment = tuple(step * math.fsum(coefficient * derivative[index]
            for coefficient, derivative in zip(_B, derivatives)) for index in range(6))
        corrected = tuple(increment[i] - compensation[i] for i in range(6))
        high = tuple(current[i] + corrected[i] for i in range(6))
        trial_compensation = tuple((high[i] - current[i]) - corrected[i] for i in range(6))
        derivatives.append(_rhs(acceleration, right_time, high))
        function_evaluations += 6
        error = tuple(step * math.fsum(coefficient * derivative[index]
            for coefficient, derivative in zip(_E, derivatives)) for index in range(6))
        if not all(math.isfinite(value) for value in high + error):
            raise FloatingPointError("non-finite Dormand--Prince state or error estimate")
        error_value = _error_norm(error, current, high, position_atol, velocity_atol, relative_tol)
        if not math.isfinite(error_value):
            raise FloatingPointError("non-finite Dormand--Prince error norm")
        max_error = max(max_error, error_value)
        if error_value <= 1.0:
            # ``step`` is explicitly capped by the next local boundary.  When
            # it reaches that boundary, assign the boundary itself rather than
            # comparing absolute epochs with a tolerance (which is unsafe for
            # large JD origins).
            local_time = right_time
            current = high
            compensation = trial_compensation
            accepted_steps += 1
            min_accepted = min(min_accepted, step)
            max_accepted = max(max_accepted, step)
            accepted.append((epoch_value + local_time, current))
            if local_time == next_target_local:
                emit_targets()
            if breakpoint_index < len(constrained) and local_time == constrained[breakpoint_index] - epoch_value:
                breakpoint_index += 1
            if error_value == 0.0:
                factor = max_factor
            else:
                factor = min(max_factor, max(min_factor, safety * error_value ** (-1.0 / 5.0)))
            if last_attempt_rejected:
                factor = min(1.0, factor)
            step = min(maximum_step, step * factor)
            first_derivative = derivatives[-1]
            last_attempt_rejected = False
        else:
            rejected_steps += 1
            last_attempt_rejected = True
            factor = max(min_factor, safety * error_value ** (-1.0 / 5.0))
            step *= factor
            if step < minimum_step and remaining > minimum_step * (1.0 + 1e-14):
                raise RuntimeError("step rejection would violate min_step")

    stats = {
        "status": "finished",
        "compensated": True,
        "endpoint_consistent": True,
        "max_step_mismatch_days": max_step_mismatch,
        "accepted_steps": accepted_steps,
        "rejected_steps": rejected_steps,
        "attempted_steps": attempted_steps,
        "function_evaluations": function_evaluations,
        "min_accepted_step": 0.0 if accepted_steps == 0 else min_accepted,
        "max_accepted_step": max_accepted,
        "final_step": step,
        "max_error_norm": max_error,
        "breakpoints": constrained,
        "epoch": epoch_value,
        "final_time": final_time,
    }
    return IntegrationResult(tuple(samples), tuple(accepted), stats)


__all__ = ["Acceleration", "IntegrationResult", "Sample", "State6", "integrate_precise_dopri54"]

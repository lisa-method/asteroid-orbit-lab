"""Modified-midpoint polynomial extrapolation for independent orbit checks.

Gragg's smoothed midpoint sequence n=2,4,...,10 is extrapolated in 1/n**2.
This is a distinct integration algorithm from the frozen RK4 and DP5 codes.
Only increments are extrapolated; Kahan compensation carries endpoint sums.
It shares the supplied force callback, so agreement is a numerical check,
not an independent validation of the physical force specification.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ExtrapolationResult:
    samples: tuple
    accepted_endpoints: tuple
    stats: dict


def midpoint_increment(rhs, t, y, compensation, right, n):
    """Smoothed modified midpoint, represented as displacements from y."""
    h = (right - t) / n
    base = tuple(math.fsum((a, -b)) for a, b in zip(y, compensation))
    previous = (0.0,) * 6
    current = tuple(h * a for a in rhs(t, base))
    for j in range(1, n):
        stage = tuple(math.fsum((y[i], -compensation[i], current[i])) for i in range(6))
        derivative = rhs(t + (right - t) * (j / n), stage)
        following = tuple(math.fsum((previous[i], 2*h*derivative[i])) for i in range(6))
        previous, current = current, following
    final_state = tuple(math.fsum((y[i], -compensation[i], current[i])) for i in range(6))
    last = rhs(right, final_state)
    return tuple(0.5 * math.fsum((current[i], previous[i], h*last[i])) for i in range(6))


def extrapolation_step(rhs, t, y, compensation, right):
    sequence = (2, 4, 6, 8, 10)
    previous = []
    for j, n in enumerate(sequence):
        row = [midpoint_increment(rhs, t, y, compensation, right, n)]
        for k in range(1, j + 1):
            divisor = (n / sequence[j-k])**2 - 1.0
            row.append(tuple(math.fsum((row[k-1][i],
                (row[k-1][i] - previous[k-1][i]) / divisor)) for i in range(6)))
        previous = row
    high = row[-1]
    error = tuple(high[i] - row[-2][i] for i in range(6))
    return high, error


def integrate_extrapolation(acceleration, state, output_times, *,
        rtol=1e-15, atol_position=1e-18, atol_velocity=1e-19,
        max_step=0.5, min_step=1e-10, max_steps=1000000, breakpoints=()):
    """Forward integration from relative t=0 with explicit requested nodes.

    The final diagonal is order 10; its order-8 neighbour estimates local
    error at order 9. This estimate is empirical, not a rigorous error bound.
    """
    y = tuple(float(v) for v in state)
    targets = tuple(float(v) for v in output_times)
    if len(y) != 6 or not all(math.isfinite(v) for v in y):
        raise ValueError('six finite state components required')
    if not targets or any(not math.isfinite(t) or t < 0 for t in targets) or any(b <= a for a,b in zip(targets, targets[1:])):
        raise ValueError('finite strictly increasing nonnegative output times required')
    if not all(math.isfinite(v) and v >= 0 for v in (rtol, atol_position, atol_velocity)) or max(rtol, atol_position, atol_velocity) == 0:
        raise ValueError('positive finite error scale required')
    if not (0 < min_step <= max_step < math.inf) or not isinstance(max_steps, int) or max_steps <= 0:
        raise ValueError('invalid step budget')
    boundaries = tuple(float(t) for t in breakpoints)
    if any(not math.isfinite(t) for t in boundaries) or any(b <= a for a,b in zip(boundaries, boundaries[1:])):
        raise ValueError('invalid breakpoints')
    boundaries = tuple(t for t in boundaries if 0 < t < targets[-1])
    calls = 0
    def rhs(t, state):
        nonlocal calls
        calls += 1
        a = tuple(acceleration(t, state[:3], state[3:]))
        if len(a) != 3 or not all(math.isfinite(v) for v in a):
            raise FloatingPointError('nonfinite acceleration')
        return (*state[3:], *a)
    compensation = (0.0,)*6
    t, h, attempts, rejected, boundary_index = 0.0, max_step, 0, 0, 0
    samples, endpoints, steps = [], [(0.0, y)], []
    for target in targets:
        while t < target:
            while boundary_index < len(boundaries) and boundaries[boundary_index] <= t:
                boundary_index += 1
            boundary = min(target, boundaries[boundary_index]) if boundary_index < len(boundaries) else target
            left = boundary - t
            requested = min(h, max_step, left)
            if requested < min_step and left > min_step:
                raise RuntimeError('required step below min_step')
            right = boundary if requested == left else t + requested
            if right <= t:
                raise RuntimeError('time stagnation')
            step = right - t
            attempts += 1
            if attempts > max_steps:
                raise RuntimeError('step budget exceeded')
            increment, error = extrapolation_step(rhs, t, y, compensation, right)
            corrected = tuple(increment[i] - compensation[i] for i in range(6))
            high = tuple(y[i] + corrected[i] for i in range(6))
            cnew = tuple((high[i] - y[i]) - corrected[i] for i in range(6))
            scales = tuple((atol_position if i < 3 else atol_velocity) + rtol * max(abs(y[i]), abs(high[i])) for i in range(6))
            norm = max(abs(error[i])/scales[i] if scales[i] else (0.0 if error[i] == 0 else math.inf) for i in range(6))
            if not all(math.isfinite(v) for v in high) or not math.isfinite(norm):
                raise FloatingPointError('nonfinite extrapolation result')
            factor = 4.0 if norm == 0 else min(4.0, max(0.2, 0.85*norm**(-1/9)))
            if norm <= 1:
                y, compensation, t = high, cnew, right
                endpoints.append((t, y)); steps.append(step)
            else:
                rejected += 1
                factor = min(0.8, factor)
            h = min(max_step, step*factor)
        samples.append((target, y))
    return ExtrapolationResult(tuple(samples), tuple(endpoints), {
        'algorithm':'modified_midpoint_extrapolation_10_8', 'status':'finished',
        'accepted_steps':len(steps), 'rejected_steps':rejected, 'attempted_steps':attempts,
        'function_evaluations':calls, 'min_accepted_step':min(steps, default=0),
        'max_accepted_step':max(steps, default=0), 'final_time':t,
        'compensated':True, 'shared_rhs_with_other_solvers':True})

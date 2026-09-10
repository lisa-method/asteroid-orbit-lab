# Apophis independent-solver audit contract

This bounded audit diagnoses the old 2029 Apophis teacher-matching branch. It
does not create an operational selector result, fresh holdout score, or fitted
force model.

## Frozen question

The annual start is 2029-01-01 and the stored close-approach refinement is the
existing 36-hour TDB window, 2029-04-13 00:00 through 2029-04-14 12:00. The old
Apophis daily states and the stored five-minute Earth/Moon/Apophis states are
the immutable reference. Refinement rows replace daily Earth/Moon nodes only
inside that window. The reference grid is used for sampled error measurement,
never as a forecast-time input.

The force branch is the old nominal `baseGR+SB16+NG` branch with Earth J2 and
the existing IAU mean-pole approximation (`baseGR+SB16+NG+J2iau`). The same
force callback is supplied to the existing variable-step RK4 and the separate
stdlib Dormand-Prince 5(4) implementation. No force parameter is fitted or
changed.

## Bounded comparisons

1. **Solver axis:** old merged ephemerides, RK4 scales `0.5`, `0.25`, `0.125`,
   and DP5(4) relative tolerances `1e-8`, `1e-9`, `1e-10` with declared
   component absolute tolerances. Every result is emitted at the exact annual
   daily-plus-refined grid. The audit records final and maximum sampled
   reference position/velocity errors, accepted-step counts, and maximum
   position/velocity separation from a designated tight DP trajectory on that
   same grid.
2. **Ephemeris axis:** same initial state, force branch, and grid, comparing
   old daily/refined ephemerides with the existing development30 hourly files
   for all nine configured planets. RK4 scales `0.5` and `0.25`, plus DP5(4)
   `tight` and `tighter` tolerances, are run on both input sources. Small-body
   ephemerides and nominal NG remain unchanged.

The exact old annual RK4 `.5` endpoint is retained as a reproduction row, and
the old RK4 `.5` versus `.25` maximum trajectory difference is reported
separately from the solver cross-check.

The annual and 36-hour grids are both reported. The short-grid result is a
local event check; it cannot validate the annual rollout.

## Availability and provenance gate

The nominal NG values are retained because this is an historical teacher
branch. `NGInput.status(start_jd)` is recorded. A downloaded UTC timestamp is
accepted only when a local manifest record matches the actual raw SHA-256 (and
its byte size when present); the conservative bound is UTC calendar date plus
two days at midnight. The solution date and osculating epoch describe the
fit/reference and do not prove historical availability. A future 2026 retrieval
timestamp therefore does not make parameters available to a historical start
before that bound. An unavailable or unknown gate does not invalidate this
diagnostic, but it prevents presenting the branch as an operational forecast.

## Reproducibility and limits

The JSON config and all input/source hashes are persisted. Each completed
case is written atomically and reused only with matching fingerprints. No new
data, package, network request, training, random split, or benchmark timing is
part of this protocol. Agreement between integrators is an empirical numerical
sensitivity bound at the declared tolerances; disagreement between old and
hourly ephemerides is an input/interpolation shift, not proof of a specific
missing force. Horizons states remain model-derived teacher data.

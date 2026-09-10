# Apophis deterministic initial-state sensitivity — 2026-09-09

This separate follow-up measures amplification of prescribed initial-state
perturbations through the inspected 2029 encounter. Freeze this contract,
config, runtime, numerical source closure, completed SPK parent matrix,
parent verification and all input hashes before any new propagation.
No covariance, physical uncertainty distribution or new validation score.

## Fixed experiment

Reuse all_direct__ultra from the completed SPK audit as baseline. Use direct
DE441 major-body/Sun ephemerides, unchanged SB16, fixed Earth J2, nominal
NG, Sun-only outer PPN and the same matched annual-hourly initial state.
Solar J2 and Earth J3/J4 remain off. Keep the same 797 requested times and
365-day horizon, native inertial coordinates and heliocentric conversion.
The same DP ultra setting and maximum step are used in all probes.

Exactly 24 new propagations: each of three ICRF Cartesian position axes
perturbed by plus/minus 1 m and plus/minus 10 m; each velocity axis by
plus/minus 1e-6 m/s and plus/minus 1e-5 m/s. Amplitudes are deterministic
diagnostic probes, not measured errors or confidence intervals. Record
the actual representable input deltas and spans after conversion to AU
and AU/day. Do not fit initial state, direction or amplitude to the teacher.
No future asteroid states enter a force or sensitivity feature.

Store every true native and converted requested state and accepted endpoint,
initial states, settings, timings, parent identity and source fingerprint.
Completed checkpoints and matrices are immutable; resume incomplete work
only. Source/input hashes must match on resume. Review the runner before
its first artifact write; require completed parent artifact verification.

## Analysis fixed before propagation

For each axis and amplitude, divide the six-component plus/minus output
difference by the actual plus/minus initial span expressed in SI units.
Report position response to position input in m/m and to velocity input
in seconds. For velocity also divide by elapsed SI seconds to compare
with free flight; this ratio is undefined at time zero.

Compare derivative vectors between both prescribed amplitudes using the
norm of their difference divided by the larger derivative norm. A zero/zero
case has relative difference zero. The descriptive consistency criterion
is <=1 percent; retain failures without changing the probes. Compare
position and velocity response vectors separately, avoiding mixed units.

Compute raw midpoint displacement using differences from the baseline,
then subtract the linear response to the actual input midpoint offset
(rounding can make the two deltas asymmetric). Record both the raw and
corrected midpoint diagnostics. Use careful summation for cancellations.
The corrected diagnostic probes nonlinearity plus numerical effects; it
does not by itself establish physical curvature or a rigorous error bound.

Report diagnostic-day growth and annual values for every axis/amplitude.
The maximum among three coordinate-axis responses is not the maximum over
all possible directions. Baseline cross-method and step comparisons come
from the parent SPK audit; no perturbed independent-solver runs are included
in this bounded experiment. Sensitivity estimates below the demonstrated
numerical resolution must be described as unresolved.

## Completion and limits

Offline verification must check frozen hashes, checkpoint identities,
actual initial perturbations, settings, output times, native conversions,
endpoint alignment and independently recompute all response diagnostics.
Any reference error is evaluator-only. No probability of impact, operational
fallback validation or claim of true-orbit accuracy follows from this audit.
The 24-rollout cost is diagnostic; do not present it as a cheap selector.
Do not modify earlier results, install dependencies, publish or commit.

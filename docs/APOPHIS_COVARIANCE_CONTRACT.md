# Apophis joint orbit/NG covariance propagation — 2026-09-09

This is a separate post-hoc inspected-event experiment. Freeze config,
contract, numerical source closure, runtime, input hashes and predecessor
results before any new propagation. No fit to future target states or
changes to prior force-selection/holdout results.

## Inputs and interpretation

The anonymous SBDB response has JPL solution 220, DE441/SB441-N16 and a
joint eight-dimensional covariance at 2021-01-01 TDB: e,q,tp,node,peri,i,A1,A2.
The main orbit epoch differs; use covariance.elements at its own epoch.
Preserve every off-diagonal term, angular units and dynamical correlations.
Decode cov=vec upper-triangular COLUMN-major storage. Degrees, AU and TDB
days refer to the element coordinates; A1/A2 use AU/day². Do not substitute
marginal diagonal errors for the joint covariance or drop the NG dimensions.

SBDB solution date 2024-06-25 and last observation 2022-04-09 precede the
2029 forecast origin. Integration from 2021 is propagation of this later
posterior, NOT a causal forecast issued in 2021. The snapshot is retrieved
in 2026. No observational covariance or orbital estimate is fitted here.
The covariance is a formal orbit-fit approximation, not a measured frequency
distribution or an independently validated impact probability.

Convert elliptic osculating e,q,tp,node,peri,i to heliocentric ICRF position
and velocity using the supplied solar GM and IAU76/80 ecliptic obliquity
84381.448 arcsec. Preserve the nominal tp decimal string before subtracting
it from epoch; apply delta_tp to the reduced time, not a large JD.
Mean A1/A2 come from this same SBDB snapshot. Document small differences
from rounded Horizons NG header values; do not silently replace the means.

Two old-epoch Horizons vectors independently check nominal element/frame
conversion. Frozen gate: 10 m position, 1e-6 m/s velocity at the covariance
epoch, matching JPL#220/ICRF/Sun/AU-D/TDB. Strict schema, covariance SPD,
availability, center/frame/epoch gates must pass before propagation.
Scaled Cholesky has no jitter or eigenvalue clipping; failures remain explicit.

## Physics, coefficient coverage and time

Extend public DE441 byte-range coverage to 2020-12-31 through 2030-01-02
with origin 2021-01-01; 14 Type-2/J2000 segments, at most 2 MiB transferred.
Use a separate loader/input design; preceding sources and results stay frozen.
Compare direct coefficients against every available old source knot over
this window: <=1 cm and <=1e-6 m/s. Do not use target future states in forces.
The controls are the retained source tables of the preceding force context,
plus the original daily Venus backbone replaced by its dense 2029 audit table.
Counts include those separate control tables, not distinct physical events.
SB16 retains its existing daily interpolation with an explicit time-origin
adapter. Main planets/Sun use direct coefficients with complete center chains.

Use Sun-only outer PPN, fixed-J2000 Earth J2, all previous Newton sources,
and the SBDB r^-2 NG law with A1/A2 varied jointly with orbital elements.
Solar J2 and Earth J3/J4 off. No added force fitted to the residual.
Native coordinates have a uniformly moving solar origin at 2021; save
native and converted heliocentric states/endpoints separately.

The output grid contains 30-day nodes before 2029, the exact forecast origin,
and the preceding 797 forecast-year diagnostic knots translated by 2922 days.
Stop at 2030-01-01 (3287 days after covariance epoch). Do not re-center the
result on the old 2029 initial state: this is a new nominal trajectory with
its own propagated covariance. Report their initial and final discrepancies
as diagnostics only; do not present a changed start as an improved old score.

## Exactly 34 propagations

Two new nominal trajectories: unchanged DP extreme and ultra settings.
Thirty-two DP-ultra probes: for each of eight Cholesky columns L_j, use
delta = +/-sqrt(8)*scale*L_j at scale 0.25 and 1.0. Points are represented
as offsets about the nominal orbital parameters, with equal weights 1/16.
They reproduce the input covariance algebraically. They are deterministic
cubature controls, not independent Monte Carlo draws or confidence samples.
No clipping of signed fitted NG probes or adaptation to outputs.
Retain actual initial states and A1/A2, nominal offsets and precise time basis.

Maximum DP step is 0.25 days. Save all accepted endpoints and output nodes.
Each checkpoint is immutable, with input/source/runtime fingerprint. Resume
only incomplete work; a completed run must preserve hashes and timestamps.
Disjoint nominal/small/full groups may execute concurrently after a single
freeze. Their elapsed times are diagnostic bookkeeping, not an online cost
benchmark; full ensembles are not assumed to be cheaper than a fixed model.

## Linear and nonlinear uncertainty diagnostics

Let B_j(t)=[X_plus(t)-X_minus(t)]/[2*sqrt(8)*scale] for the small scale.
These columns estimate J(t)L, including NG dependence. Linear propagated
covariance is B B^T. Save the six-state matrix and the augmented eight-state
matrix whose last components are A1/A2 with explicit units.

Compare the full-scale cubature weighted covariance (about its own weighted
mean) with the linear covariance. Use position and velocity 3x3 blocks
separately, Frobenius relative discrepancy against the linear block; target
<=1 percent at every prescribed diagnostic date. Also compare B at both
scales and quarter-scale covariance divided by scale². Differences are formed
before SI conversion; compute moments of offsets from the nominal to avoid
AU-scale cancellation. No mixed-unit eigenanalysis.

Report principal position standard deviations, sqrt(trace(position covariance))
and weighted mean displacement. The mean-shift diagnostic relative to the
linear major position sigma has a prespecified 1 percent criterion. It is a
numerical/nonlinear diagnostic, not proof of Gaussian tails. Retain failures.
Raw eigenvalues/residuals must remain available; negative eigenvalues are
reported unresolved rather than silently clipped. A principal 1-sigma scale
does not define a 68-percent three-dimensional containment sphere.

Compare nominal extreme/ultra at the old <=1 m criterion. No new independent
solver for the full nine-year joint ensemble is included; the earlier annual
cross-solver limit ~1.2 m remains a warning, not a bound transferable to this
longer integration. Report any empirical numerical differences explicitly.

Formal major sigma exceeding 0.1/1/10 km may be exported as a descriptive
dispersion flag. This is not a validated no-candidate rule or accuracy guarantee.
No covariance is merged with teacher residuals without a discrepancy model.

## Verification and completion

Review runner before its first output write. Offline verification checks all
raw hash/size/Git exclusions, fresh schemas/epochs/NG values, frozen closure,
nominal initial conversion, signed probes, every time/state conversion and
endpoint alignment, source gates, checkpoint identities, independent covariance
and linearity calculations. Review verifier before writing final artifacts.
Keep model-derived uncertainty separate from nominal teacher agreement,
observational calibration, warning validation and population generalization.
No installations, commits or publication. Record the limitations and next
contract in reports and durable project memory after verified completion.

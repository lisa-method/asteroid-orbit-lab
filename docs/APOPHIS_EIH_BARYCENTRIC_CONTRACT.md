# Direct barycentric EIH follow-up — 2026-09-09

Frozen before the first propagation in this coordinate formulation. This is
an explicit post-hoc frame-consistency follow-up to the completed 18-record
[heliocentric PN audit](APOPHIS_EIH_CONTRACT.md), whose preliminary DP results
were inspected before choosing this follow-up. No outcome-driven parameter
fit or new force component. Preserve the earlier matrix and all branches.

## Motivation and mathematical change

The earlier heliocentric force subtracts model-derived Newtonian and PN Sun
accelerations. Those need not equal d²r_Sun/dt² of the externally supplied
DE441 ephemeris. Evaluate the alternative directly in an inertial coordinate
frame; no numerical differentiation of ephemerides and no guessed missing
solar acceleration are introduced.

Integrate q = r_b − r_Sun(0) − t v_Sun(0), w = v_b − v_Sun(0), with q'=w
and w'=a_b. The initial q,w exactly equal the old heliocentric initial state;
this avoids changing it by rounding an addition of the initial Sun position.
At every output, r_h=q−[r_Sun(t)−r_Sun(0)−t v_Sun(0)] and
v_h=w−[v_Sun(t)−v_Sun(0)]. The frame moves uniformly, so no fictitious
Sun-origin acceleration is subtracted. Barycentric PN velocities are explicitly
recovered as w+v_Sun(0); this is not a Galilean-invariance assumption for PN.

Newtonian forces are direct Sun + 26 perturbers (10 major and SB16).
PN uses eleven major sources with Newtonian source accelerations exactly as
before. Earth J2 fixed-pole and solar J2 act directly, without indirect Sun
terms. NG uses the instantaneous heliocentric position and velocity. No
Earth J3/J4, new parameters, source fits, new ephemerides or target states.
This checks the consequences of an imposed ephemeris origin, not a complete
independent implementation of all DE441/Horizons physics.

## Prespecified matrix

All four PN arms from the preceding audit: Sun-only/all-major outer sum ×
solar J2 off/on. Each gets compensated DP extreme/ultra and compensated RK4
fine, all settings unchanged. Twelve new propagations, same annual initial,
797 requested times, eight references and primary annual-daily teacher.
Retain every branch. Compare against each matching heliocentric PN record,
along with DP setting differences and DP ultra/RK4 cross-solver differences.
Keep the prior 1 m numerical criterion and 0.1/1/10 km accuracy budgets.

Keep true native solver states/endpoints in the uniform inertial frame and
also converted heliocentric states/endpoints for the existing evaluator.
Record both coordinate bases explicitly. An independent verifier must
recompute every conversion from the raw solar ephemeris, check exact initial
conditions, native/requested alignment, all errors and cross-frame differences.
Never label an inertial native trace as directly heliocentric.

## Controls and provenance

Before running: kinematic conversion for an analytically accelerated Sun,
exact initial conversion, direct barycentric force comparison, stationary-Sun
limit, and the prior EIH tests. Freeze all numerical source/config/contract,
input, earlier-matrix and runtime hashes. Completed checkpoints are immutable.
No further propagation after completion; resume only verifies/reuses records.
No packages or data downloads are needed. Prior standalone/selector scores
remain unchanged; any improvement is an inspected-event diagnosis.

# Apophis PPN/EIH and frame audit — 2026-09-09

Separate post-hoc experiment, frozen before the first new forward propagation.
The 2029 event and all previous target references are inspected data. No model
fitting, new selector score, operational accuracy claim or new observations.

## Physical specification

Implement the beta=gamma=1, order 1/c² part of Eq. 2 in
[Holman et al. 2023](https://arxiv.org/html/2303.16246v1), independently from
the published mathematical expression, without copying ASSIST code. Massive
GR sources: Sun, Mercury, Venus, Earth, Moon, Mars/Jupiter/Saturn/Uranus/Neptune
system monopoles and Pluto system. Sixteen small perturbers remain Newtonian
only, as in that equation. Source accelerations inside PN are computed from
mutual Newtonian gravity of the eleven major sources, not second-differentiated
from tabulated states. The paper explicitly permits this order-consistent choice.

Keep velocities barycentric: v_source,b = v_source,h + v_Sun,b and
v_target,b = v_target,h + v_Sun,b. Position differences are translation
invariant; use heliocentric coordinates for those to avoid extra subtraction.
For the heliocentric target derivative use PN_target − PN_Sun, with PN_Sun
computed from all other major bodies and the Sun excluded as its own source.
The Newtonian baseline already contains the corresponding direct-minus-indirect
terms, including SB16. The old solar Schwarzschild term is replaced, never
added a second time. Planetary ephemerides remain exogenous.

This transformation is consistent with the stated restricted force model;
it is not proof that its solar acceleration exactly equals the second
derivative of DE441 Sun states. Missing source terms, interpolation and model
truncation can remain. No numerical derivative of Sun's ephemeris is used.
No claim that we reproduced all internals of Horizons is permitted.

The paper distinguishes all-major-source PPN from Horizons' Sun-only outer
sum. Test both. Inner potentials/source accelerations still use all eleven
major bodies in the Sun-only branch. The origin's PN acceleration retains
all sources in both branches because the origin is the ephemeris Sun.

## Fixed experiment

Six arms (all retained): prior fixed-pole baseline; add Pluto only;
Sun-only PPN; all-major-source PPN; each of those PPN variants plus solar J2.
Every PPN arm includes Pluto. Earth J2/radius, fixed J2000 pole, NG coefficients
and all existing perturber data are unchanged. Earth J3/J4 off. Solar J2 and
pole reuse the precise DE441 constants from the preceding audit. No tuning
of gravity constants; the old solar GM exactly matches DE441 at stored
precision and the Pluto value is 975.5 km³/s² in both sources.

Same annual-hourly initial, 2029-01-01 to 2030-01-01, 797 requested times and
eight separately evaluated references. Primary teacher: matched annual-daily.
No future target state is used by a force evaluation. The diagnostic output
grid contains an already inspected encounter refinement and is not an
operational output-scheduling claim.

Two compensated DP settings plus fine compensated RK4 in every arm: 18
records, including three exact reused prior fixed-pole baselines and 15 new
propagations. Scheduling may separate DP and RK4 jobs, but the final matrix
requires all 18 prescribed records. Cross-solver gate <=1 m, unchanged.
Record max grid position/velocity errors, errors at selected days, DP setting
differences, force-effect vectors and their cross-solver differences. Do not
infer an absolute error bound from agreement or discard worsening arms.

## Inputs and verification

Two anonymous Horizons requests, ICRF/FRAME, geometric AU-D, TDB:
Sun relative to SSB and Pluto-system barycentre relative to Sun, hourly
2028-12-31 through 2030-01-02 (8809 states each). Input URLs, coordinate
centres, headers, raw SHA/size and download source freeze are stored separately.
Existing target raw data is immutable; no new target trajectory is downloaded.

Before integration: stationary-Sun Schwarzschild limit, two-body finite-mass
1PN relative acceleration, translation/rotation/permutation/c scaling and
invalid-state tests. Test the frame composition independently at synthetic
states. Confirm unchanged baseline force values against the old factory.
Source/config/contract/runtime/input hashes are frozen before first rollout.
Afterwards independently recompute all errors and verify initial/native traces,
reuse identity, raw SHA/size/Git exclusion, and no-change resume. Completed
records are immutable. All code uses the existing uv Python 3.14.7 and stdlib;
no packages, binary ephemerides, installation, publishing or remote changes.

Reference: [Horizons coordinate/ID manual](https://ssd.jpl.nasa.gov/horizons/manual.html).
Sources accessed 2026-09-09. Article-derived facts motivate the experiment;
the numerical outcome may contradict an improvement hypothesis.

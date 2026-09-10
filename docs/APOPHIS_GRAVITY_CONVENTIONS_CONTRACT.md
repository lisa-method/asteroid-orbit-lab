# Apophis gravity-convention audit — 2026-09-09

Frozen before the first new propagation. Post-hoc diagnosis of the inspected
2029 Apophis encounter, not new selector validation or an operational accuracy
guarantee. No tuning, fitting to reference residuals, new bodies or dependencies.

The saved Horizons Apophis headers explicitly declare `EOBL_MOD = 2x0 (J2 only)`
and `EOBL_LIM=1.000 au`. Thus the prior numerical J3/J4 effect does not identify
missing Earth harmonics in this teacher. A lower teacher error can be compensating
another mismatch. Those experiments and all scores remain frozen.

Two independently motivated factors: Earth mean IAU versus fixed J2000 pole,
and solar J2 absent versus present. All four combinations are retained. Earth
J2 coefficient/radius, 25 perturbers, solar Schwarzschild, nominal header NG,
relative-calendar ephemerides and initial state are unchanged. No Earth J3/J4.
Earth J2 still has no cutoff; the teacher's cutoff convention is an open detail.
DE441 Earth J2 differs slightly from our retained value: do not change it in
this factorial experiment. Solar J2 is an isolated test-particle term, not full
relativistic DE441/Horizons dynamics or a complete moving-Sun force model.

Only the annual-hourly initial state at 2029-01-01 is used. The matched primary
teacher is annual-daily, with all eight previous references also evaluated and
kept separate. Future target states enter only evaluation. The 797 requested
times (including inspected encounter refinement) are the previous diagnostic
grid; this is not a new operational output-scheduling experiment.

Compensated DP extreme/ultra and compensated RK4 scale=0.125 are compared on the
same grid. Three unchanged baseline records are reused with exact source hashes;
nine new propagations produce twelve records. Baseline RHS equality is checked
at every baseline requested state. Native accepted endpoints are retained.
The pre-existing <=1 m cross-solver criterion remains unchanged; agreement is
empirical sensitivity, never a certified error bound. Compare force-effect
vectors across solvers, not differences of scalar reference-error norms.

Report maximum grid position and velocity errors, errors at preregistered days,
DP setting differences, cross-solver differences and force-effect differences.
Publish all branches, including worsening ones. Numerical agreement and lower
teacher mismatch do not establish uniqueness of a physical explanation.

Sources (accessed 2026-09-09):

- [JPL DE441 constants header](https://ssd.jpl.nasa.gov/ftp/eph/planets/ascii/de441/header.441),
  anonymous 22,802-byte download, immutable raw copy and SHA/size manifest.
  Read `J2SUN`, `ASUN` directly from groups 1040/1041; preserve original strings.
- [ASSIST force conventions](https://github.com/matthewholman/assist/blob/main/src/forces.c),
  Earth pole RA=0/Dec=90 degrees; solar pole RA=286.13/Dec=63.87 degrees.
  The comment linking the Earth convention to Horizons motivates a hypothesis,
  not proof of the exact internals of our saved JPL#220 solution. No code copied.
- [Holman et al. 2023](https://arxiv.org/abs/2303.16246): solar oblateness and
  full relativistic terms provide further model-comparison context.

Config, contract, source dependency closure, prior freezes/matrices and all raw
inputs are hashed before propagation. Completed records are immutable; resume
must read them without rewriting. Results remain local and Git-ignored.

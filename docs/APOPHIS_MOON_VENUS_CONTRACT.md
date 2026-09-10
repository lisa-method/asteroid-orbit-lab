# Apophis: Moon and Venus input audit v1

Frozen design, 2026-09-08, before downloading the four new references or
running new forecasts. This is a post-hoc diagnostic of an inspected object,
not a selector validation, a new holdout score, or a physical parameter fit.

## Questions and fixed physics

Both Moon and Venus gravity already exist in the model. Does extending the
five-minute Earth/Moon ephemeris across the lunar encounter change the annual
forecast? Does replacing daily Venus Hermite interpolation with hourly and
15-minute knots change it? Removing each body's force is a diagnostic of its
importance, not a proposed improved force model. No gravitational constant is
scaled or fitted. Sun + nine planetary perturbers + solar Schwarzschild + SB16
+ nominal header NG + Earth J2 with IAU pole are held fixed otherwise.

The Moon reference minimum previously occurred at the boundary of the stored
36-hour window, while radial separation was still decreasing. Download
Earth/Moon/Apophis at five-minute cadence for Apr 10–18 2029 and Venus at
15-minute cadence for Dec 31 2028–Jan 2 2030. These are anonymous, geometric
Horizons vectors: Sun centre, ICRF/FRAME, AU-D, TDB, no light-time correction.
Reject incompatible coordinates, NG headers, or overlapping old/new states
above 1 cm position / 1e-6 m/s velocity; do not silently mix a changed solution.
Raw responses and retrieval provenance are immutable and remain ignored.

## Matrix and controls

Use the nine arms in `configs/apophis_moon_venus.json`: old, Moon-only extended,
Earth+Moon extended, Venus-only hourly, Moon+Venus, Earth+Moon+Venus hourly,
Earth+Moon+Venus 15-minute, and diagnostic no-Venus/no-Moon forces. Hourly
Venus is a fourfold decimation of the same new 15-minute source (matching
epochs are checked against the stored development30 hourly source). Earth/Moon
updates replace their old knots in the extended window, retaining old daily
backbone outside. Preserve original perturber order and mass constants.

All nine arms run with the existing independent Dormand–Prince `tight` and
`tighter` settings. Old and most detailed all-force arms also run with RK4
scales 0.5 and 0.25: 22 annual integrations in total. Same Jan 1 2029 initial
state, 365-day horizon, old 797-point evaluation/output-stop grid. Reproduce
saved stage3 old solver summaries within 1e-8 km before interpreting changes.
No source changes to the frozen force, solver, interpolation, or selection code.
Absolute JD arithmetic is deliberately held unchanged in this experiment.

Save every accepted step. Evaluate expanded target reference times by cubic
Hermite interpolation of those endpoints without resetting the forecast to
reference states. This dense diagnostic has interpolation as well as solver
error; compute sampled minima from raw reference and bracket/refine with
Hermite, label the distinction. Future asteroid states are evaluator-only.

## Evidence and interpretation

Report max/final position and velocity errors on the identical old grid;
paired trajectory differences for every input/force ablation; within-arm
tolerance and step differences; detailed reference encounter geometry and
error growth around Earth and Moon. Compare dense interpolated outputs with
their values on requested knots as a consistency check. Do not use subtraction
of scalar maximum errors as a trajectory separation.

The current annual result is sensitive to solver/step at hundreds of metres
to kilometres. A small change in reference agreement is not identification
of a missing force or a numerical error bound. Improved agreement must be
distinguished from convergence, and removing a force cannot validate the
teacher's force decomposition. Record negative/ambiguous results as such.

Before first integration save source/config/contract/raw/manifest SHA-256,
Python executable/version/platform, and immutable configuration fingerprint.
Checkpoints complete one arm/solver at a time and are reusable only under
identical hashes/runtime. Final artifacts are not overwritten or resumed into
a changed experiment. No changes to prior development30/v2/fresh12 records.
No dependency installation, commit, remote, or publication is part of this audit.

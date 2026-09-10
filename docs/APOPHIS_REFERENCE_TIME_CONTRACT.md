# Apophis reference and time-coordinate audit v1

2026-09-08, frozen design before new reference retrievals or integrations.
User requested verification of reference consistency and numerical time
arithmetic. Inspected object, post-hoc audit, no new selector validation.
No physical force, mass, NG coefficient, or initial state is fitted.

## A. Reference-only comparisons

Download four anonymous Horizons vector tables for 99942: annual Jan1 2029
through Jan1 2030 daily (366) and hourly (8761), and Apr10–18 2029 daily (9)
and hourly (193). Keep coordinates Sun centre, ICRF/FRAME, geometric AU-D,
TDB, and same table2 settings. Compare exact common epochs among new tables,
old daily2020–2030, old5min36h, and new5min8d. Check orbit identity, NG,
frame/units/time headers, finite monotonic samples and full provenance.
Reject an orbit-solution change; differences with the same solution are
results to investigate, not grounds for replacing historical raw inputs.

Test cadence at fixed bounds, bounds at fixed cadence, initial-state
consistency, and reference difference growth around the Earth encounter.
Keep labels on every teacher. Re-evaluate saved forecasts on common dates
against each teacher; never choose the reference by the smallest error.
Horizons integrates small-body solutions on demand; its manual describes
integration from the osculating epoch to the requested start. This is context,
not proof of the mechanism of any measured discrepancy:
https://ssd.jpl.nasa.gov/horizons/manual.html#numerical-integration

## B. Time-coordinate comparisons with fixed physical inputs

Reuse the detailed all-force inputs of Moon/Venus v1.1:
Earth/Moon5min Apr10–18 merged into old daily, Venus15min, other six planets
and SB16 daily. Sun + nine perturbers + solar GR + SB16 + nominal NG + J2 IAU.
All reference target positions after the start remain evaluator-only.

Three numerical representations:
- legacy absolute-JD callbacks, reproducing saved detailed DP tight/tighter
  and RK4 scales0.5/0.25: four integrations with unchanged797 output stops;
- relative_float_knots: both solver/RHS and ephemeris operate in days since
  Jan1 2029; knot epochs are original floatJD minus start. J2 pole evaluates
  centuries as ((start-J2000)+relative_days)/36525. Same force equations;
- relative_calendar_knots: same relative pathway, but ephemeris knots and
  requested target times use integer Gregorian TDB calendar differences,
  converting to days only after subtraction. Preserve all original states.
  Stored timestamp labels here are exact daily/hourly/5-minute grid times.

Both relative variants run DP tight/tighter/extreme (1e-10/1e-12/1e-13,
absolute tolerances from saved config) and RK4 scales0.5/0.25/0.125:
12 relative runs,16 total. Keep all accepted endpoints in native time basis.
Compare paired position/velocity shifts, error by horizon and solver/step
differences. Do not declare a tolerance sweep a rigorous error bound.

After input checks, additionally propagate the new annual-hourly initial
state with relative_calendar_knots, DP tighter/extreme: two runs, unless the
initial six-state is exactly identical, in which case reuse and report that.
Report old and new initial forecasts against every annual teacher on common
dates. This crosses initial-state and reference choice without fitting.
No rollout is reset from a future teacher state. No new candidate tuning.

## Gates and preservation

Before first propagation freeze this contract, configs, all transitive source
imports, consumed raw/manifests, prior saved matrix, and Python/runtime.
Checkpoint each completed representation/solver/initial combination. Reuse
only under identical freeze. Legacy four summary fields must reproduce to
1e-8 in their stated units before new-arm interpretation. Native trace
lengths, finiteness, coverage, exact initial and output consistency are checked.
Scientific summaries are recomputed offline from saved states.

Tests must cover cubic Hermite with velocity, native time translation,
sub-JD-ULP stages, exact knot recovery, calendar/float distinction and force
agreement at common knots including J2. Old modules/results remain frozen;
new modules do not silently become the operational v2 implementation.
No installations, commit, remote, publication, or external messages.

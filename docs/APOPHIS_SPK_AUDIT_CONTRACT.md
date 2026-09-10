# Apophis DE441 interpolation and independent force audit — 2026-09-09

Freeze this contract, numerical source closure, configs, runtime and all
input hashes before any new propagation. This is a post-hoc inspected-event
diagnosis following the 2.351305 km direct-barycentric Sun-only PPN result;
no independent selector validation or physical orbit uncertainty is claimed.

## Questions and invariant physics

Can interpolation between saved planetary ephemeris nodes account for the
remaining mismatch? Does an independently coded high-precision force agree
with the existing implementation at identical supplied states?

Keep Sun + ten major perturbers (eight planets, Moon, Pluto system), SB16
Newtonian, Sun-only OUTER PPN with all eleven major bodies in inner sums,
nominal A1/A2/A3, fixed J2000 Earth J2 only. Solar J2 and Earth J3/J4 off.
Same constants, same matched annual-hourly initial state, annual-daily
teacher, 797 output knots and eight separate reference tables. No fitted
coefficients, no target trajectory downloads, no ID-dependent force law.

## Exogenous input and independent checks

Read unmodified byte ranges of NASA NAIF de441_part-2.bsp covering
2028-12-31 through 2030-01-02 TDB. Include center chains via EMB/Mercury/Venus
barycenters; targets 1..10, 199, 299, 301, 399. At most 2 MiB transferred;
validate HTTP 206, exact ranges/size/source identity and preserve raw chunks
with SHA/size/source metadata. No complete kernel or SPICE installation.
Input design is fixed independently before coefficient downloads.

Read only Type 2/J2000 records with analytic Chebyshev velocity. Compute
reduced time from (origin_ET - record_midpoint) + delta_seconds, avoiding
addition to a distant segment-start or absolute Julian date. Disable
extrapolation and enforce correct record/center coverage. Synthetic tests
must cover both endians, multiple records/boundaries, split-time precision,
analytic derivatives and malformed inputs. Independently parse DAF/index
structure in the loader and check the chain back to the SSB.

Before propagation, compare direct SPK with every existing planetary/Sun
node in the retained year: <=1 cm position and <=1e-6 m/s velocity.
Keep failures explicit; a failed source gate means the candidate cannot
be called a pure interpolation replacement. Do not adjust these thresholds
after looking. A failed gate pauses dependent propagation for investigation.
Diagnostic evaluations at interval quarter/mid/three-quarter points compare
old Hermite and native coefficients; no target reference state enters inputs.

Independently coded Decimal50 Newton, PPN, J2 and NG terms are checked
against float implementations on all 797 saved baseline predicted states,
with genuine barycentric velocities and correct Sun-relative NG. SB16
contributes only to Newtonian terms. Store per-term discrepancies and total
RHS discrepancy; total absolute numerical threshold 1e-17 AU/day².
This tests implementation agreement at the supplied state, not identity
with closed JPL code. A failed force gate pauses dependent propagation.

## Frozen six-arm matrix

1. `baseline`: three reused Sun-only PPN barycentric records, unmodified.
2. `same_knots`: sample native SPK at each source's exact old knot times,
   then use the unchanged cubic Hermite interpolator. This isolates source
   agreement/rounding from removal of interpolation.
3. `hourly`: sample all major sources and Sun every hour, then use the same
   Hermite interpolator. This is a uniform cadence control, including the
   removal of the old five-minute Earth/Moon refinement in this arm.
4. `earth_moon_direct`: replace Earth and Moon only with direct SPK, other
   sources and Sun retain their original interpolators.
5. `other_major_direct`: replace Sun and other major perturbers with SPK,
   retain old Earth/Moon interpolators.
6. `all_direct`: direct SPK for Sun and all major bodies.

All arms retain the old SB16 interpolation. Each uses the previous DP
extreme/ultra settings and compensated RK4 fine (scale 0.125), unchanged
step maxima and causal encounter selector. Eighteen records: fifteen new
propagations, three exact baseline reuses. All outcomes retained.

Use the previous uniform inertial native coordinates and explicit conversion
to heliocentric outputs. The same input heliocentric state is exact at zero.
The RK4 selector receives converted predicted heliocentric state. Record
true native requested states/endpoints and converted states/endpoints.
Compare sampled annual errors, diagnostic-day growth, force/cadence effects,
DP extreme/ultra and DP ultra/RK4 differences (previous <=1 m criterion).
Accuracy budgets remain 0.1/1/10 km; no event-level recall or probability.

## Verification and completion

Independent offline verifier must recompute raw/index/center/frame checks,
record specs, initial/native alignment, every conversion and reference error,
paired differences and checkpoint identities. Freeze before propagation;
only incomplete checkpoints can be resumed. Final verifier reviewed before
its first artifact write. Completed numerical sources/results unchanged.
Update report and project memory with all branches, failed gates and limits.
No installs, publication, fitted residual closure or unannounced new rollout
matrix. Further investigations require a separate declared follow-up.

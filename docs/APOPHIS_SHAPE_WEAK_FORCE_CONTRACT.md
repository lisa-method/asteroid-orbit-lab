# Apophis: shape and higher zonal gravity audit

2026-09-08. Frozen before any new propagation. User requested a weak-force
test plus research on Apophis shape and comparison with other bodies.
Post-hoc sensitivity audit, no new selector score or fitted parameter.

## Inputs and unchanged baseline

Reuse the reference/time audit's full physical inputs, relative_calendar
time, old797 integration stops, initial Jan1 2029 and 365-day duration.
Use old and new annual-hourly initial states, always report both annual
teachers on common366 dates. Other reference comparisons remain labelled.
Nominal A1/A2 remain exactly as in their header. No extra radiation/thermal
force is stacked on those parameters: that would risk double counting.

## Higher Earth zonals

Add J3 alone, J4 alone, and J3+J4 to the baseline, with no rescaling or fit.
Use IERS Conventions chapter6 table6.2: normalized C30(J2000)=0.9571612e-6,
C40=0.5399659e-6 and secular rates4.9e-12/4.7e-12 per Julian year.
Jn=-sqrt(2n+1)*C_n0, using the chapter's normalization. Potential scaling
radius6378.1363km, GM398600.4415km³/s² from IERS. These are TT-compatible
scaling values; retained as a conventional isolated-force approximation,
not a complete TDB/ITRF/EOP geopotential implementation. The existing point
mass GM and J2 are unchanged. Approximate mean IAU pole as in baseline;
only m=0 zonals, no terrestrial longitude/time-of-day required. Compute
direct and matching heliocentric indirect acceleration for each added term.
Source: https://iers-conventions.obspm.fr/content/chapter6/icc6.pdf

Baseline/J3/J4/J3J4 each use DP tighter/extreme and each initial source:
16 integrations. Four baseline outputs must exactly match the corresponding
completed reference/time forecasts (all797 requested six-states).
Report paired effect and its change between solver tolerances. RK4 is known
not converged here; no interpretation as a rigorous numerical bound.

## Shape as a separate finite-size sensitivity experiment

Public PDS radar model v1.0, DOI10.26033/ydyq-5756, is preliminary and is
based on 2012–2013 radar data (Brozovic et al.2018). Downloaded OBJ and labels
are immutable; compute volume and mass-normalized central second moment
S=<delta delta^T> by signed tetrahedra, assuming homogeneous density.
Do not identify an OBJ axis orientation with the actual 2029 ICRF attitude.
Do not offset orbital initial states by the mesh centroid.

For a point gravitational source with mu and displacement r, use leading
finite-size COM acceleration:
delta_a = 3mu/(2|r|^5) * [2Sr + tr(S)r - 5(r^T S r)r/|r|²].
Add this term for the Sun and all25 saved point perturbers; the origin's
point acceleration is unchanged, so no extra indirect term is applied to
the asteroid's own finite-size term. No self-force, new satellite, torque,
deformation, or spin evolution is invented.

Three fixed cyclic orientations of the PDS tensor are illustrative controls;
they are not actual attitude forecasts or exhaustive orientation bounds.
Also run an isotropic sphere S=tr(S)/3 I as an exact zero-force control.
These four controls use new initial only and DP tighter/extreme:8 runs.
Additionally report the pointwise orientation-independent force upper bound
3 tr(S) sum(mu/d^4), sampled on the baseline forecast, and size/distance.
The force bound is not a propagated position-error bound. No shape estimate
is fitted to the remaining10km residual. All24 runs are preregistered here.

## Verification and reporting

Freeze config, this contract, transitive imported source, consumed raw and
manifests, mesh moments, previous freeze/matrix and runtime before first run.
Checkpoint each of24 runs with native relative time and requested states.
Check finite/increasing traces, exact initial/native output matches, four
baselines, exact sphere controls; independently recompute position/velocity
metrics and paired shifts. Unit checks include J2 equivalence, potential
gradients, covariance, point/sphere limits and exact dumbbell comparison.
Mesh integration is checked on an analytic tetrahedron and translated mesh.
No dependencies, global changes, commit, remote or publication.

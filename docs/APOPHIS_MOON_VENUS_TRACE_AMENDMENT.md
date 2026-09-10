# Trace serialization fix v1.1 — 2026-09-08

The first execution of v1 completed old DP tight and tighter with exactly zero
differences in all four baseline summary fields. It stopped before saving old
RK4 because converting relative accepted times to absolute Julian Date rounded
two distinct adjacent endpoints to one JD. The uniqueness gate correctly
rejected this trace. Original source/config/freeze and the two DP checkpoints
remain unchanged in `outputs/apophis_moon_venus/`.

This is a serialization/evaluator fix, not a new integrator or force-model
change. V1.1 retains each solver's **native accepted time basis**: RK4 relative
days since start, DP absolute JD TDB. Metadata states the basis and start JD.
The Hermite evaluator converts requested epochs to that solver's basis without
round-tripping the RK4 endpoints through absolute JD. No sorting, dropping or
coalescing duplicate states. All four old baselines must again reproduce the
saved stage3 summaries; same 22-run design, old 797 output stops, same physics.

The v1.1 source and config use a new namespace and freeze before first new run.
The two completed old DP runs are repeated there, with v1 retained as the failed
engineering attempt. No non-baseline arm ran before this fix. This finding
does not establish the cause of the annual physical/numerical mismatch.

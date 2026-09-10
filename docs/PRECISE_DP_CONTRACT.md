# Compensated DP follow-up, 2026-09-08

This is a post-hoc numerical follow-up to the completed 24-run precise RK4
experiment, frozen separately before its first asteroid propagation. The
original 1 m cross-solver criterion failed in the old-initial-state branches:
2.73–3.39 m remain despite approximately centimetre-scale fine RK4 convergence.
Both failures and the original matrix remain intact.

The DP tableau, controller, physical force, initial conditions and 797 requested
nodes stay fixed. New `precise_dopri.py` requires relative epoch zero, uses the
representable endpoint difference for h, compensated accepted-state increments,
and `math.fsum` for weighted stage/error sums. Rejected steps do not change the
compensation. The FSAL derivative is evaluated at the compensated endpoint.
This combined numerical change is not an ablation isolating its three parts.

Twelve runs: old/annual-hourly initial state × baseline/J3J4 × three tolerances.
Tighter and extreme are inherited; ultra is rtol=1e-14, position atol=1e-16 AU,
velocity atol=1e-17 AU/day. Maximum step stays 0.25 day. Compare every run with
the frozen compensated RK4 at scale 0.125 (also 0.0625 for old baseline), using
maximum vector position difference over the 797 nodes. Retain all outcomes;
the strongest setting is specified in advance, not selected by teacher error.
The empirical cross-solver target remains ≤1 m on every branch at ultra.
This is not a rigorous global error bound or independent physical validation.

All previous source/input hashes, the new numerical module, runner, this
contract, config and runtime are frozen before propagation. Check native
endpoints, all eight teacher comparisons, checkpoint hashes and independent
vector differences offline. No force parameters are fitted; no selector,
fresh holdout, old result, dependency or publication is changed. The separate
30-object regression retains its already specified original DP settings.

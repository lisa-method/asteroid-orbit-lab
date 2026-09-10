# Input compatibility amendment — 2026-09-08

Recorded after the four downloads and before any new propagation. The original
strict overlap gate failed and stopped preparation; no forecast was run and
no raw file or original design was replaced.

Earth and Moon: new five-minute vectors match old daily and all 433 old refined
knots exactly. Venus: all 368 daily and 8,809 stored hourly overlaps match
exactly. New Apophis five-minute vectors match all 433 old five-minute knots
exactly; however, the nine overlapping daily knots differ by 4.147–86.707 m
(largest on Apr 18); velocity difference reaches 0.000292041 m/s. Old/new
Apophis headers name the same JPL#220 solution, 2024-Jun-25 solution date,
observation count, osculating elements and nominal NG law/coefficients. Header
identity does not prove identical numerical reference generation. The cause of
this daily/refined discrepancy is not determined here.

The declared Moon/Venus force-input test can proceed without mixing asteroid
references: keep the old initial state and old 797 target rows unchanged for
every integration and primary error metric. New target rows are **separate
secondary evaluator data** for eight-day encounter geometry and dense local
errors; their metadata must report the daily/refined difference. No old/new
target splicing and no revised annual validation score. The new target has
exact continuity with the old refined teacher inside its 36-hour overlap.

Keep the 1 cm / 1e-6 m/s gate for all planetary overlaps and the old refined
asteroid overlap. Accept the old daily asteroid mismatch only as an explicitly
recorded evaluator limitation, with unchanged JPL solution header and NG.
`finalize_apophis_moon_venus_inputs.py` implements these checks and writes the
immutable input-validation record. The original downloader and its design
freeze stay unchanged, and must not be rerun as though the strict gate passed.
For offline checks, rerun the finalizer or the matrix artifact verifier.

This amendment is motivated by provenance evidence, not trajectory errors or
choosing a better-scoring model. It does not add any integration to the matrix.

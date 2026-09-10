# Sampling amendment before trajectory evaluation — 2026-09-08

The first metadata-only selection stopped as required by the original
contract: the cached Jupiter catalogue contains exactly five numbered objects,
all already in development30. No new sample was saved, no target trajectories
were downloaded, and no new forecast/reference errors were computed.
The original method_freeze.json and sampler source in
outputs/fresh_holdout12/aborted_sampling_01 are retained.

Before fetching additional metadata, the Jupiter query is widened from
0.5 AU to 1.0 AU over the same 2026–2029 dates. All other groups, exclusion
rules, sorting, date limits, forces, selector, numerical settings, and metrics
remain as specified in FRESH_HOLDOUT12_CONTRACT.md. If this catalogue still
lacks two unused objects, selection stops again; no objects are silently
substituted. The chosen Jupiter distances and this sampling change must be
reported. This stratum is not directly comparable to the old <0.5 AU stratum.

The extra public CAD response and URL/time/hash are stored separately, then
the amended source/contract/catalogue freeze is written as method_freeze_v2.json
before selecting the actual sample. This is a sampling feasibility amendment,
not adaptation to trajectory performance. API parameters checked against:
https://ssd-api.jpl.nasa.gov/doc/cad.html (version 1.5).

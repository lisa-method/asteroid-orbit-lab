# Exact repeat control for the old long query

2026-09-08, after the four reference-only queries and before any new forecast.
The four new tables agree exactly at all common epochs and agree with old
refined references, while the old2020–2030 daily table differs by3.609m at
Jan1 2029 and17.211816km at Jan1 2030. Cadence alone does not explain this.
Retrieval date and requested bounds remain confounded.

Add one anonymous query using the **exact source_url stored in the old
manifest**, saving a separate raw response/manifest. Compare every common
state with the old long table and with the four new tables. Orbit/NG/header
identity is still a gate. Exact repeat agreement would support request-path
dependence; failure to reproduce would leave service-version/retrieval-time
differences unresolved. Do not claim a JPL internal mechanism merely from
same headers. The propagation matrix is unchanged, maximum18 runs.

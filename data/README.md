# Local data directory

Raw and processed datasets are stored locally and ignored by Git.

Current local layout:

```text
raw/horizons/          # immutable daily Horizons responses
raw/horizons_refined/  # immutable high-cadence event responses
raw/horizons_small_perturbers/ # immutable SB441-N16 perturber responses
raw/sbdb/              # immutable SBDB object metadata
interim/                # normalized source-specific CSV tables
processed/              # deterministic physical features and event tables
checksums/              # local manifests, source URLs and SHA-256 hashes
```

Versioned pilot-6 derived tables are stored under `interim/eda_pilot_6/` and
`processed/eda_pilot_6/`; the matching raw manifest is
`checksums/jpl_pilot_6_manifest.json`. Raw source files may be shared between
versioned pilots when their recorded SHA-256 digest is unchanged.

The B3+ force ablation uses 16 additional immutable Horizons responses and
`checksums/b3plus_small_perturbers_manifest.json`. Their masses and source are
versioned in `configs/b3plus_pilot_6.json`.

The current files form an engineering EDA pilot, not a frozen scientific
train/validation/test dataset. Rebuild derived tables from the raw layer; never
edit raw responses in place.

Do not place credentials, tokens, private data, restricted coursework data or
unlicensed third-party datasets here.

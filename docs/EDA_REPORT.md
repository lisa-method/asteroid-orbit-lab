# EDA technical pilot — JPL small-body dynamics

> Generated 2026-09-02T20:01:03.358043+00:00. This is an exploratory technical pilot, not the final train/validation/test dataset.

## Executive summary

- Parsed **20,090** asteroid states and **36,162** massive-body states.
- Coverage is 2020-01-01T00:00:00 through 2030-12-31T00:00:00 at an exact daily cadence in TDB.
- Missing/duplicate/non-finite state rows: **0**.
- Horizons vector headers consistently report heliocentric Sun-center, ICRF, AU/day and geometric (uncorrected) states: **True**.
- Correlation between `log10(max eta)` and `log10(|planetary acceleration|)` is **0.7307**; the heliocentric perturbation indicator tracks the omitted-force magnitude as intended.
- Daily sampling is sufficient for broad dynamics EDA but not for precise closest-approach time or distance; candidate events require local hourly/minute refinement.

## Data contract audit

| Check | Result |
| --- | --- |
| Horizons API versions seen | `1.2` |
| SBDB API versions seen | `1.3` |
| Reference center | `Sun (10)                        {source: DE441}` |
| Reference frame | `ICRF` |
| Units | `AU-D` |
| Output | `GEOMETRIC cartesian states` |
| Raw manifest files | 22 |
| Raw bytes | 11,419,526 |

The public documentation currently labels the Horizons API as version 1.3, while the returned vector payloads identify themselves as version 1.2. The response schema used here is validated from markers and headers rather than trusted solely from the version string.

## Object metadata

| Object | Class | NEO | PHA | a, AU | e | i, deg | MOID, AU | Observations | Arc, d | Non-grav. parameters |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Seraphina | MBA | no | no | 2.9013 | 0.131057 | 10.416 | 1.5376 | 10327 | 40005 | — |
| Eros | AMO | yes | no | 1.45824 | 0.222878 | 10.829 | 0.1488 | 9130 | 46582 | — |
| Apollo | APO | yes | yes | 1.47072 | 0.56018 | 6.3522 | 0.026318 | 3701 | 34822 | A2 |
| Phaethon | APO | yes | yes | 1.27146 | 0.889672 | 22.311 | 0.018732 | 8544 | 15468 | A2 |
| Apophis | ATE | yes | yes | 0.922359 | 0.191149 | 3.341 | 0.00010792 | 7370 | 6599 | A1,A2 |

The sample intentionally spans a main-belt control plus Amor, Apollo and Aten regimes. It is useful for pipeline stress-testing but is selection-biased and must not become the final evaluation sample.

## Physical ranges and consistency

| Object | r range, AU | speed range, km/s | median a, AU | median e | energy span | h span | FD velocity p95 3pt/5pt, m/s | force residual p95 3pt/5pt, m/s² | dominant perturber |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Seraphina | 2.510–3.285 | 15.31–20.02 | 2.8991 | 0.1311 | 1.80e-03 | 1.19e-03 | 0.0614/4.94e-07 | 4e-09/1.07e-11 | Jupiter system (100.0%) |
| Eros | 1.133–1.783 | 19.66–30.94 | 1.4582 | 0.2228 | 2.04e-04 | 1.43e-04 | 1.03/0.000123 | 2.53e-07/1.6e-10 | Jupiter system (95.1%) |
| Apollo | 0.647–2.295 | 13.04–46.26 | 1.4707 | 0.5599 | 5.15e-04 | 4.44e-04 | 6.9/0.00758 | 4.91e-06/9.87e-09 | Jupiter system (88.8%) |
| Phaethon | 0.140–2.403 | 6.38–109.36 | 1.2714 | 0.8898 | 2.29e-04 | 1.47e-03 | 46.8/0.529 | 0.000109/2.16e-06 | Jupiter system (92.5%) |
| Apophis | 0.746–1.311 | 23.42–37.64 | 0.9226 | 0.1912 | 1.68e-01 | 9.63e-02 | 4.37/0.00167 | 1.93e-06/1.64e-09 | Jupiter system (59.0%) |

`energy span` and `h span` are not expected to be zero: Horizons includes planetary/small-body perturbations and, for some objects, fitted non-gravitational terms. The table reports both three-point and five-point centered differences. Their gap is a direct cadence-sensitivity warning; the remaining force residual also combines omitted perturbers, relativity and non-gravitational effects.

![Heliocentric trajectories](../figures/eda/orbit_xy.svg)

![Perturbation indicator](../figures/eda/max_eta.svg)

![Earth separation](../figures/eda/earth_distance.svg)

## High-cadence encounter refinement

The strongest daily-grid candidate was re-queried on a 5-minute synchronized grid from 2029-04-13T00:00:00 to 2029-04-14T12:00:00 TDB.

| Body | Epoch TDB | Distance, km | Relative speed, km/s | eta | rho |
| --- | --- | ---: | ---: | ---: | ---: |
| Earth | 2029-04-13T21:45:00 | 38,014 | 7.422 | 46.8 | 0.0253 |
| Moon (at Earth minimum) | 2029-04-13T21:45:00 | 412,520 | 7.423 | 0.00489 | 1.19 |

![Apophis 2029 refined encounter](../figures/eda/apophis_2029_refined.svg)

The refined minimum is still grid-based, not a continuous optimization or operational hazard product. Its purpose is to quantify how much the daily backbone smears a fast encounter.

## Closest daily-sampled configurations

| Rank | Object | Perturber | Epoch TDB | Distance, AU | Distance, km | Relative speed, km/s | eta | rho |
| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | Apophis | Earth | 2029-04-14T00:00:00 | 0.000435525 | 65,154 | 6.809 | 15.9 | 0.0434 |
| 2 | Apophis | Moon | 2029-04-15T00:00:00 | 0.00158962 | 237,804 | 6.403 | 0.0146 | 0.688 |
| 3 | Apollo | Venus | 2021-09-19T00:00:00 | 0.069853 | 10,449,854 | 11.919 | 0.000237 | 10.3 |
| 4 | Phaethon | Mercury | 2028-01-31T00:00:00 | 0.118971 | 17,797,791 | 63.576 | 1.23e-06 | 97.1 |
| 5 | Apophis | Venus | 2024-03-08T00:00:00 | 0.12444 | 18,615,939 | 8.025 | 9.25e-05 | 18.3 |
| 6 | Apollo | Moon | 2023-05-28T00:00:00 | 0.210763 | 31,529,663 | 12.117 | 7.98e-07 | 90.1 |
| 7 | Apollo | Earth | 2021-11-22T00:00:00 | 0.211673 | 31,665,776 | 23.565 | 6.25e-05 | 21.4 |
| 8 | Apollo | Mercury | 2021-09-18T00:00:00 | 0.255124 | 38,166,082 | 3.485 | 1.62e-06 | 150 |
| 9 | Phaethon | Earth | 2027-12-23T00:00:00 | 0.265565 | 39,727,932 | 26.785 | 5.6e-05 | 27 |
| 10 | Phaethon | Moon | 2027-12-23T00:00:00 | 0.268094 | 40,106,357 | 25.789 | 6.76e-07 | 118 |
| 11 | Apophis | Mercury | 2027-10-01T00:00:00 | 0.344373 | 51,517,527 | 5.646 | 1.27e-06 | 211 |
| 12 | Phaethon | Venus | 2022-06-06T00:00:00 | 0.364904 | 54,588,884 | 31.009 | 7.93e-06 | 53.7 |
| 13 | Eros | Moon | 2025-12-01T00:00:00 | 0.395611 | 59,182,510 | 4.188 | 4.19e-07 | 173 |
| 14 | Eros | Earth | 2025-11-30T00:00:00 | 0.397648 | 59,487,252 | 3.725 | 3.4e-05 | 40.3 |
| 15 | Eros | Venus | 2020-10-16T00:00:00 | 0.428259 | 64,066,592 | 8.863 | 2.36e-05 | 63.8 |
| 16 | Phaethon | Mars system | 2028-04-24T00:00:00 | 0.537396 | 80,393,333 | 21.722 | 2.58e-06 | 79.7 |
| 17 | Apophis | Mars system | 2029-03-13T00:00:00 | 0.586578 | 87,750,807 | 4.531 | 9.45e-07 | 74.6 |
| 18 | Eros | Mercury | 2026-03-02T00:00:00 | 0.806805 | 120,696,302 | 24.904 | 2.19e-06 | 625 |
| 19 | Seraphina | Mars system | 2030-02-09T00:00:00 | 1.17108 | 175,190,613 | 6.785 | 2.48e-06 | 177 |
| 20 | Apollo | Mars system | 2021-07-03T00:00:00 | 1.3467 | 201,463,016 | 32.984 | 2.69e-07 | 170 |

These are minima on a one-day grid, not certified closest approaches. In particular, fast Earth encounters can be materially underestimated or shifted by many hours. They are discovery candidates for a refined event catalogue, not labels for model evaluation.

## Findings that affect data preparation

1. **Conventions are internally consistent.** The raw headers agree on Sun-center, ICRF, geometric states, TDB and AU/day; these fields must become explicit columns or dataset-level metadata rather than implicit assumptions.
2. **The pilot is strongly selection-biased.** Four of five bodies are NEOs and three are PHA objects. Final sampling needs SBDB-based strata across orbit class, eccentricity, inclination, perihelion and encounter intensity.
3. **Daily cadence is multi-purpose but not event-grade.** Retain a daily backbone for 7–365 day rollouts and add nested high-cadence windows around candidate encounters.
4. **Earth and Moon must remain separate.** The force table uses Earth and Moon separately, avoiding double counting the Earth–Moon barycenter while preserving lunar perturbations.
5. **Heliocentric planetary forces require the indirect term.** Features and `eta` were computed from `mu_p[(r_p-r)/|r_p-r|^3 - r_p/|r_p|^3]`; omitting the second term would create a frame artefact and misleading perturber rankings.
6. **Horizons is a richer teacher than the planned B3.** Payloads report DE441 plus small perturbers, and SBDB indicates fitted non-gravitational parameters for some targets. A residual against a planets-only B3 is therefore not automatically an unknown force or an ML target.
7. **Do not finalize thresholds from this sample.** `eta` and `rho` are useful continuous diagnostics, but routing thresholds must be selected on a larger train/validation pilot and frozen before test.

## Recommended next data-preparation step

Build a 30-object train/validation pilot using predeclared SBDB strata. Preserve the daily 2020–2030 backbone, refine only candidate encounter windows at hourly and then minute cadence, and reserve whole objects plus complete events before fitting any thresholds. The five objects here remain an engineering regression set and should not be reused as the final test set.

## Reproduction

The raw files are immutable and ignored by Git; manifests in `data/checksums/` record source URLs and SHA-256 digests. No credentials or authenticated services were used. All scripts run with the standard library only.

```bash
env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache uv run --no-project --python-preference only-system python src/download_jpl_pilot.py --config configs/eda_pilot.json --root .
env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache uv run --no-project --python-preference only-system python src/download_event_refinement.py --base-config configs/eda_pilot.json --event-config configs/eda_apophis_2029_refinement.json --root .
env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache uv run --no-project --python-preference only-system python src/run_eda.py --config configs/eda_pilot.json --event-config configs/eda_apophis_2029_refinement.json --root .
```

Official references: [Horizons API](https://ssd-api.jpl.nasa.gov/doc/horizons.html), [Horizons manual](https://ssd.jpl.nasa.gov/horizons/manual.html), [SBDB API](https://ssd-api.jpl.nasa.gov/doc/sbdb.html), [JPL astrodynamic parameters](https://ssd.jpl.nasa.gov/astro_par.html).

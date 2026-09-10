# B3+ small-force ablation — six-object engineering pilot

> Generated 2026-09-04T19:05:41.301471+00:00. This is a teacher-matching ablation, not a frozen historical forecast test.

## Protocol

- `B3`: Sun + nine planetary perturbers.
- `B3+GR`: adds the leading solar Schwarzschild 1PN term.
- `B3+GR+SB16`: adds the 16 massive asteroid perturbers associated with SB441-N16.
- `B3+GR+SB16+NG`: adds target-specific nominal Horizons A1/A2/A3 terms where present.
- Every stage is a fully recursive rollout from the same initial Horizons state.
- The GR term is a solar two-body correction, not the full EIH Solar-system formulation.

## Nominal non-gravitational parameters

| Object | A1, AU/d² | sigma A1 | A2, AU/d² | sigma A2 | Last observation |
| --- | ---: | ---: | ---: | ---: | --- |
| Apollo | 0 | — | -3.6569e-15 | 1.99e-16 | 2026-04-15 |
| Phaethon | 0 | — | -6.13994e-15 | 3.65e-16 | 2026-03-03 |
| Apophis | 5e-13 | 4.89e-13 | -2.90177e-14 | 1.86e-16 | 2022-04-09 |

These parameters reproduce the nominal Horizons force header. They are not treated as universally known forecast-time features; notably Apophis A1 has uncertainty comparable to its nominal value.

## Error by horizon

| Model | Horizon, d | Position median, km | Position p95, km | Velocity median, m/s |
| --- | ---: | ---: | ---: | ---: |
| B3 | 7 | 0.004944 | 0.07515 | 1.632e-05 |
| B3 | 30 | 0.09036 | 1.488 | 6.977e-05 |
| B3 | 90 | 0.8477 | 13.84 | 0.0002364 |
| B3 | 180 | 4.574 | 172.6 | 0.0008964 |
| B3 | 365 | 24.95 | 223.1 | 0.004209 |
| B3+GR | 7 | 0.000112 | 0.00263 | 3.722e-07 |
| B3+GR | 30 | 0.002143 | 0.04766 | 1.709e-06 |
| B3+GR | 90 | 0.0237 | 0.4444 | 7.137e-06 |
| B3+GR | 180 | 0.1131 | 1.975 | 1.6e-05 |
| B3+GR | 365 | 0.4646 | 3.333 | 4.623e-05 |
| B3+GR+SB16 | 7 | 4.511e-06 | 0.002618 | 1.5e-08 |
| B3+GR+SB16 | 30 | 8.492e-05 | 0.0474 | 6.782e-08 |
| B3+GR+SB16 | 90 | 0.0008233 | 0.4383 | 2.072e-07 |
| B3+GR+SB16 | 180 | 0.003379 | 1.944 | 4.512e-07 |
| B3+GR+SB16 | 365 | 0.01574 | 3.301 | 1.233e-06 |
| B3+GR+SB16+NG | 7 | 3.583e-06 | 2.878e-05 | 1.255e-08 |
| B3+GR+SB16+NG | 30 | 6.883e-05 | 0.0005398 | 5.391e-08 |
| B3+GR+SB16+NG | 90 | 0.0006435 | 0.00523 | 1.717e-07 |
| B3+GR+SB16+NG | 180 | 0.002859 | 0.02165 | 4.47e-07 |
| B3+GR+SB16+NG | 365 | 0.01326 | 0.04856 | 1.352e-06 |

## Incremental trajectory shift caused by each added force

| Stage | Horizon, d | Shift median, km | Shift p95, km | Velocity shift median, m/s |
| --- | ---: | ---: | ---: | ---: |
| B3 → B3+GR | 7 | 0.004908 | 0.07229 | 1.62e-05 |
| B3 → B3+GR | 30 | 0.09212 | 1.477 | 7.266e-05 |
| B3 → B3+GR | 90 | 0.8777 | 13.26 | 0.0002413 |
| B3 → B3+GR | 180 | 4.555 | 172.2 | 0.0008925 |
| B3 → B3+GR | 365 | 25.16 | 223.3 | 0.004202 |
| B3+GR → B3+GR+SB16 | 7 | 7.899e-05 | 0.0007289 | 2.619e-07 |
| B3+GR → B3+GR+SB16 | 30 | 0.001478 | 0.01595 | 1.158e-06 |
| B3+GR → B3+GR+SB16 | 90 | 0.01523 | 0.1075 | 4.375e-06 |
| B3+GR → B3+GR+SB16 | 180 | 0.08408 | 0.515 | 1.212e-05 |
| B3+GR → B3+GR+SB16 | 365 | 0.3567 | 2.396 | 2.46e-05 |
| B3+GR+SB16 → B3+GR+SB16+NG | 7 | 1.272e-06 | 0.0026 | 4.207e-09 |
| B3+GR+SB16 → B3+GR+SB16+NG | 30 | 2.337e-05 | 0.04709 | 1.803e-08 |
| B3+GR+SB16 → B3+GR+SB16+NG | 90 | 0.0002103 | 0.4352 | 5.353e-08 |
| B3+GR+SB16 → B3+GR+SB16+NG | 180 | 0.0008106 | 1.923 | 9.914e-08 |
| B3+GR+SB16 → B3+GR+SB16+NG | 365 | 0.005379 | 3.255 | 7.922e-07 |

## 365-day position error by object

| Model | Object | Regime | Median, km | p95, km |
| --- | --- | --- | ---: | ---: |
| B3 | Apollo | intermediate | 45.88 | 94.92 |
| B3 | Apophis | extreme | 106.8 | 2.546e+04 |
| B3 | Eros | intermediate | 37.01 | 58.68 |
| B3 | Massalia | quiet | 8.397 | 10.15 |
| B3 | Phaethon | extreme | 203 | 223.1 |
| B3 | Seraphina | quiet | 3.255 | 5.176 |
| B3+GR | Apollo | intermediate | 0.3925 | 0.8883 |
| B3+GR | Apophis | extreme | 3.04 | 323.1 |
| B3+GR | Eros | intermediate | 0.1611 | 0.3379 |
| B3+GR | Massalia | quiet | 0.4694 | 0.7975 |
| B3+GR | Phaethon | extreme | 0.4325 | 0.6054 |
| B3+GR | Seraphina | quiet | 1.154 | 2.394 |
| B3+GR+SB16 | Apollo | intermediate | 0.02186 | 0.05436 |
| B3+GR+SB16 | Apophis | extreme | 3.012 | 321.7 |
| B3+GR+SB16 | Eros | intermediate | 0.0141 | 0.01916 |
| B3+GR+SB16 | Massalia | quiet | 0.006096 | 0.01242 |
| B3+GR+SB16 | Phaethon | extreme | 0.2267 | 0.7412 |
| B3+GR+SB16 | Seraphina | quiet | 0.00739 | 0.009269 |
| B3+GR+SB16+NG | Apollo | intermediate | 0.02899 | 0.0426 |
| B3+GR+SB16+NG | Apophis | extreme | 0.03794 | 1,591 |
| B3+GR+SB16+NG | Eros | intermediate | 0.0141 | 0.01916 |
| B3+GR+SB16+NG | Massalia | quiet | 0.006096 | 0.01242 |
| B3+GR+SB16+NG | Phaethon | extreme | 0.0106 | 0.02051 |
| B3+GR+SB16+NG | Seraphina | quiet | 0.00739 | 0.009269 |

## Refined Apophis–Earth 2029 event

Horizons five-minute grid minimum: **38,014.448 km** at **2029-04-13T21:45:00 TDB**.

| Model | Minimum, km | Distance error, km | Time error, min | Final 36 h error, km |
| --- | ---: | ---: | ---: | ---: |
| B3 | 38,014.547 | +0.099 | +0.0 | 3.650 |
| B3+GR | 38,014.548 | +0.099 | +0.0 | 3.651 |
| B3+GR+SB16 | 38,014.548 | +0.099 | +0.0 | 3.651 |
| B3+GR+SB16+NG | 38,014.548 | +0.099 | +0.0 | 3.651 |

## Numerical sensitivity and cost

| Model | Runtime, s | RK4 steps | Point-mass perturbers |
| --- | ---: | ---: | ---: |
| B3 | 46.416 | 159,078 | 9 |
| B3+GR | 48.213 | 159,078 | 9 |
| B3+GR+SB16 | 129.252 | 160,345 | 25 |
| B3+GR+SB16+NG | 128.840 | 160,345 | 25 |

The rerun of B3 differs from the stored B3 result by at most 0 km in reported position error.
The full-model refined-event step sensitivity is 4.71e-05 km in position.

## Main findings

- Solar GR is the dominant missing smooth force: it reduces the 365-day median from 24.95 km to 0.465 km.
- SB16 matters most for main-belt accuracy and reduces the overall 365-day median further to 0.0157 km.
- Nominal A1/A2 reduces the overall 365-day median to 0.0133 km and p95 to 0.0486 km, but the maximum is 1,871.5 km for the post-encounter Apophis case. With only 24 windows, p95 excludes this single extreme value.
- For the Apophis rollout starting 2029-01-01, A1/A2 improves the pre-encounter 90-day error from 0.311 km to 0.00194 km but increases the 365-day post-encounter error from 377.9 km to 1,871.5 km. The flyby amplifies the remaining mismatch, so fitted terms cannot be judged only by aggregate medians.
- The isolated 36-hour event starts immediately before the flyby; GR, SB16 and A1/A2 have almost no time to accumulate there. Its remaining 3.65 km final error points instead to close-encounter model details such as Earth J2 and full EIH relativity.
- SB16 increases runtime from about 46 s to 129 s for the 24 rollout windows, a factor of roughly 2.8 in this standard-library implementation.

## Guardrails

- The six objects and all evaluated windows have already been inspected; this remains an engineering regression set.
- Matching Horizons with its nominal fitted A1/A2 values measures model reproduction, not honest historical forecasting.
- Apollo and Phaethon parameters in this snapshot use observations through March/April 2026, so their 2026-01-01 windows are explicitly not forecast-valid.
- Earth J2 is present in the Apophis Horizons header but is not included here because a correct implementation requires an epoch-dependent Earth-pole frame; it must be a separate verified ablation.
- Remaining error may include full EIH relativity, Earth oblateness, additional perturbers, interpolation mismatch and force-model details.
- Closest approach is still a minimum on a five-minute grid, not a continuous optimization or hazard product.

## Reproduction

```bash
env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache uv run --no-project --python-preference only-system python src/download_small_perturbers.py --config configs/b3plus_pilot_6.json --root .
env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache uv run --no-project --python-preference only-system python src/run_b3plus_ablation.py --config configs/b3plus_pilot_6.json --root .
```

Mass source: [Yao et al. 2025, A&A 701 A15, Table 1; masses associated with SB441-N16](https://www.aanda.org/articles/aa/full_html/2025/09/aa54652-25/aa54652-25.html). Reference trajectories and force headers: [JPL Horizons](https://ssd.jpl.nasa.gov/horizons/).

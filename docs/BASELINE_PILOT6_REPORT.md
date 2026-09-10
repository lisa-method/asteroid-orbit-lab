# B0/B1/B2 engineering baseline — six-object pilot

> Generated 2026-09-04T18:31:00.519563+00:00. This is an engineering comparison, not a frozen final test.

## Protocol

- Fit objects: Massalia, Seraphina, Eros.
- Fit starts: 2020-01-01, 2021-01-01, 2022-01-01, 2023-01-01, 2024-01-01.
- Evaluation starts: 2026-01-01, 2027-01-01, 2028-01-01, 2029-01-01.
- Horizons: 7, 30, 90, 180, 365 days.
- Integrator: fixed-step RK4; fit step up to 0.25 day and evaluation step up to 0.0625 day.
- B1 fits whole recursive trajectories; no numerical differentiation of positions is used.

## Synthetic recovery gate

| Parameter | Truth | Recovered | Relative/absolute error |
| --- | ---: | ---: | ---: |
| Reference acceleration at 1 AU, AU/day² | 0.000295912208284 | 0.000295912208131 | 5.19e-10 |
| Exponent n | 2 | 2.00000000 | 3.85e-09 |

Gate status: **PASS**. The synthetic target uses a finer integration step than the fitted model.

## Recovered law on JPL trajectories

- Reference acceleration at 1 AU: **0.000295930433502 AU/day²**.
- Exponent: **n = 2.00008022**.
- Difference from inverse-square exponent: **+8.02191e-05**.
- Reference-amplitude difference from the fixed solar value: **+0.006%**.

This point estimate absorbs planetary, small-body and any fitted non-gravitational effects present in Horizons. It is a system-identification sanity check, not a new measurement of the solar gravitational parameter.

## Object-level stability

| Held-out fit object | Recovered n from other two | Reference-amplitude difference | Held-out trajectory loss |
| --- | ---: | ---: | ---: |
| Massalia | 2.00007957 | +0.006% | 2.63e-08 |
| Seraphina | 2.00004012 | +0.005% | 1.28e-08 |
| Eros | 2.00045330 | +0.040% | 5.31e-07 |

## Integrator step sensitivity

Comparing B2 at 0.0625 and 0.03125 day steps gives a median position difference of **1.65e-06 km**, p95 **0.00042 km**, and maximum **4.82 km** (Phaethon, start 2028-01-01, horizon 365 days).

## Evaluation by horizon

| Model | Horizon, d | Position median, km | Position p95, km | Velocity median, m/s |
| --- | ---: | ---: | ---: | ---: |
| B0 | 7 | 2.65e+05 | 1.58e+06 | 873 |
| B0 | 30 | 4.75e+06 | 3.4e+07 | 3.61e+03 |
| B0 | 90 | 4.07e+07 | 2.62e+08 | 1.06e+04 |
| B0 | 180 | 1.86e+08 | 6.81e+08 | 2.71e+04 |
| B0 | 365 | 6.39e+08 | 1.25e+09 | 2.91e+04 |
| B1 | 7 | 54.8 | 145 | 0.183 |
| B1 | 30 | 1.05e+03 | 3.05e+03 | 0.831 |
| B1 | 90 | 9.27e+03 | 3.39e+04 | 2.27 |
| B1 | 180 | 3.16e+04 | 1.23e+05 | 4.43 |
| B1 | 365 | 1.06e+05 | 3.51e+05 | 9.92 |
| B2 | 7 | 23.3 | 68.8 | 0.0772 |
| B2 | 30 | 422 | 1.21e+03 | 0.313 |
| B2 | 90 | 3.7e+03 | 1.2e+04 | 0.94 |
| B2 | 180 | 1.42e+04 | 9.26e+04 | 1.94 |
| B2 | 365 | 5.31e+04 | 3.56e+05 | 5.24 |

## 365-day position error by object

| Model | Object | Regime | Median, km | p95, km |
| --- | --- | --- | ---: | ---: |
| B0 | Apollo | intermediate | 8.89e+08 | 1.39e+09 |
| B0 | Apophis | extreme | 9.67e+08 | 1.06e+09 |
| B0 | Eros | intermediate | 9.73e+08 | 1.12e+09 |
| B0 | Massalia | quiet | 4.72e+08 | 5.74e+08 |
| B0 | Phaethon | extreme | 4.69e+08 | 1.17e+09 |
| B0 | Seraphina | quiet | 3.1e+08 | 3.67e+08 |
| B1 | Apollo | intermediate | 1.13e+05 | 2.88e+05 |
| B1 | Apophis | extreme | 1.75e+05 | 1.65e+08 |
| B1 | Eros | intermediate | 8.7e+04 | 1.16e+05 |
| B1 | Massalia | quiet | 6.29e+04 | 7.83e+04 |
| B1 | Phaethon | extreme | 1.19e+05 | 1.79e+05 |
| B1 | Seraphina | quiet | 2.14e+05 | 3.49e+05 |
| B2 | Apollo | intermediate | 6.26e+04 | 2.47e+05 |
| B2 | Apophis | extreme | 7.49e+04 | 1.65e+08 |
| B2 | Eros | intermediate | 3.84e+04 | 4.99e+04 |
| B2 | Massalia | quiet | 6.73e+04 | 7.59e+04 |
| B2 | Phaethon | extreme | 4.77e+04 | 5.39e+04 |
| B2 | Seraphina | quiet | 2.15e+05 | 3.56e+05 |

## Interpretation guardrails

- The six objects are an inspected engineering set; these numbers are not final generalization estimates.
- A lower B1/B2 error does not yet establish an adaptive advantage; B3 restricted N-body is the next required reference.
- Apophis rollouts that cross the 2029 encounter are a stress diagnostic, not an operational hazard calculation.

## Reproduction

```bash
env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache uv run --no-project --python-preference only-system python src/run_physics_baselines.py --config configs/baseline_pilot_6.json --root .
```

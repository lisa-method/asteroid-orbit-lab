# B3 restricted N-body engineering baseline — six-object pilot

> Generated 2026-09-04T18:38:01.866025+00:00. This is an engineering comparison, not a frozen final test.

## Force model

- Sun plus nine configured perturbers; asteroids are massless test particles.
- Earth and Moon are separate; the Earth–Moon barycenter is not added.
- Planetary forces include direct and indirect heliocentric terms.
- Cubic-Hermite interpolation uses the daily JPL body positions and velocities.
- RK4 steps shrink near the Sun and close planetary approaches.

## B2 versus B3 by horizon

| Model | Horizon, d | Position median, km | Position p95, km | Velocity median, m/s |
| --- | ---: | ---: | ---: | ---: |
| B2 | 7 | 23.3 | 68.8 | 0.0772 |
| B2 | 30 | 422 | 1.21e+03 | 0.313 |
| B2 | 90 | 3.7e+03 | 1.2e+04 | 0.94 |
| B2 | 180 | 1.42e+04 | 9.26e+04 | 1.94 |
| B2 | 365 | 5.31e+04 | 3.56e+05 | 5.24 |
| B3 | 7 | 0.00494 | 0.0751 | 1.63e-05 |
| B3 | 30 | 0.0904 | 1.49 | 6.98e-05 |
| B3 | 90 | 0.848 | 13.8 | 0.000236 |
| B3 | 180 | 4.57 | 173 | 0.000896 |
| B3 | 365 | 24.9 | 223 | 0.00421 |

## 365-day position error by object

| Model | Object | Regime | Median, km | p95, km |
| --- | --- | --- | ---: | ---: |
| B2 | Apollo | intermediate | 6.26e+04 | 2.47e+05 |
| B2 | Apophis | extreme | 7.49e+04 | 1.65e+08 |
| B2 | Eros | intermediate | 3.84e+04 | 4.99e+04 |
| B2 | Massalia | quiet | 6.73e+04 | 7.59e+04 |
| B2 | Phaethon | extreme | 4.77e+04 | 5.39e+04 |
| B2 | Seraphina | quiet | 2.15e+05 | 3.56e+05 |
| B3 | Apollo | intermediate | 45.9 | 94.9 |
| B3 | Apophis | extreme | 107 | 2.55e+04 |
| B3 | Eros | intermediate | 37 | 58.7 |
| B3 | Massalia | quiet | 8.4 | 10.2 |
| B3 | Phaethon | extreme | 203 | 223 |
| B3 | Seraphina | quiet | 3.26 | 5.18 |

## Step-sensitivity cases

| Object | Start | Horizon, d | Position difference, km | Velocity difference, m/s | Steps coarse/fine |
| --- | --- | ---: | ---: | ---: | ---: |
| Phaethon | 2028-01-01 | 365 | 8.9e-05 | 5.49e-09 | 10,078/20,161 |
| Apophis | 2029-01-01 | 365 | 0.307 | 3.79e-05 | 12,041/24,080 |

## Refined Apophis–Earth 2029 event

Horizons grid minimum: **38,014.448 km** at **2029-04-13T21:45:00 TDB**.

| Model | Minimum, km | Distance error, km | Time error, min | Final position error, km |
| --- | ---: | ---: | ---: | ---: |
| B2 | 47,187.116 | +9,172.667 | +35.0 | 150,405.393 |
| B3 | 38,014.547 | +0.099 | +0.0 | 3.650 |

B3 event step-sensitivity maximum is 4.71e-05 km in position and 1.14e-06 m/s in velocity.

## Cost

B3 evaluation used 159,078 RK4 steps and 636,312 force evaluations in 46.108 seconds.

## Guardrails

- Horizons remains richer than this planets-only B3 because it includes additional small perturbers and, for some asteroids, fitted non-gravitational terms.
- The six-object set has already been inspected and is not a final test.
- Closest-approach values remain five-minute grid minima, not continuous optimizations or operational hazard products.

## Reproduction

```bash
env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache uv run --no-project --python-preference only-system python src/run_nbody_baseline.py --config configs/nbody_pilot_6.json --root .
```

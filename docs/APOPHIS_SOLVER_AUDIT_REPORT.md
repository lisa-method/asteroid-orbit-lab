# Apophis independent-solver audit report

Generated 2026-09-08T01:08:22.151523+00:00.

This is a bounded numerical/teacher audit, not an operational validation or fresh holdout.

**Численная сходимость до метров не достигнута.** Подробная интерпретация,
проверки и ограничения: [APOPHIS_SOLVER_AUDIT_INTERPRETATION.md](APOPHIS_SOLVER_AUDIT_INTERPRETATION.md).

NG status at the declared start: `available`; source: `Horizons header`; availability basis: `downloaded UTC date + 2 d; solution calendar date + 2 d; timestamp source=data/checksums/jpl_pilot_6_manifest.json:retrieved_at_utc; manifest_sha256=4a7e0bdf12c9baff16cd96daf8bae59f21655ee8a87d6fa6a80a489ac920358e`.

## Results

### apophis_2029_long365d

Grid: 797 samples, 2029-01-01T00:00:00 through 2030-01-01T00:00:00 TDB; reference grid minimum 38014.4 km.

| Run | max position error km | final position error km | max velocity error m/s |
|---|---:|---:|---:|
| rk4:0.5 | 13.1481 | 13.1481 | 0.00162042 |
| rk4:0.25 | 11.1509 | 11.1509 | 0.00137348 |
| rk4:0.125 | 12.9777 | 12.9777 | 0.00159935 |
| dopri54:loose | 12.3953 | 12.3953 | 0.00150609 |
| dopri54:medium | 12.0419 | 12.0419 | 0.00148223 |
| dopri54:tight | 12.0059 | 12.0059 | 0.00147888 |
| dopri54:tighter | 11.7469 | 11.7469 | 0.00144694 |
| dopri54:extreme | 11.9587 | 11.9587 | 0.00147331 |

| Planet ephemeris | max position error km | final position error km |
|---|---:|---:|
| old_daily_refined | 13.1481 | 13.1481 |
| development30_hourly | 12.9798 | 12.9798 |

Hourly minus old merged input: max position shift `0.277863` km, final `0.208569` km.
Old RK4 .5→.25 step sensitivity: max `1.99777` km, final `1.99777` km.

Hourly input solver checks:

| Run | max position error km | final position error km |
|---|---:|---:|
| rk4:0.5 | 12.9798 | 12.9798 |
| rk4:0.25 | 10.9698 | 10.9698 |
| dopri54:tight | 11.7485 | 11.7485 |
| dopri54:tighter | 11.7786 | 11.7786 |

### apophis_earth_2029_refined36h

Grid: 433 samples, 2029-04-13T00:00:00 through 2029-04-14T12:00:00 TDB; reference grid minimum 38014.4 km.

| Run | max position error km | final position error km | max velocity error m/s |
|---|---:|---:|---:|
| rk4:0.5 | 0.00179122 | 0.000722285 | 2.0709e-05 |
| rk4:0.25 | 0.00148566 | 0.000414881 | 1.65408e-05 |
| rk4:0.125 | 0.00112156 | 9.78209e-06 | 1.12555e-05 |
| dopri54:loose | 0.00114616 | 3.71198e-05 | 9.95809e-06 |
| dopri54:medium | 0.00114616 | 3.71198e-05 | 9.95809e-06 |
| dopri54:tight | 0.00118343 | 7.73335e-05 | 1.01288e-05 |
| dopri54:tighter | 0.0011948 | 0.000131474 | 1.11263e-05 |
| dopri54:extreme | 0.0012042 | 0.000104368 | 1.03781e-05 |

| Planet ephemeris | max position error km | final position error km |
|---|---:|---:|
| old_daily_refined | 0.00179122 | 0.000722285 |
| development30_hourly | 0.00293685 | 0.00188853 |

Hourly minus old merged input: max position shift `0.00117344` km, final `0.00117344` km.
Old RK4 .5→.25 step sensitivity: max `0.00113709` km, final `0.00113709` km.

Hourly input solver checks:

| Run | max position error km | final position error km |
|---|---:|---:|
| rk4:0.5 | 0.00293685 | 0.00188853 |
| rk4:0.25 | 0.00185118 | 0.000766005 |
| dopri54:tight | 0.00225655 | 0.00118662 |
| dopri54:tighter | 0.00217968 | 0.00112478 |

## Interpretation and limits

The solver comparison measures numerical sensitivity; these results do not
establish a numerical error bound or convergence to metres. The old-versus-hourly
shift measures ephemeris/interpolation sensitivity and does not identify a
missing physical force. The nominal NG parameters were retrieved in 2026 before
the 2029 forecast start, but their uncertainty is not propagated. The historical
branch remains a teacher-matching diagnostic. See the linked interpretation for
the stage3 artifact checks and the missing runtime-fingerprint limitation.

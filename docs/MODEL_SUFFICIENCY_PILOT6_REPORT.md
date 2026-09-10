# Первая карта достаточности моделей — pilot 6

Generated 2026-09-06T21:32:54.748395+00:00. Engineering regression set; no final test or trained selector.

## Протокол

- 150 model/case records; starts ['2029-01-01']; horizons [7, 30, 90, 180, 365] days.
- Primary error: maximum over the daily output grid, with the fixed existing Apophis five-minute refinement when included. Not a continuous-time bound.
- All candidates start from the same supplied Horizons state and use recursive rollout. Nominal fitted NG parameters are excluded.
- Earth/Moon ephemerides merge daily and existing refined nodes; J2 uses an approximate epoch-dependent IAU mean pole.
- Timing: 3 repeated causal prefixes; resident ephemerides. Shared load 0.611 s is reported separately.
- Timing includes force construction, integration, interpolation and output instrumentation. Feature/selector costs do not yet exist and are not included in oracle cost.
- Numerical eligibility: observed coarse/fine maximum <= 10% of requested tolerance; this is a sensitivity check, not a rigorous error guarantee.

## Ошибка на всём интервале

| Model | Horizon, d | Cases | Median max error, km | Worst max error, km | Median runtime, s | Worst step difference, km |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| B2 | 7 | 6 | 18.9952 | 26.3978 | 0.003074 | 4.713e-07 |
| B2 | 30 | 6 | 355.719 | 533.061 | 0.01287 | 1.402e-06 |
| B2 | 90 | 6 | 2940.8 | 12030.7 | 0.03801 | 2.292e-06 |
| B2 | 180 | 6 | 11459.4 | 2.16959e+07 | 0.07571 | 4.408e-06 |
| B2 | 365 | 6 | 51207.4 | 1.93739e+08 | 0.1558 | 0.001491 |
| B3 | 7 | 6 | 0.00517948 | 0.0619843 | 0.06599 | 9.36e-07 |
| B3 | 30 | 6 | 0.0977365 | 0.981445 | 0.2805 | 1.403e-06 |
| B3 | 90 | 6 | 1.07751 | 7.11331 | 0.8408 | 3.9e-05 |
| B3 | 180 | 6 | 8.75659 | 3521.56 | 1.689 | 0.2284 |
| B3 | 365 | 6 | 58.5092 | 29981.6 | 3.442 | 2 |
| B3+GR | 7 | 6 | 0.000438781 | 0.00163635 | 0.06695 | 8.204e-07 |
| B3+GR | 30 | 6 | 0.00955112 | 0.0301872 | 0.2869 | 1.711e-06 |
| B3+GR | 90 | 6 | 0.0565784 | 0.309041 | 0.8652 | 4.032e-05 |
| B3+GR | 180 | 6 | 0.163846 | 272.102 | 1.732 | 0.229 |
| B3+GR | 365 | 6 | 0.633993 | 360.34 | 3.523 | 2.006 |
| B3+GR+SB16 | 7 | 6 | 4.04224e-06 | 0.00161791 | 0.1825 | 1.207e-06 |
| B3+GR+SB16 | 30 | 6 | 7.4106e-05 | 0.0299358 | 0.7692 | 2.198e-06 |
| B3+GR+SB16 | 90 | 6 | 0.000749074 | 0.31058 | 2.461 | 3.886e-05 |
| B3+GR+SB16 | 180 | 6 | 0.00362436 | 272.641 | 4.913 | 0.2279 |
| B3+GR+SB16 | 365 | 6 | 0.0198416 | 359.482 | 10.24 | 1.997 |
| B3+GR+SB16+J2 | 7 | 6 | 4.04224e-06 | 0.00161791 | 0.1902 | 1.207e-06 |
| B3+GR+SB16+J2 | 30 | 6 | 7.4106e-05 | 0.0299358 | 0.8386 | 2.198e-06 |
| B3+GR+SB16+J2 | 90 | 6 | 0.000749074 | 0.31058 | 2.604 | 3.876e-05 |
| B3+GR+SB16+J2 | 180 | 6 | 0.00362436 | 196.495 | 5.143 | 0.2281 |
| B3+GR+SB16+J2 | 365 | 6 | 0.0198416 | 1701.89 | 10.72 | 1.998 |

## Offline oracle

Oracle uses reference errors and is not an implemented predictor of model choice.

### Position tolerance 0.1 km

Feasible cases: 26/30; no eligible model: 4.

| Fixed model | Feasible cases | Fixed cost on these cases, s | Oracle cost on the same cases, s |
| --- | ---: | ---: | ---: |
| B2 | 0 | 0 | 0 |
| B3 | 9 | 1.246 | 1.242 |
| B3+GR | 18 | 9.079 | 9.056 |
| B3+GR+SB16 | 26 | 82.89 | 66.36 |
| B3+GR+SB16+J2 | 26 | 87.19 | 66.36 |

Selection counts: `{"B3": 8, "B3+GR": 10, "B3+GR+SB16": 8, "none": 4}`.

### Position tolerance 1 km

Feasible cases: 28/30; no eligible model: 2.

| Fixed model | Feasible cases | Fixed cost on these cases, s | Oracle cost on the same cases, s |
| --- | ---: | ---: | ---: |
| B2 | 0 | 0 | 0 |
| B3 | 15 | 4.624 | 4.62 |
| B3+GR | 27 | 33.07 | 32.94 |
| B3+GR+SB16 | 28 | 102.9 | 42.25 |
| B3+GR+SB16+J2 | 28 | 108.3 | 42.25 |

Selection counts: `{"B3": 14, "B3+GR": 13, "B3+GR+SB16": 1, "none": 2}`.

### Position tolerance 10 km

Feasible cases: 28/30; no eligible model: 2.

| Fixed model | Feasible cases | Fixed cost on these cases, s | Oracle cost on the same cases, s |
| --- | ---: | ---: | ---: |
| B2 | 1 | 0.003096 | 0.003096 |
| B3 | 22 | 15.97 | 15.91 |
| B3+GR | 28 | 36.56 | 36.13 |
| B3+GR+SB16 | 28 | 102.9 | 36.13 |
| B3+GR+SB16+J2 | 28 | 108.3 | 36.13 |

Selection counts: `{"B2": 1, "B3": 20, "B3+GR": 7, "none": 2}`.

## Ограничения и воспроизведение

- Objects and dates were already inspected; nested horizons are dependent. No object-generalization or rare-failure guarantee is estimated.
- The strongest force model may also fail a requested tolerance. Failures are retained, including post-encounter cases.
- Small runtime differences can reflect timing noise. This is a comparison of the present standard-library implementation.
- Full EIH, high-precision Earth rotation and an independent numerical solver have not been validated here.
- State/error samples, numerical differences and per-repeat prefix timings are saved in the ignored output directory.

```bash
env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv run --no-project --python-preference only-system python -B src/run_model_sufficiency.py --root . --config configs/model_sufficiency_pilot6.json
```

## Карта выбора по отдельным задачам

В ячейке — самый дешёвый допустимый кандидат по измеренной median cost. «Нет» означает, что ни один кандидат не прошёл одновременно позиционный допуск и numerical gate. Это oracle с доступом к эталонным ошибкам.

| Объект | Горизонт, дни | 0.1 km | 1 km | 10 km |
| --- | ---: | --- | --- | --- |
| Seraphina | 7 | B3 | B3 | B3 |
| Seraphina | 30 | B3 | B3 | B3 |
| Seraphina | 90 | B3+GR+SB16 | B3 | B3 |
| Seraphina | 180 | B3+GR+SB16 | B3+GR | B3 |
| Seraphina | 365 | B3+GR+SB16 | B3+GR+SB16 | B3 |
| Massalia | 7 | B3 | B3 | B3 |
| Massalia | 30 | B3 | B3 | B3 |
| Massalia | 90 | B3+GR | B3 | B3 |
| Massalia | 180 | B3+GR+SB16 | B3+GR | B3 |
| Massalia | 365 | B3+GR+SB16 | B3+GR | B3+GR |
| Eros | 7 | B3 | B3 | B3 |
| Eros | 30 | B3+GR | B3 | B3 |
| Eros | 90 | B3+GR | B3+GR | B3 |
| Eros | 180 | B3+GR | B3+GR | B3 |
| Eros | 365 | B3+GR+SB16 | B3+GR | B3+GR |
| Apollo | 7 | B3 | B3 | B2 |
| Apollo | 30 | B3+GR | B3 | B3 |
| Apollo | 90 | B3+GR | B3+GR | B3 |
| Apollo | 180 | B3+GR | B3+GR | B3+GR |
| Apollo | 365 | B3+GR+SB16 | B3+GR | B3+GR |
| Phaethon | 7 | B3 | B3 | B3 |
| Phaethon | 30 | B3+GR | B3+GR | B3+GR |
| Phaethon | 90 | B3+GR | B3 | B3 |
| Phaethon | 180 | B3+GR+SB16 | B3+GR | B3+GR |
| Phaethon | 365 | Нет | B3+GR | B3+GR |
| Apophis | 7 | B3 | B3 | B3 |
| Apophis | 30 | B3+GR | B3 | B3 |
| Apophis | 90 | Нет | B3+GR | B3 |
| Apophis | 180 | Нет | Нет | Нет |
| Apophis | 365 | Нет | Нет | Нет |

## RTN на 365 днях: B3+GR+SB16+J2

Компоненты endpoint error в reference RTN, km; максимум по всей сетке показан отдельно. Добавление сил не гарантирует уменьшения ошибки.

| Объект | Radial | Transverse | Normal | Max grid, km |
| --- | ---: | ---: | ---: | ---: |
| Seraphina | -0.00409071 | 0.00550293 | 0.000384671 | 0.00686761 |
| Massalia | -0.00896403 | 0.0100641 | -0.000249432 | 0.0134797 |
| Eros | -0.00227014 | 0.0178312 | -7.27788e-05 | 0.0179753 |
| Apollo | -0.00863428 | 0.0199169 | 4.86473e-05 | 0.021708 |
| Phaethon | 0.221094 | -0.163416 | 0.000455235 | 0.274932 |
| Apophis | 373.92 | -1660 | 31.6561 | 1701.89 |

Суммы стоимости по вложенным горизонтам относятся к отдельным запросам прогноза каждого горизонта; они не оценивают стоимость одного общего multi-horizon batch. Близкие по времени кандидаты могут менять oracle-метку из-за timing noise, поэтому частота разных меток сама по себе не доказывает полезность learned selector.

Карту можно пересоздать без интегрирования:

```bash
env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv run --no-project --python-preference only-system python -B src/report_model_sufficiency.py --results outputs/model_sufficiency/pilot6/results.json --output docs/MODEL_SUFFICIENCY_PILOT6_REPORT.md
```

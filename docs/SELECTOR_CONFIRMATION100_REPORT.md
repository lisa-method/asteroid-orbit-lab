# Независимое подтверждение селектора v4 на 100 телах

Эксперимент читает замороженные v4 физические кандидаты и не переобучает селектор. Полная выборка содержит 100 объектов, 500 зависимых horizon cases и все четыре метода; прямой замер wall-clock времени (perf_counter) выполнен только на заранее выбранных 24 объектах (120 cases × 4 метода × 3 чередующихся повтора). Horizons является model-derived reference при заданном initial state.

## Основная точность

Успешный case одновременно проходит position error и production/fine numerical budget. Доли и failures считаются отдельно для каждого метода и допуска.

| Метод | 0.1 km | 1 km | 10 km | Все 5 H / тел при 1 km | Максимальная ошибка при 1 km |
| --- | ---: | ---: | ---: | ---: | ---: |
| tree_v3 | 497 / 500 | 499 / 500 | 499 / 500 | 99 / 100 | 1.26505 km |
| physics_v4 | 498 / 500 | 500 / 500 | 500 / 500 | 100 / 100 | 0.806438 km |
| hybrid_v4 | 498 / 500 | 500 / 500 | 500 / 500 | 100 / 100 | 0.522314 km |
| fixed_full | 498 / 500 | 500 / 500 | 500 / 500 | 100 / 100 | 0.153166 km |

## Распределение ошибок и предупреждения при 1 km

| Метод | Median error | p95 error | Numerical flags | No-candidate truth | Warnings | Silent failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| tree_v3 | 0.0135041 km | 0.151278 km | 0 | 0 | 3 | 1 |
| physics_v4 | 0.0135604 km | 0.234158 km | 0 | 0 | 154 | 0 |
| hybrid_v4 | 0.0120948 km | 0.191754 km | 0 | 0 | 154 | 0 |
| fixed_full | 0.00130045 km | 0.0250942 km | 0 | 0 | 0 | 0 |

`numerical flags` означает production/fine difference > 0.1 km при tolerance 1 km; этот порог отдельно проверяется в eligibility. Warning cases не вычитаются из score. No-candidate truth и silent failures — разные состояния.

## 1 km по всем девяти strata

| Stratum | Method | Objects | Cases | Eligible | All 5 H | Warnings | Unflagged failures | Worst error km |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Earth_moderate | tree_v3 | 16 | 80 | 80 | 16 | 0 | 0 | 0.110682 |
| Earth_moderate | physics_v4 | 16 | 80 | 80 | 16 | 8 | 0 | 0.806438 |
| Earth_moderate | hybrid_v4 | 16 | 80 | 80 | 16 | 8 | 0 | 0.243463 |
| Earth_moderate | fixed_full | 16 | 80 | 80 | 16 | 0 | 0 | 0.0512747 |
| Earth_tight | tree_v3 | 4 | 20 | 20 | 4 | 3 | 0 | 0.0429104 |
| Earth_tight | physics_v4 | 4 | 20 | 20 | 4 | 4 | 0 | 0.191673 |
| Earth_tight | hybrid_v4 | 4 | 20 | 20 | 4 | 4 | 0 | 0.522314 |
| Earth_tight | fixed_full | 4 | 20 | 20 | 4 | 0 | 0 | 0.0429104 |
| Jupiter | tree_v3 | 12 | 60 | 60 | 12 | 0 | 0 | 0.281953 |
| Jupiter | physics_v4 | 12 | 60 | 60 | 12 | 1 | 0 | 0.502867 |
| Jupiter | hybrid_v4 | 12 | 60 | 60 | 12 | 1 | 0 | 0.281953 |
| Jupiter | fixed_full | 12 | 60 | 60 | 12 | 0 | 0 | 0.0136492 |
| Mars | tree_v3 | 12 | 60 | 60 | 12 | 0 | 0 | 0.269273 |
| Mars | physics_v4 | 12 | 60 | 60 | 12 | 17 | 0 | 0.486925 |
| Mars | hybrid_v4 | 12 | 60 | 60 | 12 | 17 | 0 | 0.288835 |
| Mars | fixed_full | 12 | 60 | 60 | 12 | 0 | 0 | 0.0302328 |
| NG | tree_v3 | 6 | 30 | 30 | 6 | 0 | 0 | 0.287323 |
| NG | physics_v4 | 6 | 30 | 30 | 6 | 29 | 0 | 0.153166 |
| NG | hybrid_v4 | 6 | 30 | 30 | 6 | 29 | 0 | 0.153166 |
| NG | fixed_full | 6 | 30 | 30 | 6 | 0 | 0 | 0.153166 |
| Venus | tree_v3 | 12 | 60 | 60 | 12 | 0 | 0 | 0.222982 |
| Venus | physics_v4 | 12 | 60 | 60 | 12 | 32 | 0 | 0.243037 |
| Venus | hybrid_v4 | 12 | 60 | 60 | 12 | 32 | 0 | 0.172554 |
| Venus | fixed_full | 12 | 60 | 60 | 12 | 0 | 0 | 0.100969 |
| inner_controls | tree_v3 | 16 | 80 | 80 | 16 | 0 | 0 | 0.291924 |
| inner_controls | physics_v4 | 16 | 80 | 80 | 16 | 29 | 0 | 0.460566 |
| inner_controls | hybrid_v4 | 16 | 80 | 80 | 16 | 29 | 0 | 0.460566 |
| inner_controls | fixed_full | 16 | 80 | 80 | 16 | 0 | 0 | 0.0137866 |
| low_q | tree_v3 | 6 | 30 | 30 | 6 | 0 | 0 | 0.279048 |
| low_q | physics_v4 | 6 | 30 | 30 | 6 | 23 | 0 | 0.14082 |
| low_q | hybrid_v4 | 6 | 30 | 30 | 6 | 23 | 0 | 0.14082 |
| low_q | fixed_full | 6 | 30 | 30 | 6 | 0 | 0 | 0.0380365 |
| outer_controls | tree_v3 | 16 | 80 | 79 | 15 | 0 | 1 | 1.26505 |
| outer_controls | physics_v4 | 16 | 80 | 80 | 16 | 11 | 0 | 0.507749 |
| outer_controls | hybrid_v4 | 16 | 80 | 80 | 16 | 11 | 0 | 0.288879 |
| outer_controls | fixed_full | 16 | 80 | 80 | 16 | 0 | 0 | 0.00689667 |

## Candidate eligibility and force coverage

| Candidate | Forces included | Forces omitted | Horizon | Eligible physical cases / 100 |
| --- | --- | --- | ---: | ---: |
| V2-B2 | Sun + test particle | planet, GR, SB16, Earth J2, NG | 7 d | 0 |
| V2-B2 | Sun + test particle | planet, GR, SB16, Earth J2, NG | 30 d | 0 |
| V2-B2 | Sun + test particle | planet, GR, SB16, Earth J2, NG | 90 d | 0 |
| V2-B2 | Sun + test particle | planet, GR, SB16, Earth J2, NG | 180 d | 0 |
| V2-B2 | Sun + test particle | planet, GR, SB16, Earth J2, NG | 365 d | 0 |
| V2-P | B2 + planets + Earth J2 + NG if available | GR, SB16 | 7 d | 99 |
| V2-P | B2 + planets + Earth J2 + NG if available | GR, SB16 | 30 d | 82 |
| V2-P | B2 + planets + Earth J2 + NG if available | GR, SB16 | 90 d | 44 |
| V2-P | B2 + planets + Earth J2 + NG if available | GR, SB16 | 180 d | 28 |
| V2-P | B2 + planets + Earth J2 + NG if available | GR, SB16 | 365 d | 11 |
| V2-P-GR | P + solar GR | SB16 | 7 d | 100 |
| V2-P-GR | P + solar GR | SB16 | 30 d | 100 |
| V2-P-GR | P + solar GR | SB16 | 90 d | 99 |
| V2-P-GR | P + solar GR | SB16 | 180 d | 98 |
| V2-P-GR | P + solar GR | SB16 | 365 d | 91 |
| V2-P-GR-SB16 | P + GR + SB16 | none within this candidate suite | 7 d | 100 |
| V2-P-GR-SB16 | P + GR + SB16 | none within this candidate suite | 30 d | 100 |
| V2-P-GR-SB16 | P + GR + SB16 | none within this candidate suite | 90 d | 100 |
| V2-P-GR-SB16 | P + GR + SB16 | none within this candidate suite | 180 d | 100 |
| V2-P-GR-SB16 | P + GR + SB16 | none within this candidate suite | 365 d | 100 |

Eligibility counts above are physical candidate results at 1 km, independently of which method selected the candidate. Force availability is therefore not conflated with method selection.

## Геометрия выбранных встреч

Используется кандидат, выбранный на 365 суток при запросе 1 km. Проверяется только зафиксированная каталогом встреча в окне ±2 суток; это не поиск всех будущих сближений. Reference использует 5-minute asteroid nodes и те же exogenous planetary ephemerides. Ошибки ниже относятся к уточнённому минимуму в этом окне, а не к continuous-time или наблюдательной гарантии.

| Метод | Встреч | Median Δd, m | p95 Δd, m | Max Δd, m | Max Δt, s | Max step Δd, m |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| tree_v3 | 56 | 0.202847 | 1.04052 | 2.5352 | 15.04 | 0.593897 |
| physics_v4 | 56 | 0.451078 | 2.80558 | 3.51495 | 15.04 | 0.593925 |
| hybrid_v4 | 56 | 0.32074 | 1.68437 | 2.80461 | 15.04 | 0.593925 |
| fixed_full | 56 | 0.182113 | 1.04052 | 2.4971 | 15.04 | 0.593897 |

## Timing on paired 24-object subset

| Method | Total median seconds | Median case seconds | Reduction vs full | Successes / 120 | Feature cost included |
| --- | ---: | ---: | ---: | ---: | --- |
| tree_v3 | 421.726 | 1.328281 | +28.77% | 120 / 120 | yes |
| physics_v4 | 413.839 | 2.233359 | +30.10% | 120 / 120 | yes |
| hybrid_v4 | 418.163 | 2.240717 | +29.37% | 120 / 120 | yes |
| fixed_full | 592.082 | 3.022735 | +0.00% | 120 / 120 | no |

Timing использует только одинаковые 24 объекта для всех методов, три повтора с чередованием порядка, и включает feature cost там, где он нужен. Successes здесь относятся только к этим 24 объектам и не заменяют full-sample score.

### Стоимость по горизонту

Сумма медиан на одинаковых 24 телах для каждого горизонта; все времена в секундах.

| H, d | tree_v3 | physics_v4 | hybrid_v4 | fixed_full | Экономия physics к full |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 7 | 2.860 | 4.612 | 4.606 | 6.607 | +30.20% |
| 30 | 13.959 | 19.950 | 19.926 | 32.873 | +39.31% |
| 90 | 38.796 | 54.082 | 54.171 | 87.252 | +38.02% |
| 180 | 76.091 | 98.169 | 97.701 | 162.510 | +39.59% |
| 365 | 290.020 | 237.027 | 241.759 | 302.840 | +21.73% |

### Порядок в трёх повторах

Здесь складываются фактические времена одного номера повтора по всем 120 cases. Это дополнительная проверка устойчивости порядка; основной показатель выше — сумма per-case медиан.

| Повтор | tree_v3, s | physics_v4, s | hybrid_v4, s | fixed_full, s | Порядок от быстрого к медленному |
| ---: | ---: | ---: | ---: | ---: | --- |
| 1 | 422.561 | 414.200 | 419.130 | 592.774 | physics_v4 < hybrid_v4 < tree_v3 < fixed_full |
| 2 | 421.096 | 415.241 | 419.710 | 594.314 | physics_v4 < hybrid_v4 < tree_v3 < fixed_full |
| 3 | 422.911 | 415.149 | 419.673 | 595.675 | physics_v4 < hybrid_v4 < tree_v3 < fixed_full |

## Operational warnings по допускам

| Method | Tol | Strong encounter | OOD prediction | No-candidate prediction | Actual no-candidate | Warnings | Warning reasons |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| tree_v3 | 0.1 | 3 | 0 | 0 | 1 | 3 | — |
| tree_v3 | 1 | 3 | 0 | 0 | 0 | 3 | — |
| tree_v3 | 10 | 3 | 0 | 0 | 0 | 3 | — |
| physics_v4 | 0.1 | 3 | 154 | 0 | 1 | 154 | outside_training_support: 154, strong_encounter_unvalidated: 3; OOD features: initial_eccentricity, log10_gr_proxy_km, log10_initial_radius_au, log10_max_scattering_strength, log10_min_planet_distance_over_hill, log10_ng_amplitude_au_d2, log10_planet_proxy_km, log10_small_body_proxy_km |
| physics_v4 | 1 | 3 | 154 | 0 | 0 | 154 | outside_training_support: 154, strong_encounter_unvalidated: 3; OOD features: initial_eccentricity, log10_gr_proxy_km, log10_initial_radius_au, log10_max_scattering_strength, log10_min_planet_distance_over_hill, log10_ng_amplitude_au_d2, log10_planet_proxy_km, log10_small_body_proxy_km |
| physics_v4 | 10 | 3 | 154 | 0 | 0 | 154 | outside_training_support: 154, strong_encounter_unvalidated: 3; OOD features: initial_eccentricity, log10_gr_proxy_km, log10_initial_radius_au, log10_max_scattering_strength, log10_min_planet_distance_over_hill, log10_ng_amplitude_au_d2, log10_planet_proxy_km, log10_small_body_proxy_km |
| hybrid_v4 | 0.1 | 3 | 154 | 29 | 1 | 167 | no_candidate_predicted: 29, outside_training_support: 154, strong_encounter_unvalidated: 3; OOD features: initial_eccentricity, log10_gr_proxy_km, log10_initial_radius_au, log10_max_scattering_strength, log10_min_planet_distance_over_hill, log10_ng_amplitude_au_d2, log10_planet_proxy_km, log10_small_body_proxy_km |
| hybrid_v4 | 1 | 3 | 154 | 0 | 0 | 154 | outside_training_support: 154, strong_encounter_unvalidated: 3; OOD features: initial_eccentricity, log10_gr_proxy_km, log10_initial_radius_au, log10_max_scattering_strength, log10_min_planet_distance_over_hill, log10_ng_amplitude_au_d2, log10_planet_proxy_km, log10_small_body_proxy_km |
| hybrid_v4 | 10 | 3 | 154 | 0 | 0 | 154 | outside_training_support: 154, strong_encounter_unvalidated: 3; OOD features: initial_eccentricity, log10_gr_proxy_km, log10_initial_radius_au, log10_max_scattering_strength, log10_min_planet_distance_over_hill, log10_ng_amplitude_au_d2, log10_planet_proxy_km, log10_small_body_proxy_km |
| fixed_full | 0.1 | 0 | 0 | 0 | 1 | 0 | — |
| fixed_full | 1 | 0 | 0 | 0 | 0 | 0 | — |
| fixed_full | 10 | 0 | 0 | 0 | 0 | 0 | — |

## Force choices и failures

### Выборы

- `tree_v3`: V2-P=195, V2-P-GR=188, V2-P-GR-SB16=117
- `physics_v4`: V2-P=156, V2-P-GR=179, V2-P-GR-SB16=165
- `hybrid_v4`: V2-P=163, V2-P-GR=159, V2-P-GR-SB16=178
- `fixed_full`: V2-P-GR-SB16=500

### Все failures выбранной модели

| Method | Tol | Object | H | Selected model | Error km | Numerical diff km | Full residual km | Status | Warning |
| --- | ---: | --- | ---: | --- | ---: | ---: | ---: | --- | --- |
| tree_v3 | 0.1 | 310442 | 365 | V2-P-GR-SB16 | 0.100969 | 0.000275604 | 0.100969 | predicted_feasible | False |
| physics_v4 | 0.1 | 310442 | 365 | V2-P-GR-SB16 | 0.100969 | 0.000275604 | 0.100969 | outside_training_support | True |
| hybrid_v4 | 0.1 | 310442 | 365 | V2-P-GR-SB16 | 0.100969 | 0.000275604 | 0.100969 | outside_training_support | True |
| fixed_full | 0.1 | 310442 | 365 | V2-P-GR-SB16 | 0.100969 | 0.000275604 | 0.100969 | fixed_full | False |
| tree_v3 | 0.1 | 229215 | 30 | V2-P | 0.131822 | 6.93649e-07 | 3.42551e-05 | predicted_feasible | False |
| tree_v3 | 1 | 229215 | 90 | V2-P | 1.26505 | 2.28839e-06 | 0.000328756 | predicted_feasible | False |
| tree_v3 | 10 | 229215 | 365 | V2-P | 22.8875 | 6.16756e-06 | 0.00688996 | predicted_feasible | False |
| tree_v3 | 0.1 | 893065 | 365 | V2-P-GR-SB16 | 0.153166 | 0.000664104 | 0.153166 | predicted_feasible | False |
| physics_v4 | 0.1 | 893065 | 365 | V2-P-GR-SB16 | 0.153166 | 0.000664104 | 0.153166 | outside_training_support | True |
| hybrid_v4 | 0.1 | 893065 | 365 | V2-P-GR-SB16 | 0.153166 | 0.000664104 | 0.153166 | outside_training_support | True |
| fixed_full | 0.1 | 893065 | 365 | V2-P-GR-SB16 | 0.153166 | 0.000664104 | 0.153166 | fixed_full | False |

Residuals приведены фактически; они не приписываются отдельной силе без отдельного causal audit. Полная модель служит сохранённым сравнением для того же initial state и горизонта.

## Sampling quality and limits

Sample использует canonical safe IDs, сохраняет actual JPL designation/name и conventional stratum/event metadata. Counts-only selection и quality audit выполнены до загрузки target vectors. В metadata amendment перед vector selection зафиксированы строгие 4 Earth tight cases и nominal feasibility остальных quotas; audit содержит 237 metadata files и 122 quality rejections. Raw inventory и exact provenance проверены отдельным raw verifier.

Raw inventory: passed=True; unique raw paths=684; manifests=23; manifest records=703; new target files=156; verifier SHA=2b3d526c97aeadf34df780e270d2ea2363c2a73fe53bf753d8fca32c7eef4d4d.

Новые 100 тел disjoint от development/calibration material. Старый v4 holdout24 остаётся отдельной frozen оценкой и не смешивается с prevalence этого confirmation sample. Если strong encounter или no-candidate truth отсутствуют, их detection не считается validated. Сильные warnings и fallback не дают universal guarantee.

Experiment freeze SHA256: `d748a396ede50127c3c3c6f8429d075b987559a3134755d47f33e611f825ee39`.
Matrix SHA256: `be005d353b8f9fa224156732ff8aae80702d6afe911965e86f5b583d32aa0cd2`.
Direct cost SHA256: `2591ac48cb671f989e66ed320a0290b488c331bbc594cac5a2eed9f578573d0b`.
Verification SHA256: `e143b8637fb9600bbfb8b5fdebcdbffdffcdb3ee4b01f04c4ff5b4a1fa06ebe5`.

CLI:

```sh
PYTHONPATH=src uv run --no-project --python-preference only-system --python 3.14.7 python -B src/run_selector_confirmation.py verify
PYTHONPATH=src uv run --no-project --python-preference only-system --python 3.14.7 python -B src/report_selector_confirmation.py
```

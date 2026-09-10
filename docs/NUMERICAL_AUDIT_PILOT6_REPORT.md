# B2 RK4 numerical audit — six-object pilot

> Generated 2026-09-06T21:15:05.852141+00:00. This is an empirical step-sensitivity audit, not a rigorous truncation-error bound.

## Protocol

- Force model: fixed solar two-body acceleration (`mu_sun`) for every policy.
- Output grid: daily samples from day 0 through day 365 for 24 object/start windows.
- Historical B2 policy: fixed RK4 step up to 0.0625 day.
- Comparison reference: fixed RK4 step up to 0.03125 day.
- Variable policies use the existing solar-distance `encounter_aware_step_selector` with an empty perturber tuple and the listed scale factors.
- Differences are measured at the common daily grid; step halving is treated as empirical convergence evidence only.

## Aggregate differences against the fine fixed reference

| Policy | Grid position median, km | Grid position p95, km | Grid position max, km | Grid velocity max, m/s | Median RK4 steps | Median runtime, s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| fixed 0.0625 d | 1.84988e-05 | 3.51854 | 4.81814 | 0.000633047 | 5840 | 0.0638333 |
| variable scale 1 | 1.84988e-05 | 0.217318 | 0.260109 | 4.18537e-05 | 5840 | 0.0669295 |
| variable scale 0.5 | 0 | 0.217324 | 0.260166 | 4.20116e-05 | 11680 | 0.13354 |
| variable scale 0.25 | 9.94647e-06 | 0.217184 | 0.260105 | 4.1794e-05 | 23360 | 0.266513 |

## Worst grid differences by case

| Object | Start | Historical fixed vs fine, km | Variable scale 1 vs fine, km | Variable scale 0.5 vs fine, km | Variable scale 0.25 vs fine, km |
| --- | --- | ---: | ---: | ---: | ---: |
| Seraphina | 2026-01-01 | 5.63283e-06 | 5.63283e-06 | 0 | 2.66486e-06 |
| Seraphina | 2027-01-01 | 5.00517e-06 | 5.00517e-06 | 0 | 4.22195e-06 |
| Seraphina | 2028-01-01 | 9.20218e-06 | 9.20218e-06 | 0 | 7.83468e-06 |
| Seraphina | 2029-01-01 | 6.90718e-06 | 6.90718e-06 | 0 | 6.52926e-06 |
| Massalia | 2026-01-01 | 5.33232e-06 | 5.33232e-06 | 0 | 1.63406e-05 |
| Massalia | 2027-01-01 | 3.33847e-06 | 3.33847e-06 | 0 | 4.93871e-06 |
| Massalia | 2028-01-01 | 9.07984e-06 | 9.07984e-06 | 0 | 7.63523e-06 |
| Massalia | 2029-01-01 | 7.86138e-06 | 7.86138e-06 | 0 | 7.28712e-06 |
| Eros | 2026-01-01 | 2.12844e-05 | 2.12844e-05 | 0 | 1.97525e-06 |
| Eros | 2027-01-01 | 1.45108e-06 | 1.45108e-06 | 0 | 1.22029e-05 |
| Eros | 2028-01-01 | 8.80443e-06 | 8.80443e-06 | 0 | 2.06792e-06 |
| Eros | 2029-01-01 | 2.70538e-05 | 2.70538e-05 | 0 | 2.29391e-05 |
| Apollo | 2026-01-01 | 1.57132e-05 | 1.57132e-05 | 0 | 1.78853e-05 |
| Apollo | 2027-01-01 | 0.000418022 | 0.000418022 | 0 | 3.337e-05 |
| Apollo | 2028-01-01 | 8.48073e-05 | 8.48073e-05 | 0 | 5.20249e-06 |
| Apollo | 2029-01-01 | 3.7279e-05 | 3.7279e-05 | 0 | 2.1174e-06 |
| Phaethon | 2026-01-01 | 3.00881 | 0.189228 | 0.189244 | 0.189079 |
| Phaethon | 2027-01-01 | 3.06528e-06 | 3.06528e-06 | 0 | 1.20583e-05 |
| Phaethon | 2028-01-01 | 4.81814 | 0.260109 | 0.260166 | 0.260105 |
| Phaethon | 2029-01-01 | 3.6085 | 0.222275 | 0.222279 | 0.222144 |
| Apophis | 2026-01-01 | 9.8507e-05 | 9.8507e-05 | 0 | 3.94719e-05 |
| Apophis | 2027-01-01 | 0.000116083 | 0.000116083 | 0 | 1.32079e-05 |
| Apophis | 2028-01-01 | 0.000100586 | 0.000100586 | 0 | 2.95353e-05 |
| Apophis | 2029-01-01 | 0.000102661 | 0.000102661 | 0 | 3.21723e-06 |

## Empirical convergence

| Pair | Median max position shift, km | Max shift, km | Median empirical order |
| --- | ---: | ---: | ---: |
| scale_1_vs_scale_0.5 | 1.84988e-05 | 0.000418022 | 0.2478 |
| scale_0.5_vs_scale_0.25 | 9.94647e-06 | 0.0014963 | — |

## Provisional policy

The tested variable policy with fewest RK4 steps satisfying the provisional 10 m grid budget against a distinct finest run is **scale 1**. Its worst difference is 0.00127707 km, median RK4 count is 5840, and median runtime is 0.0669295 s per window.
This recommendation is conditional on the six-object engineering set and the two-body force model. It should be rechecked after adding planetary perturbers and encounter windows; the observed differences are convergence diagnostics, not a formal error bound.

## Интерпретация и воспроизведение — 2026-09-07

Fixed step 0.03125 day — более мелкий сравнительный расчёт, но не независимо
проверенный эталон. Для Phaethon различие с переменным шагом всё ещё достигает
0.26 km. Между переменными policies scale 1 и 0.25 максимум 0.00127707 km;
между 0.5 и 0.25 — 0.00149630 km. Немонотонность требует осторожности:
наблюдаемое совпадение не доказывает порядок сходимости или строгую точность.

В первой карте достаточности используется общая production scale 0.5 и
проверка против 0.25 для каждого объекта, горизонта и force model. Здесь
scale 1 прошёл предварительный бюджет 10 m для B2, но оптимизация шага разных
кандидатов остаётся отдельным экспериментом. Finest policy не допускается
к рекомендации через сравнение с самой собой; если остальные не проходят,
runner возвращает отсутствие проверенной policy.

```bash
env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv run --no-project --python-preference only-system python -B src/run_numerical_audit.py --root . --config configs/numerical_audit_pilot6.json
```

Автоматический отчёт и полные численные записи: `outputs/numerical_audit/`.

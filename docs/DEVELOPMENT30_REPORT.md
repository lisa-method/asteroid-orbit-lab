# Development30: forecast-time model selection

При допуске **1 km** подходящий кандидат есть для **55/60**
проверочных окон на 12 отложенных объектах. Сравнение ниже учитывает все окна,
включая случаи, где набор кандидатов оказался недостаточен.

- `horizon_rule`: 55/60 допустимых прогнозов; 166.8 s; экономия относительно постоянной SB16-модели 32.0%.
- `physics_rule`: 55/60 допустимых прогнозов; 177.5 s; экономия относительно постоянной SB16-модели 27.6%.

Коэффициенты двух простых правил вычислены только по 18 train-объектам
и зафиксированы до проверки на остальных объектах.

Это ограниченный development-эксперимент с раздельными train и validation
объектами. Он не заменяет финальную оценку и не даёт гарантии точности. Отчёт
построен из локальных зафиксированных артефактов после validation и timing.

## Данные и freeze

Выборка содержит 30 объектов: 18 train и 12 validation; все пять горизонтов
сохраняются внутри объекта. Получено 150 object×horizon cases и 600
model/case records (360 train + 240 validation). Вложенные горизонты одного
объекта не являются независимыми наблюдениями. Финальный test ещё не выбран.

Артефакт правил создан до старта validation и проверен по SHA-256. После
freeze повторное fitting не допускается. `rules.json`:
`96842192ae88a925d3d5fb9c5fe9d45f4fd020e049a50885556b7f65593dc7d5`; train results:
`1a0a39f702baff467bb1b13142c317a48a3de23020b80088bf83fe012ce740d0`; validation results:
`d058c632cd18643748ef8ac07c9ed93728cb441bc045c2cae9f6828d33737f96`; selection results:
`2cebdc671bcbcba290ade3a054ab88b03df1ba85b0d95586fecd1c725a3a6a04`. Коэффициенты и caps — эмпирические
сводки train, а не гарантии ошибки.

## Candidate sufficiency

Эффективная ошибка равна `max(position_error, numerical_difference / 0.1)`, а
ячейка показывает число допустимых model records / число cases. Колонки Any
candidate и None показывают наличие хотя бы одной допустимой модели и отсутствие
такой модели; нельзя предполагать, что strongest model покрывает объединение
всех случаев.

| Split | Tolerance (km) | B2 | B3 | B3+GR | B3+GR+SB16 | Any candidate | None |
|---|---:|---:|---:|---:|---:|---:|---:|
| train | 0.1 | 0/90 | 32/90 | 68/90 | 87/90 | 87/90 | 3/90 |
| train | 1 | 0/90 | 49/90 | 88/90 | 90/90 | 90/90 | 0/90 |
| train | 10 | 0/90 | 70/90 | 90/90 | 90/90 | 90/90 | 0/90 |
| validation | 0.1 | 0/60 | 21/60 | 40/60 | 54/60 | 54/60 | 6/60 |
| validation | 1 | 0/60 | 34/60 | 54/60 | 55/60 | 55/60 | 5/60 |
| validation | 10 | 0/60 | 49/60 | 58/60 | 58/60 | 58/60 | 2/60 |

Кандидаты ровно четыре: B2, B3, B3+GR и B3+GR+SB16. B3 включает все девять
planetary perturbers, включая Moon; B2 — Sun-only. В этой карте нет
non-gravitational terms, Earth J2 или full EIH, и ни одна модель не получает
гарантии физической или численной точности.

## Операционный выбор модели на validation

| Method | Tolerance (km) | Successes / cases | Failures | Fallbacks | Confident failures | Selected models | Full runtime (s) |
|---|---:|---:|---:|---:|---:|---|---:|
| horizon_rule | 0.1 | 53/60 | 7 | 24 | 3 | B3:12, B3+GR:24, B3+GR+SB16:24 | 208.7 |
| horizon_rule | 1 | 55/60 | 5 | 0 | 5 | B3:12, B3+GR:36, B3+GR+SB16:12 | 166.8 |
| horizon_rule | 10 | 58/60 | 2 | 0 | 2 | B3:24, B3+GR:36 | 91.74 |
| physics_rule | 0.1 | 54/60 | 6 | 24 | 2 | B3:13, B3+GR:13, B3+GR+SB16:34 | 228.5 |
| physics_rule | 1 | 55/60 | 5 | 0 | 5 | B3:27, B3+GR:18, B3+GR+SB16:15 | 177.5 |
| physics_rule | 10 | 58/60 | 2 | 0 | 2 | B3:41, B3+GR:18, B3+GR+SB16:1 | 101 |

Время в таблицах — сумма медиан трёх повторов для одного запроса на каждое из
60 окон. Это не длительность всего исследовательского расчёта, который также
включает остальные кандидаты, fine runs и offline диагностику.
Отдельная загрузка эфемерид заняла 1.757 s
для train, 1.851 s для validation и
1.971 s перед прямыми operational вызовами.

`runtime_seconds` у выбранных методов — полная стоимость прямого вызова:
построение forecast-time features для physics rule, inference и рекурсивное
распространение. В benchmark records для fixed candidates записана только
измеренная стоимость causal propagation prefix; overhead селектора туда не
входит. Для отдельного fixed anchor использован прямой rollout B3+GR+SB16:
`fixed_anchor_total_runtime_seconds` = **245.1 s**.
Все методы используют один и тот же validation denominator; поэтому стоимость
и accuracy следует читать вместе при провалах baseline.

### Сравнение с достаточной постоянной моделью

| Постоянная модель | Успех при 0.1 km | При 1 km | При 10 km | Стоимость 60 прогнозов, s |
|---|---:|---:|---:|---:|
| B3+GR | 40/60 | 54/60 | 58/60 | 92.03 |
| B3+GR+SB16 | 54/60 | 55/60 | 58/60 | 245.1 |

| Допуск, km | Постоянная модель, покрывающая все разрешимые случаи | Правило | Полная стоимость, s | Экономия относительно постоянной модели |
|---:|---|---|---:|---:|
| 0.1 | B3+GR+SB16 | horizon_rule | 208.7 | 14.9% |
| 0.1 | B3+GR+SB16 | physics_rule | 228.5 | 6.8% |
| 1 | B3+GR+SB16 | horizon_rule | 166.8 | 32.0% |
| 1 | B3+GR+SB16 | physics_rule | 177.5 | 27.6% |
| 10 | B3+GR | horizon_rule | 91.74 | 0.3% |
| 10 | B3+GR | physics_rule | 101 | -9.8% |

Для B3+GR использованы 50 уже измеренных propagation components и 30 дополнительных прямых вызовов для 10 окон. Все новые вызовы выполнены последовательно после основного timing; ошибки повторно сверены с labels. Постоянная модель для сравнения определяется после опыта по покрытию и стоимости и не является новым selector.

Положительный процент означает экономию, отрицательный — замедление. При 0.1 km horizon rule дополнительно ошибается на одном разрешимом случае, поэтому его экономию нельзя трактовать как равную точность. При 10 km сравнение только с SB16 завышало бы пользу выбора: постоянная B3+GR уже покрывает все 58 разрешимых окон.

Это локальные CPU-замеры с тремя повторами. Они не включают общий cold load
данных, не устанавливают ускорение на другом оборудовании и не дают интервалов
статистической неопределённости для стоимости.
Разница около 0.3% при 10 km не подтверждает устойчивого преимущества horizon rule.
При 1 km physics rule чаще совпадает с oracle по названию кандидата, но суммарно
дороже horizon rule. Совпадение метки само по себе не измеряет экономию времени.

Offline oracle показывает потенциальную стоимость только на случаях, где хотя
бы одна модель прошла допуск. Это диагностическая верхняя граница экономии,
условная на измеренные prefix costs, а не достигнутое ML-ускорение и не результат
рабочего selector. Его стоимость нельзя напрямую вычитать из полной стоимости
всех 60 запросов: denominator и учёт overhead различаются.

| Tolerance (km) | Oracle-feasible / cases | Oracle feasible runtime (s) | Method | Oracle model matches on feasible | False fallback on feasible | Missed no-candidate |
|---:|---:|---:|---|---:|---:|---:|
| 0.1 | 54/60 | 131.2 | horizon_rule | 38 | 20 | 2 |
| 0.1 | 54/60 | 131.2 | physics_rule | 35 | 20 | 2 |
| 1 | 55/60 | 76.62 | horizon_rule | 26 | 0 | 5 |
| 1 | 55/60 | 76.62 | physics_rule | 39 | 0 | 5 |
| 10 | 58/60 | 78.26 | horizon_rule | 35 | 0 | 2 |
| 10 | 58/60 | 78.26 | physics_rule | 50 | 0 | 2 |

`Oracle model matches` считается только на oracle-feasible случаях.
`False fallback` означает лишний fallback там, где допустимая модель была;
`Missed no-candidate` означает уверенный выбор в oracle-infeasible случае.

### 1-km by horizon

| Method | Horizon (d) | Successes / cases | Selected full runtime (s) | Fixed anchor direct runtime (s) |
|---|---:|---:|---:|---:|
| horizon_rule | 7 | 12/12 | 0.7611 | 2.055 |
| horizon_rule | 30 | 12/12 | 5.462 | 14.57 |
| horizon_rule | 90 | 11/12 | 16.57 | 42.59 |
| horizon_rule | 180 | 10/12 | 25.1 | 67 |
| horizon_rule | 365 | 10/12 | 118.9 | 118.9 |
| physics_rule | 7 | 12/12 | 0.8161 | 2.055 |
| physics_rule | 30 | 12/12 | 5.384 | 14.57 |
| physics_rule | 90 | 11/12 | 18.32 | 42.59 |
| physics_rule | 180 | 10/12 | 32.35 | 67 |
| physics_rule | 365 | 10/12 | 120.7 | 118.9 |

### 1-km failures by target stratum

| Method | Stratum | Failures / cases | Failed objects |
|---|---|---:|---|
| horizon_rule | Earth | 3/10 | 153814 |
| horizon_rule | Jupiter | 0/10 | — |
| horizon_rule | Mars | 0/10 | — |
| horizon_rule | Venus | 2/10 | 613569 |
| horizon_rule | inner_controls | 0/10 | — |
| horizon_rule | outer_controls | 0/10 | — |
| physics_rule | Earth | 3/10 | 153814 |
| physics_rule | Jupiter | 0/10 | — |
| physics_rule | Mars | 0/10 | — |
| physics_rule | Venus | 2/10 | 613569 |
| physics_rule | inner_controls | 0/10 | — |
| physics_rule | outer_controls | 0/10 | — |

Перечни failed objects относятся к фактическому validation eligibility, а не
к event metadata, переданным селектору.

### Случаи, где недостаточны все четыре кандидата при 1 km

| Объект | Горизонт, суток | Ошибка SB16-кандидата, km | Разница при уменьшении шага, m |
|---|---:|---:|---:|
| 153814 | 90 | 1.05237 | 0.15935 |
| 153814 | 180 | 4.78886 | 0.43612 |
| 153814 | 365 | 13.4528 | 0.83948 |
| 613569 | 180 | 1.66977 | 0.11517 |
| 613569 | 365 | 12.0892 | 1.6972 |

На train самый полный кандидат прошёл 1 km во всех 90 окнах: примеров класса
«нет достаточной модели» для этого допуска там не было. Поэтому успешный выбор
среди имеющихся моделей и распознавание недостаточности всего набора — две
разные задачи. Текущие правила пропускают такие отказы на validation.
Расхождение с teacher у этих объектов намного больше наблюдаемой чувствительности
к шагу. Это повод для отдельного аудита сил, начальных данных и независимого
solver; конкретная причина этим опытом не установлена.

## Зафиксированные параметры правил

Калибровочные коэффициенты для B2/B3/B3+GR:

| Model | Train max effective error / max(proxy, 1e-6 km) |
|---|---:|
| B2 | 2.096 |
| B3 | 2.61 |
| B3+GR | 13.05 |

У B3+GR+SB16 нет proxy для пропущенной силы; его physics-rule score —
эмпирический train maximum для каждого горизонта:

| Horizon (d) | Strongest empirical cap (km) |
|---:|---:|
| 7 | 2.929e-05 |
| 30 | 0.001707 |
| 90 | 0.0909 |
| 180 | 0.2104 |
| 365 | 0.8238 |

Калибровка proxy не учитывает переход состояния и служит только диагностикой
routing; это не bound траекторной ошибки. На входе прогноза только начальное
состояние, causal planetary ephemerides, горизонт, допуск и scalars,
доступные во время прогноза. Даты CAD events, object/group/split identities,
будущие состояния астероида, references и labels исключены из selector inputs.

Для каждого distinct `(method, case, chosen_model)` выполнены три прямых вызова.
По возможности representative был 1-km
tolerance; идентичные selected paths других tolerances используют ту же timing
measurement и явно помечены в selection records. Runner оценивает rollout после
timing и aborts при расхождении state/error recomputation с benchmark label более
чем на `1e-5 km`; завершённый selection artifact тем самым прошёл этот check.

## Диагностика геометрии

Ниже B2 records на горизонте 365 дней; `geometry_compare` усреднён по
validation objects для всех девяти тел/систем. Predicted — траектория
кандидата B2, sampled на offline evaluation grid; reference — Horizons teacher.
Для Mars и Jupiter
сохраняются соответствующие Horizons system barycentres; они не отождествляются
с CAD labels планетных encounters. Reference annual minimum может не совпадать
с выбранным CAD event.

| Body/system | n comparisons | Mean predicted minimum (km) | Mean reference minimum (km) | Mean Δdistance (km) | Mean absolute Δdistance (km) | Mean Δtime (d) | Mean absolute Δtime (d) |
|---|---:|---:|---:|---:|---:|---:|---:|
| 199 (Mercury) | 12 | 2.816e+08 | 2.816e+08 | 2.05e+04 | 2.576e+04 | -0.006829 | 0.008041 |
| 299 (Venus) | 12 | 2.527e+08 | 2.528e+08 | -8.513e+04 | 1.929e+05 | -0.01426 | 0.03604 |
| 301 (Moon) | 12 | 2.457e+08 | 2.462e+08 | -5.545e+05 | 5.641e+05 | -0.05101 | 0.08984 |
| 399 (Earth) | 12 | 2.457e+08 | 2.462e+08 | -5.537e+05 | 5.655e+05 | -0.03858 | 0.0705 |
| 4 (Mars system) | 12 | 2.394e+08 | 2.391e+08 | 2.99e+05 | 3.084e+05 | -0.2165 | 0.3101 |
| 5 (Jupiter system) | 12 | 5.547e+08 | 5.546e+08 | 1.265e+05 | 1.415e+05 | -0.4484 | 0.4584 |
| 6 (Saturn system) | 12 | 1.401e+09 | 1.401e+09 | -3.509e+05 | 3.655e+05 | 0.3152 | 0.3209 |
| 7 (Uranus system) | 12 | 2.774e+09 | 2.775e+09 | -5.807e+05 | 5.96e+05 | 0.6244 | 0.6244 |
| 8 (Neptune system) | 12 | 4.467e+09 | 4.467e+09 | -2.105e+05 | 2.166e+05 | 0.159 | 0.1607 |

| Target stratum | Validation objects | Mean B2 max position error at 365 d (km) |
|---|---:|---:|
| Earth | 2 | 7.911e+06 |
| Jupiter | 2 | 1.892e+07 |
| Mars | 2 | 6.305e+05 |
| Venus | 2 | 1.22e+06 |
| inner_controls | 2 | 8.788e+04 |
| outer_controls | 2 | 8.144e+04 |

Это conditional propagation/benchmark diagnostics против model-derived Horizons
teacher, а не ML discovery claims, impact predictions или population estimates.
В таблице приведены signed и absolute differences: signed mean может взаимно
сокращать ошибки между объектами.

## Reproducibility and limitations

Проверка сохранённых траекторий пересчитала 402,680
норм позиционных ошибок и соответствующих step differences из raw reference
и accepted endpoints. Максимальное расхождение с сохранёнными сводками:
2.98e-08 km. Проверены 600 model records,
360 решений, object-disjoint split и frozen source/input hashes. Это проверка
согласованности, а не независимый динамический solver.

Каталог содержит 1350 object/horizon/body минимумов и
20 отдельно проверенных выбранных событий;
0 минимумов событий лежат на границе окна.
59 новых Horizons raw-файлов прошли SHA-256/size/header/coverage checks;
daily/refined overlaps совпали по положению и скорости.

Текущий контракт: [docs/DEVELOPMENT30_CONTRACT.md](../docs/DEVELOPMENT30_CONTRACT.md).
Порядок команд и продолжения после паузы:
[DEVELOPMENT30_REPRODUCIBILITY.md](DEVELOPMENT30_REPRODUCIBILITY.md).
Источники: [JPL CAD](https://ssd-api.jpl.nasa.gov/doc/cad.html),
[JPL SBDB](https://ssd-api.jpl.nasa.gov/doc/sbdb_query.html) и
[Horizons manual](https://ssd.jpl.nasa.gov/horizons/manual.html). Координаты
Sun-centered ICRF, geometric, TDB, AU/AU-day; planetary ephemerides —
exogenous inputs, asteroids — massless test particles.

Запуск использует pinned system Python через `uv`, например:

```text
uv run --no-project --python-preference only-system --python 3.14.7 python -B -m unittest discover -s tests
```

Pilot object-disjoint, но selection-biased по заранее заданным target strata,
имеет только 12 validation objects и не устанавливает rare-encounter
generalization. Horizons содержит current orbit-fit information, поэтому это
conditional teacher benchmark/system-identification experiment, а не
historical forecast из contemporaneous observations.

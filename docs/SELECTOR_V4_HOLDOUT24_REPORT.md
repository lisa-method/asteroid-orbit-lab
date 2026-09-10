# Селектор v4: проверка на 24 новых телах

Метод v4 и его артефакты были зафиксированы до выбора и загрузки новой выборки. Проверены 24 новых тела, пять зависимых горизонтов на тело и четыре физических кандидата: 480 записей матрицы, 192 production/fine traces и 1440 решений для трёх допусков. Horizons используется как model-derived reference при заданном initial state.

## Результаты по допускам

Успех одновременно требует max sampled position error ≤ tolerance и production/fine difference ≤ 10% tolerance. Нулевой процент ошибок на этой выборке не является универсальной гарантией.

| Метод | 0.1 km | 1 km | 10 km | 1 km тел со всеми 5 H / 24 | 1 km max error | warnings / 1 km | OOD / 1 km | no-candidate truth / 1 km |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| tree_v3 (CART) | 120 / 120 | 120 / 120 | 120 / 120 | 24 / 24 | 0.319901 km | 0 | 0 | 0 |
| physics_v4 | 120 / 120 | 120 / 120 | 120 / 120 | 24 / 24 | 0.490128 km | 30 | 30 | 0 |
| hybrid_v4 (CART) | 120 / 120 | 120 / 120 | 120 / 120 | 24 / 24 | 0.452721 km | 30 | 30 | 0 |
| fixed_full | 120 / 120 | 120 / 120 | 120 / 120 | 24 / 24 | 0.0552423 km | 0 | 0 | 0 |

Пять горизонтов одного тела образуют зависимый набор; 120 окон не являются 120 независимыми объектами. Warning и no-candidate truth считаются отдельно: fallback не превращает прогноз в гарантированно точный.

## Стоимость при 1 km

| Метод | Сумма медиан, s | К full | Выборов full / 120 |
| --- | ---: | ---: | ---: |
| tree_v3 (CART) | 363.929 | -25.40% | 29 |
| physics_v4 | 327.140 | -32.94% | 36 |
| hybrid_v4 (CART) | 349.444 | -28.37% | 40 |
| fixed_full | 487.860 | +0.00% | 120 |

Время включает causal feature builder, inference, сборку сил и rollout; это сумма медиан трёх чередующихся повторов каждого из 120 случаев. Сравнение physics_v4 и hybrid_v4 показывает добавочную роль обучаемой поправки на тех же физических proxy. Сравнение с v3 приводится только на этой же fresh24 выборке; исторический v3 score 119/120 относится к прежнему frozen эксперименту и здесь не переписывается.

| Горизонт | tree_v3 | physics_v4 | hybrid_v4 | fixed_full |
| --- | ---: | ---: | ---: | ---: |
| 7 d | 1.835 s | 2.664 s | 2.672 s | 4.396 s |
| 30 d | 11.006 s | 15.425 s | 15.396 s | 26.491 s |
| 90 d | 34.616 s | 46.831 s | 46.702 s | 78.928 s |
| 180 d | 68.840 s | 85.520 s | 85.443 s | 134.894 s |
| 365 d | 247.631 s | 176.699 s | 199.231 s | 243.149 s |

Проверка разброса: ниже суммы фактических запусков с одинаковым номером повтора. Они отличаются от основной метрики — суммы медиан по случаям. Общий cold load, загрузка данных, evaluator и fine audit в online cost не входят; тяжёлая параллельная работа во время замеров не запускалась.

| Метод | Повтор 1, s | Повтор 2, s | Повтор 3, s | Сумма медиан feature cost, s |
| --- | ---: | ---: | ---: | ---: |
| tree_v3 (CART) | 365.061 | 364.173 | 364.217 | 10.259 |
| physics_v4 | 328.530 | 329.439 | 327.505 | 15.945 |
| hybrid_v4 (CART) | 351.240 | 350.036 | 349.312 | 15.962 |
| fixed_full | 489.629 | 493.475 | 487.171 | 0.000 |

## Выборы физических моделей и различия physics/hybrid

| Метод | B2 | P | P+GR | P+GR+SB16 |
| --- | ---: | ---: | ---: | ---: |
| tree_v3 (CART) | 0 | 53 | 38 | 29 |
| physics_v4 | 0 | 38 | 46 | 36 |
| hybrid_v4 (CART) | 0 | 41 | 39 | 40 |
| fixed_full | 0 | 0 | 0 | 120 |

Features включают исходный v3 vector, force proxies и геометрию; target future states в feature builder не передаются. Для каждого тела и горизонта ниже указано, когда physics_v4 и hybrid_v4 выбрали разные модели. Причины берутся из сохранённых predicted caps, rejected forces и support flags, а не восстанавливаются по результатам reference.

| Тело | H | physics | hybrid | physics причины | hybrid причины |
| --- | ---: | --- | --- | --- | --- |
| 468910 | 365 | V2-P-GR | V2-P-GR-SB16 | P: 337.168 km; P-GR: 0.640699 km; P-GR-SB16: 0.0678248 km | P: 284.122 km; P-GR: 1.14085 km; P-GR-SB16: 0.147033 km |
| 434080 | 365 | V2-P-GR | V2-P-GR-SB16 | P: 36.7503 km; P-GR: 0.845558 km; P-GR-SB16: 0.0678248 km | P: 27.2747 km; P-GR: 1.50563 km; P-GR-SB16: 0.0678248 km |
| 54686 | 365 | V2-P-GR | V2-P-GR-SB16 | P: 41.9364 km; P-GR: 0.682808 km; P-GR-SB16: 0.0678248 km | P: 31.1236 km; P-GR: 1.21583 km; P-GR-SB16: 0.0678248 km |
| 317958 | 365 | V2-P-GR | V2-P-GR-SB16 | P: 3.594 km; P-GR: 0.763202 km; P-GR-SB16: 0.0678248 km | P: 1.37164 km; P-GR: 1.09201 km; P-GR-SB16: 0.0678248 km |
| 875500 | 180 | V2-P-GR | V2-P | P: 1.16265 km; P-GR: 0.306887 km; P-GR-SB16: 0.0232225 km | P: 0.443722 km; P-GR: 0.374242 km; P-GR-SB16: 0.0232225 km |
| 2855 | 90 | V2-P-GR | V2-P | P: 1.21056 km; P-GR: 0.0311711 km; P-GR-SB16: 0.00805066 km | P: 0.898434 km; P-GR: 0.0380124 km; P-GR-SB16: 0.00805066 km |
| 1673 | 90 | V2-P-GR | V2-P | P: 1.19529 km; P-GR: 0.580603 km; P-GR-SB16: 0.00805066 km | P: 0.887098 km; P-GR: 0.679281 km; P-GR-SB16: 0.00805066 km |

Разные решения physics_v4 и hybrid_v4 наблюдались в 7 из 120 случаев. Это описательная проверка на данной выборке и не доказывает причинное преимущество ML.

## Предупреждения, silent failures и близкие сближения

- **tree_v3 (CART)**: OOD 0, no-candidate predicted 0, actual no-candidate 0, warning 0; OOD reasons: нет.
- **physics_v4**: OOD 30, no-candidate predicted 0, actual no-candidate 0, warning 30; OOD reasons: initial_eccentricity, log10_gr_proxy_km, log10_max_scattering_strength, log10_min_planet_distance_over_hill, log10_ng_amplitude_au_d2, log10_planet_proxy_km, log10_small_body_proxy_km.
- **hybrid_v4 (CART)**: OOD 30, no-candidate predicted 0, actual no-candidate 0, warning 30; OOD reasons: initial_eccentricity, log10_gr_proxy_km, log10_max_scattering_strength, log10_min_planet_distance_over_hill, log10_ng_amplitude_au_d2, log10_planet_proxy_km, log10_small_body_proxy_km.
- **fixed_full**: OOD 0, no-candidate predicted 0, actual no-candidate 0, warning 0; OOD reasons: нет.

На новом holdout strong guard отмечает 0 из 120 окон; истинных no-candidate случаев при 1 km — 0. Поэтому recall предупреждений о сильном сближении и недостаточности полного набора здесь не измерен. OOD warning описывает выход за изученные диапазоны, а не установленную ошибку прогноза.

Для каждого метода приведены worst failures по каждому допуску:

| Метод | Допуск | Тело | H | Ошибка | Статус | warning |
| --- | ---: | --- | ---: | ---: | --- | --- |
| tree_v3 (CART) | 0.1 | — | — | — | нет failures | — |
| tree_v3 (CART) | 1 | — | — | — | нет failures | — |
| tree_v3 (CART) | 10 | — | — | — | нет failures | — |
| physics_v4 | 0.1 | — | — | — | нет failures | — |
| physics_v4 | 1 | — | — | — | нет failures | — |
| physics_v4 | 10 | — | — | — | нет failures | — |
| hybrid_v4 (CART) | 0.1 | — | — | — | нет failures | — |
| hybrid_v4 (CART) | 1 | — | — | — | нет failures | — |
| hybrid_v4 (CART) | 10 | — | — | — | нет failures | — |
| fixed_full | 0.1 | — | — | — | нет failures | — |
| fixed_full | 1 | — | — | — | нет failures | — |
| fixed_full | 10 | — | — | — | нет failures | — |

## Надёжность без исключения предупреждённых случаев

| Метод | Допуск km | Превышения без warning / все unflagged | Warnings | Из них прогноз достаточен | Predicted no-candidate | Нет достаточного кандидата |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| tree_v3 | 0.1 | 0 / 120 | 0 | 0 | 0 | 0 |
| physics_v4 | 0.1 | 0 / 90 | 30 | 30 | 0 | 0 |
| hybrid_v4 | 0.1 | 0 / 84 | 36 | 36 | 7 | 0 |
| fixed_full | 0.1 | 0 / 120 | 0 | 0 | 0 | 0 |
| tree_v3 | 1 | 0 / 120 | 0 | 0 | 0 | 0 |
| physics_v4 | 1 | 0 / 90 | 30 | 30 | 0 | 0 |
| hybrid_v4 | 1 | 0 / 90 | 30 | 30 | 0 | 0 |
| fixed_full | 1 | 0 / 120 | 0 | 0 | 0 | 0 |
| tree_v3 | 10 | 0 / 120 | 0 | 0 | 0 | 0 |
| physics_v4 | 10 | 0 / 90 | 30 | 30 | 0 | 0 |
| hybrid_v4 | 10 | 0 / 90 | 30 | 30 | 0 | 0 |
| fixed_full | 10 | 0 / 120 | 0 | 0 | 0 | 0 |

## Новые объекты и годовой прогноз hybrid при 1 km

| Тело | Страта | Начало | Кандидат | Max error, m | Production/fine, m | Статус |
| --- | --- | --- | --- | ---: | ---: | --- |
| 4953 | Earth | 2027-05-07 | V2-P-GR | 200.832 | 0.014 | predicted_feasible |
| 267221 | Earth | 2029-03-05 | V2-P-GR-SB16 | 13.433 | 0.393 | outside_training_support |
| 435548 | Earth | 2028-12-03 | V2-P-GR | 120.915 | 0.028 | predicted_feasible |
| 490581 | Earth | 2028-11-14 | V2-P-GR | 34.546 | 0.062 | predicted_feasible |
| 5131 | Venus | 2028-05-27 | V2-P-GR | 95.753 | 0.022 | predicted_feasible |
| 468910 | Venus | 2028-03-10 | V2-P-GR-SB16 | 55.242 | 0.018 | predicted_feasible |
| 360502 | Venus | 2029-10-13 | V2-P-GR | 133.684 | 0.068 | predicted_feasible |
| 469737 | Venus | 2029-10-04 | V2-P-GR-SB16 | 36.909 | 1.614 | outside_training_support |
| 434080 | Mars | 2027-01-09 | V2-P-GR-SB16 | 9.734 | 0.010 | predicted_feasible |
| 365449 | Mars | 2027-12-26 | V2-P-GR-SB16 | 16.411 | 0.631 | outside_training_support |
| 490354 | Mars | 2029-01-01 | V2-P-GR-SB16 | 12.565 | 0.058 | outside_training_support |
| 54686 | Mars | 2027-02-24 | V2-P-GR-SB16 | 10.082 | 0.012 | predicted_feasible |
| 394130 | Jupiter | 2029-05-08 | V2-P-GR-SB16 | 4.200 | 0.005 | outside_training_support |
| 551740 | Jupiter | 2027-06-26 | V2-P-GR-SB16 | 9.475 | 0.012 | predicted_feasible |
| 317958 | Jupiter | 2028-05-05 | V2-P-GR-SB16 | 6.784 | 0.007 | predicted_feasible |
| 875500 | Jupiter | 2027-10-06 | V2-P-GR-SB16 | 8.818 | 0.025 | predicted_feasible |
| 2855 | inner_controls | 2029-01-01 | V2-P-GR | 163.607 | 0.014 | predicted_feasible |
| 3077 | inner_controls | 2029-01-01 | V2-P-GR-SB16 | 8.937 | 0.007 | outside_training_support |
| 3056 | inner_controls | 2029-01-01 | V2-P-GR | 177.856 | 0.006 | predicted_feasible |
| 3473 | inner_controls | 2029-01-01 | V2-P-GR | 130.234 | 0.007 | predicted_feasible |
| 1673 | outer_controls | 2029-01-01 | V2-P-GR-SB16 | 7.210 | 0.046 | predicted_feasible |
| 2551 | outer_controls | 2029-01-01 | V2-P-GR | 329.516 | 0.005 | predicted_feasible |
| 2211 | outer_controls | 2029-01-01 | V2-P-GR-SB16 | 5.173 | 0.011 | predicted_feasible |
| 1461 | outer_controls | 2029-01-01 | V2-P-GR-SB16 | 6.883 | 0.005 | outside_training_support |

## Вывод по этому циклу

При главном допуске 1 km silent failures: прежний tree 0, physics 0, hybrid 0. Hybrid меняет время относительно physics на +6.82%; относительно full — на -28.37%.
На этой новой выборке прежний v3 тоже не имеет silent failures при 1 km. Поэтому уменьшение их числа относительно v3 не продемонстрировано; исправление прежнего Izvekov — только inspected replay.
При одинаковом числе успешных окон обучаемая поправка здесь не даёт выигрыша во времени относительно физического правила. Это отрицательный результат для добавочной пользы выбранного ML метода, а не доказательство невозможности полезного ML вообще.
Метод после открытия этого holdout не перенастраивался. Один согласованный цикл завершён; следующие изменения не включаются в этот score.
До freeze прошли 304 unit tests. Также пройдены deterministic training refit и полный аудит train/calibration inputs. Финальные проверки ниже пересчитывают метрики из сохранённых траекторий; они не являются новым независимым physical solver.

## Что изменилось в программе

Оба новых селектора используют оценки пяти групп сил, вычисленные вдоль дешёвого B2 прогноза: планеты, солнечная GR, SB16, Earth J2 и доступный NG. Для каждого кандидата суммируются только исключённые из него группы. К этому масштабу добавляется наблюдённый на train остаток полного кандидата для данного горизонта. Physics rule калибрует общий multiplier, hybrid обучает CART-поправку к тому же масштабу. Признаки не используют будущие состояния целевого тела.

Это позволяет исправлять недооценку отсутствующей силы без персональной ветки по номеру астероида. Однако интеграл нормы ускорения вдоль B2 не является строгой границей ошибки: он не описывает полностью усиление возмущений при рассеянии, неопределённость начального состояния или неизвестные NG параметры. Проверка training support и strong-encounter guard поэтому сохраняет отдельный warning, даже когда численный прогноз оказался точным.

На уже изученном 3418 Izvekov оба v4 выбирают full в трёх прежних ошибочных решениях v3: 30 d / 0.1 km, 90 d / 1 km и 365 d / 10 km. На 90 d это меняет выбранную ошибку с 1.695 km до 0.351 m. Объект теперь входит в train; этот replay показывает устранение известного дефекта, но не увеличивает fresh score.

Отдельный replay warning на известном Апофисе использует только initial state и exogenous inputs. Hybrid отмечает 7/30/90 d как outside_training_support, 180/365 d как strong_encounter_unvalidated при всех трёх допусках. Основной physical rollout в этом replay не выполнялся. Fallback — общий v2, а не специализированный Apophis backend; прежний годовой teacher residual 2.552137 km не уменьшен этим циклом. [Границы решения Апофиса](APOPHIS_ENCOUNTER_CLOSURE_REPORT.md).

Raw audit: 291 unique paths, 21 manifests; все SHA/size/Git exclusions проверены. Из новых тел available NG: 2, not_provided: 22; 80 общих daily/refined узлов совпадают.
Offline verification пересчитала 322144 error rows, 192 traces, 64 event records и 480 timing medians.

Event geometry хранится в годовых records для 16 encounter objects и пересчитывается только на известном refined ±2-day window. Сильные encounter и no-candidate случаи, если их нет в этих данных, остаются непротестированными; отсутствие warning само по себе не является доказательством безопасности.

## Выборка, NG и воспроизводимость

Train v4: 42 ранее inspected тела (18 development train + старые selector24); calibration: 24 ранее inspected тела (12 validation + fresh12). Новые 24 тела disjoint от обеих групп. Выборка включает по четыре Earth/Venus/Mars/Jupiter encounters и inner/outer controls. Загружены только 40 target tables (24 daily + 16 refined); planetary/SB exogenous context переиспользован из frozen development30. NG availability и provenance сохранены в manifest; отсутствующий NG не трактуется как измеренный ноль.

Runtime: `3.14.7 (main, Aug  5 2026, 10:29:49) [Clang 21.0.0 (clang-2100.1.1.101)]`; implementation `cpython`.
Method freeze: `6a52d17635b3de4c56e80e4b56209ddc9e01fa81978ee85080d973e26af75832`.
Matrix SHA256: `4960518766bd9dd9140bf02a9f04166c0d48a4db203b96242f10b3cf3163aff8`.
Direct cost SHA256: `c71485aee55755178217fb8c801f3303ffd66fda506816c6b314b63930a28ff7`.
Verification SHA256: `7a6c706544d85b52b2a12008dc0fe747c4eb9ae738aa45fad539a12d71cf5c3b`.

Артефакты: [contract](SELECTOR_V4_CONTRACT.md), [matrix](../outputs/selector_v4_holdout24/matrix.json), [direct cost](../outputs/selector_v4_holdout24/direct_cost.json), [verification](../outputs/selector_v4_holdout24/verification.json).

Воспроизведение после завершения входных фаз:

```sh
PYTHONPATH=src uv run --no-project --python-preference only-system --python 3.14.7 python -B src/run_selector_v4_holdout24.py verify
PYTHONPATH=src uv run --no-project --python-preference only-system --python 3.14.7 python -B src/report_selector_v4_holdout24.py
```

Отчёт не добавляет новые scores для Апофиса и не изменяет frozen методы или артефакты.

# Свежий whole-object holdout frozen v2 — 2026-09-08

Метод и horizon rule зафиксированы до отбора 12 новых объектов. Ни один из них не входит в прежние 30 development, 6 engineering targets, SB16 или ранее просмотренный Gaia target 1620. Rules не переобучались.

Это первая свежая проверка конкретной v2; маленькая целевая выборка не даёт гарантии для всей популяции. Пять горизонтов одного объекта зависимы.

[Контракт](FRESH_HOLDOUT12_CONTRACT.md), [sampling amendment](FRESH_HOLDOUT12_SAMPLING_AMENDMENT.md).

## Отбор и данные

Первый отбор остановился до новых trajectory calculations: все пять объектов старого Jupiter <0.5 AU каталога уже были просмотрены. До загрузки новых reference trajectories граница для Jupiter расширена до 1 AU; отобраны 0.62777/0.67931 AU события. Остальные strata и метод сохранены. Старый freeze и source snapshot сохранены. Логический ключ Jupiter catalogue в sample остался прежним; amended method freeze связывает его с новым файлом cad_jupiter_2026_2029_10au.json.

| Объект | Группа | Начало TDB | CAD distance, AU |
|---|---|---|---:|
| 440212 | Earth | 2028-08-31 | 0.014991 |
| 528807 | Earth | 2029-03-02 | 0.018773 |
| 437844 | Venus | 2027-03-19 | 0.021710 |
| 475534 | Venus | 2029-11-11 | 0.023332 |
| 155341 | Mars | 2028-01-04 | 0.022017 |
| 220095 | Mars | 2027-03-01 | 0.023055 |
| 139359 | Jupiter | 2029-05-11 | 0.627772 |
| 851249 | Jupiter | 2029-01-07 | 0.679310 |
| 2806 Graz (1953 GG) | inner_controls | 2029-01-01 | — |
| 3013 Dobrovoleva (1979 SD7) | inner_controls | 2029-01-01 | — |
| 1254 Erfordia (1932 JA) | outer_controls | 2029-01-01 | — |
| 251 Sophia (A885 TA) | outer_controls | 2029-01-01 | — |

Один новый CAD snapshot и 20 анонимных Horizons responses: 12 daily + 8 refined (5 минут, ±2 дня). Планеты hourly и SB16 daily повторно используются из прежних immutable данных. Sun-centred ICRF/FRAME, geometric AU/day, TDB. Future target states передаются только evaluator.

## Точность

Каждая ячейка — число допустимых окон из 60. Max position error на merged reference grid ≤допуска, production/fine distance ≤10% допуска.

B2 — только Солнце. P — Солнце, девять planetary perturbers, Earth J2 и NG при доступных параметрах; GR добавляет solar relativity, SB16 — 16 массивных малых тел.

| Модель | 100 m | 1 km | 10 km |
|---|---:|---:|---:|
| V2-B2 | 0/60 | 0/60 | 1/60 |
| V2-P | 20/60 | 33/60 | 49/60 |
| V2-P-GR | 50/60 | 59/60 | 60/60 |
| V2-P-GR-SB16 | 60/60 | 60/60 | 60/60 |
| Frozen horizon selector | 60/60 | 60/60 | 60/60 |

Селектор использует горизонт и допуск; состав сил затем применяется к начальному состоянию и доступным NG inputs. Это прежнее правило, откалиброванное на 18 train-объектах, без новых контекстных признаков.

| Допуск, km | Максимальная ошибка выбранной модели, m | Объект | Горизонт, суток |
|---:|---:|---|---:|
| 0.1 | 58.577 | 440212 | 365 |
| 1 | 207.439 | 3013 | 180 |
| 10 | 1176.342 | 437844 | 30 |

Наибольшая ошибка полной модели: **58.577 m**, объект 440212, 365 суток; production/fine difference 0.039 m.

| Допуск, km | Feasible | Ошибки выбора при наличии кандидата | No candidate | Из них flagged |
|---:|---:|---:|---:|---:|
| 0.1 | 60/60 | 0 | 0 | 0 |
| 1 | 60/60 | 0 | 0 | 0 |
| 10 | 60/60 | 0 | 0 | 0 |

| Объект | Годовая max error полной модели, m | Step difference, m | NG status |
|---|---:|---:|---|
| 440212 | 58.577 | 0.039 | not_provided |
| 528807 | 10.663 | 0.032 | not_provided |
| 437844 | 33.464 | 0.843 | available |
| 475534 | 32.346 | 0.114 | available |
| 155341 | 23.465 | 0.029 | not_provided |
| 220095 | 9.526 | 0.048 | not_provided |
| 139359 | 5.376 | 0.008 | available |
| 851249 | 5.702 | 0.005 | not_provided |
| 2806 | 10.827 | 0.004 | not_provided |
| 3013 | 7.522 | 0.004 | not_provided |
| 1254 | 1.058 | 0.009 | not_provided |
| 251 | 7.064 | 0.005 | not_provided |

NG status: `{'not_provided': 9, 'available': 3}`. Параметры доступны для 437844, 475534 и 139359; источники получены до всех forecast starts, sigma отсутствуют. Первые два header задают A2 с r^-2 законом, третий — другой distance law. Все параметры прочитаны общим adapter без подгонки. Эта матрица не отделяет индивидуальный вклад NG отдельной ablation и не доказывает физический механизм ускорения.

| Объект с NG | A2, AU/day² | alpha, m, n, k, r0(AU) |
|---|---:|---|
| 437844 | 3.778771997531e-14 | 1, 2, 5.093, 0, 1 |
| 475534 | 3.891652340826e-14 | 1, 2, 5.093, 0, 1 |
| 139359 | -2.375397525611e-13 | 0.1112620426, 2.15, 5.093, 4.6142, 2.808 |

Наибольшая production/fine difference полной модели: 0.843 m. Наибольшая sampled velocity error: 4.860511e-05 m/s. Velocity threshold отдельно не калибровался; основной gate — position + step sensitivity.

## Нарушения допуска

Нарушений выбранной моделью на этих 60 окнах при трёх допусках не обнаружено.

Здесь нет ни одного no-candidate случая. Поэтому нулевое число пропущенных отказов не проверяет способность заранее предупреждать о недостаточности всего набора моделей. Старый промах v2 на 983/H90 при 100 m также сохраняется в отдельном development replay.

## Прямой runtime при 1 km

Суммы медиан трёх вызовов на каждом из 60 окон; 360 actual calls. Selector/fixed порядок чередуется. Начальное состояние, NG и эфемериды уже в памяти; inference + force construction + rollout входят в timer. Чтение данных и evaluator вне таймера. Comparator fixed full выбран по development до открытия holdout.

| Стратегия | Время, s | Успехи |
|---|---:|---:|
| Horizon selector | 186.264 | 60/60 |
| Fixed full | 268.261 | 60/60 |

Изменение затрат: **30.57% экономии** (отрицательное значение — замедление). Вывод о выгоде требует учитывать coverage и величину нарушений одновременно. Это простой horizon rule, не ML speedup; при 0.1/10 km direct cost не измерялся.

При 1 km более дешёвая постоянная GR-модель покрывает 59/60 окон, поэтому не обеспечивает одинаковое покрытие с full и selector. При 10 km GR уже проходит все 60 окон: выигрыш против full при 1 km нельзя переносить на этот допуск. Основной direct timing запускался после завершения других тяжёлых проектных расчётов; prefix timings матрицы не используются как оценка достигнутой экономии.

## Проверки, воспроизведение и границы

Проверены 240 records, 180 choices, 96 traces; пересчитаны 161072 error samples. 125 raw files прошли SHA-256/size/Git-ignore. Все source/input hashes frozen v2 сверены. Uncertainty propagation и непрерывный максимум ошибки отсутствуют. Unknown NG/sigma не являются измеренным нулём. Успех Horizons matching не равен точности реальных наблюдений.

```bash
# Existing uv + system Python 3.14.7; no package downloads or environment creation
env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv run --no-project --python-preference only-system --python 3.14.7 python -B src/run_fresh_holdout.py --root . matrix
# Repeat the same prefix with phase cost; completed checkpoints are reused.
# Strong verification: python -B src/verify_fresh_holdout.py --root . (same uv prefix)
# Report: python -B src/report_fresh_holdout.py --root . (through the same uv invocation)
```

Matrix SHA-256: `81fc39c40ff9394f16c7a583d950a0908873ab8a39b3cc5044a396a8c132ef3f`. Direct cost SHA-256: `625504886da69afa317c4199a375008b9d90b0a9af4c695507d0802886f4e09f`.

После просмотра этот holdout становится inspected set. Следующую настройку можно делать как новую версию с отдельной свежей проверкой. Исходные development v1/v2 scores и artifacts не переписаны. Установок, постоянной среды, commit, remote или публикации нет.

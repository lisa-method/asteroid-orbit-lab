# Первая свежая проверка frozen v2 — 2026-09-08

Контракт записывается до отбора объектов и получения их trajectory errors.
Пользователь разрешил проверку на новых объектах и отдельный numerical audit
Апофиса. Это небольшой независимый whole-object holdout текущего метода,
не финальная репрезентативная оценка всей популяции и не новый training set.

## Метод фиксируется до отбора

Используются без изменения `configs/force_models_v2.json`, четыре force
candidates, numerical settings и `outputs/force_models_v2/rules.json`.
Rule обучен на прежних 18 train objects; все ранее просмотренные development
объекты исключаются из нового holdout. Никакой новой калибровки, context-aware
rule или ML в этом опыте нет. Hashes исходного метода, правил, sample algorithm,
этого контракта и четырёх catalogue snapshots фиксируются до отбора.
Отдельный audit Апофиса не изменяет данный метод после freeze.

## Отбор 12 объектов

Шесть прежних strata: Earth, Venus, Mars, Jupiter, inner_controls и
outer_controls, по два новых numbered asteroid в каждой. Используются уже
сохранённые публичные CAD/SBDB snapshots; новые trajectory references ещё
не загружены. Исключаются все 30 development objects, 6 engineering targets,
16 SB441-N16 perturbers и ранее просмотренный Gaia target 1620.

Для encounter strata порядок фиксирован: Earth, Venus, Mars, Jupiter.
Берутся два ближайших по catalogue nominal distance ещё не занятых объекта;
ties: epoch JD, numeric designation. Одна строка/событие на объект. Начало
окна — полночь TDB за 30 календарных дней до даты события. Допускаются
старты 2027-01-01–2029-12-31, чтобы получение сегодняшних NG inputs
предшествовало forecast start. Старые каталоговые ограничения: 0.2 AU,
для Jupiter 0.5 AU. При недостатке объектов опыт останавливается без
подмены группы. Mars/Jupiter force centres — system barycentres 4/5.

Для двух main-belt control strata кандидаты упорядочены SHA-256 строки
`fresh-holdout12-2026-09-08-v1:selection:<stratum>:<id>`, начало 2029-01-01.
Это целевой стратифицированный stress sample, не случайная выборка популяции.
Каталоговая геометрия служит только отбору; не передаётся forecast/selector.
Все пять горизонтов одного объекта зависимы. После раскрытия ошибок объекты
не заменяются; неуспехи остаются в знаменателе.

## Данные и метрики

Для каждого объекта: 366 daily states; для 8 encounter objects дополнительно
5-minute reference в ±2 суток от полуночи даты выбранного события. Только
анонимные последовательные read-only запросы JPL. Raw immutable, с URL,
timestamp, SHA-256, bytes, API version, explicit coordinate contract.
Повторно используются frozen hourly planetary и daily SB16 ephemerides.
Sun-centred ICRF/FRAME geometric vectors, AU/day, TDB. Forecast получает
одно initial state, exogenous ephemerides и доступные на старте NG parameters.
Из raw NG header не читаются будущие target states. Unknown NG/sigma остаётся
ограничением nominal forecast, не измеренным нулём.

Матрица: 12 × 5 горизонтов (7/30/90/180/365 суток) × 4 модели = 240 records.
Три annual production repeats с daily prefixes и один fine run на object/model.
Сохраняются все accepted endpoints; evaluator интерполирует их на merged
reference grid. Ошибки положения/скорости, RTN, numerical step difference.
Достаточность: max position error ≤0.1/1/10 km и production/fine difference
≤10% допуска. Это максимум на сетке, не continuous-time error guarantee.
Считаются coverage каждой модели, selector misses среди feasible cases,
no-candidate cases и fallback detection. Основной допуск 1 km; 0.1 и 10 km
вторичные. Полезный успех: selector не проигрывает fixed full в coverage
при меньшей прямой стоимости. Нулевые ошибки не трактуются как гарантия.

## Стоимость и freeze

Заранее выбранный основной fixed comparator — V2-P-GR-SB16, на основании
development v2. Остальные fixed candidates показываются по accuracy и
prefix cost, но comparator не заменяется по свежим labels. Прямые вызовы
selector и fixed full при 1 km: 60 окон × 2 стратегии × 3 повтора,
чередование порядка. Timer включает inference, сборку сил и rollout;
initial state/NG/ephemerides уже в памяти. Cold load и evaluation отдельно.
Скоростной замер не совмещается с другим тяжёлым расчётом проекта.

Отдельные directories: data/raw/fresh_holdout12,
data/processed/fresh_holdout12, outputs/fresh_holdout12. Завершённые
checkpoints и traces не переписываются; resume проверяет hashes и runtime.
Run source/input freeze добавляется до первого нового forecast. Проверяются
240 records, 180 choices, complete timings, raw provenance и trace-derived
errors. Никакие existing frozen files не изменяются. Сбои/недоступные данные
записываются явно; по ошибкам holdout этот метод не перенастраивается.

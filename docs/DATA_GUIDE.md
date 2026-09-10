# Данные и data contract

## Confirmation100: крупная независимая выборка — 2026-09-10

[Контракт](SELECTOR_CONFIRMATION100_CONTRACT.md) фиксирует 100 новых тел,
не пересекающихся ни с training/calibration, ни с прежними inspected holdouts.
Метод v4 не изменён. Выборка включает 4 тесных и 16 умеренных Earth encounters,
по 12 Venus/Mars/Jupiter, по 16 inner/outer belt controls, 6 тел с опубликованным
A2 и 6 low-perihelion. Прямой timing subset — 24 ID, выбранных заранее.
Всего 500 вложенных окон; независимая единица — тело, а не строка таблицы.
Это стратифицированная сложная выборка, не оценка долей во всей популяции.

До открытия target vectors сохранены полные CAD/SBDB snapshots, проверены
canonical designation/SPK ID и quality gate: condition code≤3, data arc≥365d,
не two-body fit. Включены numbered и unnumbered тела. В пределах заданного
пула нашлись только 4 подходящих новых tight Earth cases; квота изменена
по metadata feasibility до freeze, без просмотра ошибок траекторий.
Метаданные: 237 files, 122 rejected candidate/stratum occurrences.

`data/processed/selector_confirmation100/sample.json` и 601 dependency
зафиксированы в `outputs/selector_confirmation100/experiment_freeze.json`,
SHA256 `d748a396ede50127c3c3c6f8429d075b987559a3134755d47f33e611f825ee39`.
Raw namespace `data/raw/selector_confirmation100/`; два manifest:
`data/checksums/selector_confirmation100_metadata_manifest.json` и
`data/checksums/selector_confirmation100_manifest.json`.
156 target tables загружены: 100 daily×366 и 56 refined×1153 через 5min.
Все 280 общих узлов совпадают точно. Координаты heliocentric Sun/ICRF,
geometric, TDB, AU и AU/day. Exogenous ephemerides сохранены прежними.

Horizons NG headers дают 10 `available` и 90 `not_provided`. Дата доступности
проверяется до forecast origin; `not_provided` не означает физический ноль.
Общий raw audit проходит для 684 уникальных paths / 23 manifests: SHA256,
размеры, Git exclusion и отсутствие tracked raw. Результат:
`outputs/selector_confirmation100/raw_verification.json`.
Отдельная проверка физической матрицы и timing также прошла: 2000 records,
800 traces, 1170832 error samples, 224 event records и 480 timing medians.
[Итоговый отчёт](SELECTOR_CONFIRMATION100_REPORT.md) и
[физическая интерпретация](SELECTOR_CONFIRMATION100_DIAGNOSTICS.md).
После просмотра результатов эти 100 тел считаются inspected; выборку
нельзя повторно объявить новым test после изменения метода.


## Selector v4: ещё 24 новых тела — 2026-09-09/10

Перед выбором новой выборки зафиксированы 355 зависимостей метода:
`outputs/selector_v4/method_freeze.json`, SHA256
`6a52d17635b3de4c56e80e4b56209ddc9e01fa81978ee85080d973e26af75832`.
Train расширен до 42 уже изученных тел (прежние 18 train + старые selector24),
calibration — 24 других изученных тела (12 validation + fresh12).
Новые 24 ID не пересекаются с ними, pilot6, Geographos и SB16.
Исправленный replay 3418 входит в development и не считается fresh score.

`data/processed/selector_v4_holdout24/sample.json` содержит по четыре тела
Earth/Venus/Mars/Jupiter и двух поясных страт. Immutable raw namespace:
`data/raw/selector_v4_holdout24/`; manifest:
`data/checksums/selector_v4_holdout24_manifest.json`. Добавлены 40 target
tables: 24 daily по 366 узлов и 16 refined по 1153 узла через 5 min.
Все 80 общих daily/refined узлов совпадают точно. Планетные и SB таблицы
переиспользованы напрямую из прежнего frozen exogenous context.
Начальные даты — 2027–2029; Horizons geometric vectors: heliocentric ICRF,
TDB, AU и AU/day. Будущие target states нужны только evaluator, не selector.

NG параметры доступны для 469737 и 490354 до начала соответствующих
прогнозов. Для 22 тел `not_provided` означает отсутствие входных параметров,
а не известное отсутствие физической силы. Проверены header identity,
дата доступности, immutable source hashes и параметры feature builder.
В каталожном пуле после исключений нет новых Earth encounters ≤0.01 AU:
выборка не подтверждает работу warning в режиме встречи Апофиса.

Общий raw inventory: 291 уникальный путь, 21 manifest. SHA256, размеры,
Git ignore и отсутствие tracked raw проверены на pinned Python 3.14.7:
`outputs/selector_v4_holdout24/raw_verification.json`. Канонический полный
аудит train/calibration — `outputs/selector_v4/training_input_audit_complete.json`:
1320 исходных model/error rows, 1320 frozen cost assignments, 330 feature
cases и 2160 сравнений с ранее сохранёнными полями v3. Ранний
`training_input_audit.json` был неполным и не заменяет complete audit.
[Контракт v4](SELECTOR_V4_CONTRACT.md), [API и команды](SELECTOR_V4_USAGE.md).

## Selector v3: 24 новых тела — 2026-09-09

Метод зафиксирован до выбора ID и открытия новых target states:
`outputs/selector_v3/method_freeze.json`, 170 зависимостей, SHA256
`af6dd0c4ba72e08c3be5c5bbe3507051e0c387157f35867c7d7eb0e6e028ad13`.
Train/calibration используют прежние 18/12 development тела; pilot6,
development30, fresh12, Geographos и SB16 исключены из новой выборки.
Метаданные каталога определяют по четыре объекта Earth/Venus/Mars/Jupiter
и двух поясных контрольных страт. Даты событий разрешены sampler/evaluator,
но не входят в forecast-time features; истинные будущие target states
используются только для оценки. Каталожные расстояния новых встреч намного
больше, чем у Апофиса; это не выборка экстремального рассеяния.

`data/processed/selector_holdout24/sample.json` содержит 24 уникальных ID,
начала 2027–2029 годов и provenance. Manifest:
`data/checksums/selector_holdout24_manifest.json`. Новый raw namespace
`data/raw/selector_holdout24/` содержит 49 файлов: 24 daily target tables
по 366 узлов, 16 event refinements по 1153 узла через 5 min и 9 byte-identical
копий ранее загруженных planet files. Общие 80 target nodes совпадают точно.
Рабочий exogenous loader продолжает читать frozen development30 planets;
копии не изменяют силы. Все годовые прогнозы покрыты общими exogenous
эфемеридами; их пересечение JD 2461010.5–2462866.5 TDB.

NG доступны для 152664, 387733, 665390; у остальных 21 статус
`not_provided`, который не означает физически известный ноль. Источники
параметров предшествуют forecast origin. Входы, система координат, TDB,
AU/AU per day и geometric vectors сохраняют соглашения frozen v2.

Полный raw inventory: 20 manifests, 270 entries, 251 уникальный raw path.
SHA256, размер, Git ignore и отсутствие tracked raw проходят проверку.
Первый byte audit был запущен агентом на Python 3.12; его файл сохранён.
Отдельный повтор на pinned Python 3.14.7 подтвердил идентичность всех
содержательных результатов: `outputs/selector_holdout24/raw_verification_python314.json`
и `raw_runtime_validation.json`. Propagation, training и основной benchmark
выполняются только на Python 3.14.7. Raw immutable; transport retry не меняет
sampling или метод. [Контракт](SELECTOR_V3_CONTRACT.md),
[проверенные результаты](SELECTOR_HOLDOUT24_REPORT.md).

## Joint covariance inputs — 2026-09-09

`data/raw/apophis_covariance/sbdb_cov_vec.json` — 4979 bytes, SBDB API 1.3,
JPL solution220, DE441/SB441-N16. Covariance epoch2459215.5 TDB отличается
от `orbit.epoch`2461200.5; mean берётся из `covariance.elements`.
Порядок `e,q,tp,node,peri,i,A1,A2`, 36 upper-triangle column-major values.
`tp` сохраняется текстом, углы в deg, q в AU, NG в AU/day². Fit2024-06-25,
last_obs2022-04-09: допустимы для forecast origin2029, не для issued2021.
Два отдельных old-epoch Horizons vectors проверяют element/frame conversion.
Raw и manifests immutable, без аутентификации.

`data/raw/covariance_spk/` — отдельные 30 ranges DE441, 1048656 bytes;
coverage2020-12-31—2030-01-02, origin2021-01-01, 14 Type2 segments.
Старые SPK sources не менялись. Всего теперь 202 уникальных raw paths,
19 manifests, 221 manifest entries, SHA/size/Git exclusion проверены.

`outputs/apophis_covariance/` содержит freeze, 34 checkpoints, matrix,
verification и явный Cartesian export `forecast_origin_covariance.json`.
Матрица относится только к включённому новому nominal; не прикреплять её
к прежнему initial state2029. Порядок state covariance: x,y,z,vx,vy,vz,A1,A2.
Frozen analysis `parameter_labels` описывает input elements, а не state order;
канонический export различает оба набора labels. `numerically_unresolved`
означает провал nominal numerical gate. σ/dispersion flags не являются
calibrated coverage или validated no-candidate rule.

Поле matrix `new_nominal_teacher_diagnostic` использует legacy mixed source;
отдельный `matched_reference_diagnostic.json` использует annual_daily366.
Источники нельзя смешивать. [Результаты и команды](APOPHIS_COVARIANCE_REPORT.md).

## Native DE441 input audit — 2026-09-09

В `data/raw/apophis_spk/` сохранены 30 неизменённых HTTP byte ranges
публичного NAIF `de441_part-2.bsp`: 176192 bytes, без полного kernel и
без установки SPICE. Manifest: `data/checksums/apophis_spk_manifest.json`;
derived index и download freeze — `data/processed/apophis_spk/`.
Покрытие 2028-12-31 — 2030-01-02 TDB; 14 Type-2/J2000 сегментов,
IDs 1–10, 199, 299, 301, 399 с цепочками центров до SSB. Reader переводит
km/km/s в AU/AU-day; геометрические ICRF векторы, без light-time.
Source URL/range/size/SHA/Last-Modified фиксируются в manifest.

Всего теперь 170 уникальных raw paths / 16 manifests / 189 записей.
Новых target states и новых SB16 inputs нет. 60387 прежних планетных/солнечных
узлов проходят gate <=1 cm, <=1e-6 m/s относительно native DE441.
Межузловые сравнения хранятся отдельно от ошибок траектории Апофиса.
В `outputs/apophis_spk_audit/` — 18 traces (15 новых + 3 reused), source/force
gates, immutable matrix, analysis и offline verification. Все прежние
numerical источники и propagation results сохранены. [Отчёт](APOPHIS_SPK_AUDIT_REPORT.md).

Последующий `outputs/apophis_initial_sensitivity/` содержит 24 новых
DP-ultra probes и переиспользует SPK baseline. Новых raw входов нет;
сохранены nominal и actual representable initial deltas, SI units,
19128 requested states, 62654 native endpoints, derivative/midpoint
diagnostics и отдельный verifier. Две амплитуды — диагностические пробы,
не samples из covariance. Источники, config/contract, parent matrix,
baseline checkpoint и parent verification входят в новый freeze.
[Отчёт и команды](APOPHIS_INITIAL_SENSITIVITY_REPORT.md).

## PPN и барицентрическая система — 2026-09-09

Два новых anonymous Horizons ряда: Sun relative SSB (`10`, `500@0`) и
Pluto system relative Sun (`9`, `500@10`). Каждый содержит 8809 часовых
states с 2028-12-31 00:00 по 2030-01-02 00:00 TDB; ICRF/FRAME,
AU-D, geometric/NONE. Raw файлы в `data/raw/apophis_eih/`, суммарно
3,464,801 bytes, manifest `data/checksums/apophis_eih_manifest.json`.
Всего raw inventory теперь 140 уникальных путей / 15 manifests / 159 записей.
Новых target asteroid states нет; annual initial и восемь teachers прежние.

`outputs/apophis_eih/` содержит гелиоцентрические PPN traces, а
`outputs/apophis_eih_barycentric/` хранит отдельно нативные инерциальные
q,w и преобразованные heliocentric states. Нативная система движется
равномерно с начальным состоянием Солнца: q=rb-rSun0-t*vSun0,
w=vb-vSun0. В оцениватель передаются только явно обозначенные
heliocentric states; каждая конверсия проверяется по raw солнечному ряду.
В force callback нет будущих reference states астероида.

Новые численные файлы, configs и два контракта заморожены раздельно;
completed propagation не повторять. [Результаты и ограничения](APOPHIS_EIH_REPORT.md).

## Gravity conventions — 2026-09-09

Один новый публичный JPL файл `data/raw/gravity_conventions/header.441`,
22,802 bytes; `data/checksums/gravity_conventions_manifest.json`.
Solar `J2SUN`/`ASUN` читаются из исходных групп 1040/1041; координат новых
объектов не загружалось. Восемь прежних Apophis raw headers содержат
Earth J2 only и cutoff 1 au. Проверка заголовков и исходных строк constants
сохранена в `outputs/apophis_gravity_conventions/inputs.json`.
Теперь 138 raw paths, 14 manifests / 157 записей. Новые 12 записей аудита
содержат 9 propagations и 3 точно переиспользованных baseline traces.
[Отчёт и ограничения teacher matching](APOPHIS_GRAVITY_CONVENTIONS_REPORT.md).

## Relative force / precise numerics — 8–9 сентября 2026

Новых raw данных нет: прежние 137 файлов и 13 manifests проверены.
Новые локальные derived результаты — `outputs/precise_propagation/` (24),
`outputs/precise_dp/` (12), `outputs/relative_force_regression/` (132 runs).
Каждый набор имеет отдельные source/input/runtime freeze и immutable
checkpoints. Ни teacher, ни исходный timestamp не переписываются.

Apophis audit сохраняет 797 requested nodes и восемь отдельных references.
Общая development30 regression оценивает 366 uniform daily states на
объект, из того же raw daily запроса, что initial state. Она не заменяет
старую daily+refined оценку и не использует future event dates в forecast.
NG status: 29 `not_provided`, 1 `available` (613569); отсутствие коэффициента
не считается физически известным нулём. [Отчёт](RELATIVE_FORCE_REGRESSION_REPORT.md).

## Apophis shape — 2026-09-08

Три публичных PDS файла: `apophis_v233s7.obj`, его XML label и
`bundle_description.txt`, всего 212060 bytes. DOI10.26033/ydyq-5756,
preliminary Model B по радару 2012–2013 (Brozović et al. 2018).
[PDS bundle](https://pds.nasa.gov/ds-view/pds/viewBundle.jsp?identifier=urn:nasa:pds:gbo.ast-apophis.jpl.radar.shape_model&version=1.0).
Raw: `data/raw/apophis_shape/`; отдельный
`data/checksums/apophis_shape_manifest.json`. Текущий inventory: 13 manifests,
156 записей, 137 уникальных raw-путей, все SHA/size/Git exclusions проверены.

OBJ задаёт body-frame vertices в km, не ICRF attitude на 2029. Для данной
mesh вычислены volume, uniform-density centroid и центральный second moment
в `data/processed/apophis_shape/moments.json`. Габариты 409.741 × 349.074 ×
318.034 m относятся к файлу, не к новому измерению реального тела. Не
сдвигать orbital initial state на geometric centroid и не трактовать
preliminary mesh как точную spin/density модель. [Аудит](APOPHIS_SHAPE_WEAK_FORCE_REPORT.md).

## Apophis reference/time — 2026-09-08

Пять новых anonymous Horizons ответов в `data/raw/apophis_reference_time/`:
годовой daily/hourly (366/8761), Apr 10–18 daily/hourly (9/193) и точный
повтор старого 2020-01-01 — 2030-12-31 daily URL (4018 states). Manifests:
`data/checksums/apophis_reference_time_manifest.json` и
`data/checksums/apophis_reference_time_repeat_manifest.json`. Все имеют
одинаковый JPL#220/NG и прежний Sun-centred ICRF/FRAME, geometric AU-D, TDB.

Пять файлов занимают 2 656 223 bytes. Теперь в 12 manifests 134 уникальных
raw-пути (153 записи); все SHA-256, размеры и Git exclusion проверены.

Новые четыре ряда совпадают точно на общих узлах и с refined teacher.
Точный повтор длинного запроса совпадает со всеми старыми 4018 states.
Однако long/annual различие растёт с 3.609 m на Jan 1 2029 до 17.212 km
на Jan 1 2030. Границы запроса входят в provenance; один orbit solution ID
недостаточен для объединения рядов. Новая annual-hourly initial state
используется только в двух явно выделенных диагностических прогнозах.
Historical 797 teacher сохранён; восемь references оцениваются отдельно
на точных общих датах. Временные knots relative calendar восстанавливаются
из TDB labels без превращения их в UTC. [Результаты](APOPHIS_REFERENCE_TIME_REPORT.md).

## Apophis Moon/Venus — 2026-09-08

Четыре новых anonymous Horizons файла, 8 288 776 bytes:
Earth/Moon/Apophis Apr 10–18 2029 с шагом 5 минут (по 2305 states), Venus
Dec 31 2028–Jan 2 2030 с шагом 15 минут (35233 states). Sun-centred
ICRF/FRAME, geometric AU-D, TDB. Хранятся в data/raw/apophis_moon_venus/,
отдельный data/checksums/apophis_moon_venus_manifest.json.
На этом этапе было **129** уникальных raw-файлов, прежние не изменены.

Planetary knots идентичны старым. Новый refined Apophis совпадает со
старыми 433 refined knots, но отличается от девяти daily knots на 4–87 m
при том же JPL#220 и NG. Annual primary teacher остаётся старым 797,
новый 2305 — отдельный evaluator. Не соединять эти ряды без исследования
причины. [Amendment](APOPHIS_MOON_VENUS_INPUT_AMENDMENT.md),
[результаты и команды](APOPHIS_MOON_VENUS_REPORT.md).

## Fresh holdout12 — 2026-09-08

Добавлены 21 immutable raw-файл: один Jupiter CAD snapshot и 20 Horizons
ответов (12 годовых daily рядов по 366 состояний и 8 refined рядов по 1153
состояния, шаг 5 минут, ±2 суток). Все загрузки анонимные. Всего в проектных
manifests теперь 125 уникальных raw-файлов; прежние файлы не менялись.
Новые данные: `data/raw/fresh_holdout12/`, manifests:
`data/checksums/fresh_holdout12_catalogue_manifest.json` и
`data/checksums/fresh_holdout12_manifest.json`. Координатный контракт прежний:
Sun-centred ICRF/FRAME, geometric AU/day, TDB. Планеты hourly и SB16 daily
повторно используются из предыдущих данных.

В каждой из шести групп по два новых объекта: Earth/Venus/Mars/Jupiter
encounters и inner/outer main-belt controls. Исключены прежние 30 development,
6 engineering targets, SB16 и Gaia target 1620. До расчёта новых траекторий
старый Jupiter <0.5 AU catalogue оказался исчерпан; отдельный sampling
amendment расширил границу до 1 AU. Новые события 0.628/0.679 AU не являются
повтором старой страты <0.5 AU. Метод v2 не изменён.
[Контракт](FRESH_HOLDOUT12_CONTRACT.md),
[amendment](FRESH_HOLDOUT12_SAMPLING_AMENDMENT.md),
[точные пути и legacy catalogue alias](FRESH_HOLDOUT12_REPRODUCIBILITY.md).

Три новых daily header содержат A2: 437844, 475534 и 139359. Первые два
имеют закон r^-2; у 139359 alpha=0.1112620426, m=2.15, n=5.093, k=4.6142,
r0=2.808 AU. Общий NG adapter читает эти параметры без подгонки по residuals.
Conservative availability bound JD 2461293.5 (2026-09-10 TDB) предшествует
всем forecast starts. Sigma отсутствуют; остальные девять NG inputs имеют
status `not_provided`. Это nominal inputs, без uncertainty propagation.
Future target states и известные CAD dates служат evaluator/sampling;
forecast API получает только initial state и доступные exogenous inputs.

## Development30 — 2026-09-07

Отбор и split зафиксированы в [DEVELOPMENT30_CONTRACT.md](DEVELOPMENT30_CONTRACT.md)
до вычисления ошибок кандидатов. Новые данные хранятся отдельно от старого пилота:

- `data/raw/development30/catalogues/`: четыре immutable ответа CAD/SBDB Query;
  версии API 1.5 и 1.0. Отбор по catalogue metadata, без trajectory labels.
- `data/processed/development30/sample.json`: 30 уникальных номеров, группы,
  18 train / 12 validation, окна и hashes конфигурации/контракта/каталогов.
  Ни номер, ни группа, ни split, ни CAD event metadata не являются признаками.
- `data/raw/development30/planets/`: девять Horizons файлов с часовой сеткой
  2025-12-01 — 2030-12-31 (44545 состояний каждого тела).
- `data/raw/development30/asteroids/`: 30 ежедневных годовых рядов и 20
  пятиминутных рядов в ±2 суток от полуночи TDB даты выбранного события. Каждый daily ряд
  содержит 366 состояний, refined — 1153. Совпавшие узлы teacher идентичны.
- `data/checksums/development30_*_manifest.json`: URL, API signature,
  SHA-256/bytes и координатные соглашения. 59 Horizons файлов, 85659338 bytes.

Sun centre, ICRF/FRAME, geometric vectors, AU/day и TDB сохранены. Mars/Jupiter
в force model представлены системными барицентрами (IDs 4/5); каталог CAD
и расстояние до физического центра планеты нельзя молча отождествлять с ними.
SB16 использует прежние 16 daily ephemerides. Raw и generated outputs ignored.

Дополнительная ref-сетка передаётся только evaluator: прогноз имеет собственную
daily output grid и сохраняет accepted RK4 endpoints. `verify_development_data.py`
проверяет отбор, split, хэши, coordinate headers, coverage и совпадение raw узлов.
Повторный download использует локальный cache; JPL запросы строго последовательны.

`outputs/development30/encounter_catalogue.json` содержит 1350 case/body minima
и 20 выбранных событий; все выбранные reference minima внутри своих окон.
`features` в benchmark export содержит только три force proxies; `per_body`,
`min_solar_distance_au`, `planet_max_eta` и `max_eta_by_body` — отдельная диагностика.
Forecast-time API не получает будущие reference asteroid states или даты CAD.
Сверка raw daily/refined узлов дала нулевую разницу положений и скоростей.
По шести project manifests проверены 103 уникальных raw-файла (111069786 bytes),
включая 40 прежних, 59 новых Horizons и четыре snapshots каталогов.

В последующем outlier audit добавлен отдельный
`data/raw/development30_outlier_audit/pluto_system_daily.json`: body 9,
520 daily узлов 2027-12-30–2029-06-01, 107279 bytes, тот же coordinate contract.
GM системы 975.5 km³/s²; отдельный `development30_outlier_audit_manifest.json`.
Теперь 104 raw-файла, исходный development30 manifest не менялся.
У 613569 raw header JPL#47 содержит A2=1.18779071272e-13 AU/day²;
этот параметр сначала использован в отдельном post-hoc NG audit. В новой
v2 интеграции его читает `ng_inputs_v2.py`, проверяя доступность и provenance.
Исходная карта development30 и её feature inputs остаются прежними.

## NG inputs v2 — 2026-09-08

Новых raw-данных не добавлено. NG есть только в header 613569 из 30 daily
файлов; 20 refined headers согласуются с daily. Неизвестные NG и неизвестная
sigma явно отделены от численного нуля. Physically fitted A2 не оценивается
заново по forecast/test residuals.

`NGInput` хранит A1/A2/A3 и distance law, source SHA-256, solution date,
availability bound JD TDB и необязательные sigma для трёх коэффициентов.
Adapter принимает timestamp только из
raw metadata или matching manifest с совпадающими SHA/bytes. Для имеющихся
данных batch manifest создан 2026-09-07; применяется conservative calendar
bound 2026-09-09. Одна osculating epoch или solution date не доказывает
доступность в исторический момент. Значения читаются только до $$SOE.

В `forecast_v2.py` optional string object_id сверяется с `Rec #` NG header,
но не передаётся в выбор модели. Основной input явно содержит Sun-centred
ICRF/FRAME geometric position AU и velocity AU/day, epoch JD TDB, horizon
days и tolerance km. Выход сохраняет daily и accepted-step states, applied
forces, NG status и nominal-only limitation. Подробности и воспроизведение:
[FORCE_MODELS_V2_USAGE.md](FORCE_MODELS_V2_USAGE.md).

## 1. Общая структура

Проект не использует один готовый CSV. Dataset собирается из четырёх официальных
источников и нескольких производных таблиц.

| Источник | Содержимое | Роль |
| --- | --- | --- |
| JPL Horizons | state vectors астероидов | reference trajectories |
| JPL planetary ephemerides | состояния Солнца и планет | force model и признаки |
| JPL SBDB | классы и orbital metadata | sampling и stratification |
| Gaia FPR | RA/Dec, errors, observer state | внешняя observational validation |

## 2. Канонический core convention

Предварительный контракт для основной dynamics table:

```text
center:       heliocentric
frame:        ICRF / equatorial J2000 (Horizons REF_PLANE=FRAME)
time scale:   TDB
distance:     AU
velocity:     AU/day
acceleration: AU/day^2
vectors:      geometric, no light-time or aberration correction
```

В гелиоцентрической неинерциальной системе планетное ускорение должно включать
direct и indirect terms. Альтернативный barycentric implementation допустим,
если B1/B2/B3 используют один и тот же явно задокументированный frame.

Gaia raw epochs и координаты хранятся в нативном виде. Для сопоставления с
Horizons создаётся отдельный проверяемый TCB/UTC → TDB conversion layer.

## 3. Raw tables

### `raw/asteroid_states`

```text
object_id
epoch_tdb
x, y, z
vx, vy, vz
source_query
retrieved_at
```

### `raw/body_ephemerides`

```text
body_id
epoch_tdb
x, y, z
vx, vy, vz
mu
source_query
retrieved_at
```

Состояния планет хранятся один раз на epoch и не дублируются для каждого
астероида в raw layer.

### `raw/objects`

```text
object_id
number_mp
designation
orbit_class
a, e, i, q
moid
data_arc
n_observations
condition_code
source_query
retrieved_at
```

### `raw/gaia_ccd_observations`

Одна строка — одно CCD measurement внутри одного transit:

```text
source_id
number_mp
denomination
transit_id
observation_id
epoch
epoch_utc
ra, dec
ra_error_random, dec_error_random
ra_dec_correlation_random
ra_error_systematic, dec_error_systematic
ra_dec_correlation_systematic
x_gaia, y_gaia, z_gaia
vx_gaia, vy_gaia, vz_gaia
position_angle_scan
is_rejected
```

## 4. Processed dynamics table

Одна строка — один астероид в один epoch:

```text
object_id
epoch_tdb
r_x, r_y, r_z
v_x, v_y, v_z
a_two_body_x, a_two_body_y, a_two_body_z
a_n_body_x, a_n_body_y, a_n_body_z
a_residual_x, a_residual_y, a_residual_z
```

Planet-relative features присоединяются по `epoch_tdb` или вычисляются
детерминированно из отдельной body table.

## 5. Derived physical features

Для каждой планеты `p`:

```text
delta_r_p = r_p - r_asteroid
delta_v_p = v_p - v_asteroid
d_p       = |delta_r_p|
v_rel_p   = |delta_v_p|
eta_p     = |a_p| / |a_sun|
rho_p     = d_p / R_Hill,p
```

Vector features могут быть выражены в radial/transverse/normal basis. Это
уменьшает зависимость модели от произвольного поворота Cartesian frame.

Не использовать как ML features:

- `object_id`, название или MPC number;
- будущие asteroid states;
- statistics, рассчитанные с использованием test interval;
- Gaia rejection decisions или fitted quantities, если их временная
  доступность нарушает forecast protocol.

## 6. Gaia transit aggregation

Gaia FPR содержит 46,350,980 CCD-строк для 156,823 объектов. Несколько строк с
одним `transit_id` — зависимые измерения одного прохода.

Primary processing:

1. raw layer сохраняется неизменным;
2. rejected measurements исключаются из primary analysis, но сохраняются для
   audit;
3. RA переводится в локальную tangent-plane coordinate с учётом `cos(dec)`;
4. random covariance объединяется через precision weighting;
5. common systematic covariance добавляется один раз и не уменьшается как
   независимый шум;
6. observer position/velocity и epoch проверяются на согласованность внутри
   transit;
7. результат — одна строка `gaia_transits` на `source_id, transit_id`.

## 7. Проверенный технический Gaia pilot

Анонимный read-only TAP audit 2026-09-02 дал:

| MPC | Object | CCD rows | Transits | Rejected CCD rows |
| ---: | --- | ---: | ---: | ---: |
| 838 | Seraphina | 1167 | 142 | 90 |
| 433 | Eros | 239 | 43 | 0 |
| 1620 | Geographos | 136 | 34 | 0 |
| 1862 | Apollo | 243 | 40 | 0 |
| 3200 | Phaethon | 480 | 71 | 2 |

Итого: 2,265 CCD rows и 330 independent transit IDs. Это инженерный sample,
а не сбалансированный научный split. Apophis (`99942`) в проверенном FPR query
не обнаружился. Ceres и Vesta имеют высокую долю rejected CCD rows и не должны
автоматически считаться хорошими Gaia validation objects.

## 8. Encounter table

```text
event_id
object_id
perturber_id
t_start, t_ca, t_end
d_min
v_rel_at_ca
eta_max
rho_min
event_quality_flags
split
```

`event_id`, а не отдельная строка, является единицей event-level split.

## 9. Dataset sizes

- 6 объектов: текущий engineering regression set с режимами 2 quiet / 2
  intermediate / 2 extreme.
- 30 объектов: pilot model selection.
- Закрытый test: новые объекты, размер фиксируется до отбора; ориентир
  60–100 объектов относится к возможному расширению исследования.

Текущая дневная сетка 2020–2030 для шести тел содержит 24,108 asteroid-state
rows и 36,162 состояния массивных тел. Это умеренный dataset; основной риск —
temporal dependence и selection bias, а не объём.

Engineering set уже полностью просмотрен и может использоваться для regression
tests, но не как закрытый test. Следующий 30-object набор должен отбираться по
заранее объявленным SBDB strata и разделяться целыми объектами и событиями до
подбора thresholds.

Для B3+ отдельно хранятся 16 дневных Horizons ephemerides массивных asteroid
perturbers и checksum manifest. Их `GM/GM_sun` фиксируются в config вместе с
источником; они не смешиваются с target asteroid states.

## 10. Storage and provenance

- Raw responses immutable.
- Processed data versioned логически через query/config/checksum, но не
  коммитятся в Git.
- Каждая выгрузка хранит endpoint, параметры, retrieval timestamp и лицензию.
- Secrets, tokens и персональные данные запрещены.
- Gaia attribution и DOI обязательны в отчёте.

## 11. Таблицы первой карты достаточности

Контракт: [MODEL_SELECTION_PILOT_CONTRACT.md](MODEL_SELECTION_PILOT_CONTRACT.md).
Артефакты находятся в ignored `outputs/model_sufficiency/pilot6/`.

`metrics.csv` и `results.json` содержат одну запись на
`object_id × start_date × horizon_days × model_id`. В записи сохраняются:

- `split`, regime, число контрольных отсчётов;
- endpoint и максимальная ошибка положения в km, время максимума в днях;
- endpoint и максимальная ошибка скорости в m/s;
- endpoint RTN и максимумы модулей RTN-компонент ошибки положения в km;
- `numerical_difference_km` — максимальная разница production/fine траекторий;
- три измерения runtime, median/min/max в s, шаги RK4, force evaluations и
  число массивных perturbers.

В `*_trajectory.json` находятся предсказанные и reference состояния на
контрольной сетке: JD TDB, положение в AU, скорость в AU/day, ошибки в km и
m/s, RTN-компоненты и численные разницы. RTN для **анализа ошибки** построен
по reference state: R направлен по радиусу, N — по угловому моменту, T = N × R.
RTN-компоненты ошибки скорости — проекция Cartesian velocity residual,
а не производная вращающихся координат.

Эти reference states, ошибки и oracle labels не являются forecast-time
признаками. Рабочие признаки будут сохраняться отдельно с явным описанием
доступности. Объект, все его временные окна и событие должны оставаться в
одной object/event группе; соседние отсчёты и вложенные горизонты не дают
независимых примеров для оценки generalization.

JSON результата также содержит config snapshot, версию Python, platform,
время общей загрузки и SHA-256 исходников, конфигураций и raw manifests.
Oracle для каждого допуска хранит выбранную модель или `null`, число
feasible/infeasible задач и сравнение стоимости на одинаковых подмножествах.
`checkpoint_records.json` — промежуточный артефакт; наличие полного
`results.json` и итогового отчёта отличает завершённый запуск от частичного.

## 12. Предварительный прогноз сближений

В `outputs/encounter_screening/pilot6/forecast_features.json` находятся
предсказанные window minima для каждой пары case/body: расстояние, время,
relative speed, eta и Hill proxy у минимума, boundary flag и параметры сетки.
`case_id`, `object_id` и `strategy_id` — ключи связывания таблиц, а не ML
признаки идентичности астероида. Назначение группы и известное число дней до
события исключены из этого файла.

Отдельный `reference_minima.json` содержит teacher-derived будущую геометрию;
`results.json` — сравнение, TP/FN/FP/TN, ошибки и timing. Группы оценочных
окон и `lead_to_anchor_days` сохраняются только как metadata оценки.
Forecast API не принимает asteroid reference rows или даты будущих событий.
Планетные forecast inputs остаются daily; существующее пяти-минутное
уточнение применяется только в reference evaluator.

Это 34 зависимые задачи × 3 варианта × 9 тел, не 918 независимых encounters.
Контракт: [ENCOUNTER_SCREENING_CONTRACT.md](ENCOUNTER_SCREENING_CONTRACT.md).

## 13. Planetary screening ablation

`outputs/encounter_screening/planets_pilot6/` содержит отдельный B2/B3 запуск;
исходный `pilot6/` snapshot сохраняется. Схемы features/reference/evaluation
совместимы с предыдущим экспериментом; в features дополнительно записаны
`force_model` и `sampling`. Известные lead times и reference errors остаются
только в evaluation. Четыре стратегии на 34 задачах дают 1,224 записи
case/body/strategy, которые нельзя считать независимыми encounters.

`numerical_audit.json` содержит production/fine minima на шести Apophis
окнах с Earth encounter, включая signed distance/time differences для всех
девяти тел. Это чувствительность к шагу, а не оценка ошибки initial state или
планетных эфемерид. `verification.json` проверяет воспроизведение B2
и reference minima из предыдущего snapshot, source hashes, raw checksums,
полноту результатов, outcomes, timing и исключения Git.
При одинаковом runtime используется exact equality; при переходе с Python
3.9.6 на 3.14.7 — отдельно документированный
[bounded comparison](ENCOUNTER_RUNTIME_COMPATIBILITY.md) с сохранением
фактических максимальных различий и отметкой `exact: false`.

Checkpoint schema 2 включает config/source hashes, features, references,
evaluation и timing; JSON записывается атомарной заменой. Legacy checkpoint
от паузы сохранён отдельно, но в финальную матрицу не включался. Full run
использует Python 3.14.7. После него изменён только verifier для поддержки
cross-runtime проверки; его прежний исходник сохранён в provenance с
проверенным hash. Численный код и конфигурации не менялись во время запуска.

Новый прогноз всё ещё использует только daily planetary ephemerides. Accepted
RK4 states сохраняются по прогнозной близости к планете; готовая high-cadence
сетка known event не передаётся в propagator. Контракт:
[ENCOUNTER_PLANETS_CONTRACT.md](ENCOUNTER_PLANETS_CONTRACT.md).

# Обучаемый выбор модели и предупреждения: v3 — 2026-09-09

Пользователь поручил после диагностики Апофиса построить модель и проверить
предсказания на новых телах. Это отдельный experiment version. Все прежние
scores/sources/raw остаются frozen. Визуальное оформление отложено.

## Постановка и границы

Один physical candidate на весь rollout. Входы: initial Cartesian r/v,
start TDB, exogenous planetary ephemerides, доступные на старте NG, horizon
и requested tolerance. Future target states разрешены только evaluator.
Четыре существующих кандидата из force_models_v2.json сохранены без изменений.
Они хорошо проверены на обычных development/fresh12 случаях. Более подробный
Apophis force backend не объявляется универсальным validated fallback.

Главный допуск1km, дополнительные0.1/10km. Horizons7/30/90/180/365d.
Eligibility: max sampled position error<=tolerance и production/fine
difference<=0.1*tolerance. Это условный прогноз при заданном initial state,
не реальная observational confidence guarantee. Covariance status и warning
статус должны быть явными. Horizons — model-derived reference.

## Метод, фиксируемый до обучения

Новый небольшой CART regressor для log10 effective error каждого кандидата:
max(position error, step difference/0.1, 1e-12km). Depth3, минимум4уникальных
объекта в каждом child. SSE split, deterministic midpoints, leaf mean.
Деревья учатся только на original18 development train objects (90 windows
на candidate); original12 validation используются только для отдельной
calibration. Они давно inspected, это новый development/calibration этап,
не новая независимая оценка. Никакой настройки по future holdout24.

К upper prediction добавляется наибольший положительный log residual на
12 calibration objects, с учётом всех5horizons. Это empirical conservative
margin, НЕ доказанный90/95%coverage; нет assumptions exchangeability claim.
Из предсказанных достаточных моделей выбирается самая дешёвая по training
median cost для данного horizon. Если нет подходящей — strongest nominal
candidate с `no_candidate_predicted`, без гарантии точности.

Девять numeric features: log10 horizon; log10 initial radius; initial
eccentricity; log10 трёх B2 integrated omitted-force proxies (planet/GR/SB16),
log10 minimum d/Hill; log10 maximum mu/(d*v_rel^2); log10 sum|Ai|.
Прокси считаются существующим causal B2 feature builder. Floors для proxy
1e-12km, NG1e-30AU/day², geometry1e-12. Отсутствующий NG parameter не является
физически известным нулём: availability сохраняется отдельно. IDs только
для join/split/group constraints, не входят в prediction API.

Hill scale: планеты относительно Sun; Moon относительно Earth (exogenous
расстояние на predicted minimum). Предупреждение `strong_encounter_unvalidated`
включается, если хоть одно predicted minimum имеет d/Hill<0.25 и
mu/(d*v_rel^2)>0.01. Тогда выбирается strongest candidate. Это заранее
заданный heuristic для областей сильного рассеяния, не доказанный детектор
всех случаев недостаточности; B2 может пропустить встречу. FP/FN показываются.
В этой версии нет обещания calibrated uncertainty, выбранная сила может
не пройти допуск. Номер Апофиса не является условием маршрутизации.

## Новые объекты после method freeze

24 уникальных ранее не использованных numbered objects: по4 Earth/Venus/
Mars/Jupiter encounter и inner/outer main-belt controls. Исключаются все
pilot/development30/fresh12 bodies, Geographos и16 massive perturbers.
Metadata-only selection использует сохранённые каталоги: минимальные
расстояния, затемJD/ID для encounters; SHA256 fixed seed для controls.
Порядок страт фиксирован: Earth, Venus, Mars, Jupiter, inner, outer. При
пересечении каталогов уже выбранные ID пропускаются, берутся следующие
по тому же порядку; предварительные counts используют эту же процедуру.
Event starts=event date−30d, interval2027-01-01…2029-12-31, controls2029-01-01.
Если нет4объектов встрате — явно фиксируется ограничение до новых загрузок,
политика не меняется молча. Existing catalogues не содержат forecast errors.

Sample выбирается ТОЛЬКО после записи method_freeze.json с hashes learned
artifact/source/config/contract. Daily366 и refined1153 target states хранятся
раздельно и сверяются на общих узлах. Refinement известного события — только
для evaluator; cadence forecast определяется собственными states.

## Оценка и стоимость

Матрица:24×4×5=480records, production/fine annual rollouts с saved endpoints.
Качество: position/velocity max, failure fraction, worst windows, closest
distance/time errors для16 selected events, numerical differences, выбранные
кандидаты. Зависимые горизонты не выдаются за120независимых тел.
Нет истинно достаточной модели — самостоятельная категория. Warning metrics
различают raw prediction pass и pass среди unflagged windows; нельзя улучшить
score простым исключением неудобных случаев.

Baselines: frozen horizon rule v2, тот же rule с strong guard, fixed full,
learned tree+guard. На1km прямой online timing каждого метода на всех120окнах
с3повторами, alternating order, включает features/inference/force construction/
rollout; shared cold load, external evaluator и offline fine reference audit
исключены и описаны отдельно. Physical matrix predictions не подменяют actual
online timing. Повторные независимые prefixes проверяются против saved matrix.
На0.1/10km качество оценивается также, direct online runtime не обещается.

Положительный результат — измеримый accuracy/cost выигрыш при сравнимой
надёжности. Отрицательный результат (tree хуже правила или дороже) сохраняется.
После открытия holdout не меняются дерево, margins, warnings и candidate set.
Изученные объекты становятся regression, возможные улучшения — новая версия.

## Среда и воспроизводимость

Существующие Python3.14.7/uv/stdlib; никаких сторонних ML зависимостей,
новой среды, account/auth, commit или публикации. Local outputs/data ignored.
Atomic immutable checkpoints с source/input/runtime hashes и resume.
Проверки: synthetic tree/leakage/features/API tests; независимый пересчёт
forecast metrics и решений; SHA/size/Git exclusions raw; completed resume
сохраняет artifacts. Оформление для презентации/GIF отдельно после результатов.

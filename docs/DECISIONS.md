# Журнал решений

## 10 сентября 2026 — confirmation100 и сохранённые реальные анимации

- Пользователь запросил качественную, довольно крупную новую выборку.
  Выбраны 100 ранее не изученных тел в девяти стратах; 45 numbered и
  55 provisional, condition code≤3, arc≥365 d, не two-body fit.
  До открытия target vectors зафиксированы sampler, метод, 24 timing ID
  и 601 dependency. Tight-Earth квота изменена 10→4 только по metadata
  feasibility; moderate расширена до 16 без ослабления quality gate.
  [Контракт](SELECTOR_CONFIRMATION100_CONTRACT.md),
  [все 100 тел](SELECTOR_CONFIRMATION100_SAMPLE_AUDIT.md).
- Метод v4 и старые результаты не менялись. Primary 1 km:
  physics/hybrid/full 500/500, tree_v3 499/500; пять вложенных горизонтов
  не считаются независимыми объектами. Max errors 806.438/522.314/153.166 m
  у physics/hybrid/full. Ошибка v3 у 229215 на 90 сутках — 1.265 km;
  выбор full в v4 даёт 0.329 m. SB16 — доминирующее дополнение в этой
  абляции; индивидуальный возмутитель не идентифицирован.
- При 100 m v4/full 498/500. У 310442 full даёт 100.969 m, хотя P+GR
  даёт 85.642 m: avoidable fallback. У 893065 лучший кандидат даёт
  130.130 m: actual no-candidate. Оба отказа v4 отмечены OOD; это не
  надёжный специальный detector. У hybrid 29 no-candidate predictions:
  один true positive и 28 false positives. При 1 km все 154 OOD cases
  достаточны. Strong guard впервые проверен на одной новой встрече
  2001 AV43, существенно слабее Апофиса.
- Direct wall-clock timing (24 тела, 120 cases, 1440 вызовов):
  physics 413.839 s, hybrid 418.163 s, v3 421.726 s, full 592.082 s.
  Все методы 120/120 именно на этом subset. Экономия physics 30.10%,
  hybrid 29.37%; hybrid медленнее physics на 1.04%. Три повтора сохраняют
  порядок. Это matched comparison добавочной CART-поправки; v3 и v4
  различаются также train/calibration material, поэтому их сравнение
  не изолирует изменение архитектуры.
- Аудит пересчитал 1170832 error samples, 800 traces, 224 event records,
  6000 choices, 480 timing medians. Все 684 raw paths / 23 manifests
  прошли SHA/size/Git exclusion; 156 новых target tables, 280 общих
  узлов совпадают точно; NG available10 / not_provided90. 325 tests.
  [Итоговый отчёт](SELECTOR_CONFIRMATION100_REPORT.md),
  [физическая интерпретация](SELECTOR_CONFIRMATION100_DIAGNOSTICS.md).
- Подготовлены 19 GIF для шести тел: пять новых и прежний Апофис,
  включая сравнение двух методов для 229215. Реальные сохранённые
  prediction/reference epochs, растущие trails, overview и отдельные
  детали. У контрольного тела detail центрируется на максимуме daily
  error после оценки; будущие ошибки не используются селектором.
  Коэффициент увеличения разности подписан, 3D error не увеличивается.
  Swift использует уже доступные Apple frameworks; Python — uv/3.14.7,
  standard library. [Галерея](TRAJECTORY_SHOWCASE.md).
- Ядро бакалаврской задачи завершено как conditional teacher benchmark.
  Годовой Апофис 2.552 km, covariance и универсальная гарантия остаются
  открытыми; их нельзя объявлять решёнными результатом 100/100.
  Нового tuning по этой выборке нет. Публикация/commit/remote не выполнены.

## 10 сентября 2026 — один цикл v4, физическое правило против ML

- По запросу пользователя выполнен один ограниченный цикл улучшения
  выбора сил. V4 суммирует оценки исключённых planet/GR/SB16/Earth J2/
  available-NG групп и train full-error floor. Сравниваются scalar cap
  и depth3 CART-поправка log(error/scale), с empirical calibration.
  Same-horizon train min/max и прежний strong guard возвращают full с
  warning. Это не строгая error bound и не calibrated uncertainty.
- Train42 = прежние 18 + inspected selector24; calibration24 = прежние
  12 + fresh12. Costs берутся из прежней train18 таблицы для обоих v4,
  чтобы не смешивать несопоставимые timing summaries. Известный 3418
  теперь train: оба v4 устраняют три прежних промаха выбором full.
  На 90 d / 1 km выбранная ошибка меняется 1.695 km → 0.351 m;
  этот replay не считается новой проверкой обобщения.
- До выбора новых ID/target states зафиксированы 355 dependencies:
  method SHA256 `6a52d17635b3de4c56e80e4b56209ddc9e01fa81978ee85080d973e26af75832`.
  Новые 24 тела, по четыре в шести стратах, не пересекаются с development.
  В 120 зависимых окнах все четыре метода проходят 0.1/1/10 km gates.
  При 1 km max errors v3/physics/hybrid/full:
  319.901 / 490.128 / 452.721 / 55.242 m.
- 1440 фактических online вызовов, 480 медиан трёх повторов:
  v3 363.929 s, physics 327.140 s, hybrid 349.444 s, full 487.860 s.
  Physics дешевле full на 32.94%, hybrid дороже physics на 6.82%
  при равном числе успешных окон. Выборы двух v4 различаются в семи
  окнах; четыре дополнительных annual full у hybrid перевешивают
  экономию на трёх более коротких окнах. Добавочная польза выбранного
  ML метода здесь не показана. Это локальный результат, не запрет ML.
- В обоих v4 30/120 OOD warnings при 1 km, хотя прогнозы достаточны.
  При 0.1 km hybrid даёт 36 warnings, включая семь независимых
  no-candidate-predicted flags (один также OOD). Истинных no-candidate
  и strong cases на новом holdout нет; recall не измерен. Известный
  Apophis warning replay: 7/30/90 d OOD, 180/365 d strong. Полного
  Apophis rollout в этом цикле нет, прежний остаток 2.552137 km открыт.
- 304 tests прошли до freeze; deterministic refit и полный train/input
  audit прошли. Итоговая verification пересчитала 322144 error rows,
  192 traces, 64 event records и все 480 timing medians. Проверены
  291 raw paths / 21 manifests, 40 новых target tables, 80 общих узлов;
  NG available2 / not_provided22. Старые frozen методы/results сохранены.
  [Отчёт](SELECTOR_V4_HOLDOUT24_REPORT.md), [API](SELECTOR_V4_USAGE.md).
- Цикл завершён; активных расчётов нет. Далее обсудить наглядное оформление,
  без автоматического v5/tuning по просмотренному holdout. Python 3.14.7,
  uv, standard library; без установки, commit, remote или публикации.

## 9 сентября 2026 — bounded Apophis closure и frozen selector v3

- По запросу пользователя завершён ограниченный этап Апофиса: шесть
  независимых extrapolation расчётов и два строгих long controls. Annual
  error относительно согласованной model-derived Horizons — 2.552137 km.
  Local restart из точного teacher перед пролётом даёт 3.327516 km:
  остаток воспроизводится через встречу, но его полная причина не найдена.
  Новый annual coarse/fine spread 0.269925 m, fine/RK4 0.341994 m,
  fine/DP ultra 0.884862 m, fine/DP extreme 1.027599 m. Эти разности не
  являются абсолютной оценкой numerical error.
- Новые long extrapolation/DP различаются на 2.565973 m; относительно
  прежнего long fine — 18.915605/16.349712 m. Поэтому строгий 1 m long
  convergence gate остаётся FAIL; ensemble не пересчитан и uncertainty
  не объявляется calibrated. Годовой 1 km Апофис не закрыт. Подробности:
  [APOPHIS_ENCOUNTER_CLOSURE_REPORT.md](APOPHIS_ENCOUNTER_CLOSURE_REPORT.md).
- Вместо выбора поправок по номеру тела реализован общий CART selector v3
  с отдельным геометрическим guard. Четыре деревца depth3 оценивают
  effective error четырёх неизменённых v2 candidates. Train — прежние
  18 тел, margins — максимальный положительный log residual на прежних
  12. Это эмпирический запас, не доверительный интервал. Apophis guard
  отмечает 180/365 суток на известном regression; свежая проверка этой
  ветви не подменяется этим результатом.
- До выбора новых ID/target states зафиксированы 170 dependencies,
  model/sampler/runner и [контракт](SELECTOR_V3_CONTRACT.md). Fresh24:
  по четыре тела шести страт, 120 вложенных окон, 480 model records.
  Primary tolerance 1 km; дополнительные 0.1/10 km. Все методы и все
  кандидаты оцениваются, ошибочные решения не удаляются из итогов.
- Матрица завершена: CART 119/119/119, horizon rule 116/118/119,
  full 120/120/120 при 0.1/1/10 km. CART ошибается на 3418 при
  30/90/365 сутках соответственно; на 90 сутках P даёт 1.695189 km,
  full — 0.351065 m. Это неверная оценка достаточности, а не отсутствие
  нужной физики в библиотеке. После просмотра holdout метод не меняется.
- Полные online timings и независимая verification завершены. 1440 вызовов,
  480 медиан: CART 349.525 s, horizon 339.071 s, horizon+guard 350.050 s,
  full 481.900 s при 1 km. CART на 27.47% дешевле full и на 3.08% дороже
  простого правила по времени, при разном числе успешных окон. На 365 d
  преимущество по скорости не подтверждено. Это локальные суммы медиан,
  с features/inference/forces/rollout, без общего cold load/evaluator.
  Проверены 322144 error samples, 192 traces, 64 event records и все timings;
  251 raw paths проходят SHA/size/Git exclusion. 277 tests прошли.
  No-candidate и strong-warning случаев на fresh24 нет; надёжность
  распознавания этих режимов остаётся открытой. User-requested visuals
  будут обсуждаться после [итогового отчёта](SELECTOR_HOLDOUT24_REPORT.md).
  Новая настройка требует новой версии/holdout; текущие scores не улучшать
  post-hoc. Нет активных расчётов, установок, commit/publish.

## 9 сентября 2026 — совместная orbit/NG covariance и numerical status

- Завершены 34 frozen propagations по полной covariance JPL220: шесть
  элементов и A1/A2 с корреляциями, epoch2021-01-01, forecast origin2029.
  Это перенос fit2024, не причинный прогноз, сделанный в 2021-м.
- Формальная большая σ: 1.567km на начало2029, 4728.864km на начало2030.
  Linear/full cubature проходят 1% на всех15 датах; максимальные covariance
  расхождения 0.0001301% position /0.0001545% velocity. Это не калибровка
  хвостов или confidence/impact probability.
- Nominal DP extreme/ultra:0.693m до встречи →3640.522m в конце. ≤1m FAIL.
  Поэтому Cartesian export имеет `numerically_unresolved`, даже при хорошей
  внутренней covariance consistency. Нельзя обещать точность по одному
  force-model sufficiency score.
- Canonical export содержит собственный nominal и явные state labels/units.
  Входные SBDB elements не путаются с Cartesian state covariance. Матрица
  не переустанавливается на старый initial state2029 и не подгоняется.
- Новый nominal от2021 отличается от matched annual_daily на129.626m в2029
  и628.938km в2030. Это другой горизонт/initial; старый benchmark2.551km
  остаётся неизменным. Legacy mixed-reference диагностике выделено отдельное
  имя/объяснение; с matched reference её не объединять.
- 243 tests, независимый пересчёт covariance и545012 accepted endpoints,
  202 raw paths/19 manifests прошли integrity checks. Исправлен только новый
  verifier: fixed epsilon для max step заменён математическим ULP bound
  endpoint representation; propagations и численные outcomes не изменены.
- Следующее: независимая длинная propagation, согласование initial state,
  convergence covariance/производных, затем новые встречи для warning.
  Нет установок, commit/publish или новых selector scores. Отчёт:
  [APOPHIS_COVARIANCE_REPORT.md](APOPHIS_COVARIANCE_REPORT.md).

## 9 сентября 2026 — native DE441 и независимая реализация силы

**Решение:** после барицентрического аудита проверить экзогенную интерполяцию
и реализацию RHS до дальнейшего расширения сил. Зафиксированы отдельные
контракт и input design; сохранены 176192 bytes исходных SPK ranges,
14 Type-2 сегментов, без новых target states и без установки SPICE.
Матрица 18 записей включает шесть заданных ephemeris arms × два DP settings
и RK4, три baseline переиспользованы точно.

**Результат:** 60387 исходных узлов совпадают с native DE441 до 2.493 mm.
Между узлами различия достигают 20 km Mercury/5 km Moon/97 m Earth;
замена всех главных источников сдвигает траекторию Апофиса на 209.302 m
и меняет annual error с 2.351305 на 2.551300 km. Это отрицательный результат
для гипотезы полного объяснения остатка интерполяцией. Decimal50 Newton,
PPN, J2, NG на 797 прогнозных states проходят total gate 1e-17 AU/day².
Проверка не доказывает равенство физического набора неизвестным деталям JPL.
Критерии annual 1 km и cross-solver <=1 m (фактически 1.207–1.238 m) открыты.

**Следствие:** сохранять все arms, не выбирать соглашения по минимальному
annual residual. Последующие 24 deterministic initial-state probes завершены:
годовое усиление положения 3151–11743 m/m, скорости — 19–55 km на
1e-6 m/s. Две амплитуды согласуют производные лучше 0.0015%, проходя 1% gate.
Это локальная чувствительность выбранной модели, не covariance или новый
state fit. [Отдельный отчёт](APOPHIS_INITIAL_SENSITIVITY_REPORT.md).
Далее проектировать общий режим сильных встреч по геометрии,
точности входов и численным ограничениям. Никаких ID-dependent сил,
подгонки начального state или новых selector scores в этом аудите.
[Отчёт](APOPHIS_SPK_AUDIT_REPORT.md).

## 9 сентября 2026 — PPN и прямое барицентрическое движение

- По разрешению пользователя выполнены 18-record PPN и следующий 12-record
  frame audit: 27 новых прогонов, 3 точных reused baselines. Отдельные
  freeze до своих расчётов; frame follow-up выбран после первой серии.
- Sun-only outer PPN по Holman2023 рассматривается отдельно от isolated
  Schwarzschild и all-major PPN. Current JPL#220 implementation не доказана.
  Все физические коэффициенты сохранены без подгонки; Earth J2 only.
- Heliocentric Sun-only PPN: 3.171305 km, +Solar J2: 2.299632 km.
  Прямая барицентрическая постановка: 2.351305 / 3.062309 km соответственно.
  All-major ухудшает teacher agreement. Более полный набор сил не считается
  автоматически более точным воспроизведением конкретного teacher.
- Прямой расчёт исключает модельное ускорение солнечного начала; native
  uniform-inertial traces и heliocentric outputs сохраняются раздельно.
  Initial exact, PN barycentric velocities, NG/encounter steps heliocentric.
  Frame shift около 842 m; Pluto 343.110 m, effect solver difference 3.18 mm.
- Межметодный gate <=1 m остаётся FAIL: 1.207–1.253 m в прямой постановке.
  1 km и 100 m annual budgets не достигнуты. Следующее — согласованный
  independent backend/ephemeris, interpolation/convergence и sensitivity,
  а не объявление новой слабой силы по одному уменьшающемуся residual.
- Общие force factories не используют target ID. Frozen operational API,
  selector и прежние holdout scores не менялись. 198 tests, 193320 scalar
  errors и raw140/manifests15 прошли offline checks. Два новых открытых
  ряда Sun/Pluto, 3464801 bytes. Без установок, commit и публикации.
  [Отчёт](APOPHIS_EIH_REPORT.md), [первая фиксация](APOPHIS_EIH_CONTRACT.md),
  [frame контракт](APOPHIS_EIH_BARYCENTRIC_CONTRACT.md).

## 9 сентября 2026 — Apophis teacher contract и gravity conventions

- В восьми исходных ответах Horizons для Апофиса найдено Earth J2 only,
  cutoff 1 au. Прежний численный эффект J3/J4 остаётся верным, но улучшение
  teacher error не доказывает пропуск этих гармоник; возможна компенсация
  другого отличия. В предыдущие отчёты добавлены явные уточнения.
- До новых propagations frozen 2×2 contract: IAU/fixed Earth pole и
  solar J2 off/on. 9 новых runs + 3 reused baselines, одинаковый annual
  initial/teacher, compensated DP extreme/ultra и RK4 scale=0.125.
- Max daily errors: baseline 10.463895 km; fixed pole 7.292059 km;
  solar J2 15.606666 km; совместно 12.422688 km. Все ветви сохранены,
  подгонки не было. Ни 1 km, ни 100 m не достигнуты. Межметодный
  остаток 1.235–1.242 m также не проходит прежний критерий ≤1 m.
- Общая force factory принимает явные соглашения без ID; operational API
  и selector frozen. Для подобных тел предложен класс strong-scattering
  forecasts с проверкой inputs, numerical budget и sensitivity. Новый
  warning/маршрутизация ещё не реализован и не validated на новых событиях.
- Следующая проверка — согласованный full EIH/frame/ephemeris backend.
  Shape/spin/thermal остаются параметрами общей модели; fitted A2 нельзя
  считать дополнительной независимой от того же теплового эффекта силой.
- 185 tests, offline 77328 scalar errors / 406514 native endpoints,
  138 raw / 14 manifests проверены. Один новый JPL constants header,
  22802 bytes. [Полный отчёт](APOPHIS_GRAVITY_CONVENTIONS_REPORT.md).
  Установок, публикации и изменения прежних numerical sources нет.

## 8–9 сентября 2026 — общая относительная модель и численная устойчивость

- По разрешению пользователя устранено рассогласование RK4 h с сохранённым
  временем и добавлена компенсация шести state increments, в новых файлах.
  24-run audit и отдельный 12-run compensated DP follow-up заморожены.
  Fine RK4 сходится до сантиметров; DP/RK4 различаются на 1.24–1.27 m.
  Оба заранее заданных ≤1 m критерии не пройдены; результаты не скрываются.
- Эффект J3/J4 Апофиса около 1.9235 km подтверждается векторами двух
  алгоритмов с различием 2.12 cm / 1.57 mm. Teacher mismatch около 8.5 km
  остаётся необъяснённым. [Численный отчёт](PRECISE_PROPAGATION_REPORT.md).
- Созданы общие `relative_force_model` и `forecast_precise`: нет identity
  routing, Earth J3/J4 явные опции, NG проверяется на forecast origin.
  Физическая арифметика точно совпадает с прежним audit на 797 состояниях.
- 132 новых forecasts на всех 30 inspected development objects: две силы,
  две DP настройки; шесть заранее выбранных controls также compensated RK4.
  Обе силы проходят daily 100 m на всех30, максимум 62.4 m; cross-solver
  difference на шести controls до 7.2 cm. DP API имеет суточные сегменты
  с собственными predicted states, без новых target observations.
- J3/J4 WN5 дают shift 0.7708 m, effect sensitivity 4.94 mm; остальные
  эффекты меньше, 25 DP traces совпадают точно. Это не доказательство
  нулевой силы. Миллиметровое ухудшение 893859 сохранено и не объявляется
  значимым. [Общий отчёт](RELATIVE_FORCE_REGRESSION_REPORT.md).
- 181 тест проходит. Старые scores, source и raw сохранены; новый selector,
  full cost и fresh holdout не выполнялись. Shape/spin/thermal — следующий
  отдельный входной контракт; nominal A2 нельзя удваивать новой силой.
  Новых зависимостей, среды, commit или публикации нет.

## 2026-09-08 — форма Апофиса и Earth J3/J4

- По запросу пользователя исследованы shape/binary различия и выполнена
  отдельная preregistered 24-run weak-force/finite-size ablation. Никаких
  коэффициентов по остаткам не fitted; operational v2 не менялась.
- Публичная PDS preliminary Model B загружена с label/provenance; вычислен
  central second moment при однородной плотности. Три проекции реального OBJ
  сохранены в docs/figures/physics_diagnostics/apophis-pds-shape.svg.
  Габариты mesh не объявляются точными размерами реального Апофиса.
- Earth J3/J4 с IERS conventional coefficients уменьшают matched old/new
  error 10.418/10.462 → 8.495/8.539 km. Парный эффект около 1.923 km,
  его DP tighter/extreme sensitivity 0.550/0.604 m. 1 km бюджет не достигнут;
  прежняя межметодная numerical проблема сохраняется.
- Прямая finite-size COM поправка при трёх fixed attitudes даёт 0.04–1.91 m
  в двух DP настройках. yzx/zxy не отделены от numerical sensitivity;
  sphere даёт точный нуль. Ни полный spin/thermal расчёт, ни torque,
  деформация и resolved binary dynamics не реализованы. A1/A2 сохранены,
  дополнительный Yarkovsky поверх nominal A2 не вводится.
- В pilot6 уже есть binary 1862 Apollo. Shape, multiplicity, spin и
  target reference point — разные свойства; их доступность и uncertainty
  должны входить в будущий data contract. Не добавлять спутник без
  определения прогнозируемого компонента/барицентра и его initial orbit.
- [Отчёт](APOPHIS_SHAPE_WEAK_FORCE_REPORT.md), [контракт](APOPHIS_SHAPE_WEAK_FORCE_CONTRACT.md).
  Новые raw исключены из Git; зависимости, среда и прежние результаты
  сохранены. J3/J4 интегрировать в новый candidate contract после проверки
  других окон/стоимости и независимого numerical confirmation.

## 2026-09-08 — reference и временная арифметика Апофиса

- Пользователь разрешил проверку. До расчётов frozen contract/config,
  четыре controlled queries и amendment точного повтора исходного URL.
  Все 4018 старых states воспроизвелись; новые daily/hourly и refined
  согласованы. Long/annual расходятся на 3.609 m в начале и 17.212 km
  в конце 2029. Подтверждена зависимость от request bounds, внутренний
  механизм JPL не доказан. Ни один teacher не заменён молча.
- 18 неизменных all-force прогнозов: legacy baseline, relative float,
  relative calendar, DP/RK4 sweeps; отдельно два new-initial DP. Четыре
  baseline summaries воспроизведены точно. New modules не меняют frozen v2.
- Relative calendar уменьшает old-initial DP tighter/extreme max shift
  до 0.403 m; new-initial до 1.429 m. RK4 три шага не дают устойчивой
  сходимости; строгой numerical bound пока нет.
- Согласованные old/old и new/new forecasts дают 10.418/10.462 km.
  Замена только evaluator дала бы 6.799 km — это не улучшение метода.
  Начальное шестимерное изменение с 3.609 m position shift приводит к
  парному годовому shift 17.255 km; это не общий uncertainty bound.
- Далее — численная диагностика RK4 и межметодное подтверждение; затем
  отдельные силы с единым reference contract. [Отчёт](APOPHIS_REFERENCE_TIME_REPORT.md).
  Старые selector scores, raw и интеграционные модули сохранены.

## 2026-09-08 — визуальная история J2 и проверка Moon/Venus

- Пользователь разрешил проверку Луны/Венеры и сохранение ранней истории
  земного поля. Созданы SVG, standalone HTML и PHYSICS_DIAGNOSTICS.md;
  публикации нет. Финальные анимации должны постепенно рисовать tracks.
- Зафиксированы девять вариантов входов с двумя DP настройками и четыре
  RK4 контроля. Силы Луны/Венеры уже включены; массы не масштабировали.
- До forecast обнаружен daily/refined target mismatch 4–87 m. Amendment
  сохраняет old 797 primary teacher отдельно от new 2305 dense evaluator.
- V1 остановился при coalescing RK4 времен в абсолютном JD; v1.1 сохраняет
  native times без изменения интеграции/сил. Старые source/freeze оставлены.
- Подробный лунный минимум ~95969 km, 14 апреля ~14:32 TDB. Detailed-vs-old
  RK4 trajectory shift около 1.554 km при двух шагах; step sensitivity около 2 km.
  DP остаток около 10.5 km. Venus-only refinement без стабильного улучшения.
  Force removal даёт ~0.50/2.13 млн km, но не объясняет автоматически остаток.
- Далее: согласованность teacher/initial и relative-time solver+ephemeris.
  [Отчёт](APOPHIS_MOON_VENUS_REPORT.md). 139 tests, 129 raw, 136488 scalar errors,
  41 paired entries; все расчёты завершены.

## 2026-09-08 — свежий holdout v2 и независимая интеграция Апофиса

- Пользователь командой «делай» разрешил свежую whole-object проверку
  замороженной v2 и отдельный независимый numerical audit Апофиса. Силы,
  численная схема и horizon rule v2 не менялись по новым результатам.
- Метод зафиксирован до отбора 12 новых объектов: по два в шести strata,
  исключены 30 development, 6 engineering targets, SB16 и Gaia target 1620.
  Старый Jupiter <0.5 AU catalogue не содержал новых объектов; до новых
  trajectory errors отдельный amendment расширил эту границу до 1 AU.
  Выбраны события 0.628/0.679 AU. Старый freeze и source snapshot сохранены.
- Добавлен 21 anonymous raw input: один CAD snapshot и 20 Horizons файлов.
  Три новых A2 header обработаны общим NG adapter; 139359 имеет отличный
  от r^-2 distance law. Sigma неизвестны; коэффициенты не оценивались заново.
  NG availability bound предшествует всем forecast starts. Будущие target
  states служат только оценке; CAD dates — отбору и reference refinement.
- На 60 окнах четыре модели дают при 100 m **0/20/50/60** успешных случаев,
  при 1 km **0/33/59/60**, при 10 km **1/49/60/60**. Full и frozen horizon
  selector проходят 60/60 на каждом допуске. Full worst 58.577 m; selector
  worst 58.577/207.439/1176.342 m соответственно. Full max step difference
  0.843 m. Это sampled teacher matching, не continuous или observational bound.
- 360 изолированных прямых вызовов при 1 km: selector 186.264 s против
  fixed full 268.261 s, 30.57% экономии при одинаковых 60 успехах. Это суммы
  медиан трёх повторов с resident inputs, включая inference/force construction/
  rollout. Постоянная GR покрывает только 59/60; при 10 km уже 60/60.
  Direct runtime при 100 m/10 km не измерялся; prefix timing не заменяет его.
- No-candidate случаев в fresh12 нет, поэтому предупреждение об отказе
  не валидировано. Прежний replay промах 983/H90 при 100 m сохраняется.
  Fresh12 после просмотра — inspected set; следующей настройке нужен новый
  freeze и отдельная свежая проверка. ML не обучался.
- Независимый stdlib DP5(4) реализован без использования RK4 step, но с той
  же функцией ускорения. В годовом Apophis окне старые RK4 13.1480705 km и
  step difference 1.9977699 km воспроизведены. DP при трёх строгих tolerances
  даёт 12.005854/11.746851/11.958729 km; сходимость до метров не установлена.
  Hourly input сдвигает RK4 траекторию максимум на 0.277863 km. Короткий
  локальный DP forecast около 1.2 m не решает годовую задачу через encounter.
- Stage3 — основной numerical artifact; stage1/2 сохранены как итерации.
  Его offline verifier проверяет оба checkpoints, 44 source/input hashes,
  дополнительно 14 shared hashes из frozen v2, пересчитывает summaries и
  paired shifts из requested states. Исходный fingerprint не включал runtime
  и shared modules; эту границу воспроизводимости явно сохраняем в отчёте.
  Успех artifact check не объявляется численной сходимостью.
- Дешёвый diagnostic измерил JD ULP 40.233 microseconds и сдвиг Earth
  ephemeris 1.195 m между соседними JD в одной точке пролёта. Это разрешение
  временной координаты, не доказанная причина годовой ошибки. Следующая
  ablation — относительное время в solver и ephemeris interpolation/raw knots
  при неизменной физике; только затем решение о дополнительных силах.
- Прошли 130 tests; fresh verifier пересчитал 161072 error samples,
  240 records/180 choices/96 traces и 120 timing rows. Все 125 raw-файлов
  прошли SHA/size/Git-ignore. Completed matrix/cost resume сохранил SHA и mtime.
  Old v2 resume имеет strict inventory mismatch из-за двух новых manifests;
  существующие hashes не менялись. Не удалять inputs и не править старый freeze.
- [Fresh отчёт](FRESH_HOLDOUT12_REPORT.md),
  [воспроизведение](FRESH_HOLDOUT12_REPRODUCIBILITY.md),
  [Апофис: интерпретация](APOPHIS_SOLVER_AUDIT_INTERPRETATION.md).
  Установок, среды, commit, remote или публикации нет; расчёты завершены.

## 2026-09-08 — общий propagator и выбор модели v2

- Пользователь разрешил интеграцию J2/NG командой «делай» после объяснения
  общего подхода. Это возобновление работы после выполненной паузы 17:50.
- Новые модули отделены от frozen development30. Модель собирается из
  явных физических компонентов; её имя — только метка. Включённые силы
  вычисляются на каждом RK4 stage, один состав на весь rollout.
- Во все три планетных кандидата v2 включён Earth J2; NG применяется по
  правилу if_available. Сохраняется чистый B2. Порог включения J2 по
  расстоянию пока не вводится: сначала измеряются точность и полная стоимость.
- NGInput хранит exact nominal parameters, source/hash, availability bound
  и optional sigma. Для локального adapter недостаточно одной solution date:
  нужен timestamp raw или matching manifest, подтверждённый raw SHA/bytes.
  Консервативная граница — UTC calendar date +2 дня, фиксируемая на старте.
- В 30 daily headers NG есть только у 613569; 20 refined headers согласованы.
  Отсутствие NG и sigma не превращается в ноль. Uncertainty propagation
  остаётся отдельным этапом; текущий прогноз nominal.
- V2 horizon rule заново калибруется только на 18 train объектах. Конфиг,
  состав сил, численные настройки и calibration provenance связаны хэшами;
  fallback_model_id задан явно. Accuracy cap остаётся эмпирическим.
- Матрица на прежних 30 объектах — post-hoc regression, старые 12 validation
  дают replay. Прямой cost comparison заранее ограничен 1 km, тремя
  повторами и одинаковыми 60 окнами. Fresh holdout нужен после фиксации метода.
- Отдельный CLI принимает initial state и coordinate contract, выдаёт daily
  и accepted-step trajectory. Optional object_id проверяет принадлежность
  NG header; он не передаётся selector и force builder.
- [Контракт](FORCE_MODELS_V2_CONTRACT.md), [API и команды](FORCE_MODELS_V2_USAGE.md).
- Матрица завершена: 600 records, 240 traces; полный кандидат проходит
  150/150 окон при 100 m, наибольшая ошибка 62.37 m. Заново вычислены
  402680 error samples; проверены 104 raw SHA/size/ignore и 96 frozen v1 hashes.
- V2 horizon rule на старых 12 validation: 59/60, 60/60, 60/60 при 0.1/1/10 km.
  Промах 983/H90 при 0.1 km: выбрана GR, ошибка 140.036 m, train cap 30.367 m,
  fallback=false. Полная модель достаточна. По replay правило не подправлялось.
- 360 прямых вызовов при 1 km: selector 171.165 s, fixed full 244.857 s
  (суммы медиан трёх повторов), оба 60/60. Экономия 30.10%; это локальный
  CPU replay, не ML speedup и не fresh validation. При 0.1/10 km новый
  полный online cost не измерялся. Прошли 113 unit tests.
- Итог: [FORCE_MODELS_V2_REPORT.md](FORCE_MODELS_V2_REPORT.md). Следующее —
  context-aware выбор, fresh whole-object/event holdout, uncertainty и solver audit.

## 2026-09-07 — пять outlier окон закрыты адресными физическими поправками

- Пользователь предложил проверить географию двух объектов и возможный вклад
  пояса астероидов, спутников и Плутона. Выполнены reference geography и
  отдельный post-hoc force audit; исходные rules/results не менялись.
- 153814 (2001 WN5): расстояние от Солнца в годовом окне 0.910–2.339 AU,
  Earth minimum 248711 km. Earth J2 с прежними константами снизил ошибки
  H90/180/365 с 1.052/4.789/13.453 km до 2.605/11.058/29.853 m.
- 613569 (2006 TU7): 0.450–1.250 AU, Venus minimum 2.498 млн km.
  В raw обнаружен fitted A2=1.18779071272e-13 AU/day², r^-2 transverse law.
  Его nominal включение снизило H180/365 с 1.670/12.089 km до 7.762/13.500 m.
  Коэффициент не подгонялся; конкретный физический механизм A2 этим не доказан.
- Все пять окон проходят 0.1 km и численный бюджет 10% допуска; годовые
  production/fine differences 0.830/1.874 m. Остаточные метры требуют
  дальнейшего аудита при ужесточении требований.
- Один новый anonymous Horizons файл Pluto system, body 9, 520 daily узлов;
  GM 975.5 km³/s². Расстояние до объектов около 35 AU. Его добавление меняет
  годовые траектории на 0.212/0.857 m (fine 0.224/0.914 m); это не причина
  километровых провалов. Луна и 16 массивных астероидов уже были в baseline.
  Спутники гигантов учтены общей массой систем; близких пролётов к ним здесь нет.
- Сначала воспроизведён baseline с mandatory error/step gate 1e-5 km,
  затем по accepted traces пересчитаны 37560 position/velocity samples.
  82 unit tests, read-only review, source/input hashes и 104 raw SHA-256/size/
  Git exclusions прошли. Независимого solver и непрерывной гарантии ошибки нет.
- Новые файлы: `audit_development_outlier_forces.py`, `prepare_outlier_pluto.py`,
  `diagnose_development_outlier_geometry.py`, verifier/report scripts и
  `DEVELOPMENT30_OUTLIER_AUDIT_CONTRACT.md` / `DEVELOPMENT30_OUTLIER_AUDIT_REPORT.md`.
- Следующее: candidate contract v2 с J2 и доступными NG parameters,
  проверка всех development объектов и стоимости, затем свежий holdout.
  Старые 55/60 у selector остаются исходным результатом: post-hoc исправление
  пяти окон не является новой независимой оценкой всей системы.
- Пользователь указал крайний срок 17:50 Москвы; расчёты и проверки завершены
  раньше. Установок, remote, commit или публикации нет.

## 2026-09-07 — development30 завершён: выгода выбора зависит от допуска

- Завершены 360 train + 240 validation model records и 360 решений двух правил.
  Rules SHA-256 `96842192ae88a925d3d5fb9c5fe9d45f4fd020e049a50885556b7f65593dc7d5`
  фиксирует настройку только на 18 train-объектах до открытия 12 validation.
- При 0.1/1/10 km есть достаточный кандидат для 54/55/58 из 60 окон.
  Physics rule проходит все разрешимые случаи; horizon rule при 0.1 km
  дополнительно ошибается на объекте 983 при 90 днях. При 1 km оба дают
  55/60, при 10 km — 58/60; отказы не удалялись из denominator.
- 687 прямых operational вызовов с тремя повторами и 30 дополнительных
  fixed B3+GR вызовов. При 1 km horizon/physics rules требуют 166.79/177.54 s
  против 245.12 s у fixed SB16 на тех же 60 окнах: экономия 32.0%/27.6%.
  При 10 km fixed B3+GR покрывает те же 58 случаев за 92.03 s;
  horizon 91.74 s практически равен baseline, physics 101.02 s медленнее
  примерно на 9.8%. Преимущество сложного физического правила не подтверждено.
- Это суммы медиан полной online стоимости, без общего cold load и offline
  оценки. Повторно использованные propagation timings отмечены явно.
  Дополнительное сравнение fixed B3+GR — post-run cost audit; оно не меняет
  правила, входные признаки, force candidates или accuracy labels.
- При 1 km train не содержал no-candidate примеров. Оба правила пропустили
  все пять таких validation окон без fallback. Следующий блок — распознавание
  недостаточности набора, отдельно от выбора среди достаточных моделей.
- Сложные объекты: 153814, горизонты 90/180/365, и 613569, 180/365 суток.
  Strongest annual errors 13.45/12.09 km против 0.84/1.70 m step differences.
  Причина не установлена; нужны отдельные force/initial-state/numerical ablations
  и независимый solver. Это не основание автоматически добавлять ML residual.
- Каталог: 1350 case/body минимумов, 20 выбранных событий, все их reference
  minima внутри окна. Это не полный перечень всех локальных сближений.
- Прошли 79 unit tests, пересчёт 402680 error samples из сохранённых traces,
  проверка choices/provenance и SHA-256/size/Git exclusions 103 raw-файлов.
  В post-run verifier исправлено предположение о схеме features: экспорт
  содержит ровно три proxies, а геометрия хранится отдельно. Численные исходники,
  правила и расчётные результаты при этом не менялись.
- 30 объектов остаются development set; 12 validation теперь просмотрены.
  Дальнейшая настройка требует новой версии опыта и свежей оценки. Final test
  не выбран. Код stdlib, новых установок, среды, remote, commit или публикации нет.
- Итог: [DEVELOPMENT30_REPORT.md](DEVELOPMENT30_REPORT.md), порядок команд:
  [DEVELOPMENT30_REPRODUCIBILITY.md](DEVELOPMENT30_REPRODUCIBILITY.md).

## 2026-09-07 — development30 и два простых правила

- По запросу «делай» начат следующий согласованный этап; код остаётся stdlib
  и выполняется через uv с system Python 3.14.7. Установок и публикации нет.
- До расчёта новых trajectory errors зафиксирован
  [DEVELOPMENT30_CONTRACT.md](DEVELOPMENT30_CONTRACT.md): 30 новых объектов,
  шесть групп по пять, whole-object train/validation 18/12. Объекты старого
  regression set и 16 массивных perturbers исключены. Final test не выбран.
- CAD служит для отбора событий и окон, SBDB — для контрольных орбит;
  object/split/stratum/event metadata исключены из operational feature API.
- Планетные кандидаты учитывают все девять тел. Часовая сетка эфемерид общая
  для всего набора; солнечный B2 preliminary forecast даёт геометрию и
  интегральные proxies пропущенных сил. Это диагностические величины без
  state transition и без гарантии ошибки.
- Horizon rule использует train maxima; physics rule — train calibration
  force proxies. Для последнего кандидата empirical cap зависит от горизонта.
  При отсутствии предсказанно допустимого кандидата сохраняется fallback flag,
  а нарушения точности учитываются. Кандидаты не считаются монотонными.
- Порядок исполнения: полный train, immutable rules artifact, validation,
  прямые operational calls с полной стоимостью features/inference/rollout.
  Один и тот же выбранный путь на разных допусках может делить явно помеченный
  timing measurement; представитель 1 km имеет приоритет. Fine/evaluation и
  shared load учитываются отдельно. Три последовательных timing повтора.
- Результат на новых объектах пока не используется для доработки правил.
  Слабые силы J2/full EIH/NG остаются отдельным исследовательским блоком.

## 2026-09-04 — минимально достаточная физика и выбор модели

- В обсуждении согласован основной вопрос: какие модели достаточны при
  заданном горизонте и допуске и можно ли выбирать наиболее дешёвую достаточную
  модель по информации, доступной при прогнозе.
- Результат выбора оценивается через recursive trajectory/encounter errors,
  долю нарушений допуска и полную стоимость, а не только classification accuracy.
- Первая предложенная версия выбирает модель на весь rollout. Residual ML,
  переключение внутри траектории и Gaia orbit determination — расширения.
- Аудит кода подтвердил: baselines B0–B3+ существуют, селектор физической модели
  ещё не реализован; изменение шага RK4 не является таким селектором.
- Детальный дальнейший план: `NEXT_STEPS_MODEL_SELECTION_2026-09-04.md`.
  Допуски, календарь, stack и размер final test остаются открытыми.

## 2026-09-01 — самостоятельный проект

- Проект орбит малых тел отделён от `physics-attractor-dynamics`.
- Horizons выбран для controlled propagation benchmark.
- Gaia/MPC отмечены как observation-level extensions.

## 2026-09-02 — основной результат

- Простого восстановления `n ≈ 2` недостаточно как центральной цели.
- Главный продукт — future trajectory forecast и closest-approach prediction.
- System identification остаётся интерпретируемым sanity check.

## 2026-09-02 — restricted N-body

- В первой версии астероиды считаются massless test particles.
- Учитывается гравитация Солнца и планет.
- Mutual asteroid–asteroid interactions отложены: они резко усложняют задачу и
  обычно не нужны для выбранного масштаба.

## 2026-09-02 — close approaches

- Геометрическое пересечение орбит не считается encounter.
- Используются simultaneous distance, relative velocity, acceleration ratio и
  Hill-radius distance.
- Близкие сближения оцениваются отдельными event-level metrics и splits.

## 2026-09-02 — роль ML

- ML не предсказывает следующую координату напрямую как основной метод.
- Residual acceleration — diagnostic и возможный target correction model.
- Предпочтительная система адаптивна: cheap solver в спокойном режиме и
  restricted N-body возле сложного encounter.
- ML может корректировать smooth residual или выполнять solver routing.
- Скоростное преимущество ML не предполагается заранее; explicit planetary
  force evaluation может оказаться быстрее и надёжнее.

## 2026-09-02 — данные

- Core: Horizons asteroid states + planetary states + SBDB metadata.
- Gaia FPR: внешняя validation в RA/Dec и uncertainty.
- MPC: отдельная advanced phase после работающего Gaia/core pipeline.
- Technical smoke test: 3–5 objects; pilot: около 30; final: 60–100.

## 2026-09-02 — проектный контекст

- Канонические файлы хранятся в одном локальном Git-репозитории.
- `AGENTS.md` создан для автоматического контекста будущих Codex-задач.
- Полный сырой экспорт диалога не копируется: решения сохранены в компактной,
  проверяемой форме без служебных логов.
- Python environment и dependencies отложены до уточнения требований курса.

## 2026-09-02 — технический EDA pilot

- Engineering sample: Seraphina, Eros, Apollo, Phaethon и Apophis; он выбран
  для проверки разных динамических режимов и не является final test sample.
- Daily backbone: 2020-01-01 — 2030-12-31 TDB, heliocentric Sun-center,
  ICRF, geometric state vectors, AU и AU/day.
- Planetary force features используют direct и indirect heliocentric terms.
  Earth и Moon представлены отдельно; Earth–Moon barycenter одновременно не
  используется, чтобы избежать double counting.
- Raw JPL responses неизменяемы и сопровождаются URL, timestamp и SHA-256 в
  локальных manifests.
- Сильные daily-grid encounter candidates уточняются отдельными синхронными
  high-cadence окнами; daily minimum не считается точным closest approach.
- EDA подтвердил, что Horizons teacher богаче planets-only B3: он использует
  DE441, small perturbers и для части объектов fitted non-gravitational terms.
  Поэтому такой residual нельзя автоматически объявлять ML-target или
  неизвестной физикой.
- Permanent Python environment и сторонние зависимости не создавались: pilot
  воспроизводится standard-library скриптами через `uv` до уточнения требований
  курса.

## 2026-09-04 — шестиобъектный engineering regression set

- К исходным пяти телам добавлена (20) Massalia — стабильный main-belt объект с
  condition code 0, длинной дугой наблюдений и без fitted non-gravitational
  parameters в SBDB.
- Текущий набор зафиксирован как 2 quiet (Massalia, Seraphina), 2 intermediate
  (Eros, Apollo) и 2 extreme (Phaethon, Apophis).
- Этот баланс нужен для проверки pipeline, но не превращает шесть вручную
  выбранных объектов в репрезентативный train/test sample.
- Артефакты pilot 6 имеют отдельные config, manifest, derived directories и
  отчёты; исходный пятиобъектный pilot сохранён и не перезаписывается.

## 2026-09-04 — восстановление центрального закона и B0–B2

- Закон параметризуется через ускорение на 1 AU и безразмерный показатель `n`.
  Такая запись размерностно корректна и позволяет сравнивать `n != 2`.
- Параметры B1 оцениваются по recursive trajectory loss, а не по второй
  производной координат. Synthetic gate восстановил известные параметры с
  ошибками существенно меньше заданного допуска.
- Fit на Massalia, Seraphina и Eros дал `n = 2.00008022` и отклонение амплитуды
  на 1 AU `+0.006%` от fixed solar value. Это sanity check system
  identification, а не новое измерение `mu_sun`.
- Оценка на будущих окнах 2026–2029 использует горизонты 7, 30, 90, 180 и 365
  дней и полностью recursive rollout без teacher forcing.

## 2026-09-04 — restricted N-body baseline B3

- B3 использует Солнце и девять perturbers, direct и indirect heliocentric
  terms, cubic-Hermite interpolation планетных ephemerides и adaptive RK4 step
  возле Солнца и планет.
- На шестиобъектном regression set median 365-day position error составила
  24.9 km для B3 и 53,100 km для B2. Результат подтверждает необходимость
  планетных возмущений, но ещё не доказывает пользу ML или adaptive routing.
- В пяти-минутном Apophis–Earth окне ошибка B3 по grid minimum составила
  0.099 km и 0 минут; B2 — 9,172.7 km и 35 минут. Это не continuous closest
  approach optimization и не operational hazard product.
- Step-sensitivity tests показали, что численная ошибка выбранного интегратора
  существенно меньше model discrepancy в проверенных rollouts.
- Следующий gate перед ML: стратифицированные 30 объектов, event catalogue,
  object/event holdouts и RTN error decomposition. ML добавляется только при
  воспроизводимом остаточном сигнале поверх B3 или при доказуемой выгоде gate.

## 2026-09-04 — B3+ ablation малых сил

- B3 сохранён неизменным как regression baseline; повторный расчёт совпал с
  сохранёнными 120 endpoint metrics до последней записанной цифры.
- Force ladder фиксирован в порядке `B3 → +solar GR → +SB441-N16 → +nominal
  A1/A2/A3`, чтобы измерять вклад каждого слоя через одинаковые recursive
  rollouts.
- Leading solar Schwarzschild correction снизил общую 365-day median position
  error с 24.95 km до 0.465 km. Это главный пропущенный smooth term.
- 16 massive-asteroid perturbers снизили median до 0.0157 km; особенно велик
  эффект для main-belt объектов. Runtime вырос примерно с 46 s до 129 s.
- Nominal Horizons A1/A2 снизили общую median до 0.0133 km и p95 до 0.0486 km,
  но не дали монотонного улучшения в Apophis-2029 post-encounter tail. До
  encounter 90-day error улучшилась с 0.311 km до 0.00194 km, а после scattering
  365-day error выросла с 377.9 km до 1,871.5 km. При 24 окнах p95 исключает
  этот единственный extreme case, поэтому он обязательно показывается отдельно.
- Это не основание отвергать non-gravitational terms: close encounter усиливает
  остаточный mismatch других сил. Следующий отдельный physical ablation — Earth
  J2 и затем, если требуется, full EIH relativity.
- Nominal A1/A2 используются только для teacher matching. Apollo и Phaethon
  snapshot содержит observations после 2026-01-01, поэтому соответствующие
  ранние окна не являются forecast-valid.


## 2026-09-07 — первый эксперимент выбора достаточной модели

- Основной вопрос: какие force models достаточны для заданных горизонта и
  допуска и можно ли выбрать самую дешёвую по forecast-time информации.
  Первая версия выбирает одну модель на весь rollout. Residual ML и
  переключение внутри траектории — расширения.
- Первый bounded experiment: шесть просмотренных объектов, одно начало
  2029-01-01 и горизонты 7/30/90/180/365 дней. Это engineering regression,
  не новый train/test split. Новые окна 2026–2028 в эту матрицу не включены.
- До основного запуска зафиксированы exploratory допуски 0.1/1/10 km,
  max-grid position error и numerical budget 10% допуска. Окончательные
  требования пользователя остаются открытыми.
- Основные кандидаты: B2, B3, B3+GR, B3+GR+SB16, B3+GR+SB16+J2; fitted NG
  исключён. Три timing повтора измеряют causal prefixes при resident
  ephemerides; стоимость общего чтения записывается отдельно.
- RTN errors считаются по reference только для диагностики. Они не могут
  стать признаками forecast-time селектора. Максимум на сетке не является
  continuous-time гарантией.
- Oracle использует фактические ошибки и стоимость; он сохраняет `none`,
  если кандидаты не прошли проверку. Сравнение с постоянной моделью делается
  на одинаковых feasible случаях; затраты будущих признаков и inference
  ещё предстоит включить.
- В численном B2 audit переменный шаг scale 1 отличается от scale 0.25
  максимум на 0.00127707 km на 24 окнах. Это чувствительность, а не строгая
  оценка ошибки. Finest policy не проверяет себя сравнением с самой собой.
- Earth J2 устраняет километровый mismatch короткого Apophis окна, но его
  итоговые метровые ошибки сопоставимы с step sensitivity. На годовом окне
  no-NG ветка после J2 ухудшается, nominal-NG ветка улучшается до 13.15 km;
  coarse/fine difference около 2 km. Full EIH и независимый solver пока
  не проверены, причина остатка не установлена.
- Контракт: `docs/MODEL_SELECTION_PILOT_CONTRACT.md`. Результаты J2 и численного
  аудита сохранены в отдельных отчётах; исходные baseline reports сохраняют
  прежние protocol и выборку.

- Основной запуск завершён: 150 записей и 30 траекторий. При 0.1/1/10 km
  допустимы 26/28/28 задач; paired oracle cost savings составляют около
  20%/59%/1.2% относительно самой дешёвой постоянной модели с тем же покрытием.
  Поэтому исследовать селектор нужно отдельно для каждого допуска; при 10 km
  стоимость признаков может устранить весь выигрыш. Вклад ML ещё не измерен.
- Проверка: 37 unit tests; все численные записи, RTN norms, timing prefixes,
  oracle eligibility и paired costs согласованы; SHA-256 и размеры 40 raw
  файлов совпали. Raw и generated outputs исключены из Git. Установок,
  публикации, новых выгрузок и Git commit в этом блоке не выполнялось.


## 2026-09-07 — сначала предварительная геометрия сближений

- Пользователь выбрал начать с дешёвого предварительного прогноза траекторий
  и сближений; дополнительные малые возмущения исследовать после этого.
- Реализованы `encounter_geometry.py`, `encounter_screening.py` и отдельный
  evaluator. Forecast API принимает только initial state, epoch/horizon,
  constants и daily planet ephemerides. Reference asteroid data и refinement
  остаются в evaluator; будущие event dates не управляют forecast grid.
- Протокол: 30 main case/horizon задач, четыре Apophis lead-time задачи,
  девять тел и три варианта grid/minimum search. Это 102 forecasts и 918
  case/body/strategy оценок с тремя timing повторами, а не независимые events.
- Daily Hermite выбран provisional screening baseline: исправляет sampling
  misses daily nodes, а six-hour Hermite не даёт новых detections при большей
  стоимости. Одна 180-day-lead Apophis–Moon FN на 0.001 AU остаётся; порог
  не подгоняется для её удаления. Нельзя считать эти результаты надёжностью
  на новых событиях или достаточностью основной force model.
- Feature export исключает group/lead-to-known-event metadata; IDs служат
  только join keys. Оценка, reference minima и прогнозные геометрические
  признаки хранятся отдельно. Никакая ML-модель ещё не обучалась.
- 54 unit tests прошли; проверены coverage, alert outcomes, finite values,
  timing repeats, source hashes, raw SHA-256 и Git exclusions. Raw остаются
  неизменными; установки, публикация и новые загрузки не выполнялись.
- Контракт и результаты: `ENCOUNTER_SCREENING_CONTRACT.md`,
  `ENCOUNTER_SCREENING_PILOT6_REPORT.md`. Следующие проверки — независимые
  события и использование предупреждений в выборе модели с учётом overhead;
  дополнительные силы сравнивать отдельными ablations.

## 2026-09-07 — планеты и разрешение геометрии предварительного прогноза

- По команде пользователя выполнена B2/B3 ablation на прежних 34 задачах:
  136 forecasts с тремя timing повторами, 1,224 evaluations и 54 fine-step
  comparisons. Девять point-mass perturbers включают Earth и Moon отдельно;
  GR, SB16, J2 и fitted NG в этой ablation не добавлялись.
- B3 исправляет прежний Moon FN на октябрьском старте. Все три B3 варианта
  дают 8 TP / 0 FN в четырёх lead windows при 0.001 AU; это повтор одного
  события, не оценка generalization. Пороги не подгонялись.
- Accepted RK4 endpoints сохраняются по прогнозной близости к планете,
  без known event dates. Это существенно улучшает fidelity minimum geometry;
  six-hour output само по себе оставляет около 2,200 km ошибки Earth minimum.
  Сетка outputs может менять и остановки RK4; это отмечается отдельно от
  adaptive retention, который не меняет численный state path.
- Earth distance errors для стартов Apr6/Mar14/Jan13/Oct15 при B3 encounter
  steps: 0.0846 / 0.3189 / 4.9310 / 45.4836 km. October Moon error 128.4696 km.
  Fine-step differences Earth/Moon меньше метра в этих шести окнах; причина
  оставшихся физических/эпhemeris расхождений этой ablation не установлена.
- Median 365-day preliminary cost 3.546 s против 0.146 s у B2; у Apophis
  8.229 s против 0.152 s. Польза полного B3 как preliminary feature не
  предполагается заранее. Следующее — независимые object/event splits и
  простое warning/refinement правило с полным overhead.
- Пользователь временно приостанавливал работу и затем разрешил продолжить.
  Старый checkpoint без provenance архивирован; окончательная матрица
  рассчитана заново. Новые checkpoints атомарны и содержат config/source
  hashes; resume CLI пока отсутствует.
- Runtime изменился с Python 3.9.6 на 3.14.7. Strict exact regression ожидаемо
  не прошла на численных последних разрядах; bounded compatibility с заранее
  записанными малыми бюджетами прошла (B2 max 1.431 mm, reference 0.954 mm).
  Verifier обновлён после запуска; оригинал сохранён с hash. Численный код и
  config неизменны. Следующие запуски делать с явно выбранной версией Python.
- 62 unit tests, проверка artifact coverage/outcomes/costs/fine audit,
  source provenance, raw SHA-256/size 40 файлов и Git exclusions прошли.
  Итог: `ENCOUNTER_PLANETS_PILOT6_REPORT.md`; runtime:
  `ENCOUNTER_RUNTIME_COMPATIBILITY.md`. Установок, новых выгрузок и публикации
  не выполнялось. Выбор основной force model и новые splits не реализованы.

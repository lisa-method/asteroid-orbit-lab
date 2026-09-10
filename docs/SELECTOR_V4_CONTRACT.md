# Селектор v4: пропущенные силы, область обучения и новый holdout

Дата протокола: 2026-09-09. Пользователь разрешил один следующий цикл
улучшения после разбора v3. Прежние методы, данные и scores заморожены;
119/120 v3 при 1 km остаётся результатом прежних 24 тестовых тел.

## Исследовательский вопрос

Помогает ли обязательная проверка физических оценок и области обучения
снизить число превышений допуска без предупреждения, сохранив экономию
вычислений? Даёт ли обучение поправки к физической оценке дополнительную
пользу по сравнению с простым правилом на тех же оценках?

Меняются одновременно обучение и правила выбора. Сравнение с v3 оценивает
весь пакет изменений, не изолированный причинный эффект каждого компонента.
Главное сравнение добавочной пользы ML — physics_v4 против hybrid_v4.
Повторной настройки после открытия новой проверки в этом цикле нет.

## Неизменная физическая постановка

Initial Cartesian r/v, start TDB, horizon, tolerance, exogenous ephemerides
и доступные на старте NG — единственные входы прогноза. Один кандидат на
весь rollout. ID только для provenance, joins и whole-object split.
Те же четыре физических кандидата и numerical settings из
configs/force_models_v2.json: B2 / P / P+GR / P+GR+SB16. Подробный backend
Апофиса не входит в эту матрицу. Никакой новой подгонки физических сил.

Главный допуск 1 km; дополнительные 0.1 и 10 km. Горизонты
7/30/90/180/365 суток. Достаточность означает max sampled position error
не выше допуска и production/fine difference не выше 10% допуска.
Horizons — model-derived teacher, не абсолютная истина наблюдений.
Векторная ошибка, скорость, RTN и selected encounter geometry сохраняются.

## Development, обучение и калибровка

Все 66 ранее изученных тел используются с явными новыми ролями:

- train: прежние 18 development train + все прежние 24 selector holdout = 42;
- calibration: прежние 12 development validation + прежние fresh12 = 24.

Таким образом Izvekov и остальные новые для v3 режимы теперь изучены;
устранение их ошибок в replay не называется новым validation результатом.
Ни один объект не пересекает train/calibration/fresh v4. Никакого random
row split. Пять горизонтов одного тела зависимы. Сохраняются исходные
ошибки и numerical comparisons; старые rollouts не перезапускаются.

Все признаки для v4 пересчитываются из initial и exogenous inputs.
Для выбора по стоимости применяется прежняя таблица median cost из
original18 train v3, одинаковая для обоих v4 методов. Это train-подмножество
нынешних 42 тел. Не смешиваются одиночные prefix timings fresh24 и старые
трёхкратные medians. Фактическая стоимость проверяется прямыми вызовами
на новой выборке, а не этой таблицей цен.

## Оценки сил и обучаемая поправка

Сохраняются девять v3 numeric features и его B2 screening. В feature metadata
добавляются Earth J2 и доступная NG оценка на дополнительном таком же B2
rollout; дополнительная работа входит в online runtime. Для каждой группы:

`q_g = AU_km * trapezoid integral_0^H (H-t) ||a_g(t)|| dt`.

Первые три группы planet/GR/SB16 берутся из прежнего causal feature builder.
J2 и NG используют те же параметры и availability gate, что и физический
propagator. NG not_provided не считается физически известным нулём;
нулевой вычисленный вклад относится только к доступной модели.

Сумма исключённых групп q_m определяется force specification:
B2 исключает все пять; P исключает GR+SB16; P+GR исключает SB16; full — 0.
Эти оценки не учитывают state-transition amplification и не являются
строгими границами trajectory error. Непредставленные силы и неточность
входных параметров не получают гарантии от этого контроля.

Обучаемая величина E = max(position error, numerical difference/0.1, 1e-12 km).
На каждом горизонте floor_H — максимальное E полной модели на train.
Физический масштаб s_m = q_m + floor_H.

- physics_v4: cap_m = alpha_m * s_m, где alpha_m — максимум из 1 и E/s_m
  на train и calibration; один коэффициент для кандидата на все горизонты.
- hybrid_v4: CART depth3, min4 unique objects per child, deterministic SSE
  учит log10(E/s_m) только на train. На calibration добавляется максимальный
  положительный residual в log10. cap_m = max(s_m, s_m * 10**(prediction+margin)).

Оба правила проверяют все исключённые группы; ML не может опустить cap ниже
физического масштаба. Из кандидатов с cap<=tolerance выбирается самый дешёвый
по зафиксированной train-таблице. Одинаковые floor, support guard и стоимость
позволяют сравнить scalar rule и обучаемую поправку. Это empirical caps,
не 95% confidence intervals и не доказанные upper bounds.

## Область обучения и предупреждения

Для каждого горизонта сохраняются train min/max исходных признаков:
radius, eccentricity, три force proxy, minimum d/Hill, maximum scattering.
NG amplitude проверяется среди train с available NG, только когда параметр
доступен у нового тела; отсутствие такого train support тоже отмечается.
H обязан точно входить в контракт. Выход хотя бы одного признака за границы
без дополнительного padding означает outside_training_support и full fallback.
Это ограниченный marginal support check, не доказательство принадлежности
многомерному распределению. Он может быть излишне консервативным.

Прежний strong guard сохранён: для одного и того же predicted body minimum
d/Hill<0.25 AND mu/(d*v_rel²)>0.01. Приоритет статусов: strong encounter,
outside support, no candidate predicted, predicted feasible. Причины и
отдельные флаги сохраняются независимо. Full fallback не гарантирует успех.
Выход API: accuracy_guaranteed=False, conditional_on_initial_state=True,
covariance_calibrated=False. Ошибки при warning не исключаются из общего score.

## Следующие 24 тела

Только после method_freeze.json выбираются 24 новых numbered objects:
по четыре Earth/Venus/Mars/Jupiter и inner/outer belt controls. Исходные
каталоги и порядок страт те же, что в v3; исключаются все ранее изученные
тела, Geographos и 16 SB perturbers. Для encounters сортировка distance/JD/ID,
для controls SHA256 seed `selector-v4-holdout24-2026-09-09`. Межстратный
greedy порядок фиксирован. Starts 2027–2029, event−30 суток; controls 2029-01-01.
Предварительная проверка выводит только counts, без выбранных ID или errors.

Сохраняются 24 daily tables по 366 узлов и 16 refined по 1153 узла,
проверяется точное совпадение общих узлов и NG availability. Планетные и
SB16 эфемериды переиспользуются из frozen context без новых копий.
Эта выборка из оставшихся каталожных тел не обещает режим Апофиса:
до freeze counts-only проверка не нашла новых Earth encounters <=0.01 AU.
Strong/no-candidate sensitivity может остаться неоценимой. Для strong guard
допускается отдельно подписанный replay известного Апофиса, без нового score.

## Оценка, стоимость и правило остановки

Матрица: 24 x 4 x 5 = 480 records, 192 production/fine annual traces.
Методы: frozen tree_v3, physics_v4, hybrid_v4, fixed_full. Для всех трёх
допусков: success, all-horizons success per body, maximum error, avoidable
selection failures, no-candidate truth, предупреждения по типам,
unflagged failures, предупреждения при достаточном прогнозе.

При 1 km — 120 задач x 4 метода x 3 реальных вызова, порядок forward/reverse/
forward. Время включает свой feature builder, inference, force construction
и rollout. Shared cold load, download, evaluator и fine audit исключены.
Прямые прогнозы сверяются с annual prefixes. CPU-тяжёлые задачи не запускаются
параллельно timing. Сравниваются суммы medians и разбивка по горизонту.

Положительный результат: меньше silent tolerance violations, чем у v3,
при меньшем времени, чем full; добавочная польза ML оценивается относительно
physics_v4 по обеим метрикам. Уменьшение числа silent failures за счёт
предупреждений показывается вместе с общим success и долей предупреждений.
Нулевые промахи на 24 телах не означают универсальной надёжности. Результат
без пользы ML или без экономии сохраняется. После одного нового holdout
метод не перенастраивается ради исправления отдельных тел; далее оформление
результатов и обсуждение границ проекта.

## Проверки и среда

Python 3.14.7 через существующий uv, standard library only. Нет installs,
новой среды, commit, remote или публикации. Контракт, source closure,
модель, provenance и sampler фиксируются до нового test. Immutable atomic
checkpoints и resume сохраняют завершённые outputs. Проверки: leakage,
omitted-force mapping, training-only support/cost/floor, NG gate, OOD,
детерминированный refit, online API, sample exclusion/freeze, independent
пересчёт всех error rows/step differences/geometry/choices/timing, SHA/size/
Git exclusions raw. Старый method freeze обязан остаться неизменным.

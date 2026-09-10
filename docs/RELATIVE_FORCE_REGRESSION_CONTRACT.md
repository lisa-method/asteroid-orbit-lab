# Общая подробная модель: regression contract

2026-09-08, до открытия новых force-comparison результатов. Проверить
общую сборку относительной динамики и опциональных Earth J3/J4 на всех
30 уже изученных development30 объектах. Это engineering regression,
не новая object holdout validation; исходные selector scores сохраняются.

## Входы и варианты

Порядок, ID и start_date берутся без изменений из frozen
`data/processed/development30/sample.json`. Объекты всех шести страт,
включая прежние WN5/TU7 failures и 983, должны оставаться в итогах.
Стартовое состояние — первая точная midnight TDB строка указанной даты.
Horizon 365 дней; outputs uniform daily 0..365, без future event dates.
Референс только daily из той же таблицы; не смешивать с другим запросом.

Общие inputs — девять прежних hourly planetary ephemerides и SB16.
Их времена переводятся в относительные дни по точным TDB calendar labels,
сохраняя исходные states/интерполяцию. Можно обрезать массивы к годовому
окну с двумя окружающими knots, без изменения используемых polynomial segments.
Force: Sun+planet/SB monopoles+solar Schwarzschild+EarthJ2+NG if available.
NG availability определяется один раз на forecast origin через прежний
NGInput; unknown/not-yet-available не превращаются в известный нуль.

Две ветви: baseline J2 и та же модель с J3+J4 по зафиксированным IERS
constants/rates предыдущего shape audit. Никакого fit или identity routing.
Каждая ветвь — DP tighter/extreme с прежними tolerances и max_step=0.25d:
30×2×2=120 прогнозов. Все states и native endpoints сохраняются.

Дополнительное межметодное сравнение: по первому объекту каждой страты
в порядке frozen sample (35396,170086,675603,875508,1480,120), обе силы,
compensated RK4 с прежним encounter step selector, scale=0.25:
6×2=12 прогнозов. Всего 132. Зафиксировать расхождения, даже если более
сильная физика не улучшает teacher agreement.

## Оценка

Daily max/final position error и velocity error, DP setting differences,
парное изменение от J3+J4 и изменение его вектора между DP settings.
Для 6 control objects — RK4/DP extreme differences. Отдельно сохранён
benchmark Апофиса с более плотными 797 outputs; его нет среди этих30.

Этот 366-point audit не заменяет прежний daily+refined evaluation и не
сравнивается молча с его максимумами. Сохранить coverage при 0.1/1/10km
на этой сетке и empirical numerical gate difference<=10% tolerance,
но не объявлять это новым selector score. Step/solver differences не
являются rigorous error bounds. Добавленные forces не обязаны улучшать
совпадение с nominal Horizons, чья точная текущая physical model неизвестна.

Runtime каждой propagation записывается для диагностики. Если одновременно
выполняется другой аудит, не заявлять speedup или сравнение полной online
стоимости по этим timings. Cost-based переоценка selector требует отдельного
изолированного benchmark после freeze метода.

Freeze config/contract/transitive sources/все consumed raw и manifests,
sample и runtime до первого run. Immutable checkpoint на object/arm/solver.
Прежние данные, numerical sources, v2 rules/matrices не редактировать.
Проверка вне propagator: все summaries/pairs из traces, initial/time contracts,
hashes, raw Git exclusions, exact generic force against Apophis audit at
saved forecast states. Без новых downloads, dependencies, commit/publish.

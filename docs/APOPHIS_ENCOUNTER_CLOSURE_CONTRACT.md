# Апофис: независимая интеграция и вход/выход встречи — 2026-09-09

Пользователь поручил завершить диагностику Апофиса, затем построить селектор
и проверить его на новых телах. Визуальное оформление обсуждается позже.
Этот этап не меняет старые frozen результаты и не подгоняет силы/initial state.

## Численный эксперимент до просмотра новых результатов

Шесть новых propagations: три режима × два настройки независимого
modified-midpoint polynomial extrapolation порядка 10 с embedded order-8
оценкой. Последовательность 2/4/6/8/10, compensated increments, локальные дни.
Метод отличается от RK4/DP5, но использует тот же force backend: это
независимость интегрирования, не независимая реализация всех физических сил.
Общее описание метода: [Boost Odeint](https://www.boost.org/doc/libs/1_77_0/libs/numeric/odeint/doc/html/boost/numeric/odeint/bulirsch_stoer.html).
Наш код фиксирует порядок, в отличие от adaptive-order Boost реализации.

Настройки: coarse rtol=1e-15, atol position/velocity=1e-18/1e-19, max step=.5d;
fine rtol=2e-16, atol=2e-19/2e-20, max step=.25d. Hard boundaries на целых днях
предохраняют порядок от пересечения knots SB16 Hermite interpolation.
Не добавляются новые исходные эфемериды или библиотеки.

1. Annual: прежний initial state annual_hourly на 2029-01-01 и прежние 797
   output times, all-direct DE441, Sun-only outer PPN, Earth J2/fixed pole,
   SB16, nominal NG. Сравнение с сохранённым annual_daily и DP/RK4.
2. Long: тот же covariance mean от 2021-01-01 и exact SBDB A1/A2, прежние895
   times до2030, extended DE441. Сравнение с двумя сохранёнными DP settings.
3. Local: старт из matched annual_hourly на 2029-04-10 (day99), затем до
   конца года. Это evaluator-only перезапуск из будущего reference для
   локализации ошибки, НЕ причинный годовой прогноз и НЕ новый selector score.

Метрики: sampled position/velocity differences, вход/выход на days99/102/103/106,
Earth-frame relative state, минимум расстояния/время по Hermite между
accepted endpoints, конечный вектор расхождения. Сравнение local/annual
не идентифицирует единственную физическую причину автоматически.

Сохраняется критерий <=1m numerical difference; 1km annual teacher agreement
остаётся основным исследовательским допуском, 0.1/10km вспомогательными.
Отдельно показывать межнастроечную сходимость и межметодное согласие.
Covariance derivative/ensemble convergence не выводится из nominal checks.
Если численная диагностика не закрывается, явно сохранять нерешённый статус.
Нельзя объявлять остановку аудита доказательством полной точности Апофиса.

## Следующая часть разрешённой работы

После результатов этого этапа фиксируется новый общий selector/warning
contract, без object-ID physics. Используются уже доступные stdlib/Python3.14.7,
простые rules и небольшой обучаемый selector с whole-object development.
Новая выборка выбирается после freeze метода; никаких future asteroid
states в features. Проверяются итоговые rollouts, numerical budgets,
стоимость и warning outcomes. Все посмотренные тела теперь inspected.
Размер новой выборки и критерии фиксируются в отдельном контракте до загрузки
их траекторий. Новые installs, publication и внешний ML stack сюда не входят.

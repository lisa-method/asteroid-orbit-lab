# Планетная динамика в предварительном прогнозе

Протокол зафиксирован 2026-09-07 до запуска новой ablation, после просмотра
предыдущего [B2 screening](ENCOUNTER_SCREENING_PILOT6_REPORT.md). Пользователь
разрешил проверить влияние планет и Луны на расстояние, время и пропуски
сближений. Это продолжение engineering regression, а не preregistered test
на новых объектах.

## Сравнение

Одинаковые 30 main задач и четыре Apophis lead-time окна; те же initial states,
daily planetary ephemerides и отдельные reference minima. Пороги alert
0.001/0.01/0.05 AU сохраняются без подгонки. Девять perturbers включают Earth
и Moon отдельно; Earth–Moon barycenter не добавляется. Астероид massless.

Четыре варианта задаются в `configs/encounter_planets_pilot6.json`:

- B2 + daily Hermite: повтор исходного baseline в текущем запуске.
- B3 + daily Hermite: влияние планет при прежней сетке выходных состояний.
- B3 + six-hour Hermite: чувствительность к сгущению выходной сетки.
- B3 + encounter steps: daily outputs плюс оба конца каждого принятого RK4
  шага, если хотя бы один прогнозный конец ближе 0.01 AU к любому perturber.

Последний вариант сохраняет рассчитанный поворот траектории возле планеты,
не используя даты известного события. Условие определяется исключительно
собственным прогнозом и экзогенными планетными эфемеридами. Оно не гарантирует
обнаружение любого ещё неизвестного пролёта между шагами. B3 использует
существующий encounter-aware шаг RK4, B2 — только solar-distance guard.
Сетка интегратора и сетка сохранения состояний различаются.

Используется только существующая Newtonian point-mass B3 с direct/indirect
terms. GR, SB16, Earth J2 и fitted NG здесь не добавляются. Threshold 0.01 AU
для сохранения accepted steps фиксируется до запуска; это существующий
масштаб step guard, не настройка по новому ответу.

## Метрики и численная проверка

Для каждого case/body: minimum distance, time, relative speed, signed errors,
TP/FN/FP/TN при прежних thresholds. Глобальный минимум окна включает endpoints;
он не равен каталогу всех отдельных events. Четыре lead windows относятся к
одному физическому событию и показываются отдельно от main.

Три последовательных timing повтора с вращением порядка вариантов включают
полный forecast, проверки сгущения и поиск геометрических минимумов. Каждый
горизонт считается независимо. Resident-data loading и reference evaluation
показываются отдельно. Это стоимость preliminary forecast; achieved savings
рабочего селектора данным экспериментом не измеряются.

Для шести окон Apophis, содержащих Earth encounter (четыре lead-time и main
180/365 дней), B3 encounter-steps дополнительно сравнивается при step scales
0.5 и 0.25. Разница minima по расстоянию и времени — эмпирическая численная
чувствительность. Она не оценивает ошибку экзогенной daily эпhemeris и не
доказывает согласие с точной физической траекторией.

Reference использует тот же сохранённый model-derived Horizons snapshot с
пяти-минутным event refinement только в evaluator. Его Hermite minima также
не являются строго известными непрерывными величинами. Forecast API не
принимает future asteroid states, reference errors или даты событий.
Прогнозные признаки экспортируются отдельно от evaluation metadata.

## Артефакты

Новый ignored каталог `outputs/encounter_screening/planets_pilot6/` содержит
`results.json`, `forecast_features.json`, `reference_minima.json`,
`numerical_audit.json` и отчёт. Исходный screening snapshot сохраняется.
Исходники/config/checksum manifests хешируются до запуска; raw неизменяемы.
Новые зависимости и данные не нужны.

# Дополнительная проверка frozen v4: 100 новых тел

Протокол 2026-09-10. Пользователь разрешил крупную качественную независимую
проверку, физическую диагностику обнаруженных проблем и затем анимации.
Метод v4, его обучение и прежние результаты не меняются. Этот этап проверяет
повторяемость результата и сложные режимы, а не создаёт более удачный score
перенастройкой по новым траекториям.

## Выборка до открытия target vectors

Новые публичные JPL CAD/SBDB snapshots включают numbered и unnumbered
asteroids; кометы, физические спутники планет и наблюдательная orbit
determination остаются за рамками. Запрашиваются полные подходящие каталоги
без усечения первыми N строками. Все прежние inspected объекты и SB16
исключены, идентичность дополнительно проверяется canonical designation и
SPK ID. Неизменяемые responses и metadata selection audit сохраняются.

| Страта | Тел | Прямой timing subset |
| --- | ---: | ---: |
| Earth tight, nominal d ≤0.01 AU | 4 | 3 |
| Earth moderate, 0.01<d≤0.05 AU | 16 | 3 |
| Venus, d≤0.2 AU | 12 | 3 |
| Mars, d≤0.2 AU | 12 | 3 |
| Jupiter, d≤1 AU | 12 | 3 |
| Inner belt, a2.1–2.5 AU, q>1.8, e<0.2 | 16 | 3 |
| Outer belt, a3.0–3.4 AU, q>2.5, e<0.2 | 16 | 3 |
| Published A2 parameters | 6 | 2 |
| Low perihelion, 0.08<q<0.5 AU, e<0.99 | 6 | 1 |
| Total | 100 | 24 |

Общий quality gate: condition code≤3, data arc≥365 суток, не two-body
orbit fit. Это фильтр качества каталожной орбиты, не гарантия точности
реального будущего положения. Неудачи доставки или неверные входы не
заменяются телами с лучшими прогнозами. Проблема документируется отдельно.

Уточнение после metadata-only feasibility, до sample freeze и любых новых
target vectors: исходная квота10 tight +10 moderate неосуществима при
этом quality gate и исключении изученных тел. Во всём заданном временном
диапазоне осталось только4 eligible tight объекта. Сохраняем quality gate
и включаем все4; moderate увеличено до16. Это изменение по доступности
каталожных входов, без просмотра ошибок/целевых траекторий. Полученные
сближения не обещают силу рассеяния Апофиса; это проверяется и сообщается
отдельно, без отождествления distance threshold с strong guard.

CAD dates2027-01-31–2029-12-31; starts=event day−30 суток. Для остальных
start2029-01-01. Выбирается один ближайший каталожный event на тело.
В каждой encounter-страте кандидаты делятся на четыре равных по численности
distance-квартиля; внутри квартилей deterministic hash, затем round robin.
Earth tight сначала включает двух ближайших доступных кандидатов по
metadata, затем тот же квантильный порядок. После quality/identity rejection
рассматривается следующий кандидат, причина сохраняется. Controls/NG/low-q
выбираются hash из полного каталога. Фиксированный порядок страт совпадает
с таблицей. Seed `selector-confirmation100-2026-09-10-v1`.
Timing24 — отдельный hash от ID внутри тех же страт, до vector downloads.

Выборка целенаправленно насыщена encounters, NG и low-q; общий процент
не оценивает распространённость ошибок во всей популяции астероидов.
Отчёт обязан показывать каждую страту и body-level all-horizons success.
500 вложенных окон не являются 500 независимыми телами.

## Неизменённая проверка

H7/30/90/180/365d, primary1km, дополнительные0.1/10km. Те же четыре v2
кандидата и numerical settings: B2/P/P+GR/P+GR+SB16. Методы:
tree_v3 / physics_v4 / hybrid_v4 / fixed_full. Inputs: initial state,
forecast-time available NG, exogenous ephemerides. IDs и event dates
используются sampler/evaluator, не feature builder. Horizons — nominal
model-derived reference при фиксированном initial state.

100 daily target tables366nodes и 56 encounter refinements1153nodes,
±2d через5min. Общие узлы обязаны совпадать. Heliocentric Sun, ICRF,
geometric vectors, TDB, AU/AU per day; старый exogenous context сохраняется.
NG берётся только из Horizons header с проверкой доступности до start.
Если CAD/SBDB и Horizons NG различаются, этот факт сохраняется; никаких
подогнанных по тестовой траектории коэффициентов.

Матрица100×4×5=2000records,800 annual production/fine traces. Eligibility:
max sampled position error≤tol AND production/fine≤0.1tol. Сохраняются
velocity/RTN, known-event geometry, all choices, warning reasons,
actual no-candidate и silent failures. Ничего не исключается из denominator
из-за warning. Достаточность на сетке не является continuous-time гарантией.

Timing subset24×5×4×3=1440 прямых online calls, forward/reverse/forward.
Features/inference/forces/rollout включены; shared cold load, загрузки,
evaluator/fine исключены. Не запускать тяжёлые CPU задачи параллельно.
Сравнивать успех и время на одних и тех же timing24. Не экстраполировать
сумму времени timing24 как фактический замер всех100.

## Аудит, готовность и представление

Sample, runner, tests, contract, parent method и metadata hashes фиксируются
до новых target vectors. Только новый namespace confirmation100. Runtime
Python3.14.7/uv/stdlib; существующие frozen источники и scores неизменны.
Аудит пересчитывает trace errors, шаговую чувствительность, choices,
geometry и timing medians; проверяет все raw SHA/size/Git exclusions.

После проверки физический ответ формулируется по режимам: какие силы
достаточны, где селектор ошибается, где не хватает самого полного кандидата,
какие предупреждения сработали и сколько стоит выбор. Для значимых ошибок
нужна ограниченная физическая/численная диагностика с явно post-hoc меткой;
её нельзя смешивать с frozen score. Если выявлена ошибка реализации,
исправление и повторная оценка получают новую явную версию.

Проект считается готовым к представлению как условный propagation/model
selection benchmark, когда аудит завершён и существенные ограничения
локализованы. Это не объявление универсальной точности всех тел, решения
реальной observational orbit determination или годовой1km задачи Апофиса.
Отрицательный результат для ML допустим. Открытые физические режимы
показываются в выводах и анимациях, а не скрываются ради красивого финала.

Анимации создаются после результатов: overview и отдельный local zoom,
синхронные moving markers и растущие trails прогноза/эталона. Подписать
Horizons reference, систему координат, время, масштаб и любое усиление
разности. Объекты для иллюстрации выбираются после теста, поэтому GIF
являются иллюстрациями, не ещё одним validation score.

Источники metadata contract: [CAD](https://ssd-api.jpl.nasa.gov/doc/cad.html),
[SBDB query](https://ssd-api.jpl.nasa.gov/doc/sbdb_query.html),
[SBDB](https://ssd-api.jpl.nasa.gov/doc/sbdb.html).

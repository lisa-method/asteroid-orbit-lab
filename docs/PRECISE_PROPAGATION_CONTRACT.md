# Согласованное продвижение времени и состояния: контракт

2026-09-08. Пользователь разрешил следующий этап общей физической модели.
Это post-hoc numerical/force regression на уже изученных данных. Исходные
v2, reference/time и shape/weak-force модули и результаты заморожены.

## Гипотезы до новых propagation

В saved relative-calendar RK4 traces при масштабах 0.5/0.25/0.125 сумма
расхождений `(right_time-left_time)-nominal_step` составляет
-3.303e-11/+1.241e-11/-1.516e-10 days. Сдвиг относительно DP extreme на
100-е сутки 0.035/0.071/0.143 m, в конце года 196/242/1036 m. Эти
read-only наблюдения мотивируют проверку; они ещё не доказывают причину.

H1: применение к state приращения nominal h, отличного от представимого
приращения времени, создаёт рассогласование, усиливаемое близким пролётом.
H2: накопление малых приращений в больших heliocentric states добавляет
roundoff. Проверить endpoint-consistent h отдельно от compensated state sum.
H3: после устранения этих источников RK4 и независимый DP согласуются
существенно лучше при неизменной физике.

## Матрица Апофиса

Использовать прежние relative calendar knots, те же 797 output stops,
initial/reference пары old/old и annual_hourly/annual_daily. Force inputs:
Sun+9 planets+SB16+solar GR+nominalNG+EarthJ2, с подробными Earth/Moon/Venus
как в предыдущем shape audit. Ничего не подгонять к future target states.
Eight named reference evaluations сохраняются отдельно.

1. Old initial, baseline force: legacy RK4 scales 0.5/0.25/0.125 (3).
   Все output states должны точно воспроизвести previous reference-time.
2. Old initial, baseline force: endpoint-consistent RK4 0.5/0.25/0.125 (3).
3. Old initial, baseline force: endpoint-consistent + compensated state sum
   0.5/0.25/0.125/0.0625 (4).
4. Old initial, J3+J4: compensated RK4 0.25/0.125 (2).
5. New initial, baseline и J3+J4: compensated RK4 0.25/0.125 (4).
6. Baseline и J3+J4, old/new initial: DP tighter/extreme (8), точный repeat
   соответствующих eight previous shape-audit forecasts.

Всего 24 runs. Сохранить native endpoints, requested states и статистику;
checkpoint каждого run immutable. Итоговый index может ссылаться на
checkpoint вместо дублирования больших traces. Freeze source/config/contract,
consumed inputs и previous matrices, runtime до первого нового прогноза.

Для endpoint mode сначала вычислять right_time, затем h=right_time-left_time;
state и RK stages используют именно h. Compensated mode отличается только
компенсированным межшаговым сложением state increments. Step selector прежний,
использует лишь predicted state и exogenous ephemerides.

## Проверка и интерпретация

Пересчитать position/velocity errors и paired shifts на одной сетке.
Отдельно сравнить изменение вектора эффекта J3+J4 между solvers/settings.
Проверить exact initial/native endpoints, strict finite increasing times,
checkpoint/source hashes, старые raw SHA/size/Git exclusions.

Для практической метровой numerical agreement ориентир: максимум различий
fine RK4/DP extreme <= 1 m на 797 узлах. Это эмпирический критерий
межметодного согласия, не rigorous error bound и не независимая force model.
Если согласия нет, локализовать оставшееся расхождение и сохранить отрицательный
результат. Не объявлять 8–10 km остатка полностью физическим без оговорки.

Аналитические tests: постоянная скорость с малыми шагами при большом state,
равномерное и time-dependent ускорение, порядок RK4 на harmonic oscillator,
finite validation и exact output stops. Без новых dependencies/installations.

Расширение generic J3/J4 на другие inspected окна и standalone forecast API
имеет отдельный заранее заданный regression contract. Перенастройка selector
и новая final-test validation в этот numerical этап не входят.

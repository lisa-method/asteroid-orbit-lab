# Апофис: перенос совместной неопределённости орбиты и A1/A2

Завершено 9 сентября 2026 года. Контракт:
[APOPHIS_COVARIANCE_CONTRACT.md](APOPHIS_COVARIANCE_CONTRACT.md).
Это отдельный эксперимент на уже исследованном событии, с новым началом
интегрирования. Прежние результаты selector и годового Апофиса не изменены.

## Что получилось

Выполнены **34 заранее заданных расчёта** от 2021-01-01 до 2030-01-01 TDB.
Полная ковариация JPL связывает шесть элементов орбиты с A1 и A2; все связи
сохранены. Формальная большая полуось 1σ растёт с **1.567 km** на начало
2029 года до **4728.864 km** на начало 2030-го. Это разброс положения вдоль
самой неопределённой оси, не гарантированная ошибка и не радиус 68%-сферы.

В рассчитанном ансамбле линейная оценка и cubature очень близки: все 15
дат проходят заранее заданные 1% критерии ковариации и сдвига среднего.
Однако два номинальных DP расчёта различаются до **3640.522 m**; численный
критерий ≤1 m не выполнен. Поэтому экспорт имеет статус
`numerically_unresolved`, `diagnostic_only: true`, а не сертификат точности.

Практический вывод: для сильных сближений нужно проверять достаточность сил,
точность входной орбиты и численную устойчивость. На этом одном событии
линейная ковариация выглядит пригодной для оценки масштаба разброса, но
её физическая калибровка и пригодность как operational warning не доказаны.

## Откуда взята ковариация

Сохранён анонимный [ответ SBDB API](https://ssd-api.jpl.nasa.gov/sbdb.api?sstr=99942&cov=vec&full-prec=1&cd-epoch=1):
JPL solution **220**, DE441/SB441-N16, covariance epoch **2459215.5 TDB**
(2021-01-01), дата решения 2024-06-25, последнее наблюдение 2022-04-09.
Основная эпоха `orbit.epoch` — 2026-06-09; для старта используются именно
`covariance.elements`. Это перенос более поздней апостериорной оценки из
эпохи 2021 года, **не прогноз, выданный в 2021-м**. Данные решения доступны
до условного forecast origin 2029-01-01.

Порядок параметров: `e,q,tp,node,peri,i,A1,A2`. `cov=vec` раскрыт как верхний
треугольник по столбцам. Длинная строка `tp` вычитается из эпохи через
Decimal до преобразования разности в float. Использованы heliocentric
ICRF, geometric states, TDB, AU и AU/day; поворот ecliptic J2000 использует
IAU76/80 obliquity 84381.448 arcsec. Соглашения описаны в
[документации SBDB](https://ssd-api.jpl.nasa.gov/doc/sbdb.html).

Средние A1=5e-13 и A2=-2.901766637153165e-14 AU/day² взяты из того же
ответа, а не заменены округлёнными значениями старого Horizons header.
Их marginal σ — 4.892289925865215e-13 и 1.859286037614993e-16 AU/day².
Они не используются как независимые ошибки: в расчёте участвует вся 8×8
матрица. Подписанные формальные точки A1 не обрезаются по нулю; это
гауссовское приближение fit covariance, не физический prior на давление света.

Масштабированный Cholesky проходит без jitter и clipping; нормированный
остаток 2.214e-16, восстановление ковариации 16 точками — 3.431e-16.
Проверка начальных элементов по отдельному старому вектору Horizons #220:
0.846258 m и 1.690119e-7 m/s, в пределах зафиксированных 10 m / 1e-6 m/s.
Ненулевое расхождение не приравнивается к нулю и не устраняется подгонкой.

## Расчёт

Два nominal: DP extreme и ultra. Для каждой из восьми колонок L выполнены
парные отклонения `±sqrt(8)*scale*L_j`, при scale=0.25 и 1, все на DP ultra.
Это 32 deterministic sigma-point прогона, **не Monte Carlo выборка**.
Силы прежние: Sun-only outer PPN, fixed-J2000 Earth J2, major bodies,
SB16 и r^-2 NG. Форма, spin, solar J2 и Earth J3/J4 здесь не добавлялись.

Для 2021–2030 сохранены отдельные 30 диапазонов DE441, 1,048,656 bytes,
14 Type-2 segments; большой kernel и SPICE не устанавливались. В 87,053
сравнениях со старыми source tables максимум — 3.191637 mm (Нептун).
SB16 использует прежнюю daily interpolation с явным сдвигом начала времени.
Эти проверки относятся к внешним эфемеридам, не к точности прогноза Апофиса.

Каждый прогон содержит 895 выходных дат и все принятые шаги. До 2029 года
вывод раз в 30 дней, затем прежние 797 diagnostic knots. Максимальный шаг
0.25 day. Интегрирование идёт в инерциальных координатах с равномерно
движущимся солнечным началом; отдельные traces сохраняют native и
heliocentric states. Номинал не перенесён искусственно на старый старт 2029.

Из малой пары вычисляется `B_j=(X_plus-X_minus)/(2*sqrt(8)*0.25)`, затем
`C_linear=B B^T`. Полный cubature covariance считается относительно своего
среднего с весами 1/16. Разности состояний берутся до перевода AU в метры.
Position и velocity сравниваются отдельно; смешанных единиц в eigenanalysis нет.

## Разброс положения

Даты — 00:00 TDB; строка 13 апреля предшествует самому тесному пролёту.
Таблица содержит линейные полуоси 1σ, а не диаметры области.

| Дата | Большая полуось, km | Средняя полуось, km | Малая полуось, km |
|---|---:|---:|---:|
| 2029-01-01 | 1.567187 | 0.267855 | 0.025728 |
| 2029-04-13 | 1.198683 | 0.469995 | 0.204892 |
| 2029-04-14 | 1.667413 | 0.233566 | 0.175078 |
| 2029-04-15 | 7.349061 | 2.210067 | 0.212763 |
| 2029-04-21 | 40.246462 | 15.975443 | 0.203565 |
| 2029-06-30 | 541.041767 | 134.965436 | 0.173542 |
| 2030-01-01 | 4728.864343 | 171.276110 | 0.251961 |

Область неопределённости сильно вытянута; одно число не описывает все
направления. Это геометрия неопределённости положения, не форма астероида.

Максимальное относительное Frobenius расхождение full cubature и linear:
**0.0001301%** для position и **0.0001545%** для velocity. Максимальные
различия B между двумя масштабами — 0.0004338% / 0.0004129%.
В конце интервала сдвиг cubature mean относительно nominal — 91.877 m;
максимально он составляет 0.002082% большой σ. Все eigenvalues положения
неотрицательны. Малые различия могут содержать численную ошибку; это
не доказательство отсутствия нелинейных хвостов распределения.

## Численная граница и номинальная ошибка — разные величины

| Дата | DP extreme–ultra, m |
|---|---:|
| 2029-01-01 | 0.692819 |
| 2029-04-13 | 0.871434 |
| 2029-04-14 | 1.360445 |
| 2029-06-30 | 416.029365 |
| 2030-01-01 | 3640.521730 |

Начальные состояния двух DP nominal одинаковы. Даже субметровая разница
до встречи усиливается до километров после неё. Разница settings —
эмпирическая чувствительность, не строгая верхняя граница ошибки. Для всей
девятилетней серии независимого интегратора и convergence study ансамбля
пока нет. Формальная σ в тысячи km намного больше этой nominal разницы,
но это само по себе не калибрует ковариацию и не подтверждает её младшие оси.

Отдельная post-hoc проверка нового nominal по **366 одинаковым датам
annual_daily** даёт 129.626 m на 2029-01-01 и 628.937840 km на 2030-01-01.
От старого all-direct nominal он отличается на 129.626 m и 626.528150 km.
Начало интегрирования теперь 2021 год; эти числа нельзя выдавать за новую
оценку старого годового benchmark. Причины длинного nominal mismatch
(начальное согласование, physics/reference conventions, propagation) не
разделены этим экспериментом. Старый годовой matched error **2.551300 km**
остаётся прежним и не объяснён ковариацией.

В frozen matrix поле `new_nominal_teacher_diagnostic` наследует **другой,
legacy mixed-source reference** на 797 датах: 126.018 m / 611.726036 km.
Оно сохранено для воспроизводимости; не смешивать его с matched annual_daily.
Отдельный `matched_reference_diagnostic.json` подписывает источник и SHA.

## Что теперь есть в программе

- `sbdb_covariance.py`: строгий разбор joint covariance и её собственной эпохи.
- `covariance_tools.py`: Cholesky, sigma points, covariance transforms, eigenanalysis.
- `run_apophis_covariance.py`: frozen 34-run experiment и immutable resume.
- `analyze_apophis_covariance.py`: linear/cubature и numerical diagnostics.
- `verify_apophis_covariance.py`: независимые offline проверки данных и traces.
- `export_apophis_covariance.py`: проверенный nominal + 8×8 Cartesian covariance
  с явными `x,y,z,vx,vy,vz,A1,A2`, единицами и статусом.

Канонический экспорт: `outputs/apophis_covariance/forecast_origin_covariance.json`.
В frozen analysis `parameter_labels` относится к входным SBDB elements;
в отдельном экспорте это явно `input_orbital_parameter_labels`, а матрицы
имеют собственные `coordinate_labels`. Статус `numerically_unresolved`
не позволяет маскировать провал numerical gate успешной cubature проверкой.
Флаги σ выше 0.1/1/10 km — описательные; operational no-candidate rule и
его проверка на новых объектах по-прежнему отсутствуют.

Далее нужен отдельный контракт: независимое согласование длинной propagation
и начального состояния, convergence самих производных/ковариации, затем
новые встречи для проверки warning. Только после этого добавлять uncertainty
в гарантии forecast API. Этот этап не решает spin/thermal dynamics, не
устанавливает физическое происхождение A1/A2 и не оценивает риск столкновения.

## Проверка и воспроизведение

**243 tests passed.** Offline проверены 34 checkpoints, 30,430 requested
states, 545,012 accepted endpoints, их native→heliocentric conversion,
подписанные initial/NG offsets, source gates и все 15 covariance summaries.
BB^T и cubature пересчитаны независимо; полный analysis digest воспроизводится.
202 уникальных raw paths, 19 manifests и 221 manifest records проходят
SHA-256/size/Git exclusion checks. Вычислительный код и исходная матрица
эксперимента после freeze не менялись.
Повторный запуск runner, verifier и обоих exports сохранил SHA-256 и mtime
всех 41 завершённого JSON; новых propagations не было. Четыре parent
matrix/freeze anchors остались прежними. Это записано в
`outputs/apophis_covariance/resume_verification.json`.

Первый запуск нового verifier остановился до записи artifact: его фиксированная
погрешность для шага 1e-14 day была меньше ULP временной координаты. После
review проверяется каждый интервал по `.25 + ulp(left) + ulp(right)` и
min/max пересчитываются из traces. Максимальный representation overshoot —
2.84217e-14 day (2.456 ns). Этот ремонт verifier не менял ни шагов, ни outcomes;
километровое DP расхождение остаётся провалом. Verifier дополнен адресными тестами.

Python 3.14.7, standard library only. Сумма elapsed propagation time —
1266.02 s; группы выполнялись параллельно, это не online cost benchmark.
Новых зависимостей, окружений, коммитов или публикации нет.

```sh
env PYTHONPATH=src UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv run --no-project --python-preference only-system --python 3.14.7 python -B src/run_apophis_covariance.py --prepare-only
env PYTHONPATH=src UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv run --no-project --python-preference only-system --python 3.14.7 python -B src/run_apophis_covariance.py
env PYTHONPATH=src UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv run --no-project --python-preference only-system --python 3.14.7 python -B src/verify_apophis_covariance.py
env PYTHONPATH=src UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv run --no-project --python-preference only-system --python 3.14.7 python -B src/export_apophis_covariance.py
env PYTHONPATH=src UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv run --no-project --python-preference only-system --python 3.14.7 python -B src/check_apophis_covariance_reference.py
env PYTHONPATH=src UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv run --no-project --python-preference only-system --python 3.14.7 python -B -m unittest discover -s tests
```

Input preparation — отдельные `prepare_apophis_covariance.py`,
`prepare_covariance_spk.py`, `prepare_covariance_epoch.py`; по умолчанию только
проверяют сохранённые inputs. `--download` необходим лишь при отсутствии raw.
Завершённые outputs не удалять для повтора: resume проверяет hashes и
пропускает готовые вычисления.

Fingerprint: `861ebea05fcbbdfdf182f677aa2f8ac028bcc67fb530672ab5e5cf44ff7b7ed9`.
Matrix SHA-256: `3a8aa5727473104de6d62d388737afc24bfd0cd8bb838383ce0977992d70b35a`.
Freeze SHA-256: `8ba757732c440a94f2cee0ef3c80c4c105257f15fa85e2b2c0338b99d509ec6e`.

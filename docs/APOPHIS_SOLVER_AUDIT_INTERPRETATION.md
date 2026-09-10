# Что показала независимая интеграция Апофиса — 2026-09-08

Смена интегратора не устранила годовое расхождение порядка 12 km с Horizons.
При этом численная сходимость до метров пока не достигнута. Поэтому нельзя
ни объявлять Апофис закрытым, ни приписывать все оставшиеся километры
конкретной пропущенной силе.

## Сравнение при одинаковой физике

Начальное состояние 2029-01-01; прежние planetary + SB16 + solar GR +
nominal NG + Earth J2 (IAU mean pole). Новый стандартно-библиотечный
Dormand–Prince 5(4) не вызывает старый RK4 step. Оба алгоритма получают
одну и ту же функцию ускорения, поэтому это независимая численная
интеграция, а не независимая реализация физических уравнений.

| Численный вариант, old merged ephemerides | Годовая max/final ошибка, km |
|---|---:|
| Старый RK4, scale 0.5 | 13.148071 |
| RK4, scale 0.25 | 11.1509 |
| RK4, scale 0.125 | 12.9777 |
| DP, relative tolerance 1e-10 | 12.005854 |
| DP, relative tolerance 1e-12 | 11.746851 |
| DP, relative tolerance 1e-13 | 11.958729 |

Старый результат 13.14807051432607 km и step difference
1.9977699372970446 km воспроизведены. Ужесточение tolerances не даёт
монотонной сходимости. Числа DP различаются на сотни метров; разница
скалярных reference errors сама по себе не заменяет расстояние между
двумя траекториями. Paired shifts сохранены отдельно и пересчитаны verifier.

С hourly planetary ephemerides вместо old daily/36h-merged inputs RK4(.5)
даёт 12.979755 km. Максимальный сдвиг между этими двумя прогнозами —
0.277863 km. DP с hourly inputs даёт 11.7485/11.7786 km при двух проверенных
допусках. Это не независимое объяснение остатка: входные эфемериды и
их интерполяция образуют ещё одну ось численной проверки.

При локальном старте непосредственно перед сближением, в 36-часовом окне,
DP на старых merged inputs даёт примерно 1.15–1.20 m max error. Этот
результат не переносится на старт за несколько месяцев до пролёта.

## Что проверено и как воспроизвести аудит

Основной результат — `outputs/apophis_solver_audit/stage3/`.
Предыдущие stage1/stage2 сохранены как инженерные итерации; на них нельзя
ссылаться как на дополнительные независимые случаи. В stage3 сохранены
requested states и accepted endpoints. Offline verifier повторно вычисляет
summary errors и paired separations для двух окон и двух видов эфемерид,
проверяет fingerprint и 44 исходных hashes; ещё 14 shared-source hashes
дополнительно сверяются с frozen v2 rules.

Исходный audit fingerprint не включал runtime environment и все shared
modules. Команды выполнялись через uv с system Python 3.14.7, но checkpoint
сам по себе не доказывает runtime freeze. Verifier честно записывает
`runtime_environment_stored_in_audit: false` и environment своей проверки.
`passed: true` означает воспроизведение записанных артефактов, не физическую
достаточность модели и не строгую численную границу.

NG в этом будущем окне имеет status `available`: подтверждённая загрузка
2026 года предшествует старту 2029 года. Это nominal coefficient из teacher,
не новый fit и не оценка его неопределённости. Future target states не входят
в ускорение. В этом numerical audit оба solver останавливаются на общей
reference временной сетке; времена остановок влияют на шаговый путь.
Поэтому опыт не является новым operational encounter benchmark.

```bash
env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv run --no-project --python-preference only-system --python 3.14.7 python -B src/verify_apophis_solver_audit.py --root . --output-directory outputs/apophis_solver_audit/stage3
```

Completed stage3 не перезапускать обычным runner: checkpoints сохраняются,
но final result generator перепишет generation timestamp. Проверять
сохранённый результат командой выше; новый опыт — отдельный namespace.
Исходные v1/v2 propagation modules и результаты не изменялись.

## Точность представления времени: проверенная предпосылка следующего опыта

Отдельный скрипт `src/diagnose_apophis_time_precision.py` не интегрирует
траектории. На inspected reference epoch JD 2462240.40625 шаг между
соседними binary64 JD равен 40.233 microseconds. Земля за этот интервал
сдвигается в интерполированных эфемеридах на 1.195 m; при фиксированном
положении астероида изменение direct acceleration составляет
1.70486e-8 m/s². Само притяжение Земли в этой точке — 0.275829 m/s²,
расстояние — 38014.448 km. Перевод AU/day² → m/s² включает множитель 1000.

Это измерение разрешения временной координаты, **не измеренная ошибка
интерполяции и не установленная причина годовых 12 km**. Нулевой round-trip
у уже сохранённых absolute-JD timestamps не проверяет неокруглённые
внутренние stage times. Следующая осмысленная численная ablation — единая
относительная временная ось для solver и ephemeris interpolation с проверкой
raw knot times, при неизменных физических силах. Только после проверки её
сходимости стоит решать, какие дополнительные силы проверять дальше.

Артефакт диагностики: `outputs/apophis_time_quantization/diagnostic.json`;
он содержит исходные hashes и явное ограничение интерпретации.

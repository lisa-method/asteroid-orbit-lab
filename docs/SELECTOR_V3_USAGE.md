# Использование обучаемого селектора v3

Селектор получает начальное положение и скорость, дату старта, горизонт,
допуск и внешние эфемериды планет. Он выбирает **одну физическую модель на
весь прогноз**. Дерево предсказывает достаточность модели; будущие координаты
затем вычисляет численный интегратор. Это не нейросеть, выдающая следующие
координаты напрямую.

Артефакт обучения: `outputs/selector_v3/model.json`.
Протокол: [SELECTOR_V3_CONTRACT.md](SELECTOR_V3_CONTRACT.md).
Четыре физических кандидата остаются прежними из
`configs/force_models_v2.json`; [физический API v2](FORCE_MODELS_V2_USAGE.md).

## Программный вызов

В существующем Python 3.14.7 с `PYTHONPATH=src`:

```python
import json
from pathlib import Path
from orbit_baselines import State
from run_development_benchmark import load_development_context
from run_selector_holdout24 import forecast_online

root = Path(".").resolve()  # запуск из корня проекта
read = lambda p: json.loads((root / p).read_text())
force_config = read("configs/force_models_v2.json")
feature_config = read("configs/development30.json")
model = read("outputs/selector_v3/model.json")
old_rule = read("outputs/force_models_v2/rules.json")
context = load_development_context(root, force_config)

# r_au, v_au_per_day и start_jd_tdb передаёт вызывающая программа.
# Sun-centred, ICRF, geometric states; время TDB, без light-time correction.
initial = State(tuple(r_au), tuple(v_au_per_day))
prediction, metadata, online = forecast_online(
    initial, start_jd_tdb, 365., 1., "tree_guard",
    model, old_rule, context, force_config, feature_config,
    ng=None,
)
print(online["decision"])
# metadata["accepted_states"] содержит времена и состояния принятых шагов.
```

Эфемериды должны покрывать запрошенное окно. В зафиксированном эксперименте
проверяются горизонты 7, 30, 90, 180 и 365 суток. Для другого горизонта модель
не экстраполирует таблицу стоимости молча. Доступные NG-параметры можно
передать как `NGInput` с источником, hash и датой доступности; параметры,
полученные после начала прогноза, использовать нельзя. `ng=None` означает
отсутствие таких входов, а не доказанное отсутствие негравитационных сил.

## Как читать ответ

- `predicted_feasible`: эмпирическая оценка ошибки укладывается в допуск.
  Это оценка, которую проверяют на новых телах, а не верхняя математическая
  граница и не 95%-й доверительный интервал.
- `strong_encounter_unvalidated`: предварительная траектория проходит через
  область сильного планетного рассеяния. Возвращается полный кандидат и
  предупреждение; его точность для данного случая не гарантируется.
- `no_candidate_predicted`: ни один кандидат не прошёл эмпирическую оценку.
  Полный кандидат возвращает номинальную траекторию с явным предупреждением.

`accuracy_guaranteed=False`, `conditional_on_initial_state=True` и
`covariance_calibrated=False` сохраняют границы результата. Даже хороший
численный прогноз при заданном начальном состоянии не заменяет перенос
наблюдательной неопределённости.

Предупреждение вычисляется по прогнозируемой геометрии, без номера астероида.
Для уже изученного Апофиса оно срабатывает на горизонтах 180 и 365 суток при
старте 2029-01-01; это regression-проверка, не оценка на новом объекте.
Отдельный более точный backend диагностики Апофиса не подменяет общие v2
кандидаты и не считается универсальным валидированным fallback.

На [24 новых телах](SELECTOR_HOLDOUT24_REPORT.md) CART проходит 119 из 120
окон при 1 km. У 3418 на 90 сутках `predicted_feasible` сопровождается
ошибкой 1.695 km, хотя полный кандидат даёт 0.351 m; warning не срабатывает.
Это известное ограничение эмпирической оценки. No-candidate случаев в новой
выборке нет, поэтому надёжность этой ветви не валидирована. Метод после
открытия результатов не перенастраивается; отчёт содержит ошибки и полную
стоимость всех сравниваемых способов выбора.

Для воспроизведения этапов есть `src/run_selector_holdout24_compact.py`
с командами `matrix --resume`, `cost --resume`, `verify`. Он вызывает
исходные frozen функции и печатает короткую сводку; результаты и метод
совпадают с `src/run_selector_holdout24.py`. Все команды выполняются в
том же pinned Python 3.14.7 с `PYTHONPATH=src`.

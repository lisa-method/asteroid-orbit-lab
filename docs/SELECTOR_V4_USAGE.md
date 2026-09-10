# Селектор v4: запуск и интерпретация

V4 добавляет обязательные оценки всех пяти представленных групп пропущенных
сил и проверку same-horizon training support. Physics rule использует общий
эмпирический multiplier, hybrid учит CART-поправку к физическому масштабу.
Физический propagator и четыре кандидата прежние. [Контракт](SELECTOR_V4_CONTRACT.md).

[Цикл проверки завершён 10 сентября](SELECTOR_V4_HOLDOUT24_REPORT.md):
все четыре метода проходят 120/120 окон на 24 новых телах при 0.1/1/10 km.
При 1 km physics: 327.140 s, hybrid: 349.444 s, full: 487.860 s;
для этой выборки физическое правило дешевле hybrid при том же числе
успешных прогнозов. Оба v4 дают 30 OOD warnings; это не доказанная ошибка
траектории. Для повторения основного результата используйте `physics_v4`;
`hybrid_v4` сохраняется как проверенный ML comparator.

## Программный вызов

`src/run_selector_v4_holdout24.py::forecast_online` принимает только initial
state, start, horizon, tolerance, artifacts, exogenous context/config и NG.
Возвращает daily predictions, metadata с accepted endpoints и online metadata
с решением и полным временем. `method`: `tree_v3`, `physics_v4`, `hybrid_v4`,
`fixed_full`. Horizon — ровно один из 7/30/90/180/365 суток.

```python
from run_selector_v4_holdout24 import forecast_online

predictions, integration, online = forecast_online(
    initial, start_jd_tdb, 90.0, 1.0, "physics_v4",
    artifact_v3, artifact_v4, context, force_config, feature_config,
    ng=ng_input,
)
decision = online["decision"]
```

`initial` — `orbit_baselines.State`, AU и AU/day, heliocentric ICRF, geometric,
time TDB. `context` загружается существующим `load_development_context`;
`force_config` — configs/force_models_v2.json, `feature_config` —
configs/development30.json. Эфемериды обязаны покрывать весь forecast interval.
Artifacts: outputs/selector_v3/model.json и outputs/selector_v4/model.json.
Перед использованием сохранённых артефактов вызывайте `check_hashes` для
соответствующего method_freeze.json; CLI делает это автоматически.

Для отдельного решения без основного rollout используются
`features_v4(initial, start, horizon, context, feature_config, force_config, ng)`
и `choose_v4(features, horizon, tolerance, artifact_v4, method)`.
Номер тела, известная дата встречи и будущие reference states этим API не нужны.

## Статусы

- `predicted_feasible`: выбран кандидат с empirical cap в пределах допуска.
- `strong_encounter_unvalidated`: обнаружен заранее определённый режим
  сильного рассеяния; full fallback с предупреждением.
- `outside_training_support`: хотя бы один признак вне train min/max на
  данном горизонте; full fallback с предупреждением и именами признаков.
- `no_candidate_predicted`: ни один cap не проходит допуск; запускается full,
  но требуемая точность не подтверждается.

Приоритет primary status указан в этом порядке: strong, outside support,
no candidate, feasible. Независимые boolean flags и `warning_reasons`
сохраняют одновременные причины. `rejected_forces` перечисляет исключённые
из каждого кандидата группы, а не установленные причины фактической ошибки.
`predicted_error_caps_km` — эмпирические оценки, не строгие верхние границы.

Для интерпретации сохраняйте не только `model_id`, но и `status`,
`warning_reasons`, `outside_training_support_reasons` и caps. Например,
`outside_training_support` вместе с выбором full означает, что программа
выполнит наиболее полный доступный прогноз, но обучение не подтверждает
точность в этом режиме. Проверять warning по фактической будущей ошибке
и задним числом удалять его из ответа нельзя.

В приложении можно вывести выбранный набор сил, заданный допуск и короткую
причину предупреждения. При `predicted_feasible` корректная формулировка —
«модель выбрана по эмпирической оценке достаточности», а не «точность
гарантирована». Все предупреждённые случаи остаются в benchmark denominator.

`warning=False` не гарантирует точность: marginal support не описывает всю
многомерную область, B2 screening может пропустить сложную встречу, NG может
быть неизвестен. Всегда `accuracy_guaranteed=False`,
`conditional_on_initial_state=True`, `covariance_calibrated=False`.
NG `not_provided` означает отсутствие входных параметров, а не известный
физический ноль. Полный кандидат v4 не решает годовой остаток Апофиса.

## Артефакты и команды

Existing Python 3.14.7, uv, standard library only. Общий префикс команд:

```sh
env PYTHONPATH=src UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv run --no-project --python-preference only-system --python 3.14.7 python -B src/train_selector_v4.py verify
```

С тем же префиксом:

```text
src/train_selector_v4.py train|replay|verify|freeze
src/prepare_selector_v4_holdout24.py --counts|--freeze|--download
src/run_selector_v4_holdout24_compact.py matrix --resume
src/run_selector_v4_holdout24_compact.py cost --resume
src/run_selector_v4_holdout24_compact.py verify
src/report_selector_v4_holdout24.py
```

Последние три этапа требуют законченных предыдущих результатов. Загрузка
нуждается в anonymous network access; UV_OFFLINE относится только к
dependency manager. Все data/outputs локальны и исключены из Git.
Незавершённые матрица и cost продолжаются по immutable object/model
checkpoints. Завершённые источники, модель и результаты не перезаписываются.
Изменение метода после открытия v4 test требует отдельной версии.
Обёртка `run_selector_v4_holdout24_compact.py` вызывает неизменённые функции
frozen runner и сокращает только консольный вывод; исходный CLI на этапе
`matrix` выводит слишком большой список records.

Обучение: 42 ранее изученных тела, calibration: 24 других ранее изученных
тела. `training_verification.json` проверяет exact refit и отдельно
floor/alpha/support/cost; `runner_preflight.json` воспроизводит пять старых
physical prefixes и все четыре online API на изученном теле. Итоговая новая
проверка хранится в outputs/selector_v4_holdout24. Старый v3 score 119/120
при 1 km относится к предыдущему holdout и не изменяется replay v4.

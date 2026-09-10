# Прогноз с физическими компонентами v2

Общая программа использует одну выбранную force model на весь rollout.
Объект задаётся начальным состоянием. A2 не зашит для конкретного номера,
J2 рассчитывается для любого объекта из его текущего положения относительно
Земли. Состав и константы — в `configs/force_models_v2.json`.

## Команда отдельного прогноза

Запускать из корня проекта, Python 3.14.7 через uv, только standard library.
`configs/forecast_v2_example.json` содержит **синтетическое начальное состояние**,
а не эфемериду конкретного астероида. Его удобно использовать для проверки API:

```sh
env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never \
uv run --no-project --python-preference only-system --python 3.14.7 python -B \
src/forecast_v2.py --root . --input configs/forecast_v2_example.json \
--output outputs/force_models_v2/example_forecast.json
```

По умолчанию команда читает откалиброванное `outputs/force_models_v2/rules.json`.
Для явной модели указать, например, `--model V2-P-GR-SB16`; этот режим работает
и до калибровки selector. Имя выходного файла должно быть новым.

Вход: epoch JD TDB, position AU, velocity AU/day, горизонт в сутках,
запрошенный position tolerance в km, явные Sun-centred ICRF/FRAME geometric
координаты. Режим selector поддерживает откалиброванные 7/30/90/180/365 суток.
Для explicit model допускается другой положительный горизонт при достаточном
покрытии planetary ephemerides. Допуск в этом режиме не подбирает модель.

Необязательный `--ng-header PATH` читает NG и provenance из локального Horizons
JSON. Header должен относиться **к тому же объекту**, что и initial state.
При передаче header требуется строковый `object_id` в request; CLI сверяет
его с `Rec #` в header. Это проверка связи входных данных; номер не передаётся
в selector или функцию силы. Текущий CLI adapter поддерживает numbered objects.
Параметры принимаются только если подтверждена их доступность до старта.
Вычислительная функция получает NGInput, а не строки будущей траектории.
Без header NG остаётся неизвестным. Отсутствие NG нельзя интерпретировать
как измеренное отсутствие негравитационного ускорения.

Выход содержит daily trajectory, accepted-step trajectory, решение,
список реально включённых сил, статус NG и ограничения. Accepted steps
нужны для дальнейшего анализа encounter geometry; суточная сетка может
пропустить локальный минимум расстояния. Здесь не вычисляется uncertainty
envelope или новый гарантированный closest approach.
Время selector включает inference и propagation; propagation runtime также
записывается отдельно. До вызова проверяются хэши кода сил и selector из
сохранённого калибровочного артефакта.

## Python API

```python
from force_models_v2 import forecast_candidate_v2
from selection_v2 import forecast_with_selection_v2

# state, start_jd, context, config и ng_input подготовлены до прогноза.
# reference trajectory в эти функции не передаётся.
predictions, metadata = forecast_candidate_v2(
    state, start_jd, 365, config["models"][-1], context, config,
    config["step_scale"], ng=ng_input,
)

predictions, metadata, online = forecast_with_selection_v2(
    state, start_jd, 365, 1.0, calibrated_rules, context, config,
    ng=ng_input,
)
```

`ForceModelSpec` принимает явные boolean flags `planets`, `solar_gr`,
`small_bodies`, `earth_j2` и NG policy `off`, `if_available` либо `required`.
Названия моделей не определяют поведение силы. `required` отклоняет прогноз
с отсутствующими/недоступными коэффициентами. Дубли perturbers, Earth J2 без
Earth centre и неполное покрытие эфемерид отклоняются до интегрирования.

`NGInput.sigma_au_d2` может хранить три известные sigma, однако эта версия
не распространяет неопределённость. Nominal forecast и эмпирический предел
ошибки selector не являются физической гарантией заданного допуска.

## Воспроизведение инженерной проверки

С тем же uv prefix запускаются:

```text
python -B -m unittest discover -s tests -q
python -B src/run_force_models_v2.py --root . --resume
python -B src/verify_force_models_v2.py --root .
python -B src/run_selection_v2.py --root .
```

Матрица использует прежние 30 development объектов: это regression после
просмотра прежней validation. Селектор обучается на 18 train объектах,
остальные 12 дают replay, не независимый новый score. Старые результаты
development30 сохраняются. Следующая проверка переноса требует свежих объектов
и событий после фиксации нового метода.

Матрица поддерживает resume по завершённой паре объект/модель с совпадающими
source/input/runtime hashes. Если прерывание произошло после записи trace,
но до checkpoint, runner явно сообщает об orphan trace; автоматического
удаления/перезаписи нет. Завершённая матрица возвращается без изменения
timestamp. Подвыборка `--object-id` хранится отдельно от полного результата.
Selection cost сохраняется по завершённому объекту; полные записи требуют
трёх прямых повторов selector и fixed forecast при 1 km.

Подробные условия: [FORCE_MODELS_V2_CONTRACT.md](FORCE_MODELS_V2_CONTRACT.md).

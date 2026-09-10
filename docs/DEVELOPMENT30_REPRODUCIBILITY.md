# Воспроизведение development30

Конфигурация: `configs/development30.json`. До просмотра ошибок зафиксирован
[контракт](DEVELOPMENT30_CONTRACT.md). Используется существующий system Python
3.14.7 через uv, только standard library. Установка пакетов не нужна.

Из корня проекта в zsh/bash можно определить локальную функцию текущего сеанса:

```bash
dev_python () {
  env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache \
    UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never \
    uv run --no-project --python-preference only-system --python 3.14.7 python -B "$@"
}
```

## Данные

Следующие команды используют анонимные публичные API JPL только при отсутствии
локального cache. Запросы последовательные, существующий raw не перезаписывается.
`UV_OFFLINE` ограничивает uv; сетевые запросы самих download scripts возможны.

```bash
dev_python src/prepare_development_sample.py
dev_python src/prepare_development_sample.py --freeze
dev_python src/download_development_data.py
dev_python src/verify_development_data.py
```

Четыре catalogue snapshots и frozen sample обязательны для повторения именно
этой выборки. Новая выгрузка меняющейся базы JPL может дать другие объекты и
орбитальные решения. Нельзя выдавать такую выборку за первоначальный snapshot.
Также нужны прежние SB441-N16 raw-файлы из `configs/b3plus_pilot_6.json`.

## Эксперимент

Выполнять последовательно, без параллельных расчётов, и не редактировать
численные исходники между фазами:

```bash
dev_python -m unittest discover -s tests
dev_python src/run_development_benchmark.py --root . --config configs/development30.json --split train
dev_python src/run_development_selection.py --fit
dev_python src/run_development_benchmark.py --root . --config configs/development30.json --split validation
dev_python src/run_development_selection.py --evaluate
dev_python src/audit_development_fixed_cost.py --root .
dev_python src/verify_development_results.py
dev_python src/report_development30.py --root .
```

Порядок принципиален: coefficients/rules hash сохраняется после полного train
и до validation. Повторный `--fit` проверяет существующий immutable artifact
и не перенастраивает его. Сам operational API — `forecast_with_selection`
в `src/run_development_selection.py`; он не принимает labels, reference rows,
object/split/group или известную дату будущего события.

## Результаты и продолжение после паузы

В `outputs/development30/` сохраняются:

- `train/` и `validation/`: `run_started.json`, результаты, пообъектные
  checkpoints и accepted-step traces. 360/240 model records, 90/60 feature rows.
- `rules.json`: train-only coefficients, caps, cost ordering и provenance.
- `selection_results.json`: 360 решений для двух правил и трёх допусков,
  прямые timing measurements и постоянная SB16-модель для сравнения.
- `fixed_cost_reference.json`: полная propagation стоимость B3+GR и SB16
  на тех же 60 окнах. Дополнительные вызовы выполняются после основного timing;
  при 10 km B3+GR — необходимое сравнение с более дешёвой достаточной моделью.
- `encounter_catalogue.json`: 1350 constrained case/body minima и 20 отдельно
  проверенных выбранных событий. Это не полный каталог всех локальных пролётов.
- `data_verification.json`, `verification.json` и `report.md`.

Повторный запуск той же команды восстанавливает завершённые объекты/запросы.
Незавершённый объект считается заново. Source/input/runtime hashes должны
совпасть; при несовпадении исполнение останавливается. Нельзя вручную заменять
хэши для смешивания старых и новых результатов. Изменение исследовательского
метода требует новой версии эксперимента с отдельными результатами.

Resume предназначен для **незавершённой фазы**. После создания `rules.json`
не запускайте заново уже завершённый train: низкоуровневый benchmark runner
перепишет время генерации итогового JSON, и frozen rules справедливо отклонят
изменившийся hash. Готовые результаты следует использовать напрямую. То же
относится к завершённому validation после начала измерений selection.

Основное сравнение времени использует данные, уже загруженные в память;
shared load, offline fine runs, reference evaluation и запись artifacts
не входят в online стоимость. В прямом измерении входят features, выбор и
выбранный recursive rollout. Для одинакового выбранного пути на нескольких
допусках повторно используется явно помеченный measurement; 1 km имеет
приоритет как представитель. Все новые данные и generated outputs ignored
в Git; отчёт и код доступны как обычные файлы проекта.

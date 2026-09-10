# Fresh holdout12: воспроизведение и аудит

Основной протокол: [FRESH_HOLDOUT12_CONTRACT.md](FRESH_HOLDOUT12_CONTRACT.md).
Sampling amendment: [FRESH_HOLDOUT12_SAMPLING_AMENDMENT.md](FRESH_HOLDOUT12_SAMPLING_AMENDMENT.md).
Численный метод и правила v2 не изменяются этим опытом.

## Артефакты

- `data/processed/fresh_holdout12/method_freeze.json` — первая попытка,
  остановившаяся из-за отсутствия новых Jupiter objects при <0.5 AU.
- `outputs/fresh_holdout12/aborted_sampling_01/prepare_fresh_holdout.py` —
  точный source snapshot первой попытки; его SHA совпадает с original freeze.
- `data/processed/fresh_holdout12/method_freeze_v2.json` — amended freeze до
  отбора фактических 12 объектов. Силы и selector те же; изменение только
  sampling feasibility. `sample.json` связывает выборку с этим freeze.
- В sample логический ключ `cad_jupiter_2026_2029_05au` является сохранённым
  legacy alias. Для данного опыта он однозначно указывает на
  `data/raw/fresh_holdout12/catalogues/cad_jupiter_2026_2029_10au.json`:
  фактический `dist-max=1.0` и SHA зафиксированы новым manifest. Значения
  расстояний 0.62777/0.67931 AU не относятся к старому <0.5 AU каталогу.
- `data/checksums/fresh_holdout12_catalogue_manifest.json` и
  `fresh_holdout12_manifest.json` — новые immutable raw inputs и retrieval times.
- `outputs/fresh_holdout12/run_started.json` — source/input/runtime freeze
  перед первым forecast. `matrix_checkpoints/` и `traces/` сохраняют готовые
  object/model расчёты, `matrix.json` — полную матрицу и choices.
- `cost_checkpoints/` и `direct_cost.json` — отдельные прямые вызовы при 1 km.
- `verification.json` — пересчёт ошибок и дополнительные проверки identity,
  coverage, source/force metadata, timings; это производный изменяемый audit.

## Runtime и порядок

Используется уже существующий system Python 3.14.7 через uv, только stdlib.
Установки, постоянная среда, смена stack не нужны. Все следующие строки
запускаются из корня проекта. Сетевой этап уже выполнен: при наличии
завершённых manifests downloader только проверяет локальные данные.

```bash
env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv run --no-project --python-preference only-system --python 3.14.7 python -B src/prepare_fresh_holdout.py --root . --catalogue
env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv run --no-project --python-preference only-system --python 3.14.7 python -B src/prepare_fresh_holdout.py --root . --download
env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv run --no-project --python-preference only-system --python 3.14.7 python -B src/run_fresh_holdout.py --root . matrix
env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv run --no-project --python-preference only-system --python 3.14.7 python -B src/run_fresh_holdout.py --root . cost
env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv run --no-project --python-preference only-system --python 3.14.7 python -B src/verify_fresh_holdout.py --root .
env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv run --no-project --python-preference only-system --python 3.14.7 python -B src/report_fresh_holdout.py --root .
```

Одновременно допускается только один matrix/cost процесс. Межпроцессного
lock нет; второй запуск во время первого не является поддержанным resume.
Direct timing запускается после завершения других тяжёлых project runs.
При прерывании готовые checkpoints сохраняются; orphan trace без checkpoint
требует ручной проверки и отдельного recovery, а не автоматической перезаписи.
Завершённые matrix/cost не пересчитываются: повтор команды сверяет freeze и
возвращает сохранённый результат. Новая версия исходников требует нового
experiment namespace, а не удаления старых результатов.

Расчётные prefix timings могут испытывать влияние одновременного отдельного
solver audit; основное сравнение скорости использует последующий изолированный
direct timing. Независимый solver не меняет frozen v2 внутри данного holdout.

## Старый v2 resume после добавления новых данных

Read-only сверка показала: ни один существовавший source/input hash v2 не
изменился. Однако старый `run_force_models_v2.source_hashes()` включает все
`*manifest.json` в общей папке. Появление двух новых fresh-holdout manifests
расширяет этот inventory, поэтому старый completed `--resume` в общей рабочей
папке намеренно останавливается по строгому freeze mismatch. Это не порча
старых результатов. Для них используются сохранённые artifacts и read-only
verifier; для полного legacy rerun нужен отдельный snapshot исходного набора
файлов. Не удалять новые manifests и не обновлять старые hashes ради resume.
Fresh-holdout runner имеет свой freeze и не требует повторного old v2 запуска.

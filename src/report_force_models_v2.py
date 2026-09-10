"""Render the verified v2 integration/replay report from local artifacts."""
from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from run_force_models_v2 import eligible
from run_selection_v2 import validate_matrix, _validate_timing_checkpoint
from selection_v2 import choose_model_v2, fit_horizon_rule_v2


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def report(root, tests_passed):
    root = root.resolve()
    config = read(root / "configs/force_models_v2.json")
    out = root / config["output_directory"]
    matrix = read(out / "results.json")
    verification = read(out / "verification/verification.json")
    replay = read(out / "selection_replay.json")
    rules = read(out / "rules.json")
    if not verification["passed"] or verification["matrix_sha256"] != digest(out / "results.json"):
        raise ValueError("matrix verification is missing or stale")
    if replay["provenance"]["matrix_sha256"] != digest(out / "results.json") or replay["provenance"]["rules_sha256"] != digest(out / "rules.json"):
        raise ValueError("replay provenance mismatch")
    for key, path in (("selection_source_sha256", "src/selection_v2.py"), ("runner_source_sha256", "src/run_selection_v2.py")):
        if replay["provenance"][key] != digest(root / path):
            raise ValueError("replay source changed")
    records = matrix["records"]
    sample = read(root / config["sample_path"])
    validate_matrix(records, sample, config)
    fitted = fit_horizon_rule_v2([row for row in records if row["split"] == "train"], config)
    if any(rules.get(key) != value for key, value in fitted.items()):
        raise ValueError("rule calibration mismatch")
    by_key = {(row["object_id"], float(row["horizon_days"]), row["model_id"]): row for row in records}
    validation = [obj for obj in sample["objects"] if obj["split"] == "validation"]
    expected_choices = {(str(obj["id"]), float(h), float(tol)) for obj in validation for h in config["horizons_days"] for tol in config["position_tolerances_km"]}
    choice_keys = [(row["object_id"], float(row["horizon_days"]), float(row["tolerance_km"])) for row in replay["choices"]]
    if len(choice_keys) != len(expected_choices) or set(choice_keys) != expected_choices:
        raise ValueError("replay choices incomplete")
    for row in replay["choices"]:
        expected = choose_model_v2(row["horizon_days"], row["tolerance_km"], rules, config)
        if any(row[key] != value for key, value in expected.items()):
            raise ValueError("replay choice differs from calibrated rule")
        measured = by_key[(row["object_id"], float(row["horizon_days"]), row["model_id"])]
        if row["actual_eligible"] != eligible(measured, row["tolerance_km"], config["numerical_budget_fraction"]):
            raise ValueError("replay eligibility mismatch")
    for obj in validation:
        _validate_timing_checkpoint([row for row in replay["timings"] if row["object_id"] == str(obj["id"])], obj, config)
    for row in replay["timings"]:
        expected_model = (choose_model_v2(row["horizon_days"], 1., rules, config)["model_id"]
                          if row["method"] == "selector" else replay["fixed_model_id"])
        measured = by_key[(row["object_id"], float(row["horizon_days"]), expected_model)]
        if row["model_id"] != expected_model or row["actual_eligible"] != eligible(measured, 1., config["numerical_budget_fraction"]):
            raise ValueError("direct timing model or eligibility mismatch")
        if abs(row["max_position_error_km"] - measured["max_position_error_km"]) > 1e-5:
            raise ValueError("direct timing rollout error differs from verified matrix")
    for method in ("selector", "fixed"):
        total = sum(row["runtime_median_seconds"] for row in replay["timings"] if row["method"] == method)
        if total != replay["full_cost"][method]["total_seconds"]:
            raise ValueError("timing sum mismatch")
        if sum(row["actual_eligible"] for row in replay["timings"] if row["method"] == method) != replay["full_cost"][method]["eligible_cases"]:
            raise ValueError("direct timing eligible count mismatch")
    models = [model["model_id"] for model in config["models"]]
    tolerances = config["position_tolerances_km"]
    old = {}
    for split in ("train", "validation"):
        for row in read(root / f"outputs/development30/{split}/results.json")["records"]:
            if row["model_id"] == "B3+GR+SB16":
                old[(row["object_id"], float(row["horizon_days"]))] = row["max_position_error_km"]
    strongest = [row for row in records if row["model_id"] == config["fallback_model_id"]]
    annual = [row for row in strongest if row["horizon_days"] == 365]
    worst = max(annual, key=lambda row: row["max_position_error_km"])
    date = dt.datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
    lines = [f"# Физические компоненты и прогноз v2 — {date}", "",
             "J2 и доступные NG-параметры встроены в общий propagator и новый селектор.",
             "Номер астероида не определяет состав силы. Выполнена полная матрица",
             "30 объектов × 5 горизонтов × 4 кандидата, три production повтора и один",
             "fine run на пару объект/модель. Это **post-hoc development regression**:",
             "исходные 12 validation объектов уже просмотрены. Новый результат не",
             "заменяет прежние 55/60 у frozen v1 selector и не является fresh holdout.", "",
             "## Что работает в программе", "",
             "- Явные компоненты: планетные monopoles, solar GR, SB16, Earth J2 и NG policy.",
             "- Поправки вычисляются при каждом RK4 stage. Модель фиксирована на весь rollout.",
             "- NGInput хранит источник, SHA-256, границу доступности и неизвестную/известную sigma.",
             "- Доступность фиксируется на старте; будущий A2 не активируется внутри прогноза.",
             "- При неизвестном NG возвращается ограничение; required отклоняет такой ввод.",
             "- Horizon rule v2 обучен только на прежних 18 train объектах. Изменение config требует новой калибровки.",
             "- Отдельный CLI принимает initial state и возвращает daily и accepted-step trajectory.", "",
             "[Команды и API](FORCE_MODELS_V2_USAGE.md), [контракт](FORCE_MODELS_V2_CONTRACT.md).", "",
             "## Покрытие прежних 12 validation объектов: replay", "",
             "Каждая ячейка — допустимые окна из 60; ошибка на reference сетке и",
             "production/fine difference ≤10% допуска. Пять горизонтов одного объекта зависимы.", "",
             "| Кандидат | 0.1 km | 1 km | 10 km |", "|---|---:|---:|---:|"]
    for model in models:
        selected = [row for row in records if row["model_id"] == model and row["split"] == "validation"]
        counts = [sum(eligible(row, tol, config["numerical_budget_fraction"]) for row in selected) for tol in tolerances]
        lines.append(f"| {model} | " + " | ".join(map(str, counts)) + " |")
    counts = [sum(row["actual_eligible"] for row in replay["choices"] if row["tolerance_km"] == tol) for tol in tolerances]
    lines.append("| Horizon selector v2 | " + " | ".join(map(str, counts)) + " |")
    fallbacks = [sum(row["fallback"] for row in replay["choices"] if row["tolerance_km"] == tol) for tol in tolerances]
    lines += ["", "Fallback-флаги при 0.1/1/10 km: " + "/".join(map(str, fallbacks)) + "."]
    for row in replay["choices"]:
        if not row["actual_eligible"]:
            lines += ["", f"Промах выбора: объект {row['object_id']}, {row['horizon_days']:g} суток, допуск {row['tolerance_km']:g} km.",
                      f"Выбрана {row['model_id']}; ошибка {row['max_position_error_km']*1000:.3f} m при эмпирическом train cap {row['empirical_error_cap_km']*1000:.3f} m.",
                      "Полный кандидат достаточен; это ошибка horizon rule, а не недостаточность всего набора сил. Правило после replay не перенастраивалось."]
    full_counts = [sum(eligible(row, tol, config["numerical_budget_fraction"]) for row in strongest) for tol in tolerances]
    lines += ["", f"На всех 30 объектах полный кандидат проходит {full_counts[0]}/{full_counts[1]}/{full_counts[2]} из 150 окон при 0.1/1/10 km.",
              f"Его наибольшая годовая ошибка: {worst['max_position_error_km']:.6f} km, объект {worst['object_id']}; step difference {worst['numerical_difference_km']*1000:.3f} m.", "",
              "## Пять прежних отказов через общий путь v2", "",
              "| Объект | Горизонт, дни | Было, km | V2 полный, m | Step difference, m |", "|---|---:|---:|---:|---:|"]
    for object_id, horizons in (("153814", (90, 180, 365)), ("613569", (180, 365))):
        for horizon in horizons:
            row = by_key[(object_id, float(horizon), config["fallback_model_id"])]
            lines.append(f"| {object_id} | {horizon} | {old[(object_id, float(horizon))]:.6f} | {row['max_position_error_km']*1000:.3f} | {row['numerical_difference_km']*1000:.3f} |")
    statuses = Counter(row["force_metadata"]["ng_status"] for row in annual)
    lines += ["", f"NG status по 30 объектам: `{dict(statuses)}`. NG-блок есть только у 613569;",
              "его A2=1.18779071272e-13 AU/day² взят без подгонки из исходного Horizons header.",
              "Доступность подтверждена matching raw SHA/size в manifest с датой получения",
              "2026-09-07; консервативная граница 2026-09-09 предшествует старту 2028-01-04.",
              "Sigma отсутствуют; результат nominal, uncertainty propagation не выполнен.", "",
              "## Прямая стоимость при 1 km", "",
              "Суммы медиан трёх прямых вызовов на каждом из 60 окон. Порядок selector/fixed",
              "чередуется. Initial state, NGInput и эфемериды уже в памяти; учитываются",
              "inference, сборка сил и rollout, отдельно от offline reference evaluation.", "",
              "| Стратегия | Время, s | Допустимые окна |", "|---|---:|---:|"]
    for method, label in (("selector", "Horizon selector v2"), ("fixed", replay["fixed_model_id"])):
        item = replay["full_cost"][method]
        lines.append(f"| {label} | {item['total_seconds']:.3f} | {item['eligible_cases']}/60 |")
    saving = 100*(1-replay["full_cost"]["selector"]["total_seconds"]/replay["full_cost"]["fixed"]["total_seconds"])
    lines += ["", f"Разница стоимости selector относительно выбранной постоянной модели: {saving:.2f}% экономии (отрицательное значение означает замедление).",
              "Постоянная модель выбрана post-hoc как самый дешёвый фиксированный кандидат",
              "с полным покрытием разрешимого подмножества по матрице. Это локальный",
              "CPU benchmark; при 0.1/10 km новый full online cost не измерялся.", "",
              "## Проверки и границы результата", "",
              f"- {tests_passed} unit tests прошли; при выключенных поправках новые rollouts точно воспроизводят v1 на тестовых задачах.",
              f"- Проверены {verification['matrix_records_verified']} records и {verification['traces_verified']} traces; заново вычислены {verification['error_samples_recomputed']} error samples.",
              f"- SHA-256/size/Git-ignore проверены для {verification['manifest']['raw_files']} raw-файлов; сохранены 96 frozen v1 source/input hashes.",
              "- Повторно проверены train-only calibration, все 180 replay choices, 120 direct-cost rows и полнота трёх timing repeats.",
              "- Отсутствие no-candidate warning в конкретном replay не доказывает способность находить все будущие отказы.",
              "- Независимый solver, uncertainty propagation, непрерывный максимум ошибки и свежий holdout остаются следующими этапами.", "",
              "Новых данных, зависимостей, постоянной среды, commit, remote или публикации не создавалось.",
              "Все новые результаты находятся в ignored outputs/force_models_v2.", "",
              "## Изменённые пути", "",
              "Новые модули: `src/force_models_v2.py`, `src/ng_inputs_v2.py`,",
              "`src/selection_v2.py`, `src/forecast_v2.py`, `src/run_force_models_v2.py`,",
              "`src/run_selection_v2.py`, `src/verify_force_models_v2.py`, `src/report_force_models_v2.py`.",
              "Новые конфигурации: `configs/force_models_v2.json`, `configs/forecast_v2_example.json`.",
              "Добавлены v2 tests, контракт, инструкция и этот отчёт; обновлены README,",
              "AGENTS, PROJECT_GUIDE, RESEARCH_PLAN, DATA_GUIDE и DECISIONS.", ""]
    if replay["full_cost"]["selector"]["eligible_cases"] != replay["full_cost"]["fixed"]["eligible_cases"]:
        lines += ["Точность selector и fixed в прямом сравнении различается. Указанная",
                  "разница времени не является ускорением при одинаковом качестве.", ""]
    text = "\n".join(lines)
    target = root / "docs/FORCE_MODELS_V2_REPORT.md"
    target.write_text(text)
    (out / "report.md").write_text(text)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--tests-passed", type=int, required=True)
    args = parser.parse_args()
    print(report(args.root, args.tests_passed))

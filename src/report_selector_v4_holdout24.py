"""Generate the factual Russian v4 holdout report from completed artifacts.

This module only reads frozen artifacts.  It deliberately performs no
propagation and writes the report once its matrix, timing, and verifier agree.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any
from prepare_selector_holdout24 import check_hashes

OUT = "outputs/selector_v4_holdout24"
SAMPLE = "data/processed/selector_v4_holdout24/sample.json"
METHOD = "outputs/selector_v4/method_freeze.json"
LABELS = {"tree_v3": "tree_v3 (CART)", "physics_v4": "physics_v4", "hybrid_v4": "hybrid_v4 (CART)", "fixed_full": "fixed_full"}
METHODS = tuple(LABELS)
HORIZONS = (7.0, 30.0, 90.0, 180.0, 365.0)
TOLS = (0.1, 1.0, 10.0)


def _read(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    if not path.is_file():
        raise FileNotFoundError(f"Required completed artifact is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(root: Path, relative: str) -> str:
    return hashlib.sha256((root / relative).read_bytes()).hexdigest()


def _fmt(value: float) -> str:
    return f"{float(value):.6g}"


def _decision_detail(row: dict[str, Any]) -> str:
    caps = row.get("predicted_error_caps_km", {})
    estimates = "; ".join(f"{m.removeprefix('V2-')}: {_fmt(caps[m])} km"
                          for m in ("V2-P", "V2-P-GR", "V2-P-GR-SB16") if m in caps)
    reasons = ", ".join(row.get("outside_training_support_reasons", []))
    return estimates + (f"; OOD: {reasons}" if reasons else "")


def _body_pass(choices: list[dict[str, Any]], method: str, tol: float, objects: list[dict[str, Any]]) -> int:
    return sum(all(bool(row["actual_eligible"]) for row in choices
                   if row["method"] == method and float(row["tolerance_km"]) == tol and row["object_id"] == obj["id"])
               for obj in objects)


def report(root: Path, target: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    matrix = _read(root, f"{OUT}/matrix.json")
    cost = _read(root, f"{OUT}/direct_cost.json")
    verification = _read(root, f"{OUT}/verification.json")
    raw_audit = _read(root, f"{OUT}/raw_verification.json")
    input_audit = _read(root, "outputs/selector_v4/training_input_audit_complete.json")
    sample = _read(root, SAMPLE)
    method_freeze = _read(root, METHOD)
    check_hashes(root, method_freeze["hashes"])
    if raw_audit.get("passed") is not True or input_audit.get("passed") is not True:
        raise ValueError("Raw and training input audits must pass")
    if verification.get("passed") is not True:
        raise ValueError("Independent v4 verification has not passed")
    if verification.get("matrix_sha256") != _sha(root, f"{OUT}/matrix.json") or verification.get("timing_sha256") != _sha(root, f"{OUT}/direct_cost.json"):
        raise ValueError("Verifier hashes do not match matrix/timing artifacts")
    records = list(matrix.get("records", [])); choices = list(matrix.get("choices", []))
    if len(records) != 480 or len(choices) != 1440:
        raise ValueError("Expected completed 480-record matrix and 1440 choices")
    summaries = {(str(row["method"]), float(row["tolerance_km"])): row for row in matrix["summaries"]}
    objects = sample["objects"]
    primary = [row for row in choices if float(row["tolerance_km"]) == 1.0]
    lines = [
        "# Селектор v4: проверка на 24 новых телах",
        "",
        "Метод v4 и его артефакты были зафиксированы до выбора и загрузки новой выборки. Проверены 24 новых тела, пять зависимых горизонтов на тело и четыре физических кандидата: 480 записей матрицы, 192 production/fine traces и 1440 решений для трёх допусков. Horizons используется как model-derived reference при заданном initial state.",
        "",
        "## Результаты по допускам",
        "",
        "Успех одновременно требует max sampled position error ≤ tolerance и production/fine difference ≤ 10% tolerance. Нулевой процент ошибок на этой выборке не является универсальной гарантией.",
        "",
        "| Метод | 0.1 km | 1 km | 10 km | 1 km тел со всеми 5 H / 24 | 1 km max error | warnings / 1 km | OOD / 1 km | no-candidate truth / 1 km |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for method in METHODS:
        vals = [summaries[(method, tol)]["eligible_cases"] for tol in TOLS]
        one = summaries[(method, 1.0)]
        lines.append(f"| {LABELS[method]} | {vals[0]} / 120 | {vals[1]} / 120 | {vals[2]} / 120 | {_body_pass(choices, method, 1.0, objects)} / 24 | {_fmt(one['worst_position_error_km'])} km | {one['warning_cases']} | {one.get('outside_training_support_cases', 0)} | {one['no_candidate_cases']} |")

    lines += ["", "Пять горизонтов одного тела образуют зависимый набор; 120 окон не являются 120 независимыми объектами. Warning и no-candidate truth считаются отдельно: fallback не превращает прогноз в гарантированно точный.", "", "## Стоимость при 1 km", "", "| Метод | Сумма медиан, s | К full | Выборов full / 120 |", "| --- | ---: | ---: | ---: |"]
    full_seconds = float(cost["full_cost"]["fixed_full"]["total_seconds"])
    for method in METHODS:
        seconds = float(cost["full_cost"][method]["total_seconds"])
        selected_full = sum(row["method"] == method and row["model_id"] == "V2-P-GR-SB16" for row in primary)
        lines.append(f"| {LABELS[method]} | {seconds:.3f} | {(seconds / full_seconds - 1) * 100:+.2f}% | {selected_full} |")
    lines += ["", "Время включает causal feature builder, inference, сборку сил и rollout; это сумма медиан трёх чередующихся повторов каждого из 120 случаев. Сравнение physics_v4 и hybrid_v4 показывает добавочную роль обучаемой поправки на тех же физических proxy. Сравнение с v3 приводится только на этой же fresh24 выборке; исторический v3 score 119/120 относится к прежнему frozen эксперименту и здесь не переписывается.", "", "| Горизонт | tree_v3 | physics_v4 | hybrid_v4 | fixed_full |", "| --- | ---: | ---: | ---: | ---: |"]
    for horizon in HORIZONS:
        cells = [sum(float(row["runtime_median_seconds"]) for row in cost["timings"] if row["method"] == m and float(row["horizon_days"]) == horizon) for m in METHODS]
        lines.append(f"| {horizon:g} d | " + " | ".join(f"{v:.3f} s" for v in cells) + " |")

    lines += ["", "Проверка разброса: ниже суммы фактических запусков с одинаковым номером повтора. Они отличаются от основной метрики — суммы медиан по случаям. Общий cold load, загрузка данных, evaluator и fine audit в online cost не входят; тяжёлая параллельная работа во время замеров не запускалась.", "",
              "| Метод | Повтор 1, s | Повтор 2, s | Повтор 3, s | Сумма медиан feature cost, s |",
              "| --- | ---: | ---: | ---: | ---: |"]
    for method in METHODS:
        timing_rows = [r for r in cost["timings"] if r["method"] == method]
        trials = [sum(r["runtime_trials_seconds"][i] for r in timing_rows) for i in range(3)]
        feature_cost = sum(sorted(r["feature_runtime_trials_seconds"])[1] for r in timing_rows)
        lines.append(f"| {LABELS[method]} | {trials[0]:.3f} | {trials[1]:.3f} | {trials[2]:.3f} | {feature_cost:.3f} |")

    lines += ["", "## Выборы физических моделей и различия physics/hybrid", "", "| Метод | B2 | P | P+GR | P+GR+SB16 |", "| --- | ---: | ---: | ---: | ---: |"]
    for method in METHODS:
        counts = Counter(row["model_id"] for row in primary if row["method"] == method)
        lines.append(f"| {LABELS[method]} | {counts['V2-B2']} | {counts['V2-P']} | {counts['V2-P-GR']} | {counts['V2-P-GR-SB16']} |")
    lines += ["", "Features включают исходный v3 vector, force proxies и геометрию; target future states в feature builder не передаются. Для каждого тела и горизонта ниже указано, когда physics_v4 и hybrid_v4 выбрали разные модели. Причины берутся из сохранённых predicted caps, rejected forces и support flags, а не восстанавливаются по результатам reference.", "", "| Тело | H | physics | hybrid | physics причины | hybrid причины |", "| --- | ---: | --- | --- | --- | --- |"]
    by = {(str(x["object_id"]), float(x["horizon_days"]), x["method"]): x for x in primary}
    differences = 0
    for obj in objects:
        for horizon in HORIZONS:
            p, h = by[(str(obj["id"]), horizon, "physics_v4")], by[(str(obj["id"]), horizon, "hybrid_v4")]
            if p["model_id"] != h["model_id"]:
                differences += 1
                lines.append(f"| {obj['id']} | {horizon:g} | {p['model_id']} | {h['model_id']} | {_decision_detail(p)} | {_decision_detail(h)} |")
    if differences == 0:
        lines.append("| — | — | совпадают во всех 120 случаях | — | — | — |")
    lines += ["", f"Разные решения physics_v4 и hybrid_v4 наблюдались в {differences} из 120 случаев. Это описательная проверка на данной выборке и не доказывает причинное преимущество ML.", "", "## Предупреждения, silent failures и близкие сближения", ""]
    for method in METHODS:
        rows = [row for row in primary if row["method"] == method]
        reasons = sorted({reason for row in rows for reason in row.get("outside_training_support_reasons", [])})
        lines.append(f"- **{LABELS[method]}**: OOD {sum(bool(r.get('outside_training_support')) for r in rows)}, no-candidate predicted {sum(bool(r.get('no_candidate_predicted')) for r in rows)}, actual no-candidate {sum(bool(r.get('no_candidate_truth')) for r in rows)}, warning {sum(bool(r.get('warning')) for r in rows)}; OOD reasons: {', '.join(reasons) or 'нет'}.")
    lines += ["", f"На новом holdout strong guard отмечает {summaries[('hybrid_v4', 1.)]['strong_flagged']} из 120 окон; истинных no-candidate случаев при 1 km — {summaries[('hybrid_v4', 1.)]['no_candidate_cases']}. Поэтому recall предупреждений о сильном сближении и недостаточности полного набора здесь не измерен. OOD warning описывает выход за изученные диапазоны, а не установленную ошибку прогноза."]
    lines += ["", "Для каждого метода приведены worst failures по каждому допуску:", "", "| Метод | Допуск | Тело | H | Ошибка | Статус | warning |", "| --- | ---: | --- | ---: | ---: | --- | --- |"]
    for method in METHODS:
        for tol in TOLS:
            rows = [r for r in choices if r["method"] == method and float(r["tolerance_km"]) == tol and not r["actual_eligible"]]
            if rows:
                row = max(rows, key=lambda r: float(r["max_position_error_km"]))
                lines.append(f"| {LABELS[method]} | {tol:g} | {row['object_id']} | {float(row['horizon_days']):g} | {_fmt(row['max_position_error_km'])} km | {row['status']} | {bool(row['warning'])} |")
            else:
                lines.append(f"| {LABELS[method]} | {tol:g} | — | — | — | нет failures | — |")
    lines += ["", "## Надёжность без исключения предупреждённых случаев", "",
              "| Метод | Допуск km | Превышения без warning / все unflagged | Warnings | Из них прогноз достаточен | Predicted no-candidate | Нет достаточного кандидата |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for tol in TOLS:
        for method in METHODS:
            s = summaries[(method, tol)]
            lines.append(f"| {method} | {tol:g} | {s['unflagged_failures']} / {s['unflagged_cases']} | {s['warning_cases']} | {s['warning_on_eligible_prediction']} | {s['no_candidate_predicted_cases']} | {s['no_candidate_cases']} |")
    lines += ["", "## Новые объекты и годовой прогноз hybrid при 1 km", "",
              "| Тело | Страта | Начало | Кандидат | Max error, m | Production/fine, m | Статус |",
              "| --- | --- | --- | --- | ---: | ---: | --- |"]
    for obj in objects:
        c = by[(str(obj['id']), 365., 'hybrid_v4')]
        lines.append(f"| {obj['id']} | {obj['stratum']} | {obj['start_date']} | {c['model_id']} | {1000*c['max_position_error_km']:.3f} | {1000*c['numerical_difference_km']:.3f} | {c['status']} |")
    lines += ["", "## Вывод по этому циклу", ""]
    physics = summaries[("physics_v4", 1.)]
    hybrid = summaries[("hybrid_v4", 1.)]
    previous = summaries[("tree_v3", 1.)]
    ps = float(cost["full_cost"]["physics_v4"]["total_seconds"])
    hs = float(cost["full_cost"]["hybrid_v4"]["total_seconds"])
    lines.append(f"При главном допуске 1 km silent failures: прежний tree {previous['unflagged_failures']}, physics {physics['unflagged_failures']}, hybrid {hybrid['unflagged_failures']}. Hybrid меняет время относительно physics на {(hs/ps-1)*100:+.2f}%; относительно full — на {(hs/full_seconds-1)*100:+.2f}%.")
    if previous['unflagged_failures'] == 0:
        lines.append("На этой новой выборке прежний v3 тоже не имеет silent failures при 1 km. Поэтому уменьшение их числа относительно v3 не продемонстрировано; исправление прежнего Izvekov — только inspected replay.")
    if hybrid['eligible_cases'] == physics['eligible_cases'] and hs >= ps:
        lines.append("При одинаковом числе успешных окон обучаемая поправка здесь не даёт выигрыша во времени относительно физического правила. Это отрицательный результат для добавочной пользы выбранного ML метода, а не доказательство невозможности полезного ML вообще.")
    elif hybrid['eligible_cases'] == physics['eligible_cases'] and hs < ps:
        lines.append("При одинаковом числе успешных окон hybrid оказался быстрее физического правила в данном локальном замере. Размер выборки и три повтора не устанавливают надёжный выигрыш для произвольных тел или другого оборудования.")
    else:
        lines.append("Число успешных окон у hybrid и physics различается: это компромисс точности и стоимости, а не ускорение при неизменных требованиях.")
    lines.append("Метод после открытия этого holdout не перенастраивался. Один согласованный цикл завершён; следующие изменения не включаются в этот score.")
    lines.append("До freeze прошли 304 unit tests. Также пройдены deterministic training refit и полный аудит train/calibration inputs. Финальные проверки ниже пересчитывают метрики из сохранённых траекторий; они не являются новым независимым physical solver.")
    lines += ["", "## Что изменилось в программе", "",
              "Оба новых селектора используют оценки пяти групп сил, вычисленные вдоль дешёвого B2 прогноза: планеты, солнечная GR, SB16, Earth J2 и доступный NG. Для каждого кандидата суммируются только исключённые из него группы. К этому масштабу добавляется наблюдённый на train остаток полного кандидата для данного горизонта. Physics rule калибрует общий multiplier, hybrid обучает CART-поправку к тому же масштабу. Признаки не используют будущие состояния целевого тела.", "",
              "Это позволяет исправлять недооценку отсутствующей силы без персональной ветки по номеру астероида. Однако интеграл нормы ускорения вдоль B2 не является строгой границей ошибки: он не описывает полностью усиление возмущений при рассеянии, неопределённость начального состояния или неизвестные NG параметры. Проверка training support и strong-encounter guard поэтому сохраняет отдельный warning, даже когда численный прогноз оказался точным.", "",
              "На уже изученном 3418 Izvekov оба v4 выбирают full в трёх прежних ошибочных решениях v3: 30 d / 0.1 km, 90 d / 1 km и 365 d / 10 km. На 90 d это меняет выбранную ошибку с 1.695 km до 0.351 m. Объект теперь входит в train; этот replay показывает устранение известного дефекта, но не увеличивает fresh score.", "",
              "Отдельный replay warning на известном Апофисе использует только initial state и exogenous inputs. Hybrid отмечает 7/30/90 d как outside_training_support, 180/365 d как strong_encounter_unvalidated при всех трёх допусках. Основной physical rollout в этом replay не выполнялся. Fallback — общий v2, а не специализированный Apophis backend; прежний годовой teacher residual 2.552137 km не уменьшен этим циклом. [Границы решения Апофиса](APOPHIS_ENCOUNTER_CLOSURE_REPORT.md)."]
    ng_counts = raw_audit['ng_status_counts']
    lines += ["", f"Raw audit: {raw_audit['unique_raw_paths']} unique paths, {raw_audit['manifest_count']} manifests; все SHA/size/Git exclusions проверены. Из новых тел available NG: {ng_counts.get('available', 0)}, not_provided: {ng_counts.get('not_provided', 0)}; {raw_audit['shared_daily_refined_nodes']} общих daily/refined узлов совпадают.",
              f"Offline verification пересчитала {verification['error_rows']} error rows, {verification['physical_traces']} traces, {verification['event_geometry_records']} event records и {verification['direct_timing_rows']} timing medians."]
    runtime = matrix.get("provenance", {}).get("runtime_environment", {})
    lines += ["", "Event geometry хранится в годовых records для 16 encounter objects и пересчитывается только на известном refined ±2-day window. Сильные encounter и no-candidate случаи, если их нет в этих данных, остаются непротестированными; отсутствие warning само по себе не является доказательством безопасности.", "", "## Выборка, NG и воспроизводимость", "", "Train v4: 42 ранее inspected тела (18 development train + старые selector24); calibration: 24 ранее inspected тела (12 validation + fresh12). Новые 24 тела disjoint от обеих групп. Выборка включает по четыре Earth/Venus/Mars/Jupiter encounters и inner/outer controls. Загружены только 40 target tables (24 daily + 16 refined); planetary/SB exogenous context переиспользован из frozen development30. NG availability и provenance сохранены в manifest; отсутствующий NG не трактуется как измеренный ноль.", "", f"Runtime: `{runtime.get('python_version', 'см. matrix provenance')}`; implementation `{runtime.get('python_implementation', 'unknown')}`.", f"Method freeze: `{_sha(root, METHOD)}`.", f"Matrix SHA256: `{_sha(root, OUT + '/matrix.json')}`.", f"Direct cost SHA256: `{_sha(root, OUT + '/direct_cost.json')}`.", f"Verification SHA256: `{_sha(root, OUT + '/verification.json')}`.", "", "Артефакты: [contract](SELECTOR_V4_CONTRACT.md), [matrix](../outputs/selector_v4_holdout24/matrix.json), [direct cost](../outputs/selector_v4_holdout24/direct_cost.json), [verification](../outputs/selector_v4_holdout24/verification.json).", "", "Воспроизведение после завершения входных фаз:", "", "```sh", "PYTHONPATH=src uv run --no-project --python-preference only-system --python 3.14.7 python -B src/run_selector_v4_holdout24.py verify", "PYTHONPATH=src uv run --no-project --python-preference only-system --python 3.14.7 python -B src/report_selector_v4_holdout24.py", "```", "", "Отчёт не добавляет новые scores для Апофиса и не изменяет frozen методы или артефакты."]
    text = "\n".join(lines) + "\n"
    target = target or root / "docs/SELECTOR_V4_HOLDOUT24_REPORT.md"
    target.write_text(text, encoding="utf-8")
    return {"report": str(target), "methods": list(METHODS), "records": len(records), "choices": len(choices), "physics_hybrid_differences": differences}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(report(args.root), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

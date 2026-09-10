"""Paired planetary-force and output-sampling ablation for encounter forecasts."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import platform
import statistics
import time

from encounter_screening import forecast_encounters
from run_eda import load_json
from run_encounter_screening import (
    compare_minimum, confusion_summary, forecast_feature_record,
    load_context, make_cases, reference_minima,
)
from run_physics_baselines import state_from_row


def predict(initial, start_jd: float, case: dict, strategy: dict, config: dict,
            context: dict, *, step_scale: float | None = None) -> dict:
    """Only forecast inputs cross this boundary; context reference fields do not."""
    return forecast_encounters(
        initial, start_jd, case["horizon_days"], context["daily_planets"], context["mu_sun"],
        cadence_days=strategy["cadence_days"], refine=True,
        default_step_days=config["default_step_days"],
        step_scale=config["step_scale"] if step_scale is None else step_scale,
        au_km=context["au_km"], day_s=context["day_s"],
        force_model=strategy["force_model"], sampling=strategy["sampling"],
        refinement_distance_au=config["refinement_distance_au"],
    )


def write_json(path: Path, value) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def source_hashes(root: Path) -> dict:
    paths = [*sorted((root / "src").glob("*.py")),
             *sorted((root / "tests").glob("*.py")),
             *sorted((root / "configs").glob("*.json")),
             *sorted((root / "data/checksums").glob("*.json")),
             root / "docs/ENCOUNTER_PLANETS_CONTRACT.md"]
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths}


def build_report(result: dict) -> str:
    config = result["config"]
    rows, costs = result["evaluation_rows"], result["timings"]
    lines = ["# Планеты в предварительном прогнозе — pilot 6", "",
             f"Запуск: {result['generated_at_utc']}. Engineering regression, не закрытый test.", "",
             "## Постановка", "",
             "B2 учитывает только Солнце; B3 — также девять существующих массивных тел, включая Earth и Moon отдельно. "
             "Во всех вариантах используются одно initial state и daily planetary ephemerides. GR, SB16, J2 и fitted NG не добавлены.", "",
             "daily и six_hour задают сетку сохраняемых состояний; между ними ищется cubic-Hermite minimum. "
             "encounter_steps дополнительно сохраняет оба конца принятого RK4 шага, если хотя бы один прогнозный конец ближе 0.01 AU к perturber. "
             "Даты известных событий и будущие asteroid states в прогноз не передаются.", "",
             f"Основных задач: {sum(c['group']=='main' for c in result['cases'])}; Apophis lead-time окон: "
             f"{sum(c['group']=='apophis_lead_time' for c in result['cases'])}; вариантов на задачу: {len(config['strategies'])}.", "",
             "Reference — тот же отдельный Horizons Hermite evaluator, с локальным пяти-минутным уточнением. "
             "Величины относятся к минимуму окна, включая endpoints; это не каталог всех events. "
             "Пороги 0.001/0.01/0.05 AU сохранены и не являются границами безопасности.", "",
             "## Обнаружение сближений", "",
             "| Группа | Вариант | Порог, AU | TP | FN | FP | TN |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for r in result["confusion"]:
        lines.append(f"| {r['group']} | {r['strategy_id']} | {r['threshold_au']:g} | {r['TP']} | {r['FN']} | {r['FP']} | {r['TN']} |")
    for body_id, name in (("399", "Earth"), ("301", "Moon")):
        lines += ["", f"## Apophis–{name}", "",
                  "Все расстояния от центра тела. Ошибки знаковые: forecast минус reference. "
                  "Окна относятся к одному событию, а не к независимым новым пролётам.", "",
                  "| Начало | Горизонт, дни | Вариант | Reference min, km | Forecast min, km | Ошибка d, km | Ошибка времени, s |",
                  "| --- | ---: | --- | ---: | ---: | ---: | ---: |"]
        for r in rows:
            if r["object_id"] != "99942" or r["body_id"] != body_id:
                continue
            if r["group"] == "main" and r["horizon_days"] not in (180, 365):
                continue
            lines.append(f"| {r['start_date']} | {r['horizon_days']} | {r['strategy_id']} | "
                         f"{r['reference_distance_km']:.3f} | {r['distance_km']:.3f} | "
                         f"{r['distance_error_km']:+.3f} | {r['time_error_hours']*3600:+.3f} |")
    lines += ["", "## Полная цена предварительного прогноза", "",
              "Медиана per-case median по шести main объектам; каждый горизонт выполнен отдельно. "
              "Время включает force setup, интегрирование, проверки сгущения, интерполяцию планет и поиск minima. "
              "Три повтора последовательны; порядок вариантов вращается. Steps — медиана числа шагов, outputs — числа сохранённых состояний.", "",
              "| Вариант | Горизонт, дни | Total, s | Propagation + sampling, s | Minima scan, s | RK4 steps | Outputs |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for horizon in config["horizons_days"]:
        for s in config["strategies"]:
            selected = [r for r in costs if r["group"] == "main" and r["horizon_days"] == horizon
                        and r["strategy_id"] == s["strategy_id"]]
            if not selected:
                continue
            med = lambda field: statistics.median(r[field] for r in selected)
            lines.append(f"| {s['strategy_id']} | {horizon} | {med('runtime_median_seconds'):.5f} | "
                         f"{med('propagation_median_seconds'):.5f} | {med('screening_median_seconds'):.5f} | "
                         f"{med('rk4_steps'):g} | {med('output_samples'):g} |")
    lines += ["", "Затраты на сложное событие показаны отдельно, поскольку медиана шести объектов может скрыть их:", "",
              "| Apophis: начало | Горизонт, дни | Вариант | Total, s | RK4 steps | Outputs |",
              "| --- | ---: | --- | ---: | ---: | ---: |"]
    for r in costs:
        if r["object_id"] == "99942" and (r["group"] != "main" or r["horizon_days"] == 365):
            lines.append(f"| {r['start_date']} | {r['horizon_days']} | {r['strategy_id']} | "
                         f"{r['runtime_median_seconds']:.5f} | {r['rk4_steps']} | {r['output_samples']} |")
    lines += ["", f"Общая загрузка: {result['shared_load_seconds']:.3f} s; separate reference evaluation: "
              f"{result['reference_seconds']:.3f} s; fine-step audit: {result['numerical_audit_seconds']:.3f} s.", "",
              "## Чувствительность к шагу", "",
              "B3 encounter_steps: production scale 0.5 и fine scale 0.25. Таблица показывает production минус fine. "
              "Все девять тел сохранены в numerical_audit.json; ниже только Earth и Moon. "
              "Это проверка чувствительности, а не строгая оценка погрешности или точности reference.", "",
              "| Начало | Горизонт, дни | Тело | Разница d, km | Разница времени, s |",
              "| --- | ---: | --- | ---: | ---: |"]
    for r in result["numerical_audit"]:
        if r["body_id"] in ("399", "301"):
            lines.append(f"| {r['start_date']} | {r['horizon_days']} | {r['body_id']} | "
                         f"{r['production_minus_fine_distance_km']:+.6f} | {r['production_minus_fine_time_seconds']:+.6f} |")
    lines += ["", "## Ограничения", "",
              "- Это уже просмотренные шесть объектов и повторные окна одного сильного события. Нулевые пропуски здесь не доказывают generalization.",
              "- Более точные расстояния в минимуме не доказывают достаточность всей траектории для заданного допуска. Рабочий селектор в этой ablation не строился.",
              "- Daily планетная интерполяция остаётся источником ошибки. Сгущение собственных asteroid states не улучшает исходную планетную эфемериду.",
              "- Сохранение accepted steps определяется локальным расстоянием прогнозных endpoints; это не сертифицированная гарантия против любого пропущенного encounter.",
              "- eta/Hill proxy относятся к предсказанному минимуму расстояния, а не к экстремумам за окно. Для Moon heliocentric Hill proxy не задаётся.",
              "- Общую пользу предварительного B3 надо проверять с его стоимостью: повторный полный rollout может устранить потенциальную экономию выбора модели.", "",
              "## Воспроизведение", "", "```bash",
              "env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv run --no-project --python-preference only-system python -B src/run_encounter_planets.py --root . --config configs/encounter_planets_pilot6.json",
              "```", "", "Контракт: [ENCOUNTER_PLANETS_CONTRACT.md](ENCOUNTER_PLANETS_CONTRACT.md).", ""]
    return "\n".join(lines)


def run(root: Path, config_path: Path, *, smoke: bool = False) -> dict:
    config = load_json(config_path)
    hashes = source_hashes(root)
    context = load_context(root, config)
    cases = make_cases(config, context)
    if smoke:
        cases = [c for c in cases if c["group"] == "apophis_lead_time"][:1]
        config = {**config, "timing_repeats": 1}
    output = root / config["artifacts"]["output_directory"]
    if smoke:
        output /= "smoke"
    output.mkdir(parents=True, exist_ok=True)
    run_metadata = {"schema_version": 2, "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "source_sha256_at_start": hashes, "config": config, "smoke": smoke,
                    "python": platform.python_version(), "platform": platform.platform()}
    write_json(output / "run_started.json", run_metadata)
    features, references, evaluation, timings, audit = [], [], [], [], []
    reference_seconds = audit_seconds = 0.0
    for case in cases:
        initial_row = next(r for r in context["daily_series"][case["object_id"]]
                           if r["epoch_tdb"] == case["start_date"] + "T00:00:00")
        start_jd, initial = initial_row["epoch_jd_tdb"], state_from_row(initial_row)
        began = time.perf_counter()
        reference = reference_minima(case, start_jd, context)
        reference_seconds += time.perf_counter() - began
        references.extend(reference)
        reference_by_body = {r["body_id"]: r for r in reference}
        trials = {s["strategy_id"]: [] for s in config["strategies"]}
        for repeat in range(config["timing_repeats"]):
            offset = repeat % len(config["strategies"])
            ordered = config["strategies"][offset:] + config["strategies"][:offset]
            for strategy in ordered:
                prediction = predict(initial, start_jd, case, strategy, config, context)
                prior = trials[strategy["strategy_id"]]
                if prior and prior[0]["minima"] != prediction["minima"]:
                    raise RuntimeError("Repeated forecast features changed")
                prior.append(prediction)
        for strategy in config["strategies"]:
            sid = strategy["strategy_id"]
            predictions = trials[sid]
            prediction = predictions[0]
            for minimum in prediction["minima"]:
                row = forecast_feature_record(case, sid, start_jd, minimum)
                row.update(force_model=strategy["force_model"], sampling=strategy["sampling"])
                features.append(row)
                evaluation.append({**case, **row, **compare_minimum(
                    minimum, reference_by_body[minimum["body_id"]], config["distance_thresholds_au"])})
            costs = [r["runtime_seconds"] for r in predictions]
            timings.append({**case, **strategy, "runtime_trials_seconds": costs,
                            "runtime_median_seconds": statistics.median(costs),
                            "runtime_min_seconds": min(costs), "runtime_max_seconds": max(costs),
                            "propagation_median_seconds": statistics.median(r["propagation_seconds"] for r in predictions),
                            "screening_median_seconds": statistics.median(r["screening_seconds"] for r in predictions),
                            **{key: prediction[key] for key in ("rk4_steps", "force_evaluations", "output_samples")}})
        if case["object_id"] == config["stress_object_id"] and (
                case["group"] == "apophis_lead_time" or case["horizon_days"] in (180, 365)):
            audit_config = config["numerical_audit"]
            strategy = next(s for s in config["strategies"] if s["strategy_id"] == audit_config["strategy_id"])
            began = time.perf_counter()
            fine = predict(initial, start_jd, case, strategy, config, context,
                           step_scale=audit_config["fine_step_scale"])
            audit_seconds += time.perf_counter() - began
            fine_by_body = {r["body_id"]: r for r in fine["minima"]}
            for production in trials[strategy["strategy_id"]][0]["minima"]:
                f = fine_by_body[production["body_id"]]
                audit.append({**case, "body_id": production["body_id"], "strategy_id": strategy["strategy_id"],
                              "production_step_scale": config["step_scale"], "fine_step_scale": audit_config["fine_step_scale"],
                              "production_distance_km": production["distance_km"], "fine_distance_km": f["distance_km"],
                              "production_minus_fine_distance_km": production["distance_km"] - f["distance_km"],
                              "production_minus_fine_time_seconds": (production["time_days"] - f["time_days"]) * context["day_s"],
                              "fine_minimum": f, "fine_rk4_steps": fine["rk4_steps"], "fine_output_samples": fine["output_samples"]})
        print(json.dumps({"completed": case["case_id"], "group": case["group"]}), flush=True)
        write_json(output / "checkpoint.json", {**run_metadata,
                   "features": features, "references": references, "evaluation_rows": evaluation,
                   "timings": timings, "numerical_audit": audit, "shared_load_seconds": context["load_seconds"],
                   "reference_seconds": reference_seconds, "numerical_audit_seconds": audit_seconds})
    # Never label an artifact as reproducible when code/config changed mid-run.
    if hashes != source_hashes(root):
        raise RuntimeError("Source files changed during the experiment; keep checkpoint and rerun")
    result = {"schema_version": 1, "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "config": config, "smoke": smoke, "cases": cases, "evaluation_rows": evaluation,
              "timings": timings, "numerical_audit": audit, "confusion": confusion_summary(evaluation, config),
              "shared_load_seconds": context["load_seconds"], "reference_seconds": reference_seconds,
              "numerical_audit_seconds": audit_seconds, "source_sha256_at_start": hashes,
              "python": platform.python_version(), "platform": platform.platform(),
              "coordinates": context["data"]["coordinates"], "constants": context["data"]["constants"]}
    for name, value in (("results.json", result), ("forecast_features.json", features),
                        ("reference_minima.json", references), ("numerical_audit.json", audit)):
        write_json(output / name, value)
    report = build_report(result)
    (output / "report.md").write_text(report)
    if not smoke:
        (root / config["artifacts"]["report"]).write_text(report)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    result = run(root, (root / args.config).resolve(), smoke=args.smoke)
    print(json.dumps({"cases": len(result["cases"]), "forecasts": len(result["timings"]),
                      "evaluation_rows": len(result["evaluation_rows"]), "audit_rows": len(result["numerical_audit"]),
                      "smoke": result["smoke"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

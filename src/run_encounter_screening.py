"""Evaluate cheap encounter screening with forecast/reference data separated."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

from encounter_screening import forecast_encounters, scan_body
from planetary_dynamics import EphemerisInterpolator
from run_eda import load_json, parse_horizons
from run_model_sufficiency import merge_rows
from run_nbody_baseline import load_perturbers
from run_physics_baselines import load_asteroid_series, state_from_row


def load_context(root: Path, config: dict) -> dict:
    started = time.perf_counter()
    data = load_json(root / config["data_config"])
    event = load_json(root / config["event_config"])
    constants = data["constants"]
    au_km, day_s = constants["au_km"], constants["day_s"]
    series = load_asteroid_series(root, data)
    daily_planets = load_perturbers(root, data)
    slug = event["event_id"].replace("-", "_")
    target = event["asteroid"]
    _, refined = parse_horizons(root / "data/raw/horizons_refined" / f"{slug}_{target['id']}.json", target["id"], target["name"])
    reference_series = {key: rows for key, rows in series.items()}
    reference_series[target["id"]] = merge_rows(series[target["id"]], refined)
    overrides = {}
    for body in event["bodies"]:
        _, daily = parse_horizons(root / "data/raw/horizons" / f"body_{body['id']}.json", body["id"], body["name"])
        _, high = parse_horizons(root / "data/raw/horizons_refined" / f"{slug}_{body['id']}.json", body["id"], body["name"])
        overrides[body["id"]] = merge_rows(daily, high)
    reference_planets = load_perturbers(root, data, overrides)
    return {"data": data, "daily_series": series, "daily_planets": daily_planets,
            "reference_asteroids": {key: EphemerisInterpolator.from_rows(rows) for key, rows in reference_series.items()},
            "reference_planets": reference_planets, "au_km": au_km, "day_s": day_s,
            "mu_sun": constants["mu_sun_km3_s2"] * day_s**2 / au_km**3,
            "load_seconds": time.perf_counter() - started}


def make_cases(config: dict, context: dict) -> list[dict]:
    cases = []
    for asteroid in context["data"]["asteroids"]:
        for horizon in config["horizons_days"]:
            cases.append({"object_id": asteroid["id"], "object_name": asteroid["name"],
                          "group": "main", "start_date": config["main_start_date"], "horizon_days": horizon})
    anchor = dt.date.fromisoformat(config["stress_anchor_date"])
    asteroid = next(a for a in context["data"]["asteroids"] if a["id"] == config["stress_object_id"])
    for lead in config["stress_lead_days"]:
        cases.append({"object_id": asteroid["id"], "object_name": asteroid["name"], "group": "apophis_lead_time",
                      "start_date": (anchor-dt.timedelta(days=lead)).isoformat(),
                      "horizon_days": lead+config["stress_days_after_anchor"], "lead_to_anchor_days": lead})
    for case in cases:
        case["case_id"] = f"{case['object_id']}:{case['start_date']}:{case['horizon_days']}"
        case["split"] = "engineering_regression"
    return cases


def reference_minima(case: dict, start_jd: float, context: dict) -> list[dict]:
    asteroid = context["reference_asteroids"][case["object_id"]]
    stop = start_jd + case["horizon_days"]
    entries = []
    for body in context["reference_planets"]:
        knots = sorted({start_jd, stop,
                        *(t for t in asteroid.epochs_jd_tdb if start_jd < t < stop),
                        *(t for t in body.ephemeris.epochs_jd_tdb if start_jd < t < stop)})
        times = [t - start_jd for t in knots]
        states = [asteroid.state_at(t) for t in knots]
        planets = [body.ephemeris.state_at(t) for t in knots]
        entry = scan_body(times, states, planets, body, context["mu_sun"], context["au_km"], context["day_s"], refine=True)
        entries.append({**case, **entry, "start_jd_tdb": start_jd})
    return entries


def compare_minimum(prediction: dict, reference: dict, thresholds_au: list[float]) -> dict:
    outcome = {}
    for threshold in thresholds_au:
        expected = reference["distance_au"] <= threshold
        alert = prediction["distance_au"] <= threshold
        outcome[f"{threshold:g}"] = "TP" if expected and alert else "FN" if expected else "FP" if alert else "TN"
    return {"outcomes": outcome,
            "distance_error_km": prediction["distance_km"] - reference["distance_km"],
            "time_error_hours": (prediction["time_days"] - reference["time_days"]) * 24,
            "relative_speed_error_km_s": prediction["relative_speed_km_s"] - reference["relative_speed_km_s"],
            "reference_distance_km": reference["distance_km"], "reference_time_days": reference["time_days"],
            "reference_bracket_gap_days": reference["bracket_gap_days_at_minimum"],
            "reference_boundary_minimum": reference["boundary_minimum"]}


def forecast_feature_record(case: dict, strategy_id: str, start_jd: float, minimum: dict) -> dict:
    """Export geometry with minimal join keys; keep evaluation design out."""
    metadata = {key: case[key] for key in ("case_id", "object_id", "start_date", "horizon_days")}
    return {**metadata, "strategy_id": strategy_id, "start_jd_tdb": start_jd, **minimum}


def confusion_summary(rows: list[dict], config: dict) -> list[dict]:
    summaries = []
    for group in sorted({r["group"] for r in rows}):
        for strategy in config["strategies"]:
            for threshold in config["distance_thresholds_au"]:
                counts = {name: 0 for name in ("TP", "FN", "FP", "TN")}
                for row in rows:
                    if row["group"] == group and row["strategy_id"] == strategy["strategy_id"]:
                        counts[row["outcomes"][f"{threshold:g}"]] += 1
                summaries.append({"group": group, "strategy_id": strategy["strategy_id"], "threshold_au": threshold,
                                  **counts, "recall": counts["TP"]/(counts["TP"]+counts["FN"]) if counts["TP"]+counts["FN"] else None,
                                  "precision": counts["TP"]/(counts["TP"]+counts["FP"]) if counts["TP"]+counts["FP"] else None})
    return summaries


def build_report(result: dict) -> str:
    lines = ["# Дешёвый прогноз сближений — pilot 6", "", f"Запуск: {result['generated_at_utc']}. Engineering regression; закрытого test нет.", "",
             "## Что проверяется", "",
             "B2 строит будущую траекторию только из начального состояния. Во всех трёх вариантах используются daily planetary ephemerides. "
             "daily_nodes проверяет расстояния в узлах; daily_hermite ищет минимум между суточными узлами; six_hour_hermite использует узлы каждые 6 часов и тот же поиск минимума.", "",
             "Reference рассчитывается отдельно по сохранённым Horizons states с объединением raw knots и имеющимся уточнением Apophis. "
             "Ни reference asteroid rows, ни известная сетка события не передаются в прогноз. Minima относятся к cubic-Hermite интерполянту, а не к строго известной непрерывной динамике.", "",
             f"Основных задач: {sum(c['group']=='main' for c in result['cases'])}; отдельных Apophis lead-time задач: {sum(c['group']!='main' for c in result['cases'])}; "
             "в каждой проверяются девять массивных тел. Вложенные горизонты и lead times одного события зависимы.", "",
             "Пороги 0.001/0.01/0.05 AU объявлены до запуска как exploratory distance alerts. Это не требования безопасности или точности траектории. "
             "Положительная задача означает наличие хотя бы одного сближения до порога в окне, включая начальный момент; это case/body detection, не полнота каталога отдельных событий.", "",
             "## Пропуски и ложные тревоги", "", "| Группа | Вариант | Порог, AU | TP | FN | FP | TN | Recall | Precision |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in result["confusion"]:
        recall = "—" if r["recall"] is None else f"{r['recall']:.3f}"
        precision = "—" if r["precision"] is None else f"{r['precision']:.3f}"
        lines.append(f"| {r['group']} | {r['strategy_id']} | {r['threshold_au']:g} | {r['TP']} | {r['FN']} | {r['FP']} | {r['TN']} | {recall} | {precision} |")
    lines += ["", "## Apophis–Earth: насколько заранее виден пролёт", "",
              "Все значения — минимум на заданном окне; ошибка времени знаковая. Повторные горизонты не считаются новыми независимыми событиями.", "",
              "| Начало | Горизонт, дни | Вариант | Reference min, km | Forecast min, km | Ошибка времени, h | Reference knot gap у min, s |",
              "| --- | ---: | --- | ---: | ---: | ---: | ---: |"]
    for r in result["evaluation_rows"]:
        if r["object_id"] == "99942" and r["body_id"] == "399" and (r["group"] != "main" or r["horizon_days"] in (180,365)):
            lines.append(f"| {r['start_date']} | {r['horizon_days']} | {r['strategy_id']} | {r['reference_distance_km']:.3f} | {r['distance_km']:.3f} | {r['time_error_hours']:+.4f} | {r['reference_bracket_gap_days']*86400:.1f} |")
    lines += ["", "## Цена предварительного расчёта", "",
              "Каждый горизонт выполнен отдельным вызовом. Стоимость включает B2 propagation, планетную интерполяцию и весь поиск минимума; "
              "три повтора выполняются последовательно с изменением порядка вариантов. Приведена медиана per-case median по основным объектам.", "",
              "| Вариант | Горизонт, дни | Total, s | Propagation, s | Screening, s |",
              "| --- | ---: | ---: | ---: | ---: |"]
    for strategy in result["config"]["strategies"]:
        for horizon in result["config"]["horizons_days"]:
            selected = [r for r in result["timings"] if r["group"] == "main" and r["strategy_id"] == strategy["strategy_id"] and r["horizon_days"] == horizon]
            if selected:
                lines.append(f"| {strategy['strategy_id']} | {horizon} | {statistics.median(r['runtime_median_seconds'] for r in selected):.5f} | {statistics.median(r['propagation_median_seconds'] for r in selected):.5f} | {statistics.median(r['screening_median_seconds'] for r in selected):.5f} |")
    lines += ["", f"Общая загрузка: {result['shared_load_seconds']:.3f} s; offline reference evaluation: {result['reference_seconds']:.3f} s. "
              "Эти затраты показаны отдельно. Сравнение с новым полноценным rollout после выбора модели ещё не выполнено.", "",
              "## Ограничения и следующий шаг", "",
              "- Промежуточный минимум восстанавливает геометрию грубой траектории, но не добавляет в B2 планетную гравитацию или gravitational focusing.",
              "- Шесть объектов уже изучены; сильный Earth encounter представлен одним событием. Ни recall, ни точность не являются оценкой на новых объектах.",
              "- Reference преимущественно daily; существующее пяти-минутное уточнение локально. Нужны новые независимо отобранные события и контроль качества reference.",
              "- Boundary minima отмечены в JSON: это минимум внутри окна, а не обязательно законченный пролёт. Равенство нулю сохраняется явно; eta при сингулярности — null.",
              "- eta в JSON включает direct и indirect heliocentric terms на предсказанном минимуме. rho — Sun-relative Hill proxy, не строгая граница; для Moon он не задаётся. Это значения у минимума расстояния, не экстремумы eta/rho за всё окно.",
              "- Реализован предварительный screening, а не выбор достаточной force model. Малые дополнительные возмущения отложены согласно текущему порядку работ.", "",
              "## Воспроизведение", "", "```bash",
              "env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv run --no-project --python-preference only-system python -B src/run_encounter_screening.py --root . --config configs/encounter_screening_pilot6.json",
              "```", ""]
    return "\n".join(lines)


def run(root: Path, config_path: Path, *, smoke: bool = False) -> dict:
    config = load_json(config_path)
    context = load_context(root, config)
    cases = make_cases(config, context)
    if smoke:
        cases = [c for c in cases if c["group"] == "apophis_lead_time"][:1]
        config = {**config, "timing_repeats": 1}
    out = root / config["artifacts"]["output_directory"]
    if smoke:
        out /= "smoke"
    out.mkdir(parents=True, exist_ok=True)
    source_paths = [*sorted((root/"src").glob("*.py")), *sorted((root/"configs").glob("*.json")), *sorted((root/"data/checksums").glob("*.json"))]
    hashes = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}
    features, references, evaluation, timings = [], [], [], []
    reference_seconds = 0.0
    for case in cases:
        start = next(row for row in context["daily_series"][case["object_id"]] if row["epoch_tdb"] == case["start_date"] + "T00:00:00")
        start_jd, initial = start["epoch_jd_tdb"], state_from_row(start)
        begun = time.perf_counter()
        reference = reference_minima(case, start_jd, context)
        reference_seconds += time.perf_counter()-begun
        references.extend(reference)
        by_body = {r["body_id"]: r for r in reference}
        first, trials = {}, {s["strategy_id"]: [] for s in config["strategies"]}
        for repeat in range(config["timing_repeats"]):
            ordered = config["strategies"][repeat:] + config["strategies"][:repeat]
            for strategy in ordered:
                prediction = forecast_encounters(initial, start_jd, case["horizon_days"], context["daily_planets"], context["mu_sun"],
                    cadence_days=strategy["cadence_days"], refine=strategy["refine"], default_step_days=config["default_step_days"],
                    step_scale=config["step_scale"], au_km=context["au_km"], day_s=context["day_s"])
                sid = strategy["strategy_id"]
                if repeat == 0:
                    first[sid] = prediction
                elif first[sid]["minima"] != prediction["minima"]:
                    raise RuntimeError("Repeated forecast features changed")
                trials[sid].append(prediction)
        for strategy in config["strategies"]:
            sid = strategy["strategy_id"]
            predicted = first[sid]
            for minimum in predicted["minima"]:
                row = forecast_feature_record(case, sid, start_jd, minimum)
                features.append(row)
                evaluation.append({**case, **row, **compare_minimum(minimum, by_body[minimum["body_id"]], config["distance_thresholds_au"])})
            costs = [p["runtime_seconds"] for p in trials[sid]]
            timings.append({**case, "strategy_id": sid, "runtime_trials_seconds": costs,
                            "runtime_median_seconds": statistics.median(costs), "runtime_min_seconds": min(costs), "runtime_max_seconds": max(costs),
                            "propagation_median_seconds": statistics.median(p["propagation_seconds"] for p in trials[sid]),
                            "screening_median_seconds": statistics.median(p["screening_seconds"] for p in trials[sid]),
                            "rk4_steps": predicted["rk4_steps"], "force_evaluations": predicted["force_evaluations"], "output_samples": predicted["output_samples"]})
        print(json.dumps({"completed": case["case_id"], "group": case["group"]}), flush=True)
        (out/"checkpoint.json").write_text(json.dumps({"features": features, "references": references, "timings": timings}, allow_nan=False)+"\n")
    result = {"schema_version": 1, "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "config": config,
              "smoke": smoke, "cases": cases, "evaluation_rows": evaluation, "timings": timings,
              "confusion": confusion_summary(evaluation, config), "shared_load_seconds": context["load_seconds"],
              "reference_seconds": reference_seconds, "source_sha256_at_start": hashes, "python": sys.version.split()[0],
              "coordinates": context["data"]["coordinates"], "constants": context["data"]["constants"]}
    for name, value in (("forecast_features.json", features), ("reference_minima.json", references), ("results.json", result)):
        (out/name).write_text(json.dumps(value, indent=2, allow_nan=False)+"\n")
    report = build_report(result)
    (out/"report.md").write_text(report)
    if not smoke:
        (root/config["artifacts"]["report"]).write_text(report)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    result = run(args.root.resolve(), args.config.resolve(), smoke=args.smoke)
    print(json.dumps({"cases": len(result["cases"]), "evaluation_rows": len(result["evaluation_rows"]), "smoke": result["smoke"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

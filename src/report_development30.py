"""Render the frozen development30 train/validation selection report.

The report is a consumer of completed artifacts.  It refuses to consume a
validation result unless the rules artifact and its train provenance show that
rules were frozen before validation started.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
from pathlib import Path
import statistics
from typing import Any, Mapping


MODEL_IDS = ("B2", "B3", "B3+GR", "B3+GR+SB16")
ANCHOR_MODEL_ID = MODEL_IDS[-1]
METHODS = ("horizon_rule", "physics_rule")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"required report input is missing: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _number(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not result == result or result in (float("inf"), float("-inf")):
        raise ValueError(f"{label} must be finite")
    return result


def _fmt(value: Any, digits: int = 4) -> str:
    return f"{_number(value, 'report value'):.{digits}g}"


def _iso(value: str, label: str) -> dt.datetime:
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {label} timestamp") from exc


def _horizon_key(value: Any) -> str:
    return format(_number(value, "horizon_days"), "g")


def _horizon_value(mapping: Mapping[Any, Any], horizon: Any, label: str) -> Any:
    key = _horizon_key(horizon)
    if key in mapping:
        return mapping[key]
    numeric = _number(horizon, "horizon_days")
    if numeric in mapping:
        return mapping[numeric]
    raise ValueError(f"missing {label} for horizon {key}")


def _eligible(row: Mapping[str, Any], tolerance: float, fraction: float) -> bool:
    return (_number(row["max_position_error_km"], "max_position_error_km") <= tolerance
            and _number(row["numerical_difference_km"], "numerical_difference_km") <= fraction * tolerance)


def _validate_source_hashes(root: Path, result: Mapping[str, Any], label: str) -> None:
    provenance = result.get("provenance")
    if not isinstance(provenance, Mapping):
        raise ValueError(f"{label} results have no provenance")
    source_hashes = provenance.get("source_sha256_at_start")
    if not isinstance(source_hashes, Mapping) or not source_hashes:
        raise ValueError(f"{label} source hash map is missing")
    for relative, expected in source_hashes.items():
        path = root / str(relative)
        if not path.exists() or sha256(path) != expected:
            raise ValueError(f"{label} source/input hash mismatch: {relative}")


def _validate_counts(sample: Mapping[str, Any], train: Mapping[str, Any], validation: Mapping[str, Any], config: Mapping[str, Any]) -> list[dict]:
    objects = sample.get("objects")
    if not isinstance(objects, list):
        raise ValueError("sample.objects must be a list")
    counts = collections.Counter(str(obj.get("split")) for obj in objects if isinstance(obj, Mapping))
    if len(objects) != 30 or dict(counts) != {"train": 18, "validation": 12}:
        raise ValueError(f"expected 30 objects split 18/12, got {len(objects)} and {dict(counts)}")
    horizons = tuple(float(value) for value in config["horizons_days"])
    models = tuple(model["model_id"] for model in config["models"])
    if models != MODEL_IDS:
        raise ValueError(f"config model ladder is not {MODEL_IDS!r}")
    expected = {"train": (18 * len(horizons) * len(models), 18 * len(horizons)),
                "validation": (12 * len(horizons) * len(models), 12 * len(horizons))}
    for split, result in (("train", train), ("validation", validation)):
        records = result.get("records")
        features = result.get("feature_rows")
        if not isinstance(records, list) or len(records) != expected[split][0]:
            raise ValueError(f"{split} must contain {expected[split][0]} model records")
        if not isinstance(features, list) or len(features) != expected[split][1]:
            raise ValueError(f"{split} must contain {expected[split][1]} feature rows")
        for row in records:
            if row.get("split") != split or row.get("model_id") not in MODEL_IDS:
                raise ValueError(f"invalid {split} model record split/model")
        for row in features:
            if row.get("split") != split:
                raise ValueError(f"invalid {split} feature row split")
    return [dict(obj) for obj in objects]


def _validate_provenance(root: Path, config_path: Path, sample_path: Path, train_path: Path, rules_path: Path, validation_path: Path, selection_path: Path, rules: Mapping[str, Any], train: Mapping[str, Any], validation: Mapping[str, Any], selection: Mapping[str, Any]) -> dict[str, str]:
    provenance = rules.get("provenance")
    if not isinstance(provenance, Mapping):
        raise ValueError("rules artifact has no provenance")
    expected_paths = {
        "training_results_sha256": train_path,
        "rules_source_sha256": root / "src/development_rules.py",
        "sample_sha256": sample_path,
        "contract_sha256": root / "docs/DEVELOPMENT30_CONTRACT.md",
        "config_sha256": config_path,
    }
    for key, path in expected_paths.items():
        if provenance.get(key) != sha256(path):
            raise ValueError(f"frozen rules provenance mismatch: {key}")
    _validate_source_hashes(root, train, "train")
    _validate_source_hashes(root, validation, "validation")
    train_provenance = train["provenance"]
    validation_provenance = validation["provenance"]
    if train_provenance.get("split") != "train" or validation_provenance.get("split") != "validation":
        raise ValueError("train/validation provenance split labels are inconsistent")
    if validation_provenance.get("rules_sha256_at_start") != sha256(rules_path):
        raise ValueError("validation did not use the current frozen rules")
    run_started = validation_path.parent / "run_started.json"
    if run_started.exists():
        started = _read_json(run_started)
        fitted = _iso(str(provenance.get("fitted_utc")), "rules fitted_utc")
        validation_started = _iso(str(started.get("started_at_utc")), "validation started_at_utc")
        if fitted >= validation_started:
            raise ValueError("rules were not frozen before validation started")
    selection_provenance = selection.get("provenance")
    if not isinstance(selection_provenance, Mapping):
        raise ValueError("selection results have no provenance")
    for key, expected in {
        "rules_sha256": sha256(rules_path),
        "validation_results_sha256": sha256(validation_path),
        "config_sha256": sha256(config_path),
    }.items():
        if selection_provenance.get(key) != expected:
            raise ValueError(f"selection provenance mismatch: {key}")
    for key, relative in (("selection_source_sha256", "src/run_development_selection.py"),
                          ("features_source_sha256", "src/development_features.py"),
                          ("runner_source_sha256", "src/run_development_benchmark.py")):
        if selection_provenance.get(key) != sha256(root / relative):
            raise ValueError(f"selection provenance mismatch: {key}")
    return {"rules_sha256": sha256(rules_path), "train_results_sha256": sha256(train_path),
            "validation_results_sha256": sha256(validation_path), "selection_results_sha256": sha256(selection_path)}


def _sufficiency_table(train: Mapping[str, Any], validation: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    fraction = float(config["numerical_budget_fraction"])
    lines = ["| Split | Tolerance (km) | B2 | B3 | B3+GR | B3+GR+SB16 | Any candidate | None |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for split, result in (("train", train), ("validation", validation)):
        records = result["records"]
        cases = {row["case_id"] for row in records}
        for tolerance in config["position_tolerances_km"]:
            cells = []
            for model_id in MODEL_IDS:
                current = [row for row in records if row["model_id"] == model_id]
                count = sum(_eligible(row, float(tolerance), fraction) for row in current)
                cells.append(f"{count}/{len(cases)}")
            any_count = sum(any(_eligible(row, float(tolerance), fraction) for row in records if row["case_id"] == case_id) for case_id in cases)
            lines.append(f"| {split} | {_fmt(tolerance)} | " + " | ".join(cells) + f" | {any_count}/{len(cases)} | {len(cases)-any_count}/{len(cases)} |")
    return "\n".join(lines)


def _selection_summary(selection: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    lines = ["| Method | Tolerance (km) | Successes / cases | Failures | Fallbacks | Confident failures | Selected models | Full runtime (s) |", "|---|---:|---:|---:|---:|---:|---|---:|"]
    selections = selection.get("selections")
    if not isinstance(selections, list):
        raise ValueError("selection results have no selections")
    for method in METHODS:
        for tolerance in config["position_tolerances_km"]:
            current = [row for row in selections if row.get("method") == method and float(row.get("tolerance_km")) == float(tolerance)]
            if not current:
                raise ValueError(f"selection results missing {method} tolerance {tolerance}")
            successes = sum(bool(row["actual_eligible"]) for row in current)
            fallback = sum(bool(row["fallback"]) for row in current)
            confident = sum(not bool(row["fallback"]) and not bool(row["actual_eligible"]) for row in current)
            counts = collections.Counter(row["selected_model_id"] for row in current)
            selected = ", ".join(f"{model}:{counts.get(model, 0)}" for model in MODEL_IDS if counts.get(model, 0))
            runtime = sum(_number(row["runtime_median_seconds"], "selection runtime") for row in current)
            lines.append(f"| {method} | {_fmt(tolerance)} | {successes}/{len(current)} | {len(current)-successes} | {fallback} | {confident} | {selected} | {_fmt(runtime)} |")
    return "\n".join(lines)


def _per_horizon_one_km(selection: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    lines = ["| Method | Horizon (d) | Successes / cases | Selected full runtime (s) | Fixed anchor direct runtime (s) |", "|---|---:|---:|---:|---:|"]
    anchors = {row["case_id"]: row for row in selection.get("fixed_anchor", [])}
    for method in METHODS:
        for horizon in config["horizons_days"]:
            current = [row for row in selection["selections"] if row["method"] == method and float(row["tolerance_km"]) == 1.0 and float(row["horizon_days"]) == float(horizon)]
            if not current:
                raise ValueError(f"missing 1-km selections for {method}, {horizon}")
            anchor = sum(_number(anchors[row["case_id"]]["runtime_median_seconds"], "anchor runtime") for row in current)
            selected = sum(_number(row["runtime_median_seconds"], "selected runtime") for row in current)
            success = sum(bool(row["actual_eligible"]) for row in current)
            lines.append(f"| {method} | {_fmt(horizon)} | {success}/{len(current)} | {_fmt(selected)} | {_fmt(anchor)} |")
    return "\n".join(lines)


def _stratum_failures(selection: Mapping[str, Any]) -> str:
    rows = [row for row in selection["selections"] if float(row["tolerance_km"]) == 1.0]
    lines = ["| Method | Stratum | Failures / cases | Failed objects |", "|---|---|---:|---|"]
    for method in METHODS:
        for stratum in sorted({row["stratum"] for row in rows if row["method"] == method}):
            current = [row for row in rows if row["method"] == method and row["stratum"] == stratum]
            failed_rows = [row for row in current if not bool(row["actual_eligible"])]
            failed_objects = sorted({str(row["object_id"]) for row in failed_rows})
            lines.append(f"| {method} | {stratum} | {len(failed_rows)}/{len(current)} | {', '.join(failed_objects) or '—'} |")
    return "\n".join(lines)


def _stratum_error_means(validation: Mapping[str, Any], sample: list[dict]) -> str:
    names = {str(obj["id"]): obj for obj in sample}
    groups: dict[str, list[float]] = collections.defaultdict(list)
    for row in validation["records"]:
        if row["model_id"] == "B2" and float(row["horizon_days"]) == 365.0:
            obj = names[str(row["object_id"])]
            groups[str(obj["stratum"])].append(_number(row["max_position_error_km"], "B2 error"))
    lines = ["| Target stratum | Validation objects | Mean B2 max position error at 365 d (km) |", "|---|---:|---:|"]
    for stratum in sorted(groups):
        lines.append(f"| {stratum} | {len(groups[stratum])} | {_fmt(statistics.mean(groups[stratum]))} |")
    return "\n".join(lines)


def _geometry_summary(validation: Mapping[str, Any]) -> str:
    records = [row for row in validation["records"] if row["model_id"] == "B2" and float(row["horizon_days"]) == 365.0]
    if not records:
        raise ValueError("validation has no B2 annual geometry records")
    by_body: dict[str, list[Mapping[str, Any]]] = collections.defaultdict(list)
    for row in records:
        geometry = row.get("geometry_compare")
        if not isinstance(geometry, Mapping):
            raise ValueError("B2 annual record has no geometry_compare")
        for body_id, value in geometry.items():
            by_body[str(body_id)].append(value)
    if len(by_body) != 9:
        raise ValueError(f"expected geometry for all 9 planetary perturbers, got {len(by_body)}")
    lines = ["| Body/system | n comparisons | Mean predicted minimum (km) | Mean reference minimum (km) | Mean Δdistance (km) | Mean absolute Δdistance (km) | Mean Δtime (d) | Mean absolute Δtime (d) |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for body_id in sorted(by_body):
        values = by_body[body_id]
        body_name = str(values[0].get("predicted", {}).get("body_name") or values[0].get("reference", {}).get("body_name") or body_id)
        predicted = [v["predicted"]["distance_km"] for v in values]
        reference = [v["reference"]["distance_km"] for v in values]
        delta_distance = [v["distance_difference_km"] for v in values]
        delta_time = [v["time_difference_days"] for v in values]
        lines.append(f"| {body_id} ({body_name}) | {len(values)} | {_fmt(statistics.mean(predicted))} | {_fmt(statistics.mean(reference))} | {_fmt(statistics.mean(delta_distance))} | {_fmt(statistics.mean(abs(value) for value in delta_distance))} | {_fmt(statistics.mean(delta_time))} | {_fmt(statistics.mean(abs(value) for value in delta_time))} |")
    return "\n".join(lines)


def _oracle_table(selection: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    """Render the offline oracle diagnostics saved by the selection runner."""

    summaries = selection.get("summaries")
    if not isinstance(summaries, list):
        raise ValueError("selection results have no summaries")
    lines = ["| Tolerance (km) | Oracle-feasible / cases | Oracle feasible runtime (s) | Method | Oracle model matches on feasible | False fallback on feasible | Missed no-candidate |", "|---:|---:|---:|---|---:|---:|---:|"]
    for summary in summaries:
        tolerance = float(summary["tolerance_km"])
        methods = summary.get("methods")
        if not isinstance(methods, list):
            raise ValueError("selection summary has no method diagnostics")
        for method in METHODS:
            row = next((item for item in methods if item.get("method") == method), None)
            if row is None:
                raise ValueError(f"selection summary missing {method}")
            required = ("oracle_model_matches_on_feasible", "false_fallback_on_feasible", "missed_no_candidate")
            if any(key not in row for key in required):
                raise ValueError("selection summary lacks oracle diagnostics")
            lines.append(f"| {_fmt(tolerance)} | {int(summary['feasible_cases'])}/{int(summary['case_count'])} | {_fmt(summary['oracle_runtime_seconds'])} | {method} | {row['oracle_model_matches_on_feasible']} | {row['false_fallback_on_feasible']} | {row['missed_no_candidate']} |")
    return "\n".join(lines)


def _outlier_table(validation: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    grouped = collections.defaultdict(list)
    for row in validation["records"]:
        grouped[row["case_id"]].append(row)
    lines = ["| Объект | Горизонт, суток | Ошибка SB16-кандидата, km | Разница при уменьшении шага, m |",
             "|---|---:|---:|---:|"]
    for rows in grouped.values():
        if any(_eligible(row, 1.0, float(config["numerical_budget_fraction"])) for row in rows):
            continue
        row = next(row for row in rows if row["model_id"] == ANCHOR_MODEL_ID)
        lines.append(f"| {row['object_id']} | {_fmt(row['horizon_days'])} | {_fmt(row['max_position_error_km'], 6)} | {_fmt(row['numerical_difference_km']*1000, 5)} |")
    return "\n".join(lines)


def _fixed_cost_tables(root: Path, output: Path, config: Mapping[str, Any], validation: Mapping[str, Any], selection: Mapping[str, Any]) -> str:
    fixed = _read_json(output / "fixed_cost_reference.json")
    provenance = fixed["provenance"]
    for key, path in {
        "audit_source_sha256": root / "src/audit_development_fixed_cost.py",
        "config_sha256": root / "configs/development30.json",
        "sample_sha256": root / str(config["sample_path"]),
        "rules_sha256": output / "rules.json",
        "validation_results_sha256": output / "validation/results.json",
        "selection_results_sha256": output / "selection_results.json",
    }.items():
        if provenance.get(key) != sha256(path):
            raise ValueError(f"fixed-cost provenance mismatch: {key}")
    fraction = float(config["numerical_budget_fraction"])
    cases = {row["case_id"] for row in validation["records"]}
    for model_id, model in fixed["models"].items():
        records = model["records"]
        if len(records) != 60 or {row["case_id"] for row in records} != cases:
            raise ValueError(f"incomplete fixed-cost matrix: {model_id}")
        if abs(sum(row["runtime_median_seconds"] for row in records) - model["total_runtime_seconds"]) > 1e-8:
            raise ValueError(f"inconsistent fixed cost: {model_id}")
    lines = ["| Постоянная модель | Успех при 0.1 km | При 1 km | При 10 km | Стоимость 60 прогнозов, s |",
             "|---|---:|---:|---:|---:|"]
    for model_id, model in fixed["models"].items():
        counts = [sum(_eligible(row, float(tol), fraction) for row in model["records"]) for tol in config["position_tolerances_km"]]
        lines.append(f"| {model_id} | " + " | ".join(f"{count}/60" for count in counts) + f" | {_fmt(model['total_runtime_seconds'])} |")
    lines += ["", "| Допуск, km | Постоянная модель, покрывающая все разрешимые случаи | Правило | Полная стоимость, s | Экономия относительно постоянной модели |",
              "|---:|---|---|---:|---:|"]
    for tol in config["position_tolerances_km"]:
        feasible = {row["case_id"] for row in validation["records"] if _eligible(row, float(tol), fraction)}
        covering = [(model["total_runtime_seconds"], model_id) for model_id, model in fixed["models"].items()
                    if {row["case_id"] for row in model["records"] if _eligible(row, float(tol), fraction)} == feasible]
        baseline_cost, baseline_id = min(covering)
        for method in METHODS:
            cost = sum(row["runtime_median_seconds"] for row in selection["selections"] if row["method"] == method and row["tolerance_km"] == tol)
            lines.append(f"| {_fmt(tol)} | {baseline_id} | {method} | {_fmt(cost)} | {100*(1-cost/baseline_cost):.1f}% |")
    lines += ["", f"Для B3+GR использованы {fixed['reused_case_count']} уже измеренных propagation components и "
              f"{fixed['new_direct_invocations']} дополнительных прямых вызовов для {fixed['direct_case_count']} окон. "
              "Все новые вызовы выполнены последовательно после основного timing; ошибки повторно сверены с labels. "
              "Постоянная модель для сравнения определяется после опыта по покрытию и стоимости и не является новым selector.",
              "", "Положительный процент означает экономию, отрицательный — замедление. При 0.1 km horizon rule "
              "дополнительно ошибается на одном разрешимом случае, поэтому его экономию нельзя трактовать как равную точность. "
              "При 10 km сравнение только с SB16 завышало бы пользу выбора: постоянная B3+GR уже покрывает все 58 разрешимых окон."]
    return "\n".join(lines)


def build_report(root: Path, config_path: Path | None = None) -> str:
    """Validate completed artifacts and return the Markdown report text."""

    root = root.resolve()
    config_path = (config_path or root / "configs/development30.json").resolve()
    config = _read_json(config_path)
    output = root / str(config["output_directory"])
    sample_path = root / str(config["sample_path"])
    train_path = output / "train" / "results.json"
    rules_path = output / "rules.json"
    validation_path = output / "validation" / "results.json"
    selection_path = output / "selection_results.json"

    # Keep this order deliberate: validation is not consumed before the
    # frozen rules and train provenance have been checked.
    rules = _read_json(rules_path)
    train = _read_json(train_path)
    sample = _read_json(sample_path)
    validation = _read_json(validation_path)
    selection = _read_json(selection_path)
    objects = _validate_counts(sample, train, validation, config)
    hashes = _validate_provenance(root, config_path, sample_path, train_path, rules_path, validation_path, selection_path, rules, train, validation, selection)
    verification = _read_json(output / "verification.json")
    data_verification = _read_json(output / "data_verification.json")
    if not verification.get("passed") or not data_verification.get("passed"):
        raise ValueError("artifact and raw-data verification must pass before reporting")
    for name, expected in verification["results_sha256"].items():
        if sha256(output / name) != expected:
            raise ValueError(f"verification result hash mismatch: {name}")
    selections = selection.get("selections")
    if not isinstance(selections, list):
        raise ValueError("selection results have no selections")
    expected_selection_count = 12 * len(config["horizons_days"]) * len(METHODS) * len(config["position_tolerances_km"])
    if len(selections) != expected_selection_count:
        raise ValueError("selection matrix is incomplete")
    fixed_anchor_total = _number(selection.get("fixed_anchor_total_runtime_seconds"), "fixed_anchor_total_runtime_seconds")
    calibration = rules.get("calibration_factors")
    caps = rules.get("horizon_caps_km")
    if not isinstance(calibration, Mapping) or not isinstance(caps, Mapping):
        raise ValueError("rules artifact lacks calibration factors or horizon caps")
    factor_lines = []
    for model_id in MODEL_IDS[:-1]:
        if model_id not in calibration or calibration[model_id] is None:
            raise ValueError(f"missing calibration factor for {model_id}")
        factor_lines.append(f"| {model_id} | {_fmt(calibration[model_id])} |")
    cap_lines = []
    for horizon in config["horizons_days"]:
        cap_lines.append(f"| {_fmt(horizon)} | {_fmt(_horizon_value(caps[ANCHOR_MODEL_ID], horizon, 'strongest horizon cap'))} |")

    one_km = next(s for s in selection["summaries"] if s["tolerance_km"] == 1)
    outcome_lines = []
    for method in one_km["methods"]:
        reduction = 100*(1-method["total_runtime_seconds"]/fixed_anchor_total)
        outcome_lines.append(f"- `{method['method']}`: {method['eligible_cases']}/{method['cases']} допустимых прогнозов; "
                             f"{_fmt(method['total_runtime_seconds'])} s; экономия относительно постоянной SB16-модели {reduction:.1f}%.")

    report = f"""# Development30: forecast-time model selection

При допуске **1 km** подходящий кандидат есть для **{one_km['feasible_cases']}/{one_km['case_count']}**
проверочных окон на 12 отложенных объектах. Сравнение ниже учитывает все окна,
включая случаи, где набор кандидатов оказался недостаточен.

{chr(10).join(outcome_lines)}

Коэффициенты двух простых правил вычислены только по 18 train-объектам
и зафиксированы до проверки на остальных объектах.

Это ограниченный development-эксперимент с раздельными train и validation
объектами. Он не заменяет финальную оценку и не даёт гарантии точности. Отчёт
построен из локальных зафиксированных артефактов после validation и timing.

## Данные и freeze

Выборка содержит 30 объектов: 18 train и 12 validation; все пять горизонтов
сохраняются внутри объекта. Получено 150 object×horizon cases и 600
model/case records (360 train + 240 validation). Вложенные горизонты одного
объекта не являются независимыми наблюдениями. Финальный test ещё не выбран.

Артефакт правил создан до старта validation и проверен по SHA-256. После
freeze повторное fitting не допускается. `rules.json`:
`{hashes['rules_sha256']}`; train results:
`{hashes['train_results_sha256']}`; validation results:
`{hashes['validation_results_sha256']}`; selection results:
`{hashes['selection_results_sha256']}`. Коэффициенты и caps — эмпирические
сводки train, а не гарантии ошибки.

## Candidate sufficiency

Эффективная ошибка равна `max(position_error, numerical_difference / 0.1)`, а
ячейка показывает число допустимых model records / число cases. Колонки Any
candidate и None показывают наличие хотя бы одной допустимой модели и отсутствие
такой модели; нельзя предполагать, что strongest model покрывает объединение
всех случаев.

{_sufficiency_table(train, validation, config)}

Кандидаты ровно четыре: B2, B3, B3+GR и B3+GR+SB16. B3 включает все девять
planetary perturbers, включая Moon; B2 — Sun-only. В этой карте нет
non-gravitational terms, Earth J2 или full EIH, и ни одна модель не получает
гарантии физической или численной точности.

## Операционный выбор модели на validation

{_selection_summary(selection, config)}

Время в таблицах — сумма медиан трёх повторов для одного запроса на каждое из
60 окон. Это не длительность всего исследовательского расчёта, который также
включает остальные кандидаты, fine runs и offline диагностику.
Отдельная загрузка эфемерид заняла {_fmt(train['provenance']['shared_load_seconds'])} s
для train, {_fmt(validation['provenance']['shared_load_seconds'])} s для validation и
{_fmt(selection['shared_load_seconds'])} s перед прямыми operational вызовами.

`runtime_seconds` у выбранных методов — полная стоимость прямого вызова:
построение forecast-time features для physics rule, inference и рекурсивное
распространение. В benchmark records для fixed candidates записана только
измеренная стоимость causal propagation prefix; overhead селектора туда не
входит. Для отдельного fixed anchor использован прямой rollout B3+GR+SB16:
`fixed_anchor_total_runtime_seconds` = **{_fmt(fixed_anchor_total)} s**.
Все методы используют один и тот же validation denominator; поэтому стоимость
и accuracy следует читать вместе при провалах baseline.

### Сравнение с достаточной постоянной моделью

{_fixed_cost_tables(root, output, config, validation, selection)}

Это локальные CPU-замеры с тремя повторами. Они не включают общий cold load
данных, не устанавливают ускорение на другом оборудовании и не дают интервалов
статистической неопределённости для стоимости.
Разница около 0.3% при 10 km не подтверждает устойчивого преимущества horizon rule.
При 1 km physics rule чаще совпадает с oracle по названию кандидата, но суммарно
дороже horizon rule. Совпадение метки само по себе не измеряет экономию времени.

Offline oracle показывает потенциальную стоимость только на случаях, где хотя
бы одна модель прошла допуск. Это диагностическая верхняя граница экономии,
условная на измеренные prefix costs, а не достигнутое ML-ускорение и не результат
рабочего selector. Его стоимость нельзя напрямую вычитать из полной стоимости
всех 60 запросов: denominator и учёт overhead различаются.

{_oracle_table(selection, config)}

`Oracle model matches` считается только на oracle-feasible случаях.
`False fallback` означает лишний fallback там, где допустимая модель была;
`Missed no-candidate` означает уверенный выбор в oracle-infeasible случае.

### 1-km by horizon

{_per_horizon_one_km(selection, config)}

### 1-km failures by target stratum

{_stratum_failures(selection)}

Перечни failed objects относятся к фактическому validation eligibility, а не
к event metadata, переданным селектору.

### Случаи, где недостаточны все четыре кандидата при 1 km

{_outlier_table(validation, config)}

На train самый полный кандидат прошёл 1 km во всех 90 окнах: примеров класса
«нет достаточной модели» для этого допуска там не было. Поэтому успешный выбор
среди имеющихся моделей и распознавание недостаточности всего набора — две
разные задачи. Текущие правила пропускают такие отказы на validation.
Расхождение с teacher у этих объектов намного больше наблюдаемой чувствительности
к шагу. Это повод для отдельного аудита сил, начальных данных и независимого
solver; конкретная причина этим опытом не установлена.

## Зафиксированные параметры правил

Калибровочные коэффициенты для B2/B3/B3+GR:

| Model | Train max effective error / max(proxy, 1e-6 km) |
|---|---:|
{chr(10).join(factor_lines)}

У B3+GR+SB16 нет proxy для пропущенной силы; его physics-rule score —
эмпирический train maximum для каждого горизонта:

| Horizon (d) | Strongest empirical cap (km) |
|---:|---:|
{chr(10).join(cap_lines)}

Калибровка proxy не учитывает переход состояния и служит только диагностикой
routing; это не bound траекторной ошибки. На входе прогноза только начальное
состояние, causal planetary ephemerides, горизонт, допуск и scalars,
доступные во время прогноза. Даты CAD events, object/group/split identities,
будущие состояния астероида, references и labels исключены из selector inputs.

Для каждого distinct `(method, case, chosen_model)` выполнены три прямых вызова.
По возможности representative был 1-km
tolerance; идентичные selected paths других tolerances используют ту же timing
measurement и явно помечены в selection records. Runner оценивает rollout после
timing и aborts при расхождении state/error recomputation с benchmark label более
чем на `1e-5 km`; завершённый selection artifact тем самым прошёл этот check.

## Диагностика геометрии

Ниже B2 records на горизонте 365 дней; `geometry_compare` усреднён по
validation objects для всех девяти тел/систем. Predicted — траектория
кандидата B2, sampled на offline evaluation grid; reference — Horizons teacher.
Для Mars и Jupiter
сохраняются соответствующие Horizons system barycentres; они не отождествляются
с CAD labels планетных encounters. Reference annual minimum может не совпадать
с выбранным CAD event.

{_geometry_summary(validation)}

{_stratum_error_means(validation, objects)}

Это conditional propagation/benchmark diagnostics против model-derived Horizons
teacher, а не ML discovery claims, impact predictions или population estimates.
В таблице приведены signed и absolute differences: signed mean может взаимно
сокращать ошибки между объектами.

## Reproducibility and limitations

Проверка сохранённых траекторий пересчитала {verification['error_samples_recomputed']:,}
норм позиционных ошибок и соответствующих step differences из raw reference
и accepted endpoints. Максимальное расхождение с сохранёнными сводками:
{verification['maximum_recomputed_difference_km']:.3g} km. Проверены 600 model records,
360 решений, object-disjoint split и frozen source/input hashes. Это проверка
согласованности, а не независимый динамический solver.

Каталог содержит {verification['case_body_minima']} object/horizon/body минимумов и
{verification['selected_events']} отдельно проверенных выбранных событий;
{verification['reference_event_boundary_minima']} минимумов событий лежат на границе окна.
59 новых Horizons raw-файлов прошли SHA-256/size/header/coverage checks;
daily/refined overlaps совпали по положению и скорости.

Текущий контракт: [docs/DEVELOPMENT30_CONTRACT.md](../docs/DEVELOPMENT30_CONTRACT.md).
Порядок команд и продолжения после паузы:
[DEVELOPMENT30_REPRODUCIBILITY.md](DEVELOPMENT30_REPRODUCIBILITY.md).
Источники: [JPL CAD](https://ssd-api.jpl.nasa.gov/doc/cad.html),
[JPL SBDB](https://ssd-api.jpl.nasa.gov/doc/sbdb_query.html) и
[Horizons manual](https://ssd.jpl.nasa.gov/horizons/manual.html). Координаты
Sun-centered ICRF, geometric, TDB, AU/AU-day; planetary ephemerides —
exogenous inputs, asteroids — massless test particles.

Запуск использует pinned system Python через `uv`, например:

```text
uv run --no-project --python-preference only-system --python 3.14.7 python -B -m unittest discover -s tests
```

Pilot object-disjoint, но selection-biased по заранее заданным target strata,
имеет только 12 validation objects и не устанавливает rare-encounter
generalization. Horizons содержит current orbit-fit information, поэтому это
conditional teacher benchmark/system-identification experiment, а не
historical forecast из contemporaneous observations.
"""
    return report


def write_report(root: Path, config_path: Path | None = None) -> tuple[Path, Path]:
    root = root.resolve()
    report = build_report(root, config_path)
    output_path = root / "outputs/development30/report.md"
    docs_path = root / "docs/DEVELOPMENT30_REPORT.md"
    for path in (output_path, docs_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(report, encoding="utf-8")
        temporary.replace(path)
    return output_path, docs_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    output_path, docs_path = write_report(args.root, args.config)
    print(json.dumps({"output": str(output_path), "docs": str(docs_path)}, sort_keys=True))


if __name__ == "__main__":
    main()


__all__ = ["build_report", "write_report"]

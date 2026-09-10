"""Generate the Russian confirmation100 report from completed artifacts only."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
from statistics import median
from typing import Any

OUT = "outputs/selector_confirmation100"
SAMPLE = "data/processed/selector_confirmation100/sample.json"
MANIFEST = "data/checksums/selector_confirmation100_manifest.json"
EXPERIMENT = f"{OUT}/experiment_freeze.json"
RAW_VERIFICATION = f"{OUT}/raw_verification.json"
METHODS = ("tree_v3", "physics_v4", "hybrid_v4", "fixed_full")
TOLS = (0.1, 1.0, 10.0)
HORIZONS = (7.0, 30.0, 90.0, 180.0, 365.0)


def _read(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(root: Path, relative: str) -> str:
    return hashlib.sha256((root / relative).read_bytes()).hexdigest()


def _num(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("non-finite numeric artifact value")
    return result


def _pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    values = sorted(values)
    index = (len(values) - 1) * q
    lo, hi = math.floor(index), math.ceil(index)
    if lo == hi:
        return values[lo]
    return values[lo] + (values[hi] - values[lo]) * (index - lo)


def _eligible(row: dict[str, Any], tol: float, fraction: float = 0.1) -> bool:
    return _num(row["max_position_error_km"]) <= tol and _num(row["numerical_difference_km"]) <= fraction * tol


def _check_completed(root: Path, matrix: dict[str, Any], cost: dict[str, Any], verification: dict[str, Any], sample: dict[str, Any], manifest: dict[str, Any], experiment: dict[str, Any], raw_verification: dict[str, Any]) -> None:
    if verification.get("passed") is not True:
        raise ValueError("verification is not passed")
    matrix_path, cost_path = f"{OUT}/matrix.json", f"{OUT}/direct_cost.json"
    if verification.get("matrix_sha256") != _sha(root, matrix_path) or verification.get("timing_sha256") != _sha(root, cost_path):
        raise ValueError("verification hashes do not match artifacts")
    if raw_verification.get("passed") is not True or raw_verification.get("manifest_sha256", {}).get(MANIFEST) != _sha(root, MANIFEST):
        raise ValueError("raw verification is not passed or manifest hash mismatches")
    objects = sample.get("objects")
    if not isinstance(objects, list) or len(objects) != 100 or len({str(o.get("id")) for o in objects}) != 100:
        raise ValueError("confirmation sample must contain 100 unique objects")
    timing_ids = sample.get("timing_object_ids")
    if not isinstance(timing_ids, list) or len(timing_ids) != 24 or len(set(map(str, timing_ids))) != 24:
        raise ValueError("confirmation timing subset must contain 24 unique IDs")
    if manifest.get("complete") is not True or manifest.get("sample_sha256") != _sha(root, SAMPLE):
        raise ValueError("raw manifest is incomplete or mismatched")
    if experiment.get("hashes", {}).get(SAMPLE) != _sha(root, SAMPLE):
        raise ValueError("experiment freeze sample hash mismatch")
    if manifest.get("experiment_freeze_sha256") != _sha(root, EXPERIMENT):
        raise ValueError("manifest experiment-freeze hash mismatch")
    expected = len(objects) * 4 * 5
    if matrix.get("record_count") != expected or len(matrix.get("records", [])) != expected:
        raise ValueError("matrix record count is incomplete")
    expected_timing = 24 * 4 * 5
    if len(cost.get("timings", [])) != expected_timing:
        raise ValueError("timing subset is incomplete")


def report(root: Path, target: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    matrix = _read(root, f"{OUT}/matrix.json")
    cost = _read(root, f"{OUT}/direct_cost.json")
    verification = _read(root, f"{OUT}/verification.json")
    sample = _read(root, SAMPLE)
    manifest = _read(root, MANIFEST)
    experiment = _read(root, EXPERIMENT)
    raw_verification = _read(root, RAW_VERIFICATION)
    _check_completed(root, matrix, cost, verification, sample, manifest, experiment, raw_verification)
    records = matrix["records"]
    choices = matrix["choices"]
    objects = sample["objects"]
    by_case = {(str(r["object_id"]), float(r["horizon_days"]), str(r["model_id"])): r for r in records}
    summaries = {(str(r["method"]), float(r["tolerance_km"])): r for r in matrix["summaries"]}
    lines = ["# Независимое подтверждение селектора v4 на 100 телах", "", "Эксперимент читает замороженные v4 физические кандидаты и не переобучает селектор. Полная выборка содержит 100 объектов, 500 зависимых horizon cases и все четыре метода; прямой замер wall-clock времени (perf_counter) выполнен только на заранее выбранных 24 объектах (120 cases × 4 метода × 3 чередующихся повтора). Horizons является model-derived reference при заданном initial state.", "", "## Основная точность", "", "Успешный case одновременно проходит position error и production/fine numerical budget. Доли и failures считаются отдельно для каждого метода и допуска.", "", "| Метод | 0.1 km | 1 km | 10 km | Все 5 H / тел при 1 km | Максимальная ошибка при 1 km |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    diagnostic_lines = []
    for method in METHODS:
        selected = [r for r in choices if r["method"] == method]
        one = [r for r in selected if float(r["tolerance_km"]) == 1.0]
        errors = [_num(r["max_position_error_km"]) for r in one]
        body_all = sum(all(bool(r["actual_eligible"]) for r in one if str(r["object_id"]) == str(o["id"])) for o in objects)
        numerical_flags = sum(_num(r["numerical_difference_km"]) > 0.1 for r in one)
        lines.append(f"| {method} | " + " | ".join(f"{summaries[(method, t)]['eligible_cases']} / {len(objects)*5}" for t in TOLS) + f" | {body_all} / {len(objects)} | {max(errors):.6g} km |")
        diagnostic_lines.append(f"| {method} | {_pct(errors,.5):.6g} km | {_pct(errors,.95):.6g} km | {numerical_flags} | {sum(bool(r.get('no_candidate_truth')) for r in one)} | {sum(bool(r.get('warning')) for r in one)} | {sum(not bool(r['actual_eligible']) and not bool(r.get('warning')) for r in one)} |")
    lines += ["", "## Распределение ошибок и предупреждения при 1 km", "", "| Метод | Median error | p95 error | Numerical flags | No-candidate truth | Warnings | Silent failures |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"] + diagnostic_lines
    lines += ["", "`numerical flags` означает production/fine difference > 0.1 km при tolerance 1 km; этот порог отдельно проверяется в eligibility. Warning cases не вычитаются из score. No-candidate truth и silent failures — разные состояния.", "", "## 1 km по всем девяти strata", "", "| Stratum | Method | Objects | Cases | Eligible | All 5 H | Warnings | Unflagged failures | Worst error km |", "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for stratum in sorted({str(o.get("stratum", "unknown")) for o in objects}):
        ids = {str(o["id"]) for o in objects if str(o.get("stratum", "unknown")) == stratum}
        for method in METHODS:
            rows = [r for r in choices if str(r["object_id"]) in ids and r["method"] == method and float(r["tolerance_km"]) == 1.0]
            if len(rows) != len(ids) * len(HORIZONS):
                raise ValueError(f"stratum case count is not exactly five horizons: {stratum} {method}")
            for oid in ids:
                horizons = {float(r["horizon_days"]) for r in rows if str(r["object_id"]) == oid}
                if horizons != set(HORIZONS):
                    raise ValueError(f"object does not have exactly the five horizons: {stratum} {oid} {method}")
            all_h = sum(all(bool(r["actual_eligible"]) for r in rows if str(r["object_id"]) == oid) for oid in ids)
            lines.append(f"| {stratum} | {method} | {len(ids)} | {len(rows)} | {sum(bool(r['actual_eligible']) for r in rows)} | {all_h} | {sum(bool(r.get('warning')) for r in rows)} | {sum(not bool(r['actual_eligible']) and not bool(r.get('warning')) for r in rows)} | {max((_num(r['max_position_error_km']) for r in rows), default=0):.6g} |")
    lines += ["", "## Candidate eligibility and force coverage", "", "| Candidate | Forces included | Forces omitted | Horizon | Eligible physical cases / 100 |", "| --- | --- | --- | ---: | ---: |"]
    availability = defaultdict(int)
    for record in records:
        if _eligible(record["record"], 1.0):
            availability[(str(record["model_id"]), float(record["horizon_days"]))] += 1
    force_labels = {"V2-B2": ("Sun + test particle", "planet, GR, SB16, Earth J2, NG"), "V2-P": ("B2 + planets + Earth J2 + NG if available", "GR, SB16"), "V2-P-GR": ("P + solar GR", "SB16"), "V2-P-GR-SB16": ("P + GR + SB16", "none within this candidate suite")}
    for model, (included, omitted) in force_labels.items():
        for horizon in HORIZONS:
            lines.append(f"| {model} | {included} | {omitted} | {horizon:g} d | {availability[(model, horizon)]} |")
    lines += ["", "Eligibility counts above are physical candidate results at 1 km, independently of which method selected the candidate. Force availability is therefore not conflated with method selection.", "", "## Геометрия выбранных встреч", "", "Используется кандидат, выбранный на 365 суток при запросе 1 km. Проверяется только зафиксированная каталогом встреча в окне ±2 суток; это не поиск всех будущих сближений. Reference использует 5-minute asteroid nodes и те же exogenous planetary ephemerides. Ошибки ниже относятся к уточнённому минимуму в этом окне, а не к continuous-time или наблюдательной гарантии.", "", "| Метод | Встреч | Median Δd, m | p95 Δd, m | Max Δd, m | Max Δt, s | Max step Δd, m |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for method in METHODS:
        geometry = []
        for choice in choices:
            if choice["method"] == method and float(choice["horizon_days"]) == 365.0 and float(choice["tolerance_km"]) == 1.0:
                row = by_case[(str(choice["object_id"]), 365.0, str(choice["model_id"]))]
                if row["record"].get("closest_geometry"):
                    geometry.append(row["record"]["closest_geometry"])
        if geometry:
            distances = [_num(g["errors"]["distance_km"]) * 1000.0 for g in geometry]
            times = [_num(g["errors"]["time_days"]) * 86400.0 for g in geometry]
            steps = [_num(g["production_fine_shift"]["distance_km"]) * 1000.0 for g in geometry]
            lines.append(f"| {method} | {len(geometry)} | {_pct(distances,.5):.6g} | {_pct(distances,.95):.6g} | {max(distances):.6g} | {max(times):.6g} | {max(steps):.6g} |")
    lines += ["", "## Timing on paired 24-object subset", "", "| Method | Total median seconds | Median case seconds | Reduction vs full | Successes / 120 | Feature cost included |", "| --- | ---: | ---: | ---: | ---: | --- |"]
    timing = cost["timings"]
    for method in METHODS:
        rows = [r for r in timing if r["method"] == method]
        values = [_num(r["runtime_median_seconds"]) for r in rows]
        feature = sum(_num(r) for row in rows for r in row.get("feature_runtime_trials_seconds", []))
        successes = sum(bool(r.get("actual_eligible")) for r in rows)
        total = sum(values)
        lines.append(f"| {method} | {total:.3f} | {median(values):.6f} | {(1 - total / float(cost['full_cost']['fixed_full']['total_seconds'])) * 100:+.2f}% | {successes} / 120 | {'yes' if feature > 0 else 'no'} |")
    lines += ["", "Timing использует только одинаковые 24 объекта для всех методов, три повтора с чередованием порядка, и включает feature cost там, где он нужен. Successes здесь относятся только к этим 24 объектам и не заменяют full-sample score.", "", "### Стоимость по горизонту", "", "Сумма медиан на одинаковых 24 телах для каждого горизонта; все времена в секундах.", "", "| H, d | tree_v3 | physics_v4 | hybrid_v4 | fixed_full | Экономия physics к full |", "| ---: | ---: | ---: | ---: | ---: | ---: |"]
    for horizon in HORIZONS:
        total = {method: sum(_num(r["runtime_median_seconds"]) for r in timing if r["method"] == method and float(r["horizon_days"]) == horizon) for method in METHODS}
        lines.append(f"| {horizon:g} | " + " | ".join(f"{total[method]:.3f}" for method in METHODS) + f" | {(1-total['physics_v4']/total['fixed_full'])*100:+.2f}% |")
    lines += ["", "### Порядок в трёх повторах", "", "Здесь складываются фактические времена одного номера повтора по всем 120 cases. Это дополнительная проверка устойчивости порядка; основной показатель выше — сумма per-case медиан.", "", "| Повтор | tree_v3, s | physics_v4, s | hybrid_v4, s | fixed_full, s | Порядок от быстрого к медленному |", "| ---: | ---: | ---: | ---: | ---: | --- |"]
    for repeat in range(3):
        total = {method: sum(_num(r["runtime_trials_seconds"][repeat]) for r in timing if r["method"] == method) for method in METHODS}
        lines.append(f"| {repeat+1} | " + " | ".join(f"{total[method]:.3f}" for method in METHODS) + " | " + " < ".join(sorted(METHODS,key=total.get)) + " |")
    lines += ["", "## Operational warnings по допускам", "", "| Method | Tol | Strong encounter | OOD prediction | No-candidate prediction | Actual no-candidate | Warnings | Warning reasons |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
    for method in METHODS:
        for tol in TOLS:
            rows = [r for r in choices if r["method"] == method and float(r["tolerance_km"]) == tol]
            reasons = Counter(reason for r in rows for reason in r.get("warning_reasons", []))
            outside = sorted({reason for r in rows for reason in r.get("outside_training_support_reasons", [])})
            detail = ", ".join(f"{k}: {v}" for k, v in sorted(reasons.items()))
            if outside:
                detail += "; OOD features: " + ", ".join(outside)
            lines.append(f"| {method} | {tol:g} | {sum(bool(r.get('strong_encounter')) for r in rows)} | {sum(bool(r.get('outside_training_support')) for r in rows)} | {sum(bool(r.get('no_candidate_predicted')) for r in rows)} | {sum(bool(r.get('no_candidate_truth')) for r in rows)} | {sum(bool(r.get('warning')) for r in rows)} | {detail or '—'} |")
    lines += ["", "## Force choices и failures", "", "### Выборы", ""]
    for method in METHODS:
        count = Counter(str(r["model_id"]) for r in choices if r["method"] == method and float(r["tolerance_km"]) == 1.0)
        lines.append(f"- `{method}`: " + ", ".join(f"{k}={v}" for k, v in sorted(count.items())))
    lines += ["", "### Все failures выбранной модели", "", "| Method | Tol | Object | H | Selected model | Error km | Numerical diff km | Full residual km | Status | Warning |", "| --- | ---: | --- | ---: | --- | ---: | ---: | ---: | --- | --- |"]
    for row in choices:
        if bool(row["actual_eligible"]):
            continue
        key = (str(row["object_id"]), float(row["horizon_days"]), str(row["model_id"]))
        full_case = by_case.get((key[0], key[1], "V2-P-GR-SB16"), {})
        full = full_case.get("record", full_case)
        lines.append(f"| {row['method']} | {float(row['tolerance_km']):g} | {row['object_id']} | {key[1]:g} | {row['model_id']} | {_num(row['max_position_error_km']):.6g} | {_num(row['numerical_difference_km']):.6g} | {_num(full.get('max_position_error_km', float('nan'))):.6g} | {row.get('status', 'unknown')} | {bool(row.get('warning'))} |")
    lines += ["", "Residuals приведены фактически; они не приписываются отдельной силе без отдельного causal audit. Полная модель служит сохранённым сравнением для того же initial state и горизонта.", "", "## Sampling quality and limits", "", "Sample использует canonical safe IDs, сохраняет actual JPL designation/name и conventional stratum/event metadata. Counts-only selection и quality audit выполнены до загрузки target vectors. В metadata amendment перед vector selection зафиксированы строгие 4 Earth tight cases и nominal feasibility остальных quotas; audit содержит 237 metadata files и 122 quality rejections. Raw inventory и exact provenance проверены отдельным raw verifier.", "", f"Raw inventory: passed={raw_verification['passed']}; unique raw paths={raw_verification.get('unique_raw_paths', 'n/a')}; manifests={raw_verification.get('manifest_count', 'n/a')}; manifest records={raw_verification.get('manifest_record_count', 'n/a')}; new target files={raw_verification.get('new_target_files', 'n/a')}; verifier SHA={raw_verification.get('verifier_sha256', 'n/a')}.", "", "Новые 100 тел disjoint от development/calibration material. Старый v4 holdout24 остаётся отдельной frozen оценкой и не смешивается с prevalence этого confirmation sample. Если strong encounter или no-candidate truth отсутствуют, их detection не считается validated. Сильные warnings и fallback не дают universal guarantee.", "", f"Experiment freeze SHA256: `{_sha(root, EXPERIMENT)}`.", f"Matrix SHA256: `{_sha(root, OUT + '/matrix.json')}`.", f"Direct cost SHA256: `{_sha(root, OUT + '/direct_cost.json')}`.", f"Verification SHA256: `{_sha(root, OUT + '/verification.json')}`.", "", "CLI:", "", "```sh", "PYTHONPATH=src uv run --no-project --python-preference only-system --python 3.14.7 python -B src/run_selector_confirmation.py verify", "PYTHONPATH=src uv run --no-project --python-preference only-system --python 3.14.7 python -B src/report_selector_confirmation.py", "```"]
    target = target or root / "docs/SELECTOR_CONFIRMATION100_REPORT.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"report": str(target), "objects": len(objects), "records": len(records), "timing_rows": len(timing)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(report(args.root), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

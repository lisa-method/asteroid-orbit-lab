"""Render the completed fresh holdout findings without modifying frozen artifacts."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from prepare_fresh_holdout import sha
from ng_inputs_v2 import load_ng_input


def report(root):
    out = root / "outputs/fresh_holdout12"
    matrix = json.loads((out / "matrix.json").read_text())
    costs = json.loads((out / "direct_cost.json").read_text())
    verification = json.loads((out / "verification.json").read_text())
    if not verification["passed"] or "checks" not in verification or verification["matrix_sha256"] != sha(out / "matrix.json") or verification["direct_cost_sha256"] != sha(out / "direct_cost.json"):
        raise ValueError("Verified complete matrix and timing required")
    sample = json.loads((root / "data/processed/fresh_holdout12/sample.json").read_text())
    summaries = matrix["summaries"]
    full = [r for r in matrix["records"] if r["model_id"] == "V2-P-GR-SB16"]
    worst = max(full, key=lambda r: r["max_position_error_km"])
    totals = costs["full_cost"]
    ratio = 100*(1-totals["selector"]["total_seconds"] / totals["fixed_full"]["total_seconds"])
    lines = ["# Свежий whole-object holdout frozen v2 — 2026-09-08", "",
             "Метод и horizon rule зафиксированы до отбора 12 новых объектов. "
             "Ни один из них не входит в прежние 30 development, 6 engineering targets, "
             "SB16 или ранее просмотренный Gaia target 1620. Rules не переобучались.", "",
             "Это первая свежая проверка конкретной v2; маленькая целевая выборка "
             "не даёт гарантии для всей популяции. Пять горизонтов одного объекта зависимы.", "",
             "[Контракт](FRESH_HOLDOUT12_CONTRACT.md), "
             "[sampling amendment](FRESH_HOLDOUT12_SAMPLING_AMENDMENT.md).", "",
             "## Отбор и данные", "",
             "Первый отбор остановился до новых trajectory calculations: все пять "
             "объектов старого Jupiter <0.5 AU каталога уже были просмотрены. "
             "До загрузки новых reference trajectories граница для Jupiter расширена "
             "до 1 AU; отобраны 0.62777/0.67931 AU события. Остальные strata и метод "
             "сохранены. Старый freeze и source snapshot сохранены. Логический ключ "
             "Jupiter catalogue в sample остался прежним; amended method freeze "
             "связывает его с новым файлом cad_jupiter_2026_2029_10au.json.", "",
             "| Объект | Группа | Начало TDB | CAD distance, AU |", "|---|---|---|---:|"]
    for obj in sample["objects"]:
        distance = f"{obj['event']['dist_au']:.6f}" if obj["event"] else "—"
        lines.append(f"| {obj['name']} | {obj['stratum']} | {obj['start_date']} | {distance} |")
    lines += ["", "Один новый CAD snapshot и 20 анонимных Horizons responses: 12 daily + "
              "8 refined (5 минут, ±2 дня). Планеты hourly и SB16 daily повторно "
              "используются из прежних immutable данных. Sun-centred ICRF/FRAME, "
              "geometric AU/day, TDB. Future target states передаются только evaluator.", "",
              "## Точность", "", "Каждая ячейка — число допустимых окон из 60. "
              "Max position error на merged reference grid ≤допуска, "
              "production/fine distance ≤10% допуска.", "",
              "B2 — только Солнце. P — Солнце, девять planetary perturbers, "
              "Earth J2 и NG при доступных параметрах; GR добавляет solar "
              "relativity, SB16 — 16 массивных малых тел.", "",
              "| Модель | 100 m | 1 km | 10 km |", "|---|---:|---:|---:|"]
    for model in ("V2-B2", "V2-P", "V2-P-GR", "V2-P-GR-SB16"):
        values = [next(row["eligible_cases"] for row in s["fixed"] if row["model_id"] == model) for s in summaries]
        lines.append(f"| {model} | " + " | ".join(f"{value}/60" for value in values) + " |")
    lines.append("| Frozen horizon selector | " + " | ".join(f"{s['selector_eligible_cases']}/60" for s in summaries) + " |")
    lines += ["", "Селектор использует горизонт и допуск; состав сил затем применяется "
              "к начальному состоянию и доступным NG inputs. Это прежнее правило, "
              "откалиброванное на 18 train-объектах, без новых контекстных признаков.", "",
              "| Допуск, km | Максимальная ошибка выбранной модели, m | Объект | Горизонт, суток |",
              "|---:|---:|---|---:|"]
    for summary in summaries:
        selected = max((c for c in matrix["choices"] if c["tolerance_km"] == summary["tolerance_km"]),
                       key=lambda c: c["max_position_error_km"])
        lines.append(f"| {summary['tolerance_km']:g} | {selected['max_position_error_km']*1000:.3f} | {selected['object_id']} | {selected['horizon_days']:g} |")
    lines += ["", f"Наибольшая ошибка полной модели: **{worst['max_position_error_km']*1000:.3f} m**, "
              f"объект {worst['object_id']}, {worst['horizon_days']:g} суток; "
              f"production/fine difference {worst['numerical_difference_km']*1000:.3f} m.", "",
              "| Допуск, km | Feasible | Ошибки выбора при наличии кандидата | No candidate | Из них flagged |",
              "|---:|---:|---:|---:|---:|"]
    for s in summaries:
        lines.append(f"| {s['tolerance_km']:g} | {s['feasible_cases']}/60 | {s['avoidable_selection_misses']} | {s['no_candidate_cases']} | {s['no_candidate_flagged']} |")
    lines += ["", "| Объект | Годовая max error полной модели, m | Step difference, m | NG status |",
              "|---|---:|---:|---|"]
    for obj in sample["objects"]:
        row = next(r for r in full if r["object_id"] == obj["id"] and r["horizon_days"] == 365)
        status = row["force_metadata"].get("ng_status", "see trace metadata")
        lines.append(f"| {obj['id']} | {row['max_position_error_km']*1000:.3f} | {row['numerical_difference_km']*1000:.3f} | {status} |")
    annual = [r for r in full if r["horizon_days"] == 365]
    ng_counts = dict(Counter(r["force_metadata"]["ng_status"] for r in annual))
    lines += ["", f"NG status: `{ng_counts}`. Параметры доступны для 437844, 475534 и "
              "139359; источники получены до всех forecast starts, sigma отсутствуют. "
              "Первые два header задают A2 с r^-2 законом, третий — другой distance law. "
              "Все параметры прочитаны общим adapter без подгонки. Эта матрица не "
              "отделяет индивидуальный вклад NG отдельной ablation и не доказывает "
              "физический механизм ускорения.", "",
              "| Объект с NG | A2, AU/day² | alpha, m, n, k, r0(AU) |",
              "|---|---:|---|"]
    for obj in sample["objects"]:
        ng = load_ng_input(root / f"data/raw/fresh_holdout12/asteroids/asteroid_{obj['id']}_daily.json")
        if ng.parameters is not None:
            p = ng.parameters
            lines.append(f"| {obj['id']} | {p.a2_au_d2:.12e} | {p.alpha:.12g}, {p.exponent_m:.12g}, {p.exponent_n:.12g}, {p.exponent_k:.12g}, {p.r0_au:.12g} |")
    lines += ["", f"Наибольшая production/fine difference полной модели: "
              f"{max(r['numerical_difference_km'] for r in full)*1000:.3f} m. "
              f"Наибольшая sampled velocity error: {max(r['max_velocity_error_m_s'] for r in full):.8g} m/s. "
              "Velocity threshold отдельно не калибровался; основной gate — position + step sensitivity."]
    failed = [c for c in matrix["choices"] if not c["actual_eligible"]]
    lines += ["", "## Нарушения допуска", ""]
    if not failed:
        lines.append("Нарушений выбранной моделью на этих 60 окнах при трёх допусках не обнаружено.")
    else:
        lines += ["| Объект | Горизонт | Допуск, km | Выбрано | Ошибка, km | Достаточный кандидат есть | Fallback |",
                  "|---|---:|---:|---|---:|---|---|"]
        for c in failed:
            lines.append(f"| {c['object_id']} | {c['horizon_days']} | {c['tolerance_km']:g} | {c['model_id']} | {c['max_position_error_km']:.6f} | {c['any_candidate_eligible']} | {c['fallback']} |")
    lines += ["", "Здесь нет ни одного no-candidate случая. Поэтому нулевое число "
              "пропущенных отказов не проверяет способность заранее предупреждать о "
              "недостаточности всего набора моделей. Старый промах v2 на 983/H90 "
              "при 100 m также сохраняется в отдельном development replay."]
    lines += ["", "## Прямой runtime при 1 km", "",
              "Суммы медиан трёх вызовов на каждом из 60 окон; 360 actual calls. "
              "Selector/fixed порядок чередуется. Начальное состояние, NG и эфемериды "
              "уже в памяти; inference + force construction + rollout входят в timer. "
              "Чтение данных и evaluator вне таймера. Comparator fixed full выбран "
              "по development до открытия holdout.", "",
              "| Стратегия | Время, s | Успехи |", "|---|---:|---:|"]
    for method, label in (("selector", "Horizon selector"), ("fixed_full", "Fixed full")):
        row = totals[method]
        lines.append(f"| {label} | {row['total_seconds']:.3f} | {row['eligible_cases']}/60 |")
    lines += ["", f"Изменение затрат: **{ratio:.2f}% экономии** (отрицательное значение — замедление). "
              "Вывод о выгоде требует учитывать coverage и величину нарушений одновременно. "
              "Это простой horizon rule, не ML speedup; при 0.1/10 km direct cost не измерялся.", "",
              "При 1 km более дешёвая постоянная GR-модель покрывает 59/60 окон, "
              "поэтому не обеспечивает одинаковое покрытие с full и selector. При "
              "10 km GR уже проходит все 60 окон: выигрыш против full при 1 km "
              "нельзя переносить на этот допуск. Основной direct timing запускался "
              "после завершения других тяжёлых проектных расчётов; prefix timings "
              "матрицы не используются как оценка достигнутой экономии.", "",
              "## Проверки, воспроизведение и границы", "",
              f"Проверены {verification['records']} records, {verification['choices']} choices, "
              f"{verification['traces']} traces; пересчитаны {verification['error_samples_recomputed']} error samples. "
              f"{verification['raw_files_sha_size_ignored']} raw files прошли SHA-256/size/Git-ignore. "
              "Все source/input hashes frozen v2 сверены. Uncertainty propagation и "
              "непрерывный максимум ошибки отсутствуют. Unknown NG/sigma не являются "
              "измеренным нулём. Успех Horizons matching не равен точности реальных наблюдений.", "",
              "```bash", "# Existing uv + system Python 3.14.7; no package downloads or environment creation",
              "env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv run --no-project --python-preference only-system --python 3.14.7 python -B src/run_fresh_holdout.py --root . matrix",
              "# Repeat the same prefix with phase cost; completed checkpoints are reused.",
              "# Strong verification: python -B src/verify_fresh_holdout.py --root . (same uv prefix)",
              "# Report: python -B src/report_fresh_holdout.py --root . (through the same uv invocation)", "```", "",
              f"Matrix SHA-256: `{verification['matrix_sha256']}`. "
              f"Direct cost SHA-256: `{verification['direct_cost_sha256']}`.", "",
              "После просмотра этот holdout становится inspected set. Следующую настройку "
              "можно делать как новую версию с отдельной свежей проверкой. Исходные "
              "development v1/v2 scores и artifacts не переписаны. Установок, постоянной "
              "среды, commit, remote или публикации нет.", ""]
    path = root / "docs/FRESH_HOLDOUT12_REPORT.md"
    path.write_text("\n".join(lines))
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    print(report(args.root.resolve()))

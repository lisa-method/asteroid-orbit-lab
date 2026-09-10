"""Render the saved benchmark and a case-level model-choice map, without reruns."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from run_model_sufficiency import build_report


def case_map(result: dict) -> str:
    cases = {}
    for row in result["records"]:
        cases.setdefault(row["case_id"], row)
    oracles = result["oracles"]
    headings = [f"{oracle['position_tolerance_km']:g} km" for oracle in oracles]
    lines = ["## Карта выбора по отдельным задачам", "",
             "В ячейке — самый дешёвый допустимый кандидат по измеренной median cost. "
             "«Нет» означает, что ни один кандидат не прошёл одновременно позиционный "
             "допуск и numerical gate. Это oracle с доступом к эталонным ошибкам.", "",
             "| Объект | Горизонт, дни | " + " | ".join(headings) + " |",
             "| --- | ---: | " + " | ".join("---" for _ in oracles) + " |"]
    for case_id, row in cases.items():
        choices = [oracle["selections"][case_id] or "Нет" for oracle in oracles]
        lines.append(f"| {row['object_name']} | {row['horizon_days']} | " + " | ".join(choices) + " |")
    strongest = result["config"]["models"][-1]["model_id"]
    longest = max(result["config"]["horizons_days"])
    lines += ["", f"## RTN на {longest} днях: {strongest}", "",
              "Компоненты endpoint error в reference RTN, km; максимум по всей сетке "
              "показан отдельно. Добавление сил не гарантирует уменьшения ошибки.", "",
              "| Объект | Radial | Transverse | Normal | Max grid, km |",
              "| --- | ---: | ---: | ---: | ---: |"]
    for row in result["records"]:
        if row["model_id"] == strongest and row["horizon_days"] == longest:
            radial, transverse, normal = row["rtn_endpoint_position_error_km"]
            lines.append(f"| {row['object_name']} | {radial:.6g} | {transverse:.6g} | {normal:.6g} | {row['max_position_error_km']:.6g} |")
    lines += ["", "Суммы стоимости по вложенным горизонтам относятся к отдельным запросам "
              "прогноза каждого горизонта; они не оценивают стоимость одного общего "
              "multi-horizon batch. Близкие по времени кандидаты могут менять oracle-метку "
              "из-за timing noise, поэтому частота разных меток сама по себе не доказывает "
              "полезность learned selector.", "", "Карту можно пересоздать без интегрирования:", "",
              "```bash",
              "env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv run --no-project --python-preference only-system python -B src/report_model_sufficiency.py --results outputs/model_sufficiency/pilot6/results.json --output docs/MODEL_SUFFICIENCY_PILOT6_REPORT.md",
              "```", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.results.read_bytes()
    result = json.loads(source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_report(result) + "\n" + case_map(result), encoding="utf-8")
    metadata = {"results_sha256": hashlib.sha256(source).hexdigest(),
                "renderer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "report_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest()}
    (args.results.parent / "report_export_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps({"report": str(args.output), "cases": len(result["oracles"][0]["selections"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Audit fair fixed-model costs for the completed development30 selection run.

The selection runner measures a full operational call.  This audit supplies a
comparable propagation-only B3+GR reference at the same 60 validation cases:
it reuses a directly measured selected propagation where one exists and runs
three serial direct calls only for cases where B3+GR was never selected.

The script is intentionally post-run.  It refuses incomplete or changed
artifacts and never uses labels to choose a model; labels are read only after a
timed rollout to check agreement and report 10-km eligibility.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import statistics
import time
from typing import Any, Mapping

from download_jpl_pilot import write_immutable
from run_development_benchmark import (
    _find_initial,
    _interpolate_endpoints,
    _reference_grid,
    forecast_candidate,
    load_development_context,
    load_object_rows,
)
from trajectory_metrics import trajectory_error_rows, summarize_error_rows


MODEL_IDS = ("B2", "B3", "B3+GR", "B3+GR+SB16")
ANCHOR_MODEL_ID = "B3+GR+SB16"
REFERENCE_MODEL_ID = "B3+GR"
TOLERANCE_KM = 10.0
AGREEMENT_TOLERANCE_KM = 1e-5


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"required fixed-cost input is missing: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _number(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not result == result or result in (float("inf"), float("-inf")):
        raise ValueError(f"{label} must be finite")
    return result


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{time.time_ns()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _validate_source_hashes(root: Path, result: Mapping[str, Any], label: str) -> dict[str, str]:
    provenance = result.get("provenance")
    if not isinstance(provenance, Mapping):
        raise ValueError(f"{label} has no provenance")
    hashes = provenance.get("source_sha256_at_start")
    if not isinstance(hashes, Mapping) or not hashes:
        raise ValueError(f"{label} source hash map is missing")
    normalized = {str(path): str(value) for path, value in hashes.items()}
    for relative, expected in normalized.items():
        path = root / relative
        if not path.exists() or sha256(path) != expected:
            raise ValueError(f"{label} source/input hash mismatch: {relative}")
    return normalized


def _validate_inputs(root: Path, config_path: Path, config: Mapping[str, Any]) -> tuple[dict, dict, dict, dict, dict, dict[str, str]]:
    output = root / str(config["output_directory"])
    sample_path = root / str(config["sample_path"])
    train_path = output / "train" / "results.json"
    rules_path = output / "rules.json"
    validation_path = output / "validation" / "results.json"
    selection_path = output / "selection_results.json"
    sample = read_json(sample_path)
    train = read_json(train_path)
    rules = read_json(rules_path)
    validation = read_json(validation_path)
    selection = read_json(selection_path)

    objects = sample.get("objects")
    if not isinstance(objects, list) or len(objects) != 30:
        raise ValueError("development30 sample must contain 30 objects")
    if sum(str(obj.get("split")) == "validation" for obj in objects) != 12:
        raise ValueError("development30 sample must contain 12 validation objects")
    horizons = tuple(float(value) for value in config["horizons_days"])
    if tuple(model["model_id"] for model in config["models"]) != MODEL_IDS:
        raise ValueError("configuration candidate ladder changed")
    if len(train.get("records", [])) != 18 * len(horizons) * 4:
        raise ValueError("training benchmark is incomplete")
    if len(validation.get("records", [])) != 12 * len(horizons) * 4:
        raise ValueError("validation benchmark is incomplete")
    feature_rows = validation.get("feature_rows")
    selections = selection.get("selections")
    anchors = selection.get("fixed_anchor")
    if not isinstance(feature_rows, list) or len(feature_rows) != 12 * len(horizons):
        raise ValueError("validation feature matrix must contain 60 rows")
    if not isinstance(selections, list) or len(selections) != 12 * len(horizons) * 2 * 3:
        raise ValueError("selection matrix must contain 360 rows")
    if not isinstance(anchors, list) or len(anchors) != 12 * len(horizons):
        raise ValueError("fixed anchor array must contain 60 rows")
    rules_provenance = rules.get("provenance")
    if not isinstance(rules_provenance, Mapping):
        raise ValueError("rules artifact has no provenance")
    for key, path in {
        "training_results_sha256": train_path,
        "rules_source_sha256": root / "src/development_rules.py",
        "sample_sha256": sample_path,
        "contract_sha256": root / "docs/DEVELOPMENT30_CONTRACT.md",
        "config_sha256": config_path,
    }.items():
        if rules_provenance.get(key) != sha256(path):
            raise ValueError(f"frozen rules provenance mismatch: {key}")
    validation_provenance = validation.get("provenance")
    selection_provenance = selection.get("provenance")
    if not isinstance(validation_provenance, Mapping) or not isinstance(selection_provenance, Mapping):
        raise ValueError("validation or selection provenance is missing")
    if validation_provenance.get("rules_sha256_at_start") != sha256(rules_path):
        raise ValueError("validation did not use the current frozen rules")
    expected_selection = {
        "rules_sha256": sha256(rules_path),
        "validation_results_sha256": sha256(validation_path),
        "config_sha256": sha256(config_path),
        "selection_source_sha256": sha256(root / "src/run_development_selection.py"),
        "features_source_sha256": sha256(root / "src/development_features.py"),
        "runner_source_sha256": sha256(root / "src/run_development_benchmark.py"),
    }
    for key, expected in expected_selection.items():
        if selection_provenance.get(key) != expected:
            raise ValueError(f"selection provenance mismatch: {key}")
    source_hashes = _validate_source_hashes(root, train, "train")
    source_hashes.update(_validate_source_hashes(root, validation, "validation"))
    return sample, rules, validation, selection, {"train_path": str(train_path), "rules_path": str(rules_path), "validation_path": str(validation_path), "selection_path": str(selection_path)}, source_hashes


def _label_map(validation: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    labels: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in validation["records"]:
        key = (str(row["case_id"]), str(row["model_id"]))
        if key in labels:
            raise ValueError(f"duplicate validation label: {key}")
        labels[key] = row
    return labels


def _feature_map(validation: Mapping[str, Any], horizons: tuple[float, ...]) -> dict[str, Mapping[str, Any]]:
    features: dict[str, Mapping[str, Any]] = {}
    for row in validation["feature_rows"]:
        case_id = str(row["case_id"])
        if case_id in features:
            raise ValueError(f"duplicate validation feature row: {case_id}")
        if row.get("split") != "validation" or float(row["horizon_days"]) not in horizons:
            raise ValueError(f"invalid validation feature row: {case_id}")
        features[case_id] = row
    return features


def _selected_reference_rows(selection: Mapping[str, Any], case_ids: set[str]) -> dict[str, Mapping[str, Any]]:
    rows = [row for row in selection["selections"] if str(row.get("case_id")) in case_ids and row.get("selected_model_id") == REFERENCE_MODEL_ID]
    chosen: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        case_id = str(row["case_id"])
        tolerance = float(row["tolerance_km"])
        rank = (0 if row.get("method") == "horizon_rule" else 1, 0 if tolerance == 1.0 else 1, tolerance)
        current = chosen.get(case_id)
        if current is None:
            chosen[case_id] = row
            continue
        old_rank = (0 if current.get("method") == "horizon_rule" else 1, 0 if float(current["tolerance_km"]) == 1.0 else 1, float(current["tolerance_km"]))
        if rank < old_rank:
            chosen[case_id] = row
    return chosen


def _actual_eligible(label: Mapping[str, Any], config: Mapping[str, Any]) -> bool:
    fraction = float(config["numerical_budget_fraction"])
    return (float(label["max_position_error_km"]) <= TOLERANCE_KM
            and float(label["numerical_difference_km"]) <= fraction * TOLERANCE_KM)


def _with_accuracy(record: dict[str, Any], label: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    record.update({
        "max_position_error_km": float(label["max_position_error_km"]),
        "numerical_difference_km": float(label["numerical_difference_km"]),
        "eligible_at_10km": _actual_eligible(label, config),
    })
    return record


def _direct_reference_cost(root: Path, config: Mapping[str, Any], feature: Mapping[str, Any], obj: Mapping[str, Any], context: Mapping[str, Any], label: Mapping[str, Any]) -> dict[str, Any]:
    daily, reference = load_object_rows(root, root / str(config["raw_directory"]), obj)
    start_jd, initial = _find_initial(daily, str(obj["start_date"]))
    horizon = float(feature["horizon_days"])
    times, reference_states = _reference_grid(reference, start_jd, horizon)
    model = next(model for model in config["models"] if model["model_id"] == REFERENCE_MODEL_ID)
    trials: list[float] = []
    for _ in range(int(config.get("timing_repeats", 3))):
        started = time.perf_counter()
        predictions, metadata = forecast_candidate(initial, start_jd, horizon, model, context, config, float(config["step_scale"]))
        elapsed = time.perf_counter() - started
        # Error evaluation and object cleanup happen outside the timed region.
        predicted_at = _interpolate_endpoints(metadata["accepted_states"])
        errors = trajectory_error_rows(times, [predicted_at(t) for t in times], reference_states, float(context["au_km"]), float(context["day_s"]))
        observed = summarize_error_rows(errors)["max_position_error_km"]
        if abs(float(observed) - float(label["max_position_error_km"])) > AGREEMENT_TOLERANCE_KM:
            raise ValueError(f"direct B3+GR rollout disagrees with benchmark label for {feature['case_id']}: {observed} vs {label['max_position_error_km']}")
        trials.append(elapsed)
        del predictions, metadata, predicted_at, errors
    return _with_accuracy({
        "case_id": str(feature["case_id"]),
        "object_id": str(feature["object_id"]),
        "horizon_days": horizon,
        "runtime_median_seconds": statistics.median(trials),
        "runtime_trials_seconds": trials,
        "source": "direct_fixed",
        "state_rollout_agreement_tolerance_km": AGREEMENT_TOLERANCE_KM,
    }, label, config)


def _reused_reference_cost(row: Mapping[str, Any], label: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    if "propagation_median_seconds" not in row:
        raise ValueError(f"selected B3+GR row has no propagation timing: {row['case_id']}")
    return _with_accuracy({
        "case_id": str(row["case_id"]),
        "object_id": str(row["object_id"]),
        "horizon_days": float(row["horizon_days"]),
        "runtime_median_seconds": float(row["propagation_median_seconds"]),
        "source": "reused_selected_propagation",
        "source_method": str(row["method"]),
        "source_tolerance_km": float(row["tolerance_km"]),
        "state_rollout_agreement_tolerance_km": AGREEMENT_TOLERANCE_KM,
    }, label, config)


def run_audit(root: Path, config_path: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    config_path = (config_path or root / "configs/development30.json").resolve()
    config = read_json(config_path)
    sample, rules, validation, selection, paths, source_hashes = _validate_inputs(root, config_path, config)
    case_features = _feature_map(validation, tuple(float(value) for value in config["horizons_days"]))
    labels = _label_map(validation)
    selection_rows = _selected_reference_rows(selection, set(case_features))
    sample_objects = {str(obj["id"]): obj for obj in sample["objects"] if obj.get("split") == "validation"}
    output_path = root / str(config["output_directory"]) / "fixed_cost_reference.json"
    provenance = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "audit_source_sha256": sha256(root / "src/audit_development_fixed_cost.py"),
        "config_sha256": sha256(config_path),
        "sample_sha256": sha256(root / str(config["sample_path"])),
        "rules_sha256": sha256(root / str(config["output_directory"]) / "rules.json"),
        "validation_results_sha256": sha256(root / str(config["output_directory"]) / "validation" / "results.json"),
        "selection_results_sha256": sha256(root / str(config["output_directory"]) / "selection_results.json"),
        "backend_source_hashes": source_hashes,
    }
    if output_path.exists():
        existing = read_json(output_path)
        old = existing.get("provenance")
        comparable = {key: value for key, value in provenance.items() if key != "generated_utc"}
        if not isinstance(old, Mapping) or any(old.get(key) != value for key, value in comparable.items()):
            raise ValueError("existing fixed-cost artifact provenance does not match current inputs")
        return existing

    reused: list[dict[str, Any]] = []
    direct: list[dict[str, Any]] = []
    missing = sorted(set(case_features) - set(selection_rows))
    context = None
    shared_load_seconds = 0.0
    if missing:
        started = time.perf_counter()
        context = load_development_context(root, config)
        shared_load_seconds = time.perf_counter() - started
    for case_id, feature in case_features.items():
        label = labels.get((case_id, REFERENCE_MODEL_ID))
        if label is None:
            raise ValueError(f"missing validation label for {case_id} {REFERENCE_MODEL_ID}")
        if case_id in selection_rows:
            reused.append(_reused_reference_cost(selection_rows[case_id], label, config))
        else:
            if context is None:
                raise AssertionError("direct fixed context was not loaded")
            obj = sample_objects.get(str(feature["object_id"]))
            if obj is None:
                raise ValueError(f"validation object is missing from sample: {feature['object_id']}")
            direct.append(_direct_reference_cost(root, config, feature, obj, context, label))
    reference_records = reused + direct
    reference_records.sort(key=lambda row: str(row["case_id"]))
    if len(reference_records) != len(case_features) or len({row["case_id"] for row in reference_records}) != len(case_features):
        raise ValueError("fixed B3+GR cost reference does not cover exactly 60 validation cases")

    anchor_records = []
    for row in selection["fixed_anchor"]:
        case_id = str(row["case_id"])
        label = labels.get((case_id, ANCHOR_MODEL_ID))
        if label is None:
            raise ValueError(f"missing validation label for {case_id} {ANCHOR_MODEL_ID}")
        copied = dict(row)
        copied["source"] = "selection_fixed_anchor"
        anchor_records.append(_with_accuracy(copied, label, config))
    anchor_records.sort(key=lambda row: str(row["case_id"]))

    payload = {
        "schema_version": 1,
        "provenance": provenance,
        "models": {
            REFERENCE_MODEL_ID: {
                "records": reference_records,
                "total_runtime_seconds": sum(float(row["runtime_median_seconds"]) for row in reference_records),
                "eligible_cases_at_10km": sum(bool(row["eligible_at_10km"]) for row in reference_records),
            },
            ANCHOR_MODEL_ID: {
                "records": anchor_records,
                "total_runtime_seconds": sum(float(row["runtime_median_seconds"]) for row in anchor_records),
                "eligible_cases_at_10km": sum(bool(row["eligible_at_10km"]) for row in anchor_records),
            },
        },
        "new_direct_invocations": len(direct) * int(config.get("timing_repeats", 3)),
        "reused_case_count": len(reused),
        "direct_case_count": len(direct),
        "shared_load_seconds": shared_load_seconds,
        "agreement_check": {"passed": True, "tolerance_km": AGREEMENT_TOLERANCE_KM, "checked_direct_cases": len(direct)},
        "cost_semantics": "B3+GR is propagation-only timing with state setup and accepted-state capture; feature/inference overhead is excluded. B3+GR+SB16 anchor records are copied from selection fixed_anchor.",
    }
    write_immutable(output_path, (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8"))
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    result = run_audit(args.root, args.config)
    print(json.dumps({"output": str(args.root / "outputs/development30/fixed_cost_reference.json"), "new_direct_invocations": result["new_direct_invocations"], "B3+GR_eligible_at_10km": result["models"][REFERENCE_MODEL_ID]["eligible_cases_at_10km"], "B3+GR+SB16_eligible_at_10km": result["models"][ANCHOR_MODEL_ID]["eligible_cases_at_10km"]}, sort_keys=True))


if __name__ == "__main__":
    main()


__all__ = ["run_audit"]

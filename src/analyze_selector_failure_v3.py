"""Post-hoc diagnosis of failed selector-v3 choices.

This reads the completed holdout matrix and cached causal features only.  It
does not retune the selector, build a force context, propagate, or create a
new test score.  The output is an immutable diagnostic artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from prepare_fresh_holdout import immutable_json
from selector_v3 import FEATURE_NAMES, choose_v3


OUT = "outputs/selector_holdout24"
MATRIX = f"{OUT}/matrix.json"
MODEL = "outputs/selector_v3/model.json"
FEATURE_DIR = f"{OUT}/features"
RESULT = f"{OUT}/failure_diagnostic.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(root: Path, relative: str) -> Any:
    return json.loads((root / relative).read_text(encoding="utf-8"))


def _tree_usage(model: dict[str, Any]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for model_id, tree in model["trees"].items():
        counts: dict[str, int] = {}

        def visit(node: dict[str, Any]) -> None:
            if node.get("type") != "split":
                return
            index = int(node["feature_index"])
            name = FEATURE_NAMES[index] if index < len(FEATURE_NAMES) else f"feature_{index}"
            counts[name] = counts.get(name, 0) + 1
            visit(node["left"])
            visit(node["right"])

        visit(tree["root"])
        result[model_id] = {name: counts[name] for name in FEATURE_NAMES if name in counts}
    return result


def _feature_map(root: Path) -> dict[tuple[str, float], dict[str, Any]]:
    result: dict[tuple[str, float], dict[str, Any]] = {}
    files = sorted((root / FEATURE_DIR).glob("object_*.json"))
    if len(files) != 24:
        raise ValueError(f"expected 24 cached feature files, got {len(files)}")
    for path in files:
        item = _read(root, str(path.relative_to(root)))
        object_id = str(path.stem.removeprefix("object_"))
        features = item.get("features")
        if not isinstance(features, dict):
            raise ValueError(f"missing features in {path}")
        for horizon, feature in features.items():
            key = (object_id, float(horizon))
            if key in result:
                raise ValueError(f"duplicate cached feature {key}")
            if feature.get("feature_names") != list(FEATURE_NAMES) or len(feature.get("vector", [])) != len(FEATURE_NAMES):
                raise ValueError(f"feature schema mismatch {key}")
            result[key] = feature
    if len(result) != 120:
        raise ValueError(f"expected 120 cached features, got {len(result)}")
    return result


def _errors(records: list[dict[str, Any]], object_id: str, horizon: float, tolerance: float) -> dict[str, dict[str, Any]]:
    found = [row for row in records if str(row["object_id"]) == object_id and float(row["horizon_days"]) == horizon]
    if len(found) != 4:
        raise ValueError(f"expected four candidate records for {object_id}/{horizon}, got {len(found)}")
    result = {}
    for row in found:
        record = row["record"]
        result[row["model_id"]] = {
            "max_position_error_km": record["max_position_error_km"],
            "numerical_difference_km": record["numerical_difference_km"],
            "actual_eligible": float(record["max_position_error_km"]) <= tolerance and float(record["numerical_difference_km"]) <= 0.1 * tolerance,
        }
    return result


def analyze(root: Path) -> dict[str, Any]:
    root = root.resolve()
    matrix = _read(root, MATRIX)
    model = _read(root, MODEL)
    features = _feature_map(root)
    choices = matrix.get("choices")
    records = matrix.get("records")
    if not isinstance(choices, list) or not isinstance(records, list) or len(records) != 480 or len(choices) != 1440:
        raise ValueError("unexpected completed selector matrix dimensions")

    failures = [choice for choice in choices if choice["method"] in ("tree_guard", "horizon_v2") and not choice["actual_eligible"]]
    diagnostics = []
    for choice in failures:
        object_id, horizon, tolerance = str(choice["object_id"]), float(choice["horizon_days"]), float(choice["tolerance_km"])
        cached = features[(object_id, horizon)]
        selected = choice.get("selected_model_id", choice.get("model_id"))
        item = {
            "method": choice["method"],
            "object_id": object_id,
            "horizon_days": horizon,
            "tolerance_km": tolerance,
            "selected_model_id": selected,
            "actual_selected_max_position_error_km": choice["max_position_error_km"],
            "actual_selected_numerical_difference_km": choice["numerical_difference_km"],
            "actual_selected_eligible": False,
            "all_candidate_errors": _errors(records, object_id, horizon, tolerance),
            "strong_encounter": choice.get("strong_encounter"),
            "warning": choice.get("warning"),
        }
        if choice["method"] == "tree_guard":
            matching_records = [record for record in records
                                if str(record["object_id"]) == object_id
                                and float(record["horizon_days"]) == horizon
                                and record["model_id"] == selected]
            if len(matching_records) != 1:
                raise ValueError(f"missing unique matrix feature for {object_id}/{horizon}/{selected}")
            matrix_feature = matching_records[0]["feature"]
            if matrix_feature.get("vector") != cached.get("vector"):
                raise ValueError(f"cached/matrix feature vector mismatch for {object_id}/{horizon}")
            reevaluated = choose_v3(cached, horizon, tolerance, model)
            saved_fields = {
                "model_id": choice.get("model_id"),
                "selected_model_id": choice.get("selected_model_id"),
                "status": choice.get("status"),
                "fallback": bool(choice.get("fallback")),
                "warning": bool(choice.get("warning")),
                "strong_encounter": bool(choice.get("strong_encounter")),
            }
            reevaluated_fields = {
                "model_id": reevaluated["model_id"],
                "selected_model_id": reevaluated["model_id"],
                "status": reevaluated["status"],
                "fallback": bool(reevaluated["fallback"]),
                "warning": reevaluated["status"] in ("no_candidate_predicted", "strong_encounter_unvalidated"),
                "strong_encounter": bool(reevaluated.get("strong_encounter", False)),
            }
            if saved_fields != reevaluated_fields:
                raise ValueError(f"frozen tree decision differs for {object_id}/{horizon}/{tolerance}")
            item["frozen_tree_decision"] = {
                "model_id": selected,
                "predicted_error_caps_km": choice.get("predicted_error_caps_km"),
            }
            item["reevaluated_tree_decision"] = {
                "model_id": reevaluated["model_id"],
                "predicted_error_caps_km": reevaluated["predicted_error_caps_km"],
                "status": reevaluated["status"],
                "fallback": reevaluated["fallback"],
            }
            item["frozen_tree_decision_matches"] = reevaluated["model_id"] == selected
        diagnostics.append(item)

    feature_3418 = {str(h): features[("3418", float(h))] for h in (7.0, 30.0, 90.0, 180.0, 365.0)}
    initial_record = next(row for row in records if str(row["object_id"]) == "3418")
    initial = initial_record["initial_state"]
    shape = {
        "object_id": "3418",
        "initial_state_au_au_per_day": initial,
        "initial_radius_au": feature_3418["365.0"]["initial_radius_au"],
        "initial_eccentricity": feature_3418["365.0"]["initial_eccentricity"],
        "initial_semimajor_axis_au": None,
        "semimajor_axis_note": "Not stored in the cached feature/matrix schema; no force context or reconstruction was used.",
        "small_body_proxy_km_by_horizon": {h: feature_3418[h]["base_features"]["small_body_proxy_km"] for h in feature_3418},
    }
    source_paths = [MATRIX, MODEL, *[str(path.relative_to(root)) for path in sorted((root / FEATURE_DIR).glob("object_*.json"))]]
    result = {
        "schema_version": 1,
        "verification": "passed",
        "scope": "post-hoc failure diagnosis; no retuning, no propagation, no new test score",
        "source_sha256": {path: _sha(root / path) for path in source_paths},
        "failure_count": len(diagnostics),
        "failures": diagnostics,
        "tree_feature_usage_split_counts": _tree_usage(model),
        "object_3418_initial_and_proxy_diagnostics": shape,
        "interpretation": [
            "The listed errors are model-derived Horizons regression values from the completed holdout matrix.",
            "All candidate errors are shown to distinguish a selector miss from a candidate-set limitation.",
            "This artifact does not alter the frozen selector or report a new validation score.",
        ],
    }
    immutable_json(root / RESULT, result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    output = analyze(args.root)
    print(json.dumps({"verification": output["verification"], "failure_count": output["failure_count"],
                      "tree_decisions_match": all(item.get("frozen_tree_decision_matches", True) for item in output["failures"])}, indent=2))

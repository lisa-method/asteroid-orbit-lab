"""Small, deterministic, read-only verification of frozen selector-v3 training.

The verifier rebuilds the saved CART artifact from the existing development
matrix.  It does not build features, propagate trajectories, or inspect a
fresh holdout.  Its output is a compact audit record for later reports.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from prepare_fresh_holdout import check_hashes, immutable_json, sha
from selector_v3 import FEATURE_NAMES, fit_selector


CONFIG = "configs/force_models_v2.json"
SAMPLE = "data/processed/development30/sample.json"
TARGETS = "outputs/force_models_v2/results.json"
OUT = "outputs/selector_v3"
MODEL = f"{OUT}/model.json"
TRAINING_FREEZE = f"{OUT}/training_freeze.json"
REPLAY = f"{OUT}/development_replay.json"


def _read(root: Path, relative: str) -> Any:
    return json.loads((root / relative).read_text(encoding="utf-8"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _check_provenance(root: Path, document: dict[str, Any], label: str) -> int:
    provenance = document.get("provenance")
    _require(isinstance(provenance, dict), f"{label} has no provenance")
    hashes = provenance.get("hashes")
    _require(isinstance(hashes, dict) and hashes, f"{label} has no frozen hashes")
    check_hashes(root, hashes)
    return len(hashes)


def _rebuild(root: Path, config: dict[str, Any], sample: dict[str, Any], targets: dict[str, Any], model: dict[str, Any]) -> dict[str, Any]:
    objects = sample.get("objects")
    _require(isinstance(objects, list), "sample.objects is not a list")
    by_id = {str(item["id"]): item for item in objects}
    _require(len(by_id) == len(objects) == 30, "expected 30 unique sample objects")
    feature_map: dict[tuple[str, float], list[float]] = {}
    feature_files = sorted((root / OUT / "development_features").glob("object_*.json"))
    _require(len(feature_files) == 30, "expected 30 feature checkpoints")
    for path in feature_files:
        item = _read(root, str(path.relative_to(root)))
        object_id = str(item.get("object_id"))
        _require(object_id in by_id, f"feature checkpoint has unknown object {object_id}")
        _require(item.get("split") == by_id[object_id].get("split"), f"feature split differs for {object_id}")
        rows = item.get("rows")
        _require(isinstance(rows, list) and len(rows) == 5, f"feature horizons incomplete for {object_id}")
        for row in rows:
            _require(row.get("feature_names") == list(FEATURE_NAMES), f"feature names differ for {object_id}")
            vector = row.get("vector")
            _require(isinstance(vector, list) and len(vector) == len(FEATURE_NAMES), f"feature dimension differs for {object_id}")
            key = (object_id, float(row["horizon_days"]))
            _require(key not in feature_map, f"duplicate feature row {key}")
            feature_map[key] = [float(value) for value in vector]
    _require(len(feature_map) == 150, "expected 150 feature rows")

    train: list[dict[str, Any]] = []
    calibration: list[dict[str, Any]] = []
    records = targets.get("records")
    _require(isinstance(records, list) and len(records) == 600, "expected 600 target records")
    seen: set[tuple[str, float, str]] = set()
    for row in records:
        object_id = str(row["object_id"])
        key = (object_id, float(row["horizon_days"]), str(row["model_id"]))
        _require(key not in seen, f"duplicate target row {key}")
        seen.add(key)
        _require(object_id in by_id and row["split"] == by_id[object_id]["split"], f"target split differs for {object_id}")
        record = {key_name: row[key_name] for key_name in (
            "object_id", "horizon_days", "model_id", "split", "max_position_error_km",
            "numerical_difference_km", "runtime_median_seconds")}
        record["features"] = feature_map[(object_id, float(row["horizon_days"]))]
        (train if row["split"] == "train" else calibration).append(record)
    _require(seen.__len__() == 600, "target row count mismatch")
    learned = fit_selector(train, calibration, config["models"], config["numerical_budget_fraction"])
    learned_keys = sorted(learned)
    _require(all(learned[key] == model.get(key) for key in learned_keys), "deterministic refit differs from saved model")
    _require(model.get("feature_names") == list(FEATURE_NAMES), "saved model feature names differ")
    _require(model.get("feature_dimension") == len(FEATURE_NAMES), "saved model feature dimension differs")
    return {
        "exact": True,
        "compared_learned_fields": learned_keys,
        "training_rows": len(train),
        "calibration_rows": len(calibration),
        "training_objects": len({str(row["object_id"]) for row in train}),
        "calibration_objects": len({str(row["object_id"]) for row in calibration}),
        "feature_rows": len(feature_map),
        "target_rows": len(records),
    }


def verify(root: Path) -> dict[str, Any]:
    root = root.resolve()
    config = _read(root, CONFIG)
    sample = _read(root, SAMPLE)
    targets = _read(root, TARGETS)
    model = _read(root, MODEL)
    training_freeze = _read(root, TRAINING_FREEZE)
    replay = _read(root, REPLAY)

    provenance_hash_counts = {
        "training_freeze": _check_provenance(root, training_freeze, "training_freeze"),
        "model": _check_provenance(root, model, "model"),
        "replay": _check_provenance(root, replay, "development_replay"),
    }
    feature_hashes = model.get("feature_hashes")
    _require(isinstance(feature_hashes, dict) and len(feature_hashes) == 30, "expected 30 feature hashes")
    check_hashes(root, feature_hashes)
    model_sha = sha(root / MODEL)
    _require(replay.get("model_sha256") == model_sha, "replay model hash differs")

    objects = sample.get("objects", [])
    train_ids = sorted(str(item["id"]) for item in objects if item.get("split") == "train")
    calibration_ids = sorted(str(item["id"]) for item in objects if item.get("split") == "validation")
    _require(len(train_ids) == 18 and len(calibration_ids) == 12 and not set(train_ids) & set(calibration_ids), "original 18/12 labels changed")
    _require(model.get("training_object_ids") == train_ids, "saved training labels differ")
    _require(model.get("calibration_object_ids") == calibration_ids, "saved calibration labels differ")

    choices = replay.get("choices")
    _require(isinstance(choices, list) and len(choices) == 450, "expected 450 replay choices")
    choice_keys: set[tuple[str, float, float]] = set()
    for choice in choices:
        key = (str(choice["object_id"]), float(choice["horizon_days"]), float(choice["tolerance_km"]))
        _require(key not in choice_keys, f"duplicate replay choice {key}")
        choice_keys.add(key)
        expected_split = next((item["split"] for item in objects if str(item["id"]) == key[0]), None)
        _require(choice.get("split") == expected_split, f"replay split differs for {key[0]}")

    refit = _rebuild(root, config, sample, targets, model)
    result = {
        "schema_version": 1,
        "verification": "passed",
        "scope": "read-only deterministic verification of frozen development30 selector training",
        "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "known_counts": {"training_objects": 18, "calibration_objects": 12, "target_rows": 600,
                         "feature_rows": 150, "replay_choices": 450},
        "observed_counts": {"training_objects": len(train_ids), "calibration_objects": len(calibration_ids),
                            "target_rows": len(targets.get("records", [])), "feature_rows": refit["feature_rows"],
                            "replay_choices": len(choices)},
        "original_split_labels": {"training_object_ids": train_ids, "calibration_object_ids": calibration_ids},
        "deterministic_refit": refit,
        "hash_checks": {"provenance_hash_counts": provenance_hash_counts, "feature_hash_count": len(feature_hashes),
                        "model_sha256": model_sha, "replay_model_hash_matches": True},
        "feature_schema": {"names": list(FEATURE_NAMES), "dimension": len(FEATURE_NAMES),
                           "horizons_days": [float(value) for value in config["horizons_days"]]},
    }
    immutable_json(root / (OUT + "/training_verification.json"), result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(verify(args.root), indent=2, sort_keys=True))

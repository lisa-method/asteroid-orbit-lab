"""Train/calibrate only on the inspected development30 and freeze before sampling."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from ng_inputs_v2 import load_ng_input
from prepare_fresh_holdout import check_hashes, immutable_json, sha
from prepare_selector_holdout24 import _catalogue_paths
from run_apophis_reference_time import source_closure
from run_development_benchmark import _find_initial, load_development_context, load_object_rows
from run_force_models_v2 import _runtime_environment, eligible
from selector_v3 import choose_v3, features_v3, fit_selector

OUT = "outputs/selector_v3"
CONFIG = "configs/force_models_v2.json"
FEATURE_CONFIG = "configs/development30.json"
SAMPLE = "data/processed/development30/sample.json"
TARGETS = "outputs/force_models_v2/results.json"
CONTRACT = "docs/SELECTOR_V3_CONTRACT.md"


def read(root, relative):
    return json.loads((root / relative).read_text())


def checked(path, provenance):
    if not path.exists():
        return None
    row = json.loads(path.read_text())
    if row.get("provenance") != provenance:
        raise ValueError(f"Selector development provenance mismatch: {path}")
    return row


def validate_design(sample, config, records):
    objects = sample["objects"]
    by_id = {o["id"]: o for o in objects}
    train = {o["id"] for o in objects if o["split"] == "train"}
    calibration = {o["id"] for o in objects if o["split"] == "validation"}
    if len(objects) != 30 or len(by_id) != 30 or len(train) != 18 or len(calibration) != 12 or train & calibration:
        raise ValueError("Require the original whole-object 18/12 design")
    expected = {(o["id"], float(h), m["model_id"]) for o in objects
                for h in config["horizons_days"] for m in config["models"]}
    keys = {(r["object_id"], float(r["horizon_days"]), r["model_id"]) for r in records}
    if len(records) != 600 or len(keys) != len(records) or keys != expected:
        raise ValueError("Require the complete unique original v2 600-row matrix")
    for r in records:
        obj = by_id[r["object_id"]]
        if r["split"] != obj["split"] or r["start_date"] != obj["start_date"]:
            raise ValueError("Original target split/date differs from sample")
    return train, calibration


def setup(root):
    config, feature_config, sample = (read(root, p) for p in (CONFIG, FEATURE_CONFIG, SAMPLE))
    targets = read(root, TARGETS)
    target_hashes = dict(targets["provenance"]["source_hashes"])
    for inputs in targets["provenance"]["input_hashes_by_object_model"].values():
        for relative, expected in inputs.items():
            relative = relative.removeprefix("ng:")
            if relative in target_hashes and target_hashes[relative] != expected:
                raise ValueError("Conflicting frozen v2 input hashes")
            target_hashes[relative] = expected
    check_hashes(root, target_hashes)
    validate_design(sample, config, targets["records"])
    paths = source_closure(root, ["train_selector_v3"])
    paths += [CONFIG, FEATURE_CONFIG, SAMPLE, TARGETS, CONTRACT]
    hashes = {**target_hashes, **{p: sha(root / p) for p in sorted(set(paths))}}
    provenance = {"hashes": hashes, "runtime": _runtime_environment(),
                  "scope": "inspected development30 train18/calibration12; no holdout targets"}
    path = root / OUT / "training_freeze.json"
    previous = checked(path, provenance)
    if previous is None:
        immutable_json(path, {"provenance": provenance, "created_utc": dt.datetime.now(dt.timezone.utc).isoformat()})
    return config, feature_config, sample, targets, provenance


def train(root):
    config, feature_config, sample, targets, provenance = setup(root)
    model_path = root / OUT / "model.json"
    existing = checked(model_path, provenance)
    if existing is not None:
        check_hashes(root, existing["feature_hashes"])
        return existing
    context = load_development_context(root, config)
    features = {}
    feature_paths = []
    for obj in sample["objects"]:
        path = root / OUT / "development_features" / f"object_{obj['id']}.json"
        item = checked(path, provenance)
        if item is None:
            daily, _ = load_object_rows(root, root / config["raw_directory"], obj)
            start, initial = _find_initial(daily, obj["start_date"])
            ng = load_ng_input(root / config["raw_directory"] / "asteroids" / f"asteroid_{obj['id']}_daily.json")
            rows = [features_v3(initial, start, float(h), context, feature_config, ng=ng)
                    for h in config["horizons_days"]]
            item = {"provenance": provenance, "object_id": obj["id"], "split": obj["split"], "rows": rows}
            immutable_json(path, item)
            print(json.dumps({"development_features_completed": obj["id"]}), flush=True)
        if item["object_id"] != obj["id"] or item["split"] != obj["split"]:
            raise ValueError("Feature checkpoint belongs to another object/split")
        if [r["horizon_days"] for r in item["rows"]] != [float(h) for h in config["horizons_days"]]:
            raise ValueError("Incomplete feature horizons")
        for row in item["rows"]:
            features[(obj["id"], row["horizon_days"])] = row
        feature_paths.append(path.relative_to(root).as_posix())
    training, calibration = [], []
    for row in targets["records"]:
        record = {k: row[k] for k in ("object_id", "horizon_days", "model_id", "split",
                  "max_position_error_km", "numerical_difference_km", "runtime_median_seconds")}
        record["features"] = features[(row["object_id"], row["horizon_days"])]["vector"]
        (training if row["split"] == "train" else calibration).append(record)
    artifact = fit_selector(training, calibration, config["models"], config["numerical_budget_fraction"])
    artifact.update(provenance=provenance, feature_hashes={p: sha(root / p) for p in feature_paths},
                    shared_ephemeris_load_seconds=context["load_seconds"])
    immutable_json(model_path, artifact)
    return artifact


def replay(root):
    artifact = train(root)
    config, _, sample, targets, provenance = setup(root)
    path = root / OUT / "development_replay.json"
    previous = checked(path, provenance)
    if previous is not None:
        return previous
    features = {}
    for obj in sample["objects"]:
        item = read(root, OUT + f"/development_features/object_{obj['id']}.json")
        for r in item["rows"]:
            features[(obj["id"], r["horizon_days"])] = r
    lookup = {(r["object_id"], r["horizon_days"], r["model_id"]): r for r in targets["records"]}
    choices = []
    for obj in sample["objects"]:
        for h in config["horizons_days"]:
            for tol in config["position_tolerances_km"]:
                decision = choose_v3(features[(obj["id"], h)], h, tol, artifact)
                selected = lookup[(obj["id"], h, decision["model_id"])]
                choices.append({"object_id": obj["id"], "split": obj["split"], **decision,
                    "actual_eligible": eligible(selected, tol, config["numerical_budget_fraction"]),
                    "max_position_error_km": selected["max_position_error_km"]})
    result = {"provenance": provenance, "model_sha256": sha(root / OUT / "model.json"), "choices": choices,
              "scope": "inspected training/calibration replay, not an independent score"}
    immutable_json(path, result)
    return result


def freeze_method(root):
    """Only this action unlocks new target selection; all learned values are fixed."""
    artifact = train(root)
    replay(root)
    paths = source_closure(root, ["train_selector_v3", "run_selector_holdout24", "prepare_selector_holdout24"])
    paths += [OUT + "/model.json", OUT + "/training_freeze.json", OUT + "/development_replay.json",
              "outputs/force_models_v2/rules.json", "data/processed/fresh_holdout12/sample.json",
              "configs/eda_pilot_6.json", "configs/b3plus_pilot_6.json", CONTRACT]
    paths += [p.relative_to(root).as_posix() for p in _catalogue_paths(root).values()]
    for required in ("src/run_selector_holdout24.py", "tests/test_selector_v3.py",
                     "tests/test_selector_tree.py", "tests/test_prepare_selector_holdout24.py",
                     "tests/test_selector_holdout24.py", "tests/test_train_selector_v3.py"):
        if not (root / required).is_file():
            raise ValueError(f"Finish and review before method freeze: {required}")
        paths.append(required)
    hashes = {**artifact["provenance"]["hashes"], **artifact["feature_hashes"],
              **{p: sha(root / p) for p in sorted(set(paths))}}
    value = {"hashes": hashes, "runtime": _runtime_environment(),
             "model_path": OUT + "/model.json", "contract": CONTRACT,
             "scope": "freeze before selecting new24 IDs and fetching their target states"}
    path = root / OUT / "method_freeze.json"
    if path.exists():
        saved = json.loads(path.read_text())
        if any(saved[k] != v for k, v in value.items()):
            raise ValueError("Method freeze differs; use a new experiment version")
        check_hashes(root, saved["hashes"])
        return saved
    # The sampler itself rejects sampling before this file exists.
    if (root / "data/processed/selector_holdout24/sample.json").exists():
        raise ValueError("Fresh sample already exists before method freeze")
    immutable_json(path, {**value, "created_utc": dt.datetime.now(dt.timezone.utc).isoformat()})
    return value


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("phase", choices=("train", "replay", "freeze"))
    p.add_argument("--root", type=Path, default=Path("."))
    args = p.parse_args()
    result = {"train": train, "replay": replay, "freeze": freeze_method}[args.phase](args.root.resolve())
    print(json.dumps({k: v for k, v in result.items() if k in ("calibration_margin_log10", "training_row_count", "calibration_row_count", "scope")}, indent=2))

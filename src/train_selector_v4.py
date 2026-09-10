"""Train v4 on inspected objects, verify it, then freeze before fresh sampling."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from collections import Counter

from ng_inputs_v2 import load_ng_input
from prepare_selector_holdout24 import check_hashes, immutable_json, sha, _catalogue_paths
from run_apophis_reference_time import source_closure
from run_development_benchmark import _find_initial, load_development_context, load_object_rows
from run_force_models_v2 import _runtime_environment, eligible
from selector_v3 import choose_v3
from selector_v4 import choose_v4, features_v4, fit_selector_v4

OUT = "outputs/selector_v4"
CONTRACT = "docs/SELECTOR_V4_CONTRACT.md"
CONFIG = "configs/force_models_v2.json"
FEATURE_CONFIG = "configs/development30.json"
OLD_METHOD = "outputs/selector_v3/method_freeze.json"
OLD_MODEL = "outputs/selector_v3/model.json"
SOURCES = (
    ("development30", "data/processed/development30/sample.json", "outputs/force_models_v2/results.json"),
    ("fresh_holdout12", "data/processed/fresh_holdout12/sample.json", "outputs/fresh_holdout12/matrix.json"),
    ("selector_holdout24", "data/processed/selector_holdout24/sample.json", "outputs/selector_holdout24/matrix.json"),
)


def read(root, relative):
    return json.loads((root / relative).read_text())


def checked(path, provenance):
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    if value.get("provenance") != provenance:
        raise ValueError(f"V4 development provenance mismatch: {path}")
    return value


def merge_hashes(destination, values):
    for path, expected in values.items():
        path = path.removeprefix("ng:")
        if path in destination and destination[path] != expected:
            raise ValueError(f"Conflicting historical hash: {path}")
        destination[path] = expected


def load_design(root):
    """Read only previously inspected targets, preserving source identities/dates."""
    config = read(root, CONFIG)
    models = [m["model_id"] for m in config["models"]]
    horizons = [float(h) for h in config["horizons_days"]]
    old_method = read(root, OLD_METHOD)
    hashes = dict(old_method["hashes"])
    old_model = read(root, OLD_MODEL)
    objects, records = [], []
    seen = set()
    for namespace, sample_path, matrix_path in SOURCES:
        sample = read(root, sample_path)
        matrix = read(root, matrix_path)
        provenance = matrix["provenance"]
        merge_hashes(hashes, provenance.get("hashes", provenance.get("source_hashes", {})))
        for item in provenance.get("input_hashes_by_object_model", {}).values():
            merge_hashes(hashes, item)
        hashes[sample_path], hashes[matrix_path] = sha(root / sample_path), sha(root / matrix_path)
        source_objects = {str(o["id"]): o for o in sample["objects"]}
        expected_count = {"development30": 30, "fresh_holdout12": 12, "selector_holdout24": 24}[namespace]
        if len(source_objects) != len(sample["objects"]) or len(source_objects) != expected_count:
            raise ValueError("Historical sample object count is incomplete/duplicate")
        if seen & source_objects.keys():
            raise ValueError("Historical source objects overlap")
        seen.update(source_objects)
        source_records = [r["record"] if namespace == "selector_holdout24" else r for r in matrix["records"]]
        keys = {(str(r["object_id"]), float(r["horizon_days"]), r["model_id"]) for r in source_records}
        expected = {(oid, h, m) for oid in source_objects for h in horizons for m in models}
        if len(keys) != len(source_records) or keys != expected:
            raise ValueError(f"Incomplete historical candidate matrix: {namespace}")
        roles = {}
        for oid, source in source_objects.items():
            if namespace == "development30":
                if source["split"] not in ("train", "validation"):
                    raise ValueError("Original development split changed")
                role = "train" if source["split"] == "train" else "calibration"
            else:
                role = "train" if namespace == "selector_holdout24" else "calibration"
            roles[oid] = role
            objects.append({**source, "id": oid, "original_split": source["split"],
                            "split": role, "source_namespace": namespace,
                            "raw_directory": f"data/raw/{namespace}"})
        for source in source_records:
            oid = str(source["object_id"])
            if source["start_date"] != source_objects[oid]["start_date"] or source["split"] != source_objects[oid]["split"]:
                raise ValueError("Historical record date/split differs from sample")
            mid, h = source["model_id"], float(source["horizon_days"])
            records.append({k: source[k] for k in ("object_id", "model_id", "horizon_days",
                           "max_position_error_km", "numerical_difference_km")} | {
                "split": roles[oid], "source_namespace": namespace,
                "runtime_median_seconds": old_model["median_cost_seconds"][mid][str(h)],
                "cost_basis": "frozen original18 training median; shared by both v4 selectors",
            })
        # Raw targets/context and source contracts are checked from their saved provenance.
        del matrix
    if Counter(o["split"] for o in objects) != {"train": 42, "calibration": 24}:
        raise ValueError("Require exactly train42/calibration24 whole objects")
    if not set(old_model["training_object_ids"]) <= {o["id"] for o in objects if o["split"] == "train"}:
        raise ValueError("Cost source is not a subset of training objects")
    hashes[OLD_METHOD], hashes[OLD_MODEL] = sha(root / OLD_METHOD), sha(root / OLD_MODEL)
    check_hashes(root, hashes)
    return config, objects, records, hashes


def setup(root):
    config, objects, records, old_hashes = load_design(root)
    paths = source_closure(root, ["train_selector_v4"])
    paths += [CONFIG, FEATURE_CONFIG, CONTRACT]
    hashes = dict(old_hashes)
    merge_hashes(hashes, {p: sha(root / p) for p in sorted(set(paths))})
    provenance = {"hashes": hashes, "runtime": _runtime_environment(),
                  "scope": "inspected train42/calibration24; no fresh v4 target data"}
    path = root / OUT / "training_freeze.json"
    if checked(path, provenance) is None:
        immutable_json(path, {"provenance": provenance, "objects": objects,
                             "created_utc": dt.datetime.now(dt.timezone.utc).isoformat()})
    return config, objects, records, provenance


def train(root):
    config, objects, records, provenance = setup(root)
    model_path = root / OUT / "model.json"
    saved = checked(model_path, provenance)
    if saved is not None:
        check_hashes(root, saved["feature_hashes"])
        check_hashes(root, {OUT + "/training_rows.json": saved["training_rows_sha256"]})
        return saved
    context = load_development_context(root, config)
    feature_config = read(root, FEATURE_CONFIG)
    features, feature_hashes = {}, {}
    for obj in objects:
        path = root / OUT / "features" / f"object_{obj['id']}.json"
        item = checked(path, provenance)
        if item is None:
            daily, _ = load_object_rows(root, root / obj["raw_directory"], obj)
            start, initial = _find_initial(daily, obj["start_date"])
            ng = load_ng_input(root / obj["raw_directory"] / "asteroids" / f"asteroid_{obj['id']}_daily.json")
            rows = [features_v4(initial, start, float(h), context, feature_config, config, ng=ng)
                    for h in config["horizons_days"]]
            item = {"provenance": provenance, "object_id": obj["id"], "split": obj["split"], "rows": rows}
            immutable_json(path, item)
            print(json.dumps({"v4_development_features": obj["id"], "completed": len(feature_hashes) + 1, "total": 66}), flush=True)
        if item["object_id"] != obj["id"] or item["split"] != obj["split"] or [r["horizon_days"] for r in item["rows"]] != config["horizons_days"]:
            raise ValueError("Invalid v4 feature checkpoint identity/split/horizons")
        for row in item["rows"]:
            features[(obj["id"], row["horizon_days"])] = row
        feature_hashes[path.relative_to(root).as_posix()] = sha(path)
    training, calibration = [], []
    for record in records:
        feature = features[(record["object_id"], record["horizon_days"])]
        row = {**record, "features": feature["vector"], "feature_result": feature}
        (training if row["split"] == "train" else calibration).append(row)
    rows_path = root / OUT / "training_rows.json"
    immutable_json(rows_path, {"provenance": provenance, "training": training, "calibration": calibration})
    artifact = fit_selector_v4(training, calibration, config["models"], config["numerical_budget_fraction"])
    artifact.update(provenance=provenance, feature_hashes=feature_hashes,
                    training_rows_sha256=sha(rows_path), training_row_count=len(training),
                    calibration_row_count=len(calibration), cost_basis="frozen original18 train median table",
                    shared_ephemeris_load_seconds=context["load_seconds"],
                    conditional_on_initial_state=True, covariance_calibrated=False)
    immutable_json(model_path, artifact)
    return artifact


def replay(root):
    artifact = train(root)
    path = root / OUT / "development_replay.json"
    previous = checked(path, artifact["provenance"])
    if previous is not None:
        return previous
    data = read(root, OUT + "/training_rows.json")
    rows = data["training"] + data["calibration"]
    config = read(root, CONFIG)
    old = read(root, OLD_MODEL)
    lookup = {(r["object_id"], r["horizon_days"], r["model_id"]): r for r in rows}
    cases = {(r["object_id"], r["horizon_days"]): r for r in rows}
    choices = []
    for (oid, h), row in sorted(cases.items()):
        for tol in config["position_tolerances_km"]:
            for method in ("tree_v3", "physics_v4", "hybrid_v4"):
                if method == "tree_v3":
                    decision = choose_v3(row["feature_result"], h, tol, old)
                    decision["warning"] = decision["status"] != "predicted_feasible"
                else:
                    decision = choose_v4(row["feature_result"], h, tol, artifact, method=method)
                selected = lookup[(oid, h, decision["model_id"])]
                choices.append({**decision, "method": method, "object_id": oid, "split": row["split"],
                    "source_namespace": row["source_namespace"],
                    "actual_eligible": eligible(selected, tol, config["numerical_budget_fraction"]),
                    "max_position_error_km": selected["max_position_error_km"]})
    summaries = []
    for split in ("train", "calibration"):
        for tol in config["position_tolerances_km"]:
            for method in ("tree_v3", "physics_v4", "hybrid_v4"):
                subset = [c for c in choices if c["split"] == split and c["tolerance_km"] == tol and c["method"] == method]
                summaries.append({"split": split, "tolerance_km": tol, "method": method,
                    "cases": len(subset), "eligible_cases": sum(c["actual_eligible"] for c in subset),
                    "warnings": sum(c["warning"] for c in subset),
                    "silent_failures": sum(not c["actual_eligible"] and not c["warning"] for c in subset)})
    value = {"provenance": artifact["provenance"], "model_sha256": sha(root / OUT / "model.json"),
             "scope": "inspected replay; former holdouts now development; not a fresh validation score",
             "choices": choices, "summaries": summaries}
    immutable_json(path, value)
    return value


def verify_training(root):
    artifact = train(root)
    rows = read(root, OUT + "/training_rows.json")
    config = read(root, CONFIG)
    rebuilt = fit_selector_v4(rows["training"], rows["calibration"], config["models"], config["numerical_budget_fraction"])
    if any(artifact.get(k) != v for k, v in rebuilt.items()):
        raise ValueError("Deterministic v4 refit differs from saved model")
    if len(rows["training"]) != 840 or len(rows["calibration"]) != 480:
        raise ValueError("Incomplete training/calibration matrices")
    # Independent checks of scalar physical scales, costs and support bounds.
    train_rows, cal_rows = rows["training"], rows["calibration"]
    import math
    import statistics
    for h in config["horizons_days"]:
        key = str(float(h))
        full = [r for r in train_rows if r["horizon_days"] == h and r["model_id"] == config["fallback_model_id"]]
        effective = lambda r: max(r["max_position_error_km"], r["numerical_difference_km"] / .1, 1e-12)
        floor = max(map(effective, full))
        if artifact["baseline_floor_km"][key] != floor:
            raise ValueError("Full-model train floor does not reproduce")
        for i in range(1, 8):
            vals = [r["features"][i] for r in train_rows if r["horizon_days"] == h]
            if artifact["support_bounds"][key][str(i)] != [min(vals), max(vals)]:
                raise ValueError("Support bounds do not reproduce from training alone")
        for model in config["models"]:
            mid = model["model_id"]
            median = statistics.median(r["runtime_median_seconds"] for r in train_rows if r["horizon_days"] == h and r["model_id"] == mid)
            if artifact["median_cost_seconds"][mid][key] != median:
                raise ValueError("Training cost table differs")
    for model in config["models"]:
        mid = model["model_id"]
        omitted = [name for name, flag in (("planet", "planets"), ("gr", "solar_gr"),
                   ("small_body", "small_bodies"), ("earth_j2", "earth_j2")) if not model[flag]]
        if model["non_grav"] == "off":
            omitted.append("ng")
        ratios = []
        for r in train_rows + cal_rows:
            if r["model_id"] != mid:
                continue
            scale = sum(r["feature_result"]["force_proxies_km"][name] for name in omitted) + artifact["baseline_floor_km"][str(float(r["horizon_days"]))]
            ratios.append(effective(r) / scale)
        if not math.isclose(artifact["physics_alpha"][mid], max(1., max(ratios)), rel_tol=1e-14):
            raise ValueError("Physics alpha does not reproduce independently")
    value = {"passed": True, "model_sha256": sha(root / OUT / "model.json"),
             "training_objects": 42, "calibration_objects": 24, "training_rows": 840, "calibration_rows": 480,
             "exact_refit": True, "independent_floor_alpha_support_cost_checks": True,
             "old_method_sha256": sha(root / OLD_METHOD), "runtime": _runtime_environment()}
    immutable_json(root / OUT / "training_verification.json", value)
    return value


def freeze_method(root):
    artifact = train(root)
    replay(root)
    verify_training(root)
    modules = ["train_selector_v4", "prepare_selector_v4_holdout24", "run_selector_v4_holdout24"]
    paths = source_closure(root, modules)
    paths += [OUT + "/" + name for name in ("model.json", "training_freeze.json", "training_rows.json", "training_verification.json", "development_replay.json")]
    paths += [CONTRACT, "tests/test_selector_v4.py", "tests/test_prepare_selector_v4_holdout24.py", "tests/test_selector_v4_holdout24.py", "tests/test_train_selector_v4.py"]
    paths += [p.relative_to(root).as_posix() for p in _catalogue_paths(root).values()]
    hashes = {**artifact["provenance"]["hashes"], **artifact["feature_hashes"]}
    merge_hashes(hashes, {p: sha(root / p) for p in sorted(set(paths))})
    check_hashes(root, hashes)
    value = {"hashes": hashes, "runtime": _runtime_environment(), "model_path": OUT + "/model.json",
             "contract": CONTRACT, "scope": "v4 frozen before selection/opening next24 targets"}
    path = root / OUT / "method_freeze.json"
    if path.exists():
        saved = read(root, OUT + "/method_freeze.json")
        if any(saved[k] != v for k, v in value.items()):
            raise ValueError("Completed v4 method freeze changed")
        return saved
    if (root / "data/processed/selector_v4_holdout24/sample.json").exists():
        raise ValueError("Fresh v4 sample was opened before method freeze")
    immutable_json(path, {**value, "created_utc": dt.datetime.now(dt.timezone.utc).isoformat()})
    return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("train", "replay", "verify", "freeze"))
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    value = {"train": train, "replay": replay, "verify": verify_training, "freeze": freeze_method}[args.phase](args.root.resolve())
    print(json.dumps({k: value[k] for k in ("passed", "scope", "training_row_count", "calibration_row_count", "physics_alpha", "summaries") if k in value}, indent=2))

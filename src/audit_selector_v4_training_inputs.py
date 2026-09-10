"""Read-only audit of the frozen selector-v4 development inputs.

The audit reads preserved matrices and feature checkpoints only.  It performs
no propagation, download, refit, or mutation of frozen artifacts.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import statistics
ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "src"))
from selector_tree import predict_tree  # noqa: E402
from ng_inputs_v2 import load_ng_input  # noqa: E402
from run_development_benchmark import _find_initial, load_object_rows  # noqa: E402
OUT = ROOT / "outputs/selector_v4/training_input_audit_complete.json"
INITIAL_OUT = ROOT / "outputs/selector_v4/training_input_audit_initial.json"
HORIZONS = (7.0, 30.0, 90.0, 180.0, 365.0)
MODELS = ("V2-B2", "V2-P", "V2-P-GR", "V2-P-GR-SB16")


def read(path: str | Path):
    return json.loads((ROOT / path).read_text())


def sha(path: str | Path) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def finite(value, label: str, nonnegative: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result) or (nonnegative and result < 0.0):
        raise ValueError(f"{label} is invalid")
    return result


def main() -> dict:
    training = read("outputs/selector_v4/training_rows.json")
    model = read("outputs/selector_v4/model.json")
    old_model = read("outputs/selector_v3/model.json")
    config = read("configs/force_models_v2.json")
    records = []
    dependency_hashes = model.get("provenance", {}).get("hashes", {})
    if not isinstance(dependency_hashes, dict):
        raise ValueError("frozen provenance hashes are missing")
    for relative, expected in dependency_hashes.items():
        path = ROOT / str(relative)
        if not path.exists() or sha(path) != expected:
            raise ValueError(f"frozen dependency hash mismatch: {relative}")
    for matrix_path in (
        "outputs/force_models_v2/results.json",
        "outputs/fresh_holdout12/matrix.json",
        "outputs/selector_holdout24/matrix.json",
    ):
        matrix = read(matrix_path)
        records.extend(r.get("record", r) for r in matrix["records"])
    source_by_key = {(str(r["object_id"]), float(r["horizon_days"]), r["model_id"]): r for r in records}
    rows = training["training"] + training["calibration"]
    if len(training["training"]) != 840 or len(training["calibration"]) != 480:
        raise ValueError("frozen row counts are incomplete")
    if len(source_by_key) != 1320:
        raise ValueError("historical source matrices do not form 1320 unique rows")
    for row in rows:
        key = (str(row["object_id"]), float(row["horizon_days"]), row["model_id"])
        source = source_by_key.get(key)
        if source is None:
            raise ValueError(f"missing historical source row {key}")
        for field in ("max_position_error_km", "numerical_difference_km"):
            if row[field] != source[field]:
                raise ValueError(f"source mismatch in {field}: {key}")
        for field in ("max_position_error_km", "numerical_difference_km", "runtime_median_seconds"):
            finite(row[field], field, True)
    objects = {(str(r["object_id"]), r["split"]) for r in rows}
    if len({o for o, _ in objects if _ == "train"}) != 42 or len({o for o, _ in objects if _ == "calibration"}) != 24:
        raise ValueError("unexpected train/calibration object counts")
    train_ids = {o for o, s in objects if s == "train"}
    cal_ids = {o for o, s in objects if s == "calibration"}
    if train_ids & cal_ids:
        raise ValueError("train/calibration overlap")
    # selector_holdout24 and fresh12 are deliberately development inputs;
    # only the newly selected v4 sample counts as fresh leakage.
    fresh_ids = {str(o["id"]) for o in read("data/processed/selector_v4_holdout24/sample.json")["objects"]}
    if (train_ids | cal_ids) & fresh_ids:
        raise ValueError("fresh object leaked into training/calibration")
    old_costs = old_model["median_cost_seconds"]
    for row in rows:
        expected = old_costs[row["model_id"]][str(float(row["horizon_days"]))]
        if row["runtime_median_seconds"] != expected:
            raise ValueError("training row cost differs from frozen v3 cost")
    cases = {}
    feature_cases = 0
    feature_field_checks = 0
    feature_hashes = model["feature_hashes"]
    for path in sorted((ROOT / "outputs/selector_v4/features").glob("object_*.json")):
        rel = path.relative_to(ROOT).as_posix()
        if rel not in feature_hashes or sha(rel) != feature_hashes[rel]:
            raise ValueError(f"feature checkpoint hash mismatch: {rel}")
        item = json.loads(path.read_text())
        oid = str(item["object_id"])
        v3_path = ROOT / f"outputs/selector_v3/development_features/object_{oid}.json"
        if not v3_path.exists():
            v3_path = ROOT / f"outputs/selector_holdout24/features/object_{oid}.json"
        v3_by_h = None
        if v3_path.exists():
            v3 = json.loads(v3_path.read_text())
            rows_v3 = v3["rows"] if "rows" in v3 else [v3["features"][str(h)] for h in HORIZONS]
            v3_by_h = {float(r["horizon_days"]): r for r in rows_v3}
        for v4 in item["rows"]:
            h = float(v4["horizon_days"])
            if h not in HORIZONS or not math.isfinite(float(v4["start_jd"])):
                raise ValueError("invalid feature horizon/start")
            if v3_by_h is not None:
                old = v3_by_h[h]
                for field in ("vector", "geometry", "initial_radius_au", "initial_eccentricity", "min_planet_distance_over_hill", "max_scattering_strength", "strong_encounter", "ng"):
                    if v4[field] != old[field]:
                        raise ValueError(f"v3/v4 feature mismatch {oid}/{h}/{field}")
                    feature_field_checks += 1
            p = v4["force_proxies_km"]
            if set(p) != {"planet", "gr", "small_body", "earth_j2", "ng"}:
                raise ValueError("incomplete proxy map")
            for key, value in p.items():
                finite(value, f"proxy {key}", True)
            for idx, key in ((3, "planet"), (4, "gr"), (5, "small_body")):
                if not math.isclose(v4["vector"][idx], math.log10(max(p[key], 1e-12)), abs_tol=1e-10):
                    raise ValueError("base proxy does not match v3 vector")
            if v4["ng"]["status"] != v4["ng_proxy_status"]:
                raise ValueError("NG status mismatch")
            if v4["ng_proxy_status"] != "available" and p["ng"] != 0.0:
                raise ValueError("unavailable NG has nonzero proxy")
            cases[(oid, h)] = v4
            feature_cases += 1
    if feature_cases != 330:
        raise ValueError(f"expected 330 feature cases, got {feature_cases}")
    if len(cases) != 330:
        raise ValueError("duplicate feature cases")
    # Every training row must point to its exact frozen feature case.
    for row in rows:
        feature = cases[(str(row["object_id"]), float(row["horizon_days"]))]
        if row["features"] != feature["vector"] or row["feature_result"] != feature:
            raise ValueError("training row feature case mismatch")
    # Bind dates/roles to all three original samples and validate raw NG gates.
    samples = {}
    for sample_path, namespace in (("data/processed/development30/sample.json", "development30"), ("data/processed/fresh_holdout12/sample.json", "fresh_holdout12"), ("data/processed/selector_holdout24/sample.json", "selector_holdout24")):
        for obj in read(sample_path)["objects"]:
            samples[str(obj["id"])] = (obj, namespace)
    raw_ng_checked = 0
    for oid, split in objects:
        obj, namespace = samples[oid]
        expected_split = "train" if namespace == "selector_holdout24" or (namespace == "development30" and obj["split"] == "train") else "calibration"
        if split != expected_split:
            raise ValueError("sample split/role mismatch")
        raw_dir = ROOT / f"data/raw/{namespace}"
        daily, _ = load_object_rows(ROOT, raw_dir, obj)
        start, _initial = _find_initial(daily, obj["start_date"])
        ng = load_ng_input(raw_dir / "asteroids" / f"asteroid_{oid}_daily.json")
        expected_status = ng.status(start)
        for h in HORIZONS:
            feature = cases[(oid, h)]
            if feature["start_jd"] != start or feature["ng"]["status"] != expected_status:
                raise ValueError("raw date or NG availability mismatch")
            if expected_status == "available":
                if ng.parameters is None or feature["ng"]["parameters"] != {name: float(getattr(ng.parameters, name)) for name in ("a1_au_d2", "a2_au_d2", "a3_au_d2")}:
                    raise ValueError("available NG parameters mismatch")
            elif feature["force_proxies_km"]["ng"] != 0.0:
                raise ValueError("unavailable NG proxy is nonzero")
            raw_ng_checked += 1
    # Independently reconstruct floors, omitted-force q scales, and alpha.
    effective = lambda r: max(float(r["max_position_error_km"]), float(r["numerical_difference_km"]) / 0.1, 1e-12)
    for h in HORIZONS:
        full = [r for r in training["training"] if float(r["horizon_days"]) == h and r["model_id"] == MODELS[-1]]
        floor = max(map(effective, full))
        if model["baseline_floor_km"][str(float(h))] != floor:
            raise ValueError("floor mismatch")
        for i in range(1, 8):
            vals = [r["features"][i] for r in training["training"] if float(r["horizon_days"]) == h]
            if model["support_bounds"][str(float(h))][str(i)] != [min(vals), max(vals)]:
                raise ValueError("support mismatch")
    for mid in MODELS:
        spec = model["force_specs"][mid]
        omitted = [name for name, flag in (("planet", "planets"), ("gr", "solar_gr"), ("small_body", "small_bodies"), ("earth_j2", "earth_j2")) if not spec[flag]]
        if spec["non_grav"] == "off":
            omitted.append("ng")
        ratios = []
        residuals = []
        for row in rows:
            if row["model_id"] != mid:
                continue
            q = sum(row["feature_result"]["force_proxies_km"][name] for name in omitted)
            scale = q + model["baseline_floor_km"][str(float(row["horizon_days"]))]
            ratio = effective(row) / scale
            ratios.append(ratio)
            if row["split"] == "calibration":
                residuals.append(math.log10(ratio) - predict_tree(model["trees"][mid], row["features"]))
        if not math.isclose(model["physics_alpha"][mid], max(1.0, max(ratios)), rel_tol=1e-12):
            raise ValueError("physics alpha reconstruction mismatch")
        if not math.isclose(model["calibration_margin_log10"][mid], max(0.0, max(residuals)), rel_tol=1e-12):
            raise ValueError("calibration margin reconstruction mismatch")
    source_hashes = {"training_rows": sha("outputs/selector_v4/training_rows.json"), "model": sha("outputs/selector_v4/model.json"), "v3_model": sha("outputs/selector_v3/model.json")}
    result = {"passed": True, "scope": "read-only frozen selector_v4 training-input audit", "source_sha256": source_hashes,
              "training_rows": 840, "calibration_rows": 480, "source_error_rows_checked": 1320,
              "training_objects": 42, "calibration_objects": 24, "feature_cases_checked": feature_cases,
              "v3_feature_field_comparisons": feature_field_checks, "cost_rows_checked": len(rows),
              "fresh_ids_disjoint": True, "ng_gate_checked": True, "tree_input_dimension": model["feature_dimension"],
              "tree_targets_and_calibration_margins_checked": True,
              "raw_ng_and_date_role_checks": raw_ng_checked,
              "feature_hashes_checked": feature_cases,
              "frozen_dependency_hashes_checked": len(dependency_hashes),
              "no_propagation": True, "no_download": True}
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


if __name__ == "__main__":
    print(json.dumps(main(), indent=2, sort_keys=True))

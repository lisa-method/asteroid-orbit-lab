"""Calibrate v2 on train and replay the inspected cohort; time primary 1-km calls."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import statistics
import time

from force_models_v2 import forecast_candidate_v2
from ng_inputs_v2 import load_ng_input
from run_development_benchmark import (_find_initial, _interpolate_endpoints, _reference_grid,
                                        load_development_context, load_object_rows)
from run_force_models_v2 import _atomic_json, _runtime_environment, eligible
from selection_v2 import choose_model_v2, fit_horizon_rule_v2, forecast_with_selection_v2
from trajectory_metrics import summarize_error_rows, trajectory_error_rows


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _checked_existing(path, provenance):
    if not path.exists():
        return None
    saved = json.loads(path.read_text())
    if saved.get("provenance") != provenance:
        raise ValueError(f"frozen provenance mismatch: {path}")
    return saved


def validate_matrix(records, sample, config):
    objects = {str(obj["id"]): obj for obj in sample["objects"]}
    if len(objects) != len(sample["objects"]):
        raise ValueError("duplicate sample objects")
    expected = {(object_id, float(h), model["model_id"]) for object_id in objects
                for h in config["horizons_days"] for model in config["models"]}
    found = set()
    for row in records:
        key = (str(row["object_id"]), float(row["horizon_days"]), row["model_id"])
        if key not in expected or key in found:
            raise ValueError("duplicate or unexpected matrix case")
        if row["split"] != objects[key[0]]["split"] or row.get("original_split", row["split"]) != row["split"]:
            raise ValueError("matrix split disagrees with the frozen sample")
        found.add(key)
    if found != expected:
        raise ValueError("incomplete matrix")


def _validate_timing_checkpoint(timings, obj, config):
    expected = {(str(obj["id"]), float(h), method) for h in config["horizons_days"] for method in ("selector", "fixed")}
    keys = [(row["object_id"], float(row["horizon_days"]), row["method"]) for row in timings]
    if len(keys) != len(expected) or set(keys) != expected:
        raise ValueError("incomplete or duplicate direct timing checkpoint")
    for row in timings:
        values = row["runtime_trials_seconds"]
        if len(values) != 3 or any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("three finite timing trials are required")
        if row["tolerance_km"] != 1. or row["runtime_median_seconds"] != statistics.median(values):
            raise ValueError("invalid timing tolerance or median")


def run(root):
    root = root.resolve()
    config_path = root / "configs/force_models_v2.json"
    config = json.loads(config_path.read_text())
    out = root / config["output_directory"]
    matrix_path = out / "results.json"
    matrix = json.loads(matrix_path.read_text())
    if matrix["scope"] != "full_development30_posthoc" or len(matrix["records"]) != 600:
        raise ValueError("the complete v2 development matrix is required")
    for relative, expected in matrix["provenance"]["source_hashes"].items():
        if digest(root / relative) != expected:
            raise ValueError(f"matrix source/input freeze changed: {relative}")
    sample = json.loads((root / config["sample_path"]).read_text())
    validate_matrix(matrix["records"], sample, config)
    train_ids = {str(obj["id"]) for obj in sample["objects"] if obj["split"] == "train"}
    train = [row for row in matrix["records"] if row.get("original_split", row["split"]) == "train"]
    if {row["object_id"] for row in train} != train_ids or len(train_ids) != 18 or len(train) != 360:
        raise ValueError("complete original 18-object training matrix required")
    provenance = {"matrix_sha256": digest(matrix_path), "config_sha256": digest(config_path),
                  "selection_source_sha256": digest(root / "src/selection_v2.py"),
                  "runner_source_sha256": digest(root / "src/run_selection_v2.py"),
                  "force_source_hashes": {path: sha for path, sha in matrix["provenance"]["source_hashes"].items() if path.startswith("src/")},
                  "runtime_environment": _runtime_environment()}
    rules_path = out / "rules.json"
    artifact = _checked_existing(rules_path, provenance)
    fitted = fit_horizon_rule_v2(train, config)
    if artifact is None:
        artifact = {**fitted, "provenance": provenance,
                    "fitted_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
        _atomic_json(rules_path, artifact)
    elif any(artifact.get(key) != value for key, value in fitted.items()):
        raise ValueError("saved rules differ from the recorded training-only calibration")
    selection_provenance = {**provenance, "rules_sha256": digest(rules_path)}
    target = out / "selection_replay.json"
    existing = _checked_existing(target, selection_provenance)
    if existing is not None:
        return existing
    objects = [obj for obj in sample["objects"] if obj["split"] == "validation"]
    rows = {(row["object_id"], float(row["horizon_days"]), row["model_id"]): row for row in matrix["records"]}
    budget = config["numerical_budget_fraction"]
    choices = []
    for obj in objects:
        for horizon in config["horizons_days"]:
            for tolerance in config["position_tolerances_km"]:
                decision = choose_model_v2(horizon, tolerance, artifact, config)
                measured = rows[(str(obj["id"]), float(horizon), decision["model_id"])]
                choices.append({"object_id": str(obj["id"]), "horizon_days": horizon, "tolerance_km": tolerance,
                                **decision, "actual_eligible": eligible(measured, tolerance, budget),
                                "max_position_error_km": measured["max_position_error_km"],
                                "numerical_difference_km": measured["numerical_difference_km"]})
    summaries = []
    for tolerance in config["position_tolerances_km"]:
        fixed = []
        for model in config["models"]:
            selected = [rows[(str(obj["id"]), float(h), model["model_id"])]
                        for obj in objects for h in config["horizons_days"]]
            fixed.append({"model_id": model["model_id"], "eligible_cases": sum(eligible(row, tolerance, budget) for row in selected),
                          "prefix_cost_seconds": sum(row["runtime_median_seconds"] for row in selected)})
        feasible = sum(any(eligible(rows[(str(obj["id"]), float(h), m["model_id"])], tolerance, budget)
                           for m in config["models"]) for obj in objects for h in config["horizons_days"])
        selected = [row for row in choices if row["tolerance_km"] == tolerance]
        summaries.append({"tolerance_km": tolerance, "cases": len(selected), "feasible_cases": feasible,
                          "selector_eligible_cases": sum(row["actual_eligible"] for row in selected),
                          "fallback_cases": sum(row["fallback"] for row in selected), "fixed": fixed})
    # Primary cost comparison is preregistered at 1 km. Keep every fixed model
    # that covers the full feasible subset; select the cheapest by matrix cost.
    primary = next(item for item in summaries if item["tolerance_km"] == 1)
    complete = [item for item in primary["fixed"] if item["eligible_cases"] == primary["feasible_cases"]]
    fixed_id = min(complete, key=lambda item: (item["prefix_cost_seconds"], item["model_id"]))["model_id"] if complete else config["fallback_model_id"]
    fixed_model = next(item for item in config["models"] if item["model_id"] == fixed_id)
    loaded = time.perf_counter()
    context = load_development_context(root, config)
    load_seconds = time.perf_counter() - loaded
    timings = []
    for obj in objects:
        checkpoint = out / "selection_checkpoints" / f"object_{obj['id']}.json"
        saved = _checked_existing(checkpoint, selection_provenance)
        if saved is not None:
            _validate_timing_checkpoint(saved["timings"], obj, config)
            timings.extend(saved["timings"])
            continue
        daily, reference = load_object_rows(root, root / config["raw_directory"], obj)
        start_jd, initial = _find_initial(daily, obj["start_date"])
        ng = load_ng_input(root / config["raw_directory"] / "asteroids" / f"asteroid_{obj['id']}_daily.json")
        object_timings = []
        for horizon in config["horizons_days"]:
            reference_times, reference_states = _reference_grid(reference, start_jd, horizon)
            trials = {"selector": [], "fixed": []}
            for repeat in range(3):
                # Alternate order to reduce a systematic warm/thermal advantage.
                for method in (("selector", "fixed") if repeat % 2 == 0 else ("fixed", "selector")):
                    if method == "selector":
                        predictions, meta, online = forecast_with_selection_v2(initial, start_jd, horizon, 1., artifact, context, config, ng=ng)
                        elapsed = online["runtime_seconds"]
                        model_id = online["decision"]["model_id"]
                    else:
                        started = time.perf_counter()
                        predictions, meta = forecast_candidate_v2(initial, start_jd, horizon, fixed_model, context, config, config["step_scale"], ng=ng)
                        elapsed = time.perf_counter() - started
                        model_id = fixed_id
                    trials[method].append(elapsed)
                    if repeat == 0:
                        interp = _interpolate_endpoints(meta["accepted_states"])
                        actual = summarize_error_rows(trajectory_error_rows(reference_times, [interp(t) for t in reference_times],
                                                                             reference_states, context["au_km"], context["day_s"]))
                        expected = rows[(str(obj["id"]), float(horizon), model_id)]
                        for key in ("max_position_error_km", "max_velocity_error_m_s"):
                            if abs(actual[key] - expected[key]) > 1e-5:
                                raise ValueError(f"direct rollout disagrees with annual prefix: {obj['id']} {horizon} {method} {key}")
                        object_timings.append({"object_id": str(obj["id"]), "horizon_days": horizon, "tolerance_km": 1., "method": method,
                                               "model_id": model_id, "actual_eligible": eligible(expected, 1., budget),
                                               "max_position_error_km": actual["max_position_error_km"]})
                    del predictions, meta
            for item in object_timings[-2:]:
                item["runtime_trials_seconds"] = trials[item["method"]]
                item["runtime_median_seconds"] = statistics.median(trials[item["method"]])
        _validate_timing_checkpoint(object_timings, obj, config)
        _atomic_json(checkpoint, {"provenance": selection_provenance, "timings": object_timings})
        timings.extend(object_timings)
        print(json.dumps({"completed_selection_object": str(obj["id"])}), flush=True)
    costs = {method: {"total_seconds": sum(row["runtime_median_seconds"] for row in timings if row["method"] == method),
                      "eligible_cases": sum(row["actual_eligible"] for row in timings if row["method"] == method)}
             for method in ("selector", "fixed")}
    result = {"posthoc_regression": True, "provenance": selection_provenance, "choices": choices,
              "summaries": summaries, "primary_cost_tolerance_km": 1., "fixed_model_id": fixed_id,
              "timings": timings, "full_cost": costs, "shared_ephemeris_load_seconds": load_seconds,
              "timing_scope": "resident initial state, NGInput and ephemerides; inference + force construction + rollout; 3 direct repeats",
              "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    _atomic_json(target, result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    result = run(args.root)
    print(json.dumps({"summaries": result["summaries"], "full_cost": result["full_cost"]}, indent=2))

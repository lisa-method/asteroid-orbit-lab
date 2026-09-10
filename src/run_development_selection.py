"""Freeze train-only rules, then measure their operational validation calls."""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

from development_features import build_features
from development_rules import choose_model, fit_rules
from download_jpl_pilot import write_immutable
from prepare_development_sample import atomic_json
from run_development_benchmark import (
    _find_initial, _interpolate_endpoints, _reference_grid,
    forecast_candidate, load_development_context, load_object_rows,
)
from trajectory_metrics import trajectory_error_rows, summarize_error_rows


PROXIES = ("planet_proxy_km", "gr_proxy_km", "small_body_proxy_km")
METHODS = ("horizon_rule", "physics_rule")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def eligible(row: dict, tolerance: float, numerical_fraction: float) -> bool:
    return row["max_position_error_km"] <= tolerance and row["numerical_difference_km"] <= numerical_fraction * tolerance


def forecast_with_selection(initial, start_jd, horizon_days, tolerance_km, method, artifact, context, config):
    """Operational API: no reference states, identity, event date or labels."""
    started = time.perf_counter()
    feature_result = build_features(initial, start_jd, horizon_days, context, config) if method == "physics_rule" else None
    after_features = time.perf_counter()
    decision = choose_model(artifact, method, horizon_days, tolerance_km,
                            {key: feature_result[key] for key in PROXIES} if feature_result else None)
    after_inference = time.perf_counter()
    model = next(model for model in config["models"] if model["model_id"] == decision["model_id"])
    predictions, meta = forecast_candidate(initial, start_jd, horizon_days, model, context, config, config["step_scale"])
    elapsed = time.perf_counter() - started
    return predictions, meta, {"decision": decision, "runtime_seconds": elapsed,
                               "feature_proxies": {key: feature_result[key] for key in PROXIES} if feature_result else None,
                               "feature_seconds": after_features-started,
                               "inference_seconds": after_inference-after_features,
                               "propagation_seconds": elapsed-(after_inference-started)}


def freeze_rules(root: Path, config: dict) -> dict:
    out = root / config["output_directory"]
    target = out / "rules.json"
    if target.exists():
        artifact = json.loads(target.read_text())
        expected = artifact["provenance"]
        for key, path in (("training_results_sha256", out / "train/results.json"),
                          ("rules_source_sha256", root / "src/development_rules.py"),
                          ("sample_sha256", root / config["sample_path"]),
                          ("contract_sha256", root / "docs/DEVELOPMENT30_CONTRACT.md"),
                          ("config_sha256", root / "configs/development30.json")):
            if digest(path) != expected[key]:
                raise ValueError(f"Frozen rule provenance changed: {key}")
        return artifact
    if (out / "validation" / "run_started.json").exists() or (out / "validation" / "results.json").exists():
        raise ValueError("Cannot fit after validation has started")
    train_path = out / "train" / "results.json"
    train = json.loads(train_path.read_text())
    expected = 18 * len(config["horizons_days"]) * len(config["models"])
    if len(train["records"]) != expected or len({r["object_id"] for r in train["records"]}) != 18:
        raise ValueError("Complete18-object training benchmark required")
    artifact = fit_rules(train["records"], train["feature_rows"], config["numerical_budget_fraction"])
    artifact["provenance"] = {"fitted_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                               "training_results_sha256": digest(train_path),
                               "rules_source_sha256": digest(root / "src/development_rules.py"),
                               "sample_sha256": digest(root / config["sample_path"]),
                               "contract_sha256": digest(root / "docs/DEVELOPMENT30_CONTRACT.md"),
                               "config_sha256": digest(root / "configs/development30.json")}
    payload = (json.dumps(artifact, indent=2, allow_nan=False) + "\n").encode()
    write_immutable(target, payload)
    return artifact


def summarize(records: list[dict], selections: list[dict], config: dict) -> list[dict]:
    results = []
    grouped = collections.defaultdict(list)
    for row in records:
        grouped[row["case_id"]].append(row)
    for tolerance in config["position_tolerances_km"]:
        fixed = []
        for model in config["models"]:
            model_rows = [r for r in records if r["model_id"] == model["model_id"]]
            fixed.append({"model_id": model["model_id"], "eligible_cases": sum(eligible(r, tolerance, config["numerical_budget_fraction"]) for r in model_rows),
                          "total_runtime_seconds": sum(r["runtime_median_seconds"] for r in model_rows)})
        feasible = {case for case, rows in grouped.items() if any(eligible(r, tolerance, config["numerical_budget_fraction"]) for r in rows)}
        oracle_model = {case: min((r for r in rows if eligible(r, tolerance, config["numerical_budget_fraction"])),
                                  key=lambda r: (r["runtime_median_seconds"], r["model_id"]))["model_id"]
                        for case, rows in grouped.items() if case in feasible}
        oracle_cost = sum(min(r["runtime_median_seconds"] for r in grouped[case] if eligible(r, tolerance, config["numerical_budget_fraction"])) for case in feasible)
        methods = []
        for method in METHODS:
            rows = [r for r in selections if r["method"] == method and r["tolerance_km"] == tolerance]
            methods.append({"method": method, "cases": len(rows), "eligible_cases": sum(r["actual_eligible"] for r in rows),
                            "failed_objects": sorted({r["object_id"] for r in rows if not r["actual_eligible"]}),
                            "fallback_cases": sum(r["fallback"] for r in rows),
                            "confident_failures": sum(not r["fallback"] and not r["actual_eligible"] for r in rows),
                            "oracle_model_matches_on_feasible": sum(r["selected_model_id"] == oracle_model.get(r["case_id"]) for r in rows),
                            "false_fallback_on_feasible": sum(r["fallback"] for r in rows if r["case_id"] in feasible),
                            "missed_no_candidate": sum(not r["fallback"] for r in rows if r["case_id"] not in feasible),
                            "total_runtime_seconds": sum(r["runtime_median_seconds"] for r in rows),
                            "total_feature_seconds": sum(r["feature_median_seconds"] for r in rows),
                            "selection_counts": dict(collections.Counter(r["selected_model_id"] for r in rows)),
                            "runtime_on_oracle_feasible_seconds": sum(r["runtime_median_seconds"] for r in rows if r["case_id"] in feasible),
                            "failures_on_oracle_feasible": sum(not r["actual_eligible"] for r in rows if r["case_id"] in feasible)})
        results.append({"tolerance_km": tolerance, "case_count": len(grouped), "feasible_cases": len(feasible),
                        "oracle_runtime_seconds": oracle_cost, "fixed_models": fixed, "methods": methods})
    return results


def evaluate(root: Path, config: dict) -> dict:
    out = root / config["output_directory"]
    artifact = json.loads((out / "rules.json").read_text())
    validation_path = out / "validation" / "results.json"
    validation = json.loads(validation_path.read_text())
    for relative, expected in validation["provenance"]["source_sha256_at_start"].items():
        if digest(root / relative) != expected:
            raise ValueError(f"Benchmark source/input changed: {relative}")
    rules_hash = digest(out / "rules.json")
    if validation["provenance"]["rules_sha256_at_start"] != rules_hash:
        raise ValueError("Validation used different frozen rules")
    if artifact["provenance"]["rules_source_sha256"] != digest(root / "src/development_rules.py"):
        raise ValueError("Rule implementation changed after freeze")
    records = validation["records"]
    lookup = {(r["case_id"], r["model_id"]): r for r in records}
    if len(lookup) != len(records) or len(records) != 12 * len(config["horizons_days"]) * len(config["models"]):
        raise ValueError("Complete unique validation model/case matrix required")
    case_meta = {}
    for row in records:
        identity = (row["object_id"], row["horizon_days"])
        if case_meta.setdefault(row["case_id"], identity) != identity:
            raise ValueError("One case_id maps to multiple object/horizon pairs")
    sample = json.loads((root / config["sample_path"]).read_text())
    objects = {o["id"]: o for o in sample["objects"] if o["split"] == "validation"}
    provenance = {"rules_sha256": rules_hash, "validation_results_sha256": digest(validation_path),
                  "selection_source_sha256": digest(Path(__file__)),
                  "features_source_sha256": digest(root / "src/development_features.py"),
                  "runner_source_sha256": digest(root / "src/run_development_benchmark.py")}
    provenance["python"] = sys.version
    provenance["config_sha256"] = digest(root / "configs/development30.json")
    if provenance["config_sha256"] != artifact["provenance"]["config_sha256"]:
        raise ValueError("Configuration changed after rule freeze")
    started_path = out / "selection_run_started.json"
    if started_path.exists() and json.loads(started_path.read_text())["provenance"] != provenance:
        raise ValueError("Selection run provenance changed; do not reuse checkpoints")
    if not started_path.exists():
        atomic_json(started_path, {"started_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "provenance": provenance})
    before_load = time.perf_counter()
    context = load_development_context(root, config)
    shared_load_seconds = time.perf_counter() - before_load
    selections = []
    fixed_anchor = []
    direct_invocations = 0
    for feature_row in validation["feature_rows"]:
        case_id = feature_row["case_id"]
        checkpoint = out / "selection_checkpoints" / (case_id.replace(":", "_") + ".json")
        if checkpoint.exists():
            prior = json.loads(checkpoint.read_text())
            if prior["provenance"] != provenance:
                raise ValueError("Checkpoint provenance changed")
            selections.extend(prior["selections"])
            fixed_anchor.append(prior["fixed_anchor"])
            direct_invocations += prior["direct_invocations"]
            continue
        obj = objects[feature_row["object_id"]]
        daily, reference = load_object_rows(root, root / config["raw_directory"], obj)
        start_jd, initial = _find_initial(daily, obj["start_date"])
        horizon = feature_row["horizon_days"]
        times, ref_states = _reference_grid(reference, start_jd, horizon)
        case_selections = []
        case_invocations = 0
        anchor_trials = None
        for method in METHODS:
            decisions = {tolerance: choose_model(artifact, method, horizon, tolerance,
                                                {k: feature_row["features"][k] for k in PROXIES} if method == "physics_rule" else None)
                         for tolerance in config["position_tolerances_km"]}
            # Timing the same chosen model repeatedly for three tolerances is
            # redundant. Measure each distinct operational path, prioritising
            # the 1-km request; explicitly label reused timing measurements.
            groups = collections.defaultdict(list)
            for tolerance in sorted(decisions, key=lambda t: (t != 1, t)):
                groups[decisions[tolerance]["model_id"]].append(tolerance)
            for model_id, tolerances in groups.items():
                representative = tolerances[0]
                trials = []
                for repeat in range(config["timing_repeats"]):
                    _, meta, measured = forecast_with_selection(initial, start_jd, horizon, representative, method, artifact, context, config)
                    if measured["decision"] != decisions[representative]:
                        raise ValueError("Operational selection differs from frozen forecast features")
                    if method == "physics_rule" and measured["feature_proxies"] != {k: feature_row["features"][k] for k in PROXIES}:
                        raise ValueError("Operational forecast features changed")
                    # Evaluation happens AFTER the operational timing ends.
                    predicted_at = _interpolate_endpoints(meta["accepted_states"])
                    errors = trajectory_error_rows(times, [predicted_at(t) for t in times], ref_states, context["au_km"], context["day_s"])
                    observed_error = summarize_error_rows(errors)["max_position_error_km"]
                    labelled_error = lookup[case_id, model_id]["max_position_error_km"]
                    if abs(observed_error-labelled_error) > 1e-5:
                        raise ValueError(f"Operational rollout disagrees with benchmark: {case_id} {model_id} {observed_error} {labelled_error}")
                    trials.append(measured)
                    case_invocations += 1
                if model_id == "B3+GR+SB16" and anchor_trials is None:
                    anchor_trials = [t["propagation_seconds"] for t in trials]
                label = lookup[case_id, model_id]
                for tolerance in tolerances:
                    decision = decisions[tolerance]
                    case_selections.append({"case_id": case_id, "object_id": obj["id"], "stratum": obj["stratum"],
                                            "horizon_days": horizon, "tolerance_km": tolerance, "method": method,
                                            "selected_model_id": model_id, "fallback": decision["fallback"],
                                            "predicted_error_km": decision["predicted_error_km"],
                                            "actual_eligible": eligible(label, tolerance, config["numerical_budget_fraction"]),
                                            "actual_max_position_error_km": label["max_position_error_km"],
                                            "actual_numerical_difference_km": label["numerical_difference_km"],
                                            "runtime_median_seconds": statistics.median(t["runtime_seconds"] for t in trials),
                                            "runtime_trials_seconds": [t["runtime_seconds"] for t in trials],
                                            "feature_median_seconds": statistics.median(t["feature_seconds"] for t in trials),
                                            "inference_median_seconds": statistics.median(t["inference_seconds"] for t in trials),
                                            "propagation_median_seconds": statistics.median(t["propagation_seconds"] for t in trials),
                                            "timing_representative_tolerance_km": representative,
                                            "timing_reused_same_selected_model": tolerance != representative})
        anchor_reused = anchor_trials is not None
        if anchor_trials is None:
            anchor_model = next(m for m in config["models"] if m["model_id"] == "B3+GR+SB16")
            anchor_trials = []
            for _ in range(config["timing_repeats"]):
                anchor_start = time.perf_counter()
                anchor_output = forecast_candidate(initial, start_jd, horizon, anchor_model, context, config, config["step_scale"])
                anchor_trials.append(time.perf_counter()-anchor_start)
                del anchor_output
                case_invocations += 1
        anchor_row = {"case_id": case_id, "object_id": obj["id"], "horizon_days": horizon,
                      "model_id": "B3+GR+SB16", "runtime_median_seconds": statistics.median(anchor_trials),
                      "runtime_trials_seconds": anchor_trials, "reused_propagation_component": anchor_reused}
        fixed_anchor.append(anchor_row)
        atomic_json(checkpoint, {"provenance": provenance, "selections": case_selections, "direct_invocations": case_invocations, "fixed_anchor": anchor_row})
        selections.extend(case_selections)
        direct_invocations += case_invocations
        print(json.dumps({"completed_case": case_id, "direct_invocations": direct_invocations}), flush=True)
    result = {"schema_version": 1, "provenance": provenance, "shared_load_seconds": shared_load_seconds,
              "direct_invocations": direct_invocations, "selections": selections,
              "fixed_anchor": fixed_anchor,
              "fixed_anchor_total_runtime_seconds": sum(r["runtime_median_seconds"] for r in fixed_anchor),
              "summaries": summarize(records, selections, config),
              "timing_note": "Three serial direct calls per distinct(method,case,chosen-model); 1-km representative prioritised. Identical selected paths at other tolerances reuse explicitly labelled timings. Offline evaluation, fine runs and data loading excluded."}
    atomic_json(out / "selection_results.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--fit", action="store_true")
    group.add_argument("--evaluate", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "configs/development30.json").read_text())
    if args.fit:
        artifact = freeze_rules(root, config)
        print(json.dumps({"rules_sha256": digest(root / config["output_directory"] / "rules.json"), "calibration": artifact["calibration_factors"]}))
    else:
        result = evaluate(root, config)
        print(json.dumps({"selection_records": len(result["selections"]), "direct_invocations": result["direct_invocations"]}))


if __name__ == "__main__":
    main()

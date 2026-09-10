"""Evaluate frozen v2 on a new object holdout without refitting any method.

Only this orchestration/evaluation layer loads target reference rows. The
frozen force/selector APIs still receive one initial state and exogenous inputs.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path
import statistics
import subprocess
import time

from force_models_v2 import forecast_candidate_v2
from ng_inputs_v2 import load_ng_input
from orbit_baselines import State, norm, subtract
from prepare_fresh_holdout import DIRECTORY, MANIFEST, check_hashes, freeze, immutable_json, sha
from run_development_benchmark import (_find_initial, _interpolate_endpoints, _reference_grid,
                                        load_development_context, load_object_rows)
from run_force_models_v2 import (_atomic_json, _record_for_horizon, _runtime_environment,
                                 _snapshot, _trace_payload, eligible)
from run_eda import parse_horizons
from selection_v2 import choose_model_v2, forecast_with_selection_v2
from trajectory_metrics import summarize_error_rows, trajectory_error_rows

OUT = "outputs/fresh_holdout12"
RAW = "data/raw/fresh_holdout12"
FIXED = "V2-P-GR-SB16"


def checked(path, provenance):
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    if value["provenance"] != provenance:
        raise ValueError(f"Checkpoint provenance mismatch: {path}")
    return value


def raw_hashes(root):
    hashes = {}
    for path in sorted((root / "data/checksums").glob("*manifest.json")):
        doc = json.loads(path.read_text())
        for row in doc.get("files", []):
            raw = Path(row["path"])
            if not raw.is_absolute():
                raw = root / raw
            relative = raw.resolve().relative_to(root).as_posix()
            if sha(raw) != row["sha256"] or raw.stat().st_size != row["bytes"]:
                raise ValueError(f"Raw hash/size mismatch: {relative}")
            hashes[relative] = row["sha256"]
    return hashes


def validate_data(root, sample):
    manifest = json.loads((root / MANIFEST).read_text())
    if not manifest["complete"] or len(manifest["files"]) != 20:
        raise ValueError("All 20 reference files are required before running")
    if manifest["sample_sha256"] != sha(root / DIRECTORY / "sample.json"):
        raise ValueError("Manifest sample mismatch")
    if len(sample["objects"]) != 12 or len({o["id"] for o in sample["objects"]}) != 12:
        raise ValueError("Exactly 12 unique holdout objects required")
    if {o["id"] for o in sample["objects"]} & set(sample["excluded_object_ids"]):
        raise ValueError("Inspected object leaked into holdout")
    shared = 0
    for obj in sample["objects"]:
        daily, reference = load_object_rows(root, root / RAW, obj)
        start, initial = _find_initial(daily, obj["start_date"])
        if len(daily) != 366 or daily[-1]["epoch_jd_tdb"] - start != 365:
            raise ValueError("Incomplete daily window")
        paths = [root / RAW / "asteroids" / f"asteroid_{obj['id']}_daily.json"]
        if obj["event"]:
            paths.append(root / RAW / "asteroids" / f"asteroid_{obj['id']}_refined.json")
            _, refined = parse_horizons(paths[-1], obj["id"], obj["name"])
            if len(refined) != 1153:
                raise ValueError("Incomplete refined grid")
            by_epoch = {row["epoch_jd_tdb"]: row for row in daily}
            for row in refined:
                if row["epoch_jd_tdb"] in by_epoch:
                    other = by_epoch[row["epoch_jd_tdb"]]
                    if row["r"] != other["r"] or row["v"] != other["v"]:
                        raise ValueError("Daily/refined states disagree")
                    shared += 1
            if load_ng_input(paths[0]).parameters != load_ng_input(paths[1]).parameters:
                raise ValueError("Daily/refined NG parameters disagree")
        for path in paths:
            header = json.loads(path.read_text())["result"].split("$$SOE")[0]
            required = ("Center body name: Sun", "Output units    : AU-D", "Reference frame : ICRF")
            if any(text not in header for text in required):
                raise ValueError(f"Coordinate contract mismatch: {path}")
        ng = load_ng_input(paths[0])
        if ng.parameters is not None and (ng.available_from_jd_tdb is None or ng.available_from_jd_tdb > start):
            raise ValueError("NG parameters not demonstrably available before this future start")
    return {"objects": 12, "files": 20, "identical_shared_nodes": shared}


def setup(root):
    sample = freeze(root)
    data_check = validate_data(root, sample)
    config = json.loads((root / "configs/force_models_v2.json").read_text())
    artifact = json.loads((root / "outputs/force_models_v2/rules.json").read_text())
    method = json.loads((root / DIRECTORY / "method_freeze_v2.json").read_text())
    inputs = raw_hashes(root)
    extra = ["src/run_fresh_holdout.py", "data/checksums/fresh_holdout12_manifest.json",
             "data/checksums/fresh_holdout12_catalogue_manifest.json",
             DIRECTORY + "/sample.json", DIRECTORY + "/method_freeze_v2.json"]
    provenance = {"hashes": {**method["hashes"], **inputs, **{p: sha(root / p) for p in extra}},
                  "runtime_environment": _runtime_environment(), "scope": "fresh_holdout12_frozen_v2"}
    out = root / OUT
    start_path = out / "run_started.json"
    previous = checked(start_path, provenance)
    if previous is None:
        immutable_json(start_path, {"provenance": provenance, "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                                   "data_validation": data_check, "raw_files_checked": len(inputs)})
    return sample, config, artifact, provenance


def verify_trace_hashes(root, item):
    check_hashes(root, item["trace_hashes"])


def run_pair(root, obj, model, context, config, provenance):
    out = root / OUT
    key = f"{obj['id']}_{model['model_id']}"
    checkpoint = out / "matrix_checkpoints" / (key + ".json")
    previous = checked(checkpoint, provenance)
    if previous is not None:
        verify_trace_hashes(root, previous)
        return previous
    paths = [out / "traces" / f"{key}_{kind}.json" for kind in ("production", "fine")]
    if any(path.exists() for path in paths):
        raise ValueError(f"Orphan traces require explicit inspection: {key}")
    daily, reference = load_object_rows(root, root / RAW, obj)
    start, initial = _find_initial(daily, obj["start_date"])
    ng = load_ng_input(root / RAW / "asteroids" / f"asteroid_{obj['id']}_daily.json")
    productions = []
    for repeat in range(3):
        value = forecast_candidate_v2(initial, start, 365, model, context, config, config["step_scale"], ng=ng)
        if productions and value[0] != productions[0][0]:
            raise ValueError("Production is not deterministic")
        productions.append(value)
    fine = forecast_candidate_v2(initial, start, 365, model, context, config,
                                config["step_scale"] * config["sensitivity_scale_factor"], ng=ng)
    records = []
    for horizon in config["horizons_days"]:
        record = _record_for_horizon(initial, start, float(horizon), model, context, config,
                                      reference, productions[0], fine, obj["id"], obj["name"],
                                      obj["start_date"], "fresh_holdout")
        record["evaluation_scope"] = "fresh_holdout12_frozen_v2"
        record["runtime_trials_seconds"] = [_snapshot(meta, horizon)["runtime_seconds"] for _, meta in productions]
        record["runtime_median_seconds"] = statistics.median(record["runtime_trials_seconds"])
        records.append(record)
    for path, value in zip(paths, (productions[0], fine)):
        immutable_json(path, _trace_payload(value[1]))
    result = {"provenance": provenance, "object_id": obj["id"], "model_id": model["model_id"],
              "records": records, "trace_paths": [p.relative_to(root).as_posix() for p in paths],
              "trace_hashes": {p.relative_to(root).as_posix(): sha(p) for p in paths}}
    immutable_json(checkpoint, result)
    print(json.dumps({"matrix_completed": key, "annual_error_km": records[-1]["max_position_error_km"]}), flush=True)
    return result


def choices_and_summary(sample, config, artifact, records):
    expected = {(o["id"], float(h), m["model_id"]) for o in sample["objects"]
                for h in config["horizons_days"] for m in config["models"]}
    rows = {(r["object_id"], float(r["horizon_days"]), r["model_id"]): r for r in records}
    if len(records) != len(expected) or set(rows) != expected:
        raise ValueError("Incomplete or duplicate holdout matrix")
    for row in records:
        for key in ("max_position_error_km", "numerical_difference_km", "runtime_median_seconds"):
            if not math.isfinite(row[key]) or row[key] < 0:
                raise ValueError("Invalid measured metric")
    choices = []
    budget = config["numerical_budget_fraction"]
    for obj in sample["objects"]:
        for h in config["horizons_days"]:
            for tolerance in config["position_tolerances_km"]:
                decision = choose_model_v2(h, tolerance, artifact, config)
                row = rows[(obj["id"], float(h), decision["model_id"])]
                feasible = any(eligible(rows[(obj["id"], float(h), m["model_id"])], tolerance, budget) for m in config["models"])
                choices.append({"object_id": obj["id"], "horizon_days": h, "tolerance_km": tolerance,
                                **decision, "actual_eligible": eligible(row, tolerance, budget), "any_candidate_eligible": feasible,
                                "max_position_error_km": row["max_position_error_km"],
                                "numerical_difference_km": row["numerical_difference_km"]})
    summaries = []
    for tolerance in config["position_tolerances_km"]:
        subset = [c for c in choices if c["tolerance_km"] == tolerance]
        summaries.append({"tolerance_km": tolerance, "cases": len(subset),
            "selector_eligible_cases": sum(c["actual_eligible"] for c in subset),
            "feasible_cases": sum(c["any_candidate_eligible"] for c in subset),
            "avoidable_selection_misses": sum(c["any_candidate_eligible"] and not c["actual_eligible"] for c in subset),
            "no_candidate_cases": sum(not c["any_candidate_eligible"] for c in subset),
            "no_candidate_flagged": sum(not c["any_candidate_eligible"] and c["fallback"] for c in subset),
            "fallback_cases": sum(c["fallback"] for c in subset),
            "fixed": [{"model_id": m["model_id"], "eligible_cases": sum(eligible(r, tolerance, budget) for r in records if r["model_id"] == m["model_id"]),
                       "prefix_cost_seconds": sum(r["runtime_median_seconds"] for r in records if r["model_id"] == m["model_id"])} for m in config["models"]]})
    return choices, summaries


def matrix(root):
    sample, config, artifact, provenance = setup(root)
    target = root / OUT / "matrix.json"
    previous = checked(target, provenance)
    if previous is not None:
        for item in previous["object_model_results"]:
            verify_trace_hashes(root, item)
        return previous
    context = load_development_context(root, config)
    completed = [run_pair(root, obj, model, context, config, provenance)
                 for obj in sample["objects"] for model in config["models"]]
    records = [r for item in completed for r in item["records"]]
    choices, summaries = choices_and_summary(sample, config, artifact, records)
    result = {"provenance": provenance, "posthoc_regression": False, "records": records,
              "object_model_results": [{k: v for k, v in item.items() if k not in ("provenance", "records")} for item in completed],
              "choices": choices, "summaries": summaries, "shared_ephemeris_load_seconds": context["load_seconds"],
              "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    immutable_json(target, result)
    return result


def cost(root):
    sample, config, artifact, provenance = setup(root)
    measured = checked(root / OUT / "matrix.json", provenance)
    if measured is None:
        raise ValueError("Finish the full matrix first")
    target = root / OUT / "direct_cost.json"
    previous = checked(target, provenance)
    if previous is not None:
        return previous
    lookup = {(r["object_id"], float(r["horizon_days"]), r["model_id"]): r for r in measured["records"]}
    context = load_development_context(root, config)
    fixed = next(m for m in config["models"] if m["model_id"] == FIXED)
    all_timings = []
    for obj in sample["objects"]:
        path = root / OUT / "cost_checkpoints" / f"object_{obj['id']}.json"
        saved = checked(path, provenance)
        if saved is not None:
            all_timings.extend(saved["timings"])
            continue
        daily, reference = load_object_rows(root, root / RAW, obj)
        start, initial = _find_initial(daily, obj["start_date"])
        ng = load_ng_input(root / RAW / "asteroids" / f"asteroid_{obj['id']}_daily.json")
        timings = []
        for horizon in config["horizons_days"]:
            times, ref = _reference_grid(reference, start, horizon)
            trial_values = {"selector": [], "fixed_full": []}
            entries = {}
            for repeat in range(3):
                for method in (("selector", "fixed_full") if repeat % 2 == 0 else ("fixed_full", "selector")):
                    if method == "selector":
                        pred, meta, online = forecast_with_selection_v2(initial, start, horizon, 1., artifact, context, config, ng=ng)
                        elapsed, model_id = online["runtime_seconds"], online["decision"]["model_id"]
                    else:
                        started = time.perf_counter()
                        pred, meta = forecast_candidate_v2(initial, start, horizon, fixed, context, config, config["step_scale"], ng=ng)
                        elapsed, model_id = time.perf_counter() - started, FIXED
                    trial_values[method].append(elapsed)
                    if repeat == 0:
                        at = _interpolate_endpoints(meta["accepted_states"])
                        actual = summarize_error_rows(trajectory_error_rows(times, [at(t) for t in times], ref, context["au_km"], context["day_s"]))
                        expected = lookup[(obj["id"], float(horizon), model_id)]
                        for key in ("max_position_error_km", "max_velocity_error_m_s"):
                            if abs(actual[key] - expected[key]) > 1e-5:
                                raise ValueError(f"Direct rollout/prefix mismatch: {obj['id']} {horizon} {method}")
                        entries[method] = {"object_id": obj["id"], "horizon_days": horizon, "method": method,
                                           "model_id": model_id, "tolerance_km": 1., "actual_eligible": eligible(expected, 1., config["numerical_budget_fraction"]),
                                           "max_position_error_km": actual["max_position_error_km"]}
                    del pred, meta
            for method, row in entries.items():
                timings.append({**row, "runtime_trials_seconds": trial_values[method], "runtime_median_seconds": statistics.median(trial_values[method])})
        immutable_json(path, {"provenance": provenance, "timings": timings})
        all_timings.extend(timings)
        print(json.dumps({"cost_completed_object": obj["id"]}), flush=True)
    totals = {method: {"total_seconds": sum(r["runtime_median_seconds"] for r in all_timings if r["method"] == method),
                       "eligible_cases": sum(r["actual_eligible"] for r in all_timings if r["method"] == method)} for method in ("selector", "fixed_full")}
    result = {"provenance": provenance, "timings": all_timings, "full_cost": totals, "fixed_model_id": FIXED,
              "matrix_sha256": sha(root / OUT / "matrix.json"), "timing_scope": "resident inputs; inference + force construction + rollout; 3 direct repeats",
              "shared_ephemeris_load_seconds": context["load_seconds"], "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
    immutable_json(target, result)
    return result


def verify(root):
    sample, config, artifact, provenance = setup(root)
    result = checked(root / OUT / "matrix.json", provenance)
    if result is None:
        raise ValueError("Complete matrix required")
    choices, summaries = choices_and_summary(sample, config, artifact, result["records"])
    if choices != result["choices"] or summaries != result["summaries"]:
        raise ValueError("Choice/summary mismatch")
    by_key = {(r["object_id"], r["model_id"], r["horizon_days"]): r for r in result["records"]}
    checked_samples = 0
    au = json.loads((root / "configs/eda_pilot_6.json").read_text())["constants"]["au_km"]
    for item in result["object_model_results"]:
        verify_trace_hashes(root, item)
        obj = next(o for o in sample["objects"] if o["id"] == item["object_id"])
        daily, reference = load_object_rows(root, root / RAW, obj)
        start, initial = _find_initial(daily, obj["start_date"])
        interpolators = []
        for relative in item["trace_paths"]:
            trace = json.loads((root / relative).read_text())
            values = [{"time_days": row["time_days"], "state": State(tuple(row["state"]["r"]), tuple(row["state"]["v"]))} for row in trace["accepted_states"]]
            if values[0]["time_days"] != 0 or values[0]["state"] != initial or values[-1]["time_days"] != 365:
                raise ValueError("Trace boundary mismatch")
            if any(b["time_days"] <= a["time_days"] for a, b in zip(values, values[1:])):
                raise ValueError("Trace time order mismatch")
            interpolators.append(_interpolate_endpoints(values))
        for h in config["horizons_days"]:
            times, ref = _reference_grid(reference, start, h)
            pred, fine = [[at(t) for t in times] for at in interpolators]
            errors = trajectory_error_rows(times, pred, ref, au, 86400.)
            summary = json.loads(json.dumps(summarize_error_rows(errors)))
            errors = json.loads(json.dumps(errors))
            step = max(norm(subtract(a.position, b.position)) * au for a, b in zip(pred, fine))
            stored = by_key[(obj["id"], item["model_id"], h)]
            if errors != stored["error_rows"] or any(summary[k] != stored[k] for k in summary) or step != stored["numerical_difference_km"]:
                raise ValueError("Trace-derived errors do not reproduce record")
            for tol in config["position_tolerances_km"]:
                if stored["eligibility"][str(float(tol))] != eligible(stored, tol, config["numerical_budget_fraction"]):
                    raise ValueError("Eligibility mismatch")
            checked_samples += len(errors)
    timing = checked(root / OUT / "direct_cost.json", provenance)
    if timing is None:
        raise ValueError("Complete direct timing required")
    expected = {(o["id"], h, method) for o in sample["objects"] for h in config["horizons_days"] for method in ("selector", "fixed_full")}
    found = {(r["object_id"], r["horizon_days"], r["method"]) for r in timing["timings"]}
    if len(timing["timings"]) != len(expected) or found != expected:
        raise ValueError("Incomplete/duplicate direct timing")
    for row in timing["timings"]:
        trials = row["runtime_trials_seconds"]
        if len(trials) != 3 or any(not math.isfinite(v) or v < 0 for v in trials) or statistics.median(trials) != row["runtime_median_seconds"]:
            raise ValueError("Invalid timing trials")
    raw = raw_hashes(root)
    git = subprocess.run(["git", "check-ignore", "--stdin"], cwd=root, input="\n".join(raw) + "\n", text=True, capture_output=True)
    if set(git.stdout.splitlines()) != set(raw):
        raise ValueError("Raw data not all Git-ignored")
    summary = {"records": len(result["records"]), "choices": len(choices), "traces": 2*len(result["object_model_results"]),
               "error_samples_recomputed": checked_samples, "raw_files_sha_size_ignored": len(raw),
               "direct_timing_rows": len(timing["timings"]), "matrix_sha256": sha(root / OUT / "matrix.json"),
               "direct_cost_sha256": sha(root / OUT / "direct_cost.json"), "passed": True}
    _atomic_json(root / OUT / "verification.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("phase", choices=("matrix", "cost", "verify"))
    args = parser.parse_args()
    value = {"matrix": matrix, "cost": cost, "verify": verify}[args.phase](args.root.resolve())
    print(json.dumps({k: v for k, v in value.items() if k in ("summaries", "full_cost", "passed", "records", "choices") and not isinstance(v, list) or k == "summaries"}, indent=2))

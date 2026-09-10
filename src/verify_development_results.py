"""Independent artifact consistency checks and the development encounter table.

This is a consistency audit, not an independent dynamical solver. Raw states
and saved accepted endpoints are used to recompute position-error norms and
step differences without trusting saved summary values.
"""
from __future__ import annotations

import collections
import hashlib
import json
import math
from pathlib import Path
import statistics

from development_rules import choose_model
from encounter_screening import scan_body
from orbit_baselines import State
from planetary_dynamics import EphemerisInterpolator
from prepare_development_sample import atomic_json
from run_development_benchmark import load_development_context, load_object_rows, _find_initial, _reference_grid
from run_development_selection import PROXIES, eligible


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_close(a, b):
    if not math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-8):
        raise AssertionError(f"Value mismatch: {a} != {b}")


def interpolator(trace):
    return EphemerisInterpolator(tuple(r["time_days"] for r in trace),
                                 tuple(State(tuple(r["state"]["r"]), tuple(r["state"]["v"])) for r in trace))


def verify(root):
    config = read(root / "configs/development30.json")
    sample = read(root / config["sample_path"])
    out = root / config["output_directory"]
    rules = read(out / "rules.json")
    selection = read(out / "selection_results.json")
    fixed_cost = read(out / "fixed_cost_reference.json")
    models = [m["model_id"] for m in config["models"]]
    all_records = {}
    all_features = {}
    catalogue = []
    errors_checked = 0
    maximum_recomputed_difference = 0.0
    input_files = set()
    for split in ("train", "validation"):
        result = read(out / split / "results.json")
        start = read(out / split / "run_started.json")
        for relative, expected in result["provenance"]["source_sha256_at_start"].items():
            assert digest(root / relative) == expected, relative
            if relative.startswith("data/raw/"):
                input_files.add(relative)
        assert result["provenance"]["runtime_environment"] == start["runtime_environment"]
        objects = [o for o in sample["objects"] if o["split"] == split]
        expected_cases = {f"{o['id']}:{o['start_date']}:{h}": (o, h) for o in objects for h in config["horizons_days"]}
        records = {(r["case_id"], r["model_id"]): r for r in result["records"]}
        assert len(records) == len(result["records"]) == len(expected_cases)*len(models)
        assert set(records) == {(case, model) for case in expected_cases for model in models}
        features = {r["case_id"]: r for r in result["feature_rows"]}
        assert len(features) == len(result["feature_rows"]) == len(expected_cases)
        assert set(features) == set(expected_cases)
        all_features.update(features)
        all_records.update(records)
        for obj in objects:
            checkpoint = read(out / split / "checkpoints" / f"object_{obj['id']}.json")["result"]
            assert checkpoint["records"] == [r for r in result["records"] if r["object_id"] == obj["id"]]
            traces = read(out / split / "traces" / f"object_{obj['id']}.json")
            daily, reference = load_object_rows(root, root / config["raw_directory"], obj)
            start_jd, _ = _find_initial(daily, obj["start_date"])
            for model in models:
                prod = interpolator(traces["production"][model])
                fine = interpolator(traces["fine"][model])
                last_error, last_steps, last_numerical = -1, -1, -1
                for horizon in config["horizons_days"]:
                    case = f"{obj['id']}:{obj['start_date']}:{horizon}"
                    row = records[case, model]
                    assert row["split"] == split and row["object_id"] == obj["id"] and row["horizon_days"] == horizon
                    assert row["determinism_max_position_difference_km"] == row["determinism_max_velocity_difference_m_s"] == 0
                    times, refs = _reference_grid(reference, start_jd, horizon)
                    au = 149597870.7
                    position_errors, step_errors = [], []
                    for t, ref in zip(times, refs):
                        p, f = prod.state_at(t), fine.state_at(t)
                        position_errors.append(math.sqrt(sum((a-b)**2 for a, b in zip(p.position, ref.position)))*au)
                        step_errors.append(math.sqrt(sum((a-b)**2 for a, b in zip(p.position, f.position)))*au)
                    error, numerical = max(position_errors), max(step_errors)
                    assert_close(error, row["max_position_error_km"])
                    assert_close(numerical, row["numerical_difference_km"])
                    maximum_recomputed_difference = max(maximum_recomputed_difference, abs(error-row["max_position_error_km"]), abs(numerical-row["numerical_difference_km"]))
                    assert position_errors[0] == 0.0
                    assert error >= last_error and numerical >= last_numerical and row["rk4_steps"] > last_steps
                    last_error, last_numerical, last_steps = error, numerical, row["rk4_steps"]
                    assert len(row["runtime_trials_seconds"]) == 3
                    assert row["runtime_median_seconds"] == statistics.median(row["runtime_trials_seconds"])
                    for stored, value in zip(row["error_rows"], position_errors, strict=True):
                        assert_close(stored["position_error_km"], value)
                        assert_close(math.sqrt(sum(v*v for v in stored["rtn_position_error_km"])), value)
                    errors_checked += len(times)
                    if model == "B2":
                        feature = features[case]
                        assert feature["determinism_max_scalar_difference"] == 0
                        assert set(feature["features"]) == set(PROXIES)
                        assert len(feature["per_body"]) == 9
                        assert {g["body_id"] for g in feature["per_body"]} == {"199", "299", "399", "301", "4", "5", "6", "7", "8"}
                        assert feature["min_solar_distance_au"] > 0 and math.isfinite(feature["planet_max_eta"])
                        for body, geometry in row["geometry_compare"].items():
                            for other in models[1:]:
                                assert records[case, other]["geometry_compare"][body]["reference"] == geometry["reference"]
                            catalogue.append({"case_id": case, "object_id": obj["id"], "split": split,
                                              "horizon_days": horizon, "start_jd_tdb": start_jd,
                                              "reference": geometry["reference"],
                                              "forecast_B2": next(g for g in feature["per_body"] if g["body_id"] == body)})
        print(json.dumps({"verified_split": split, "model_records": len(records)}), flush=True)
    expected_keys = {(case, method, tol) for case, feature in all_features.items() if feature["split"] == "validation"
                     for method in ("horizon_rule", "physics_rule") for tol in config["position_tolerances_km"]}
    choices = {(r["case_id"], r["method"], r["tolerance_km"]): r for r in selection["selections"]}
    assert len(choices) == len(selection["selections"]) == 360 and set(choices) == expected_keys
    for (case, method, tolerance), row in choices.items():
        feature = all_features[case]
        choice = choose_model(rules, method, feature["horizon_days"], tolerance,
                              {k: feature["features"][k] for k in PROXIES} if method == "physics_rule" else None)
        assert row["selected_model_id"] == choice["model_id"] and row["fallback"] == choice["fallback"]
        assert row["actual_eligible"] == eligible(all_records[case, choice["model_id"]], tolerance, config["numerical_budget_fraction"])
        assert row["runtime_median_seconds"] == statistics.median(row["runtime_trials_seconds"])
    direct_groups = {(r["case_id"], r["method"], r["selected_model_id"]) for r in choices.values()}
    assert len({r["case_id"] for r in selection["fixed_anchor"]}) == 60
    assert selection["direct_invocations"] == 3 * (len(direct_groups) + sum(not r["reused_propagation_component"] for r in selection["fixed_anchor"]))
    assert_close(selection["fixed_anchor_total_runtime_seconds"], sum(r["runtime_median_seconds"] for r in selection["fixed_anchor"]))
    validation_cases = {case for case, feature in all_features.items() if feature["split"] == "validation"}
    for model_id, fixed in fixed_cost["models"].items():
        assert len(fixed["records"]) == 60 and {r["case_id"] for r in fixed["records"]} == validation_cases
        assert_close(fixed["total_runtime_seconds"], sum(r["runtime_median_seconds"] for r in fixed["records"]))
        for row in fixed["records"]:
            label = all_records[row["case_id"], model_id]
            assert row["max_position_error_km"] == label["max_position_error_km"]
            assert row["numerical_difference_km"] == label["numerical_difference_km"]
            assert row["eligible_at_10km"] == eligible(label, 10, config["numerical_budget_fraction"])
            if row["source"] == "reused_selected_propagation":
                source = choices[row["case_id"], row["source_method"], row["source_tolerance_km"]]
                assert source["selected_model_id"] == model_id
                assert row["runtime_median_seconds"] == source["propagation_median_seconds"]
            elif row["source"] == "direct_fixed":
                assert len(row["runtime_trials_seconds"]) == 3
                assert row["runtime_median_seconds"] == statistics.median(row["runtime_trials_seconds"])
    context = load_development_context(root, config)
    assert len(context["planets"]) == 9 and len(context["small"]) == 16
    selected_events = []
    for obj in sample["objects"]:
        if not obj["event"]:
            continue
        _, reference = load_object_rows(root, root / config["raw_directory"], obj)
        start_jd, _ = _find_initial(reference, obj["start_date"])
        times, states = _reference_grid(reference, start_jd, 32)
        restricted = [(t, state) for t, state in zip(times, states) if t >= 28]
        times, states = [t for t, _ in restricted], [state for _, state in restricted]
        body = next(p for p in context["planets"] if p.body_id == obj["event"]["body_id"])
        planet_states = [body.ephemeris.state_at(start_jd+t) for t in times]
        minimum = scan_body(times, states, planet_states, body, context["mu"], context["au_km"], context["day_s"], refine=True)
        selected_events.append({"object_id": obj["id"], "split": obj["split"], "stratum": obj["stratum"],
                                "reference_minimum": minimum, "reference_minimum_jd_tdb": start_jd+minimum["time_days"],
                                "cad_jd": obj["event"]["jd"], "cad_distance_au": obj["event"]["dist_au"],
                                "same_center_as_cad_planet": body.body_id in ("399", "299"),
                                "time_difference_seconds": (start_jd+minimum["time_days"]-obj["event"]["jd"])*context["day_s"],
                                "distance_difference_km": minimum["distance_km"]-obj["event"]["dist_au"]*context["au_km"]})
    assert len(catalogue) == 1350 and len(selected_events) == 20
    atomic_json(out / "encounter_catalogue.json", {"case_body_minima": catalogue, "selected_events": selected_events,
                "note": "Constrained minima; not an exhaustive list of all local flybys. Forecast_B2 geometry is daily Hermite; reference grid has selected-event refinement. Mars/Jupiter force bodies are system barycentres."})
    result = {"passed": True, "model_records": 600, "selection_records": 360, "case_body_minima": len(catalogue),
              "selected_events": len(selected_events), "reference_event_boundary_minima": sum(e["reference_minimum"]["boundary_minimum"] for e in selected_events),
              "error_samples_recomputed": errors_checked, "maximum_recomputed_difference_km": maximum_recomputed_difference,
              "raw_inputs_in_benchmark_freeze": len(input_files), "verifier_source_sha256": digest(Path(__file__)),
              "results_sha256": {name: digest(out / name) for name in ("rules.json", "train/results.json", "validation/results.json", "selection_results.json", "fixed_cost_reference.json", "encounter_catalogue.json")}}
    atomic_json(out / "verification.json", result)
    return result


if __name__ == "__main__":
    print(json.dumps(verify(Path(__file__).resolve().parents[1]), indent=2))

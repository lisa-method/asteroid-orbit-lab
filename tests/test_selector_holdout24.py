import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from orbit_baselines import State  # noqa: E402
import run_selector_holdout24 as runner  # noqa: E402


class SelectorHoldoutRunnerTests(unittest.TestCase):
    def setUp(self):
        self.initial = State((1.0, 0.0, 0.0), (0.0, 0.017, 0.0))
        self.config = {
            "step_scale": 0.5,
            "sensitivity_scale_factor": 0.5,
            "numerical_budget_fraction": 0.1,
            "fallback_model_id": "full",
            "models": [
                {"model_id": "cheap", "planets": False, "solar_gr": False, "small_bodies": False, "earth_j2": False, "non_grav": "off"},
                {"model_id": "full", "planets": True, "solar_gr": True, "small_bodies": True, "earth_j2": True, "non_grav": "if_available"},
            ],
        }
        self.tree = {
            "schema_version": 3,
            "method": "cart_selector_v3",
            "models": ["cheap", "full"],
            "fallback_model_id": "full",
            "feature_dimension": 1,
            "horizons_days": [7.0],
            "trees": {m: {"schema_version": 1, "model": "cart_regression_tree", "n_features": 1,
                           "root": {"type": "leaf", "prediction": 0.0}} for m in ("cheap", "full")},
            "calibration_margin_log10": {"cheap": 0.0, "full": 0.0},
            "median_cost_seconds": {"cheap": {"7.0": 1.0}, "full": {"7.0": 2.0}},
        }

    def test_immutable_json_rejects_changed_completed_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            runner.immutable_json(path, {"a": 1})
            runner.immutable_json(path, {"a": 1})
            with self.assertRaises(RuntimeError):
                runner.immutable_json(path, {"a": 2})

    def test_trace_validation_rejects_nonmonotonic_and_nonfinite_points(self):
        valid = {"accepted_states": [
            {"time_days": 0.0, "state": {"r": [1, 0, 0], "v": [0, .017, 0]}},
            {"time_days": 1.0, "state": {"r": [1, .017, 0], "v": [0, .017, 0]}},
        ]}
        self.assertEqual(len(runner._trace_states(valid)), 2)
        with self.assertRaises(ValueError):
            runner._trace_states({"accepted_states": [valid["accepted_states"][0], {**valid["accepted_states"][1], "time_days": 0.0}]})
        with self.assertRaises(ValueError):
            runner._trace_states({"accepted_states": [{**valid["accepted_states"][0], "time_days": math.nan}]})

    def test_horizon_guard_promotes_strong_geometry_to_explicit_fallback(self):
        feature = {"vector": [0.0], "strong_encounter": True}
        with patch.object(runner, "choose_model_v2", return_value={"model_id": "cheap", "fallback": False}):
            decision = runner.decide("horizon_guard", feature, 7.0, 1.0, self.config, {}, self.tree)
        self.assertEqual(decision["model_id"], "full")
        self.assertEqual(decision["status"], "strong_encounter_unvalidated")
        self.assertTrue(decision["fallback"])

    def test_online_timing_builds_features_only_for_guarded_methods(self):
        context = {"mu": 1.0}
        calls = []
        fake_feature = {"vector": [0.0], "strong_encounter": False}

        def feature(*args, **kwargs):
            calls.append("feature")
            return fake_feature

        def forecast(initial, start, horizon, model, context, config, scale, *, ng=None):
            calls.append("forecast")
            return [initial], {"accepted_states": [{"time_days": 0.0, "state": initial}], "runtime_seconds": 0.0, "rk4_steps": 0}

        with patch.object(runner, "features_v3", side_effect=feature), patch.object(runner, "forecast_candidate_v2", side_effect=forecast), patch.object(runner, "choose_model_v2", return_value={"model_id": "cheap", "fallback": False}):
            runner.forecast_online(self.initial, 2450000.0, 7.0, 1.0, "fixed_full", self.tree, {}, context, self.config, {})
            self.assertEqual(calls, ["forecast"])
            calls.clear()
            runner.forecast_online(self.initial, 2450000.0, 7.0, 1.0, "horizon_guard", self.tree, {}, context, self.config, {})
        self.assertEqual(calls, ["feature", "forecast"])

    def test_forecast_online_rejects_unknown_method_and_keeps_no_object_id_api(self):
        with self.assertRaises(ValueError):
            runner.forecast_online(self.initial, 2450000.0, 7.0, 1.0, "object-999", self.tree, {}, {}, self.config, {})

    def test_choices_use_selected_physical_candidate_for_no_candidate_truth(self):
        records = []
        for horizon in runner.HORIZONS:
            for model_id, error in (("cheap", 0.05), ("full", 0.2)):
                records.append({
                    "object_id": "42", "model_id": model_id, "horizon_days": horizon,
                    "feature": {"vector": [0.0], "strong_encounter": False},
                    "record": {"max_position_error_km": error, "numerical_difference_km": 0.01},
                })
        sample = {"objects": [{"id": "42"}]}

        def choose(method, feature, horizon, tolerance, config, rules, tree):
            return {"method": method, "model_id": "cheap" if method == "horizon_v2" else "full",
                    "status": "predicted_feasible", "fallback": False, "strong_encounter": False}

        with patch.object(runner, "decide", side_effect=choose):
            choices, _ = runner._choice_rows(records, sample, self.config, {}, {})
        horizon = [row for row in choices if row["method"] == "horizon_v2" and row["tolerance_km"] == 1.0][0]
        self.assertEqual(horizon["selected_model_id"], "cheap")
        self.assertTrue(horizon["actual_eligible"])
        self.assertTrue(horizon["any_candidate_eligible"])
        self.assertFalse(horizon["no_candidate_truth"])

    def test_pair_rollout_has_two_annual_forecasts_and_five_prefix_records(self):
        calls = []
        fake_meta = {"accepted_states": [], "runtime_seconds": 0.0, "rk4_steps": 1}

        def fake_case(*args, **kwargs):
            calls.append((args, kwargs))
            return {"horizon_days": float(args[2]) if len(args) > 2 else 0.0}

        with patch.object(runner, "load_object_rows", return_value=([], [])), \
             patch.object(runner, "_find_initial", return_value=(2450000.0, self.initial)), \
             patch.object(runner, "load_ng_input", return_value=None), \
             patch.object(runner, "features_v3", return_value={"vector": [0.0]}), \
             patch.object(runner, "forecast_candidate_v2", return_value=([], fake_meta)) as forecast, \
             patch.object(runner, "_case", side_effect=fake_case):
            result = runner._run_pair(Path("."), {"id": "42", "name": "x", "start_date": "2029-01-01"},
                                      self.config["models"][0], {}, self.config, {})
        self.assertEqual(forecast.call_count, 2)
        self.assertEqual(len(result[0]), len(runner.HORIZONS))
        self.assertEqual(len(calls), 5)

    def test_choice_uses_actual_metrics_of_selected_candidate_at_each_tolerance(self):
        records = []
        for horizon in runner.HORIZONS:
            for model_id, error in (("cheap", 0.5), ("full", 0.05)):
                records.append({"object_id": "42", "model_id": model_id, "horizon_days": horizon,
                                "feature": {"vector": [0.0], "strong_encounter": False},
                                "record": {"max_position_error_km": error, "numerical_difference_km": 0.001}})

        def choose(method, feature, horizon, tolerance, config, rules, tree):
            selected = "full" if tolerance == 0.1 else "cheap"
            return {"method": method, "model_id": selected, "status": "predicted_feasible",
                    "fallback": False, "strong_encounter": False}

        with patch.object(runner, "decide", side_effect=choose):
            choices, summaries = runner._choice_rows(records, {"objects": [{"id": "42"}]}, self.config, {}, {})
        small = next(row for row in choices if row["method"] == "horizon_v2" and row["tolerance_km"] == 0.1)
        broad = next(row for row in choices if row["method"] == "horizon_v2" and row["tolerance_km"] == 1.0)
        self.assertEqual(small["selected_model_id"], "full")
        self.assertAlmostEqual(small["max_position_error_km"], 0.05)
        self.assertTrue(small["actual_eligible"])
        self.assertEqual(broad["selected_model_id"], "cheap")
        self.assertAlmostEqual(broad["max_position_error_km"], 0.5)
        self.assertTrue(broad["actual_eligible"])
        self.assertEqual(len(summaries), len(runner.TOLERANCES) * len(runner.METHODS))
        self.assertTrue(all(row["cases"] == len(runner.HORIZONS) for row in summaries))

    def test_no_candidate_truth_is_distinct_from_fixed_full_and_selector_fallback(self):
        records = [{"object_id": "42", "model_id": model, "horizon_days": horizon,
                    "feature": {"vector": [0.0], "strong_encounter": False},
                    "record": {"max_position_error_km": 2.0, "numerical_difference_km": 0.2}}
                   for horizon in runner.HORIZONS for model in ("cheap", "full")]

        def choose(method, feature, horizon, tolerance, config, rules, tree):
            return {"method": method, "model_id": "full", "status": "fixed_full" if method == "fixed_full" else "no_candidate_predicted",
                    "fallback": method != "fixed_full", "warning": method != "fixed_full", "strong_encounter": False}

        with patch.object(runner, "decide", side_effect=choose):
            choices, _ = runner._choice_rows(records, {"objects": [{"id": "42"}]}, self.config, {}, {})
        row = next(item for item in choices if item["method"] == "horizon_v2" and item["tolerance_km"] == 1.0)
        self.assertTrue(row["no_candidate_truth"])
        self.assertTrue(row["no_candidate_flagged"])
        fixed = next(item for item in choices if item["method"] == "fixed_full" and item["tolerance_km"] == 1.0)
        self.assertTrue(fixed["no_candidate_truth"])
        self.assertFalse(fixed["no_candidate_flagged"])

    def test_unguarded_v2_and_fixed_full_do_not_report_strong_feature_warning(self):
        feature = {"vector": [0.0], "strong_encounter": True}
        fixed = runner.decide("fixed_full", feature, 7.0, 1.0, self.config, {}, self.tree)
        self.assertFalse(fixed["fallback"])
        self.assertEqual(fixed["status"], "fixed_full")
        with patch.object(runner, "choose_model_v2", return_value={"model_id": "cheap", "fallback": False}):
            unguarded = runner.decide("horizon_v2", feature, 7.0, 1.0, self.config, {}, self.tree)
        self.assertFalse(unguarded["strong_encounter"])


if __name__ == "__main__":
    unittest.main()

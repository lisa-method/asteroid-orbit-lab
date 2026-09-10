from __future__ import annotations
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from orbit_baselines import State
import run_selector_v4_holdout24 as runner

class SelectorV4RunnerTests(unittest.TestCase):
    def setUp(self):
        self.initial = State((1., 0., 0.), (0., .017, 0.))
        self.config = {"step_scale": .5, "fallback_model_id": "full", "models": [{"model_id": "cheap"}, {"model_id": "full"}], "numerical_budget_fraction": .1}
        self.tree = {"trees": {"cheap": {"root": {"type": "leaf", "prediction": 0.}}, "full": {"root": {"type": "leaf", "prediction": 0.}}}, "median_cost_seconds": {"cheap": {"7.0": 1}, "full": {"7.0": 2}}}
        self.v4 = {"schema_version": 4}

    def test_immutable_json_rejects_changed_completed_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            runner.immutable_json(path, {"a": 1}); runner.immutable_json(path, {"a": 1})
            with self.assertRaises(RuntimeError): runner.immutable_json(path, {"a": 2})

    def test_v4_methods_are_exactly_four(self):
        self.assertEqual(runner.METHODS, ("tree_v3", "physics_v4", "hybrid_v4", "fixed_full"))

    def test_decisions_delegate_to_v4_api_and_keep_warning_reasons(self):
        feature = {"vector": [0.] * 9, "strong_encounter": False, "force_proxies_km": {"planet": 0.}}
        expected = {"model_id": "cheap", "status": "outside_training_support", "fallback": True, "warning": True, "reasons": ["outside_support"]}
        with patch.object(runner, "choose_v4", return_value=expected) as chooser:
            decision = runner.decide("physics_v4", feature, 7., 1., self.config, {}, self.tree, self.v4)
        chooser.assert_called_once(); self.assertTrue(decision["warning"]); self.assertEqual(decision["reasons"], ["outside_support"])

    def test_online_builds_causal_builder_for_v4(self):
        calls = []
        def f4(*args, **kwargs):
            self.assertEqual(args[5], self.config)
            self.assertEqual(len(args), 6)
            calls.append("v4")
            return {"vector": [0.] * 9}
        def forecast(*args, **kwargs):
            calls.append("forecast"); return [self.initial], {"accepted_states": [{"time_days": 0., "state": self.initial}]}
        with patch.object(runner, "features_v4", side_effect=f4), patch.object(runner, "forecast_candidate_v2", side_effect=forecast), patch.object(runner, "choose_v4", return_value={"model_id": "cheap", "status": "predicted_feasible", "fallback": False}) as chooser:
            runner.forecast_online(self.initial, 2450000., 7., 1., "physics_v4", {}, self.v4, {}, self.config, {})
            self.assertEqual(chooser.call_args.args[3], self.v4)
        self.assertEqual(calls, ["v4", "forecast"])

    def test_v3_strong_encounter_and_no_candidate_metadata_preserved(self):
        raw = {"model_id": "full", "strong_encounter": True, "status": "strong_encounter_unvalidated",
               "fallback": True, "predicted_error_caps_km": {"cheap": 3., "full": 2.}}
        with patch.object(runner, "choose_v3", return_value=raw):
            result = runner.decide("tree_v3", {}, 7., 1., self.config, {}, self.tree)
        self.assertTrue(result["strong_encounter"])
        self.assertTrue(result["no_candidate_predicted"])
        self.assertTrue(result["warning"])

    def test_unknown_method_and_nonfinite_trace_rejected(self):
        with self.assertRaises(ValueError): runner.forecast_online(self.initial, 1., 7., 1., "object-1", {}, {}, {}, self.config, {})
        with self.assertRaises(ValueError): runner._trace_states({"accepted_states": [{"time_days": math.nan, "state": {"r": [1,0,0], "v": [0,.1,0]}}]})

if __name__ == "__main__": unittest.main()

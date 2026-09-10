import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from development_rules import MODEL_IDS, fit_rules
from orbit_baselines import State, norm, subtract
from run_development_selection import forecast_with_selection, summarize


class OperationalSelectionTests(unittest.TestCase):
    def test_selection_and_recursive_rollout_match_circular_solution(self):
        horizon = 7
        label_base = {"case_id": "synthetic", "object_id": "synthetic", "split": "train", "horizon_days": horizon,
                      "max_position_error_km": 0, "numerical_difference_km": 0}
        rows = [{**label_base, "model_id": model, "runtime_median_seconds": index+1} for index, model in enumerate(MODEL_IDS)]
        proxies = {"planet_proxy_km": 0, "gr_proxy_km": 1e-5, "small_body_proxy_km": 0}
        feature_rows = [{**label_base, "features": proxies, "runtime_median_seconds": 0}]
        artifact = fit_rules(rows, feature_rows)
        mu = 0.0002959122082855911
        context = {"mu": mu, "au_km": 149597870.7, "day_s": 86400, "c_au_d": 173.1446, "planets": (), "small": ()}
        config = {"step_scale": .5, "default_step_days": .0625, "models": [{"model_id": m} for m in MODEL_IDS]}
        initial = State((1., 0., 0.), (0., math.sqrt(mu), 0.))
        states, meta, measured = forecast_with_selection(initial, 0, horizon, 1, "horizon_rule", artifact, context, config)
        expected = (math.cos(math.sqrt(mu)*horizon), math.sin(math.sqrt(mu)*horizon), 0)
        self.assertLess(norm(subtract(states[-1].position, expected))*context["au_km"], 1e-5)
        self.assertEqual(measured["decision"]["model_id"], "B2")
        self.assertGreater(meta["rk4_steps"], 1)
        self.assertGreater(measured["runtime_seconds"], measured["inference_seconds"])

    def test_report_keeps_infeasible_cases_and_confident_failures(self):
        config = {"models": [{"model_id": m} for m in MODEL_IDS], "position_tolerances_km": [1], "numerical_budget_fraction": .1}
        rows = []
        for case, error in (("possible", .01), ("none", 10)):
            for model in MODEL_IDS:
                rows.append({"case_id": case, "model_id": model, "max_position_error_km": error,
                             "numerical_difference_km": .001, "runtime_median_seconds": 1})
        selections = [{"case_id": "none", "object_id": "bad", "method": method, "tolerance_km": 1,
                       "actual_eligible": False, "fallback": False, "runtime_median_seconds": 2,
                       "feature_median_seconds": .1, "selected_model_id": "B3"} for method in ("horizon_rule", "physics_rule")]
        result = summarize(rows, selections, config)[0]
        self.assertEqual(result["case_count"], 2)
        self.assertEqual(result["feasible_cases"], 1)
        self.assertEqual(result["methods"][0]["confident_failures"], 1)
        self.assertEqual(result["fixed_models"][0]["total_runtime_seconds"], 2)


if __name__ == "__main__":
    unittest.main()

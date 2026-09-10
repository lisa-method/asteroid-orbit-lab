import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import run_b3plus_ablation
from orbit_baselines import State, two_body_acceleration
from run_numerical_audit import (
    daily_targets,
    distance_errors,
    empirical_order,
    fixed_policy,
    grid_difference,
    recommend_variable_policy,
    variable_policy,
)


class NumericalAuditTests(unittest.TestCase):
    def test_finest_policy_cannot_pass_budget_by_comparison_to_itself(self):
        case = {"variable": {
            "scale_1": {"vs_finest_variable": {"max_position_difference_km": 2.0}, "rk4_steps": 10, "runtime_seconds": 1.0},
            "scale_0.5": {"vs_finest_variable": {"max_position_difference_km": 0.0}, "rk4_steps": 20, "runtime_seconds": 2.0},
        }}
        self.assertIsNone(recommend_variable_policy([case], [1.0, 0.5], 0.01)["scale_factor"])
        self.assertEqual(recommend_variable_policy([case], [1.0, 0.5], 3.0)["scale_factor"], 1.0)

    def test_daily_grid_includes_zero_and_final_day(self):
        self.assertEqual(daily_targets(3), [0.0, 1.0, 2.0, 3.0])

    def test_variable_policy_reproduces_fixed_policy_when_no_solar_guard_triggers(self):
        mu = 2.959122082855911e-4
        initial = State((1.0, 0.0, 0.0), (0.0, mu**0.5, 0.0))
        targets = [0.0, 1.0]
        acceleration = two_body_acceleration(mu)
        fixed, fixed_steps = fixed_policy(initial, targets, acceleration, 0.0625)
        variable, variable_steps = variable_policy(
            initial, 0.0, targets, acceleration, 0.0625, 1.0
        )
        self.assertEqual(fixed_steps, variable_steps)
        for actual, expected in zip(fixed, variable):
            self.assertEqual(actual, expected)

    def test_grid_difference_identifies_worst_output_day(self):
        left = [State((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)) for _ in range(3)]
        right = [
            State((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
            State((1.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
            State((0.5, 0.0, 0.0), (0.0, 0.0, 0.0)),
        ]
        result = grid_difference(left, right, 10.0, 1.0)
        self.assertEqual(result["max_position_difference_day"], 1)
        self.assertEqual(result["max_position_difference_km"], 10.0)

    def test_empirical_order_is_diagnostic_fourth_order_ratio(self):
        self.assertAlmostEqual(empirical_order(16.0, 1.0), 4.0)
        self.assertIsNone(empirical_order(0.0, 1.0))

    def test_b3plus_main_passes_planets_to_ablations_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            config = {
                "data_config": "data.json",
                "baseline_results": "baseline.json",
                "nbody_results": "nbody.json",
                "event_config": "event.json",
                "speed_of_light_km_s": 1.0,
                "small_body_mass_source": "test",
                "small_body_mass_source_url": "https://example.invalid/test",
                "default_step_days": 1.0,
                "sensitivity_scale_factor": 0.5,
                "ablations": [{"model": "B3", "solar_gr": False, "small_bodies": False, "non_gravitational": False}],
                "sensitivity_cases": [],
                "artifacts": {"output_json": "out.json", "report": "report.md"},
            }
            data_config = {
                "constants": {"au_km": 1.0, "day_s": 1.0, "mu_sun_km3_s2": 1.0},
                "asteroids": [],
            }
            baseline = {"evaluation_start_dates": [], "horizons_days": []}
            config_path.write_text(json.dumps(config), encoding="utf-8")
            (root / "data.json").write_text(json.dumps(data_config), encoding="utf-8")
            (root / "baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
            (root / "nbody.json").write_text("{}", encoding="utf-8")
            (root / "event.json").write_text("{}", encoding="utf-8")

            def fake_load_json(path):
                return json.loads(Path(path).read_text(encoding="utf-8"))

            planets = ("planet",)
            small_bodies = ("small",)
            eval_ablations = lambda *args: ([], {}, {"rows": []})
            eval_event = lambda *args: {}
            with patch.object(run_b3plus_ablation, "load_json", side_effect=fake_load_json), \
                patch.object(run_b3plus_ablation, "load_asteroid_series", return_value={}), \
                patch.object(run_b3plus_ablation, "load_perturbers", return_value=planets), \
                patch.object(run_b3plus_ablation, "load_small_body_perturbers", return_value=small_bodies), \
                patch.object(run_b3plus_ablation, "load_non_gravitational_parameters", return_value=({}, [])), \
                patch.object(run_b3plus_ablation, "evaluate_ablations", side_effect=eval_ablations) as mocked_ablations, \
                patch.object(run_b3plus_ablation, "evaluate_refined_event", side_effect=eval_event) as mocked_event, \
                patch.object(run_b3plus_ablation, "sensitivity_check", return_value=[]), \
                patch.object(run_b3plus_ablation, "base_regression_check", return_value={}), \
                patch.object(run_b3plus_ablation, "aggregate", return_value=[]), \
                patch.object(run_b3plus_ablation, "aggregate_shifts", return_value=[]), \
                patch.object(run_b3plus_ablation, "build_report", return_value="smoke\n"), \
                patch.object(sys, "argv", ["run_b3plus_ablation.py", "--config", str(config_path), "--root", str(root)]):
                self.assertEqual(run_b3plus_ablation.main(), 0)

            self.assertIs(mocked_ablations.call_args.args[7], planets)
            self.assertIs(mocked_ablations.call_args.args[8], small_bodies)
            self.assertIs(mocked_event.call_args.args[6], small_bodies)
            self.assertEqual(len(mocked_event.call_args.args), 12)


if __name__ == "__main__":
    unittest.main()

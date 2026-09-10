import math
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trajectory_metrics import offline_oracle, summarize_error_rows, trajectory_error_rows
from orbit_baselines import State


class TrajectoryMetricsTests(unittest.TestCase):
    def test_rtn_signs_and_unit_conversion_are_known(self):
        references = [((2.0, 0.0, 0.0), (0.0, 3.0, 0.0))]
        predictions = [((2.001, 0.002, 0.003), (0.01, 3.02, 0.03))]
        row = trajectory_error_rows([0.0], predictions, references, au_km=100.0, day_s=10.0)[0]

        self.assertAlmostEqual(row["position_error_km"], math.sqrt(0.14))
        self.assertAlmostEqual(row["velocity_error_m_s"], math.sqrt(140000.0))
        for actual, expected in zip(row["rtn_position_error_km"], (0.1, 0.2, 0.3)):
            self.assertAlmostEqual(actual, expected)
        for actual, expected in zip(row["rtn_velocity_error_m_s"], (100.0, 200.0, 300.0)):
            self.assertAlmostEqual(actual, expected)

    def test_existing_state_dataclass_is_accepted(self):
        reference = State((2.0, 0.0, 0.0), (0.0, 3.0, 0.0))
        prediction = State((2.001, 0.0, 0.0), (0.0, 3.0, 0.0))
        row = trajectory_error_rows([0.0], [prediction], [reference], 100.0, 10.0)[0]
        self.assertAlmostEqual(row["position_error_km"], 0.1)
        for actual, expected in zip(row["rtn_position_error_km"], (0.1, 0.0, 0.0)):
            self.assertAlmostEqual(actual, expected)

    def test_rotation_preserves_norm_and_rotates_rtn_consistently(self):
        reference = [((2.0, 0.0, 0.0), (0.0, 3.0, 0.0))]
        prediction = [((2.001, 0.002, 0.003), (0.01, 3.02, 0.03))]
        base = trajectory_error_rows([1.0], prediction, reference, 100.0, 10.0)[0]

        def rotate_z(state):
            position, velocity = state
            rotate = lambda vector: (-vector[1], vector[0], vector[2])
            return rotate(position), rotate(velocity)

        rotated = trajectory_error_rows(
            [1.0], [rotate_z(prediction[0])], [rotate_z(reference[0])], 100.0, 10.0
        )[0]
        self.assertAlmostEqual(rotated["position_error_km"], base["position_error_km"])
        self.assertAlmostEqual(rotated["velocity_error_m_s"], base["velocity_error_m_s"])
        self.assertEqual(rotated["rtn_position_error_km"], base["rtn_position_error_km"])
        self.assertEqual(rotated["rtn_velocity_error_m_s"], base["rtn_velocity_error_m_s"])

    def test_summary_exposes_intermediate_peak_and_endpoint_rtn(self):
        rows = [
            {
                "time_days": 0.0,
                "position_error_km": 1.0,
                "velocity_error_m_s": 3.0,
                "rtn_position_error_km": (1.0, -0.5, 0.0),
                "rtn_velocity_error_m_s": (0.0, 0.0, 0.0),
            },
            {
                "time_days": 1.0,
                "position_error_km": 8.0,
                "velocity_error_m_s": 2.0,
                "rtn_position_error_km": (-8.0, 2.0, -1.0),
                "rtn_velocity_error_m_s": (0.0, 0.0, 0.0),
            },
            {
                "time_days": 2.0,
                "position_error_km": 4.0,
                "velocity_error_m_s": 5.0,
                "rtn_position_error_km": (4.0, 0.25, 3.0),
                "rtn_velocity_error_m_s": (0.0, 0.0, 0.0),
            },
        ]
        summary = summarize_error_rows(rows)
        self.assertEqual(summary["sample_count"], 3)
        self.assertEqual(summary["endpoint_position_error_km"], 4.0)
        self.assertEqual(summary["max_position_error_km"], 8.0)
        self.assertEqual(summary["max_position_error_time_days"], 1.0)
        self.assertEqual(summary["endpoint_velocity_error_m_s"], 5.0)
        self.assertEqual(summary["max_velocity_error_m_s"], 5.0)
        self.assertEqual(summary["rtn_endpoint_position_error_km"], (4.0, 0.25, 3.0))
        self.assertEqual(summary["rtn_max_abs_position_error_km"], (8.0, 2.0, 3.0))

    def test_oracle_excludes_numerically_bad_candidate_and_keeps_none(self):
        records = [
            {"case_id": "a", "model_id": "fast", "max_position_error_km": 0.5, "runtime_median_seconds": 1.0, "numerical_difference_km": 0.15},
            {"case_id": "a", "model_id": "slow", "max_position_error_km": 0.4, "runtime_median_seconds": 2.0, "numerical_difference_km": 0.01},
            {"case_id": "b", "model_id": "fast", "max_position_error_km": 2.0, "runtime_median_seconds": 1.0, "numerical_difference_km": 0.0},
            {"case_id": "b", "model_id": "slow", "max_position_error_km": 0.8, "runtime_median_seconds": 3.0, "numerical_difference_km": 0.2},
            {"case_id": "c", "model_id": "fast", "max_position_error_km": 1.1, "runtime_median_seconds": 1.0, "numerical_difference_km": 0.0},
            {"case_id": "c", "model_id": "slow", "max_position_error_km": 1.2, "runtime_median_seconds": 3.0, "numerical_difference_km": 0.0},
        ]
        result = offline_oracle(records, position_tolerance_km=1.0)
        self.assertEqual(result["selections"], {"a": "slow", "b": None, "c": None})
        self.assertEqual(result["selected_count"], 1)
        self.assertEqual(result["feasible_case_count"], 1)
        self.assertEqual(result["infeasible_case_count"], 2)
        self.assertEqual(result["oracle_total_runtime_seconds"], 2.0)
        self.assertEqual(result["selection_counts_by_model"], {"fast": 0, "slow": 1})
        self.assertEqual(result["fixed_model_summaries"]["fast"]["feasible_case_count"], 0)
        self.assertEqual(result["fixed_model_summaries"]["slow"]["feasible_case_count"], 1)
        self.assertEqual(
            result["fixed_model_summaries"]["slow"]["oracle_cost_on_model_feasible_cases_seconds"],
            2.0,
        )
        self.assertAlmostEqual(result["fixed_model_summaries"]["slow"]["oracle_to_fixed_cost_ratio"], 1.0)

    def test_oracle_paired_totals_use_only_common_feasible_cases(self):
        records = {
            ("a", "cheap"): {"max_position_error_km": 0.5, "runtime_median_seconds": 1.0, "numerical_difference_km": 0.0},
            ("a", "expensive"): {"max_position_error_km": 0.6, "runtime_median_seconds": 3.0, "numerical_difference_km": 0.0},
            ("b", "cheap"): {"max_position_error_km": 0.4, "runtime_median_seconds": 1.0, "numerical_difference_km": 0.0},
            ("b", "expensive"): {"max_position_error_km": 1.1, "runtime_median_seconds": 3.0, "numerical_difference_km": 0.0},
        }
        result = offline_oracle(records, 1.0)
        pair = result["paired_cost_totals"][0]
        self.assertEqual(pair["common_feasible_case_count"], 1)
        self.assertEqual(pair["model_a_cost_total_seconds"], 1.0)
        self.assertEqual(pair["model_b_cost_total_seconds"], 3.0)

    def test_invalid_trajectory_and_oracle_inputs_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            trajectory_error_rows([0.0, 0.0], [((1, 0, 0), (0, 1, 0))] * 2, [((1, 0, 0), (0, 1, 0))] * 2, 1.0, 1.0)
        with self.assertRaisesRegex(ValueError, "degenerate"):
            trajectory_error_rows([0.0], [((1, 0, 0), (0, 1, 0))], [((1, 0, 0), (0, 0, 0))], 1.0, 1.0)
        duplicate = [
            {"case_id": "a", "model_id": "m", "max_position_error_km": 0.1, "runtime_median_seconds": 1.0, "numerical_difference_km": 0.0},
            {"case_id": "a", "model_id": "m", "max_position_error_km": 0.1, "runtime_median_seconds": 1.0, "numerical_difference_km": 0.0},
        ]
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            offline_oracle(duplicate, 1.0)


if __name__ == "__main__":
    unittest.main()

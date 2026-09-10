import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from run_apophis_initial_sensitivity import _analysis_pair, _probe_initial, compare_amplitudes


class InitialSensitivityAnalysisTests(unittest.TestCase):
    def test_realised_probe_delta_is_measured_after_binary_addition(self):
        base = (1.0, 2.0, 3.0, 0.1, 0.2, 0.3)
        perturbed, delta = _probe_initial(
            base, {"component": "position", "axis": 0, "amplitude": 1.0,
                   "amplitude_units": "m", "sign": 1}, 149_597_870.7, 86_400.0)
        self.assertEqual(delta["velocity_m_s"], 0.0)
        self.assertEqual(delta["position_m"], (perturbed[0] - base[0]) * 149_597_870.7 * 1000.0)
        self.assertNotEqual(delta["position_m"], 0.0)

    def test_central_difference_uses_internal_differences_before_si_conversion(self):
        baseline = {"requested_states": [[1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                                          [1.0, 0.0, 0.0, 0.0, 0.0, 0.0]]}
        common = {"component": "position", "axis": 0, "amplitude": 1.0,
                  "amplitude_units": "m"}
        plus = {"key": "plus", "probe": {**common, "sign": 1},
                "actual_delta_si": {"position_m": 1.0, "velocity_m_s": 0.0},
                "requested_states": [[1.0 + 2e-12, 0.0, 0.0, 3e-12, 0.0, 0.0],
                                      [1.0 + 4e-12, 0.0, 0.0, 6e-12, 0.0, 0.0]]}
        minus = {"key": "minus", "probe": {**common, "sign": -1},
                 "actual_delta_si": {"position_m": -1.0, "velocity_m_s": 0.0},
                 "requested_states": [[1.0 - 2e-12, 0.0, 0.0, -3e-12, 0.0, 0.0],
                                       [1.0 - 4e-12, 0.0, 0.0, -6e-12, 0.0, 0.0]]}
        analysis = _analysis_pair(baseline, plus, minus, 1e9, 1.0,
                                  {"0.0": 0, "365.0": 1})
        expected_position = (plus["requested_states"][1][0] - minus["requested_states"][1][0]) * 1e9 * 1000.0 / 2.0
        expected_velocity = (plus["requested_states"][1][3] - minus["requested_states"][1][3]) * 1e9 * 1000.0 / 2.0
        self.assertAlmostEqual(analysis["diagnostics"]["365.0"]["derivative_position"][0], expected_position, delta=1e-9)
        self.assertAlmostEqual(analysis["diagnostics"]["365.0"]["derivative_velocity"][0], expected_velocity, delta=1e-9)
        self.assertEqual(analysis["diagnostics"]["365.0"]["derivative_units"]["position"], "m_per_m")

    def test_velocity_probe_free_flight_uses_position_response(self):
        baseline = {"requested_states": [[0.0] * 6, [0.0] * 6]}
        common = {"component": "velocity", "axis": 0, "amplitude": 1e-6,
                  "amplitude_units": "m_s"}
        plus = {"key": "plus", "probe": {**common, "sign": 1},
                "actual_delta_si": {"position_m": 0.0, "velocity_m_s": 1e-6},
                "requested_states": [[1e-12, 0.0, 0.0, 5e-13, 0.0, 0.0],
                                      [2e-12, 0.0, 0.0, 1e-12, 0.0, 0.0]]}
        minus = {"key": "minus", "probe": {**common, "sign": -1},
                 "actual_delta_si": {"position_m": 0.0, "velocity_m_s": -1e-6},
                 "requested_states": [[-1e-12, 0.0, 0.0, -5e-13, 0.0, 0.0],
                                       [-2e-12, 0.0, 0.0, -1e-12, 0.0, 0.0]]}
        analysis = _analysis_pair(baseline, plus, minus, 1e9, 10.0,
                                  {"0.0": 0, "365.0": 1})
        row = analysis["diagnostics"]["365.0"]
        self.assertEqual(row["position_response_relative_to_free_flight"], row["position_response_norm"] / (365.0 * 10.0))
        self.assertEqual(row["derivative_units"]["position"], "m_per_(m_per_s)=s")

    def test_corrected_midpoint_removes_representable_input_asymmetry(self):
        baseline = {"requested_states": [[1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                                          [1.0, 0.0, 0.0, 0.0, 0.0, 0.0]]}
        common = {"component": "position", "axis": 0, "amplitude": 1.0,
                  "amplitude_units": "m"}
        plus = {"key": "plus", "probe": {**common, "sign": 1},
                "actual_delta_si": {"position_m": 1.1, "velocity_m_s": 0.0},
                "requested_states": [[1.0 + 1.1e-12, 0, 0, 0, 0, 0],
                                      [1.0 + 2.2e-12, 0, 0, 0, 0, 0]]}
        minus = {"key": "minus", "probe": {**common, "sign": -1},
                 "actual_delta_si": {"position_m": -0.9, "velocity_m_s": 0.0},
                 "requested_states": [[1.0 - 0.9e-12, 0, 0, 0, 0, 0],
                                       [1.0 - 1.8e-12, 0, 0, 0, 0, 0]]}
        result = _analysis_pair(baseline, plus, minus, 1e9, 1.0, {"0.0": 0, "365.0": 1})
        row = result["annual"]
        self.assertLess(abs(row["corrected_midpoint_displacement"][0]), abs(row["raw_midpoint_displacement"][0]))

    def test_consistency_uses_larger_norm_and_zero_zero_is_consistent(self):
        zero = {"diagnostics": {"0.0": {"derivative_position": [0.0, 0.0, 0.0],
                                          "derivative_velocity": [0.0, 0.0, 0.0]}}}
        result = compare_amplitudes([zero, zero], 0.01)
        self.assertTrue(result["passes"])
        self.assertEqual(result["max_relative_difference_position"], 0.0)


if __name__ == "__main__":
    unittest.main()

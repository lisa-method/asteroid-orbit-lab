from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from analyze_apophis_covariance import analyze_records  # noqa: E402


CONFIG = {
    "forecast_epoch_jd_tdb": 2922.0,
    "covariance_epoch_jd_tdb": 0.0,
    "forecast_diagnostic_days": [0.0, 1.0],
    "sigma_scales": [0.25, 1.0],
}


def synthetic_records(curved: bool = False):
    # Positive-definite correlated lower factor in the eight SBDB parameter
    # coordinates.  The state map is linear, while the optional quadratic
    # term creates a controlled cubature mean shift at the forecast endpoint.
    lower = tuple(tuple(0.0 if j > i else (1.0 + 0.1 * i if i == j else 0.05)
                        for j in range(8)) for i in range(8))
    times = (0.0, 2922.0, 2923.0)
    records = []
    for identifier, kind in (("nominal_extreme", "nominal"), ("nominal_ultra", "nominal")):
        records.append({"id": identifier, "kind": kind, "samples": [
            {"day": t, "state": [0.0] * 6} for t in times],
            "ng": {"a1_au_d2": 0.0, "a2_au_d2": 0.0}})
    for scale in (0.25, 1.0):
        for column in range(8):
            for sign in (-1, 1):
                parameter = [sign * math.sqrt(8.0) * scale * lower[row][column] for row in range(8)]
                samples = []
                for t in times:
                    response = parameter[:6]
                    if curved and t == 2923.0:
                        response = list(response)
                        response[0] += 0.5 * parameter[0] * parameter[0]
                    samples.append({"day": t, "state": response})
                records.append({
                    "id": f"probe_s{scale:g}_j{column}_{'plus' if sign > 0 else 'minus'}".replace(".", "p"),
                    "kind": "probe", "scale": scale, "column": column, "sign": sign,
                    "samples": samples,
                    "ng": {"a1_au_d2": parameter[6], "a2_au_d2": parameter[7]},
                })
    return records


class ApophisCovarianceAnalysisTests(unittest.TestCase):
    def test_linear_correlated_cubature_matches_linear_covariance(self):
        result = analyze_records(synthetic_records(), CONFIG, 1.0, 1.0)
        self.assertEqual(result["record_count"], 34)
        endpoint = next(item for item in result["diagnostic_days"] if item["forecast_day"] == 1.0)
        self.assertLess(endpoint["relative_frobenius"]["full_vs_linear_position"], 1e-12)
        self.assertLess(endpoint["relative_frobenius"]["normalized_small_vs_linear_velocity"], 1e-12)
        self.assertLess(endpoint["B_scale_relative_difference"]["position"], 1e-12)
        self.assertLess(endpoint["B_scale_relative_difference"]["velocity"], 1e-12)
        self.assertEqual(endpoint["basis_units"], ["AU", "AU", "AU", "AU/day", "AU/day", "AU/day"])
        self.assertEqual(endpoint["augmented_basis_units"][-2:], ["AU/day^2", "AU/day^2"])
        self.assertEqual(result["forecast_origin_joint_covariance"]["forecast_day"], 0.0)

    def test_curved_response_reports_scale_difference_and_mean_shift(self):
        result = analyze_records(synthetic_records(curved=True), CONFIG, 1.0, 1.0)
        endpoint = next(item for item in result["diagnostic_days"] if item["forecast_day"] == 1.0)
        self.assertGreater(endpoint["relative_frobenius"]["full_vs_linear_position"], 0.0)
        self.assertGreater(endpoint["mean_shift"]["position_norm_m"], 0.0)
        self.assertIsNotNone(endpoint["principal_position"]["full_scale"]["sigma_axes_m"])

    def test_record_matrix_and_grid_are_strict(self):
        malformed = synthetic_records()[:-1]
        with self.assertRaises(ValueError):
            analyze_records(malformed, CONFIG, 1.0, 1.0)

    def test_scale_response_in_eighth_column_and_major_sigma_denominator(self):
        records = synthetic_records(curved=True)
        for row in records:
            if row.get('column') == 7:
                delta = row['ng']['a2_au_d2']
                row['samples'][-1]['state'][1] += delta + delta**3
                row['samples'][-1]['state'][4] += delta + delta**3
        result = analyze_records(records, CONFIG, 1.0, 1.0)
        last = result['diagnostic_days'][-1]
        self.assertGreater(last['B_scale_relative_difference']['position'], 1.)
        self.assertGreater(last['B_scale_relative_difference']['velocity'], 1.)
        major = last['principal_position']['linear']['sigma_axes_m'][0]
        trace = last['principal_position']['linear']['sigma_sqrt_trace_m']
        self.assertGreater(trace, major)
        ratio = last['mean_shift']['relative_to_linear_position_sigma']
        self.assertAlmostEqual(ratio, last['mean_shift']['position_norm_m']/major)
        malformed = synthetic_records()
        malformed[2]["samples"][1]["day"] += 0.5
        with self.assertRaises(ValueError):
            analyze_records(malformed, CONFIG, 1.0, 1.0)


if __name__ == "__main__":
    unittest.main()

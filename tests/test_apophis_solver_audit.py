from __future__ import annotations

import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from orbit_baselines import State
from run_apophis_solver_audit import _merge_rows, _shift, _summary


class ApophisSolverAuditTests(unittest.TestCase):
    def test_merge_replaces_only_matching_epoch_and_sorts(self) -> None:
        daily = [{"epoch_jd_tdb": 2.0}, {"epoch_jd_tdb": 1.0}]
        refined = [{"epoch_jd_tdb": 1.0, "refined": True}, {"epoch_jd_tdb": 1.5}]
        merged = _merge_rows(daily, refined)
        self.assertEqual([row["epoch_jd_tdb"] for row in merged], [1.0, 1.5, 2.0])
        self.assertTrue(merged[0]["refined"])

    def test_summary_and_shift_are_finite_and_use_expected_units(self) -> None:
        truth = [State((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)), State((1.0, 0.0, 0.0), (0.0, 0.0, 0.0))]
        prediction = [State((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)), State((1.0 + 1e-6, 0.0, 0.0), (0.0, 0.0, 0.0))]
        summary = _summary(prediction, truth, 100.0, 10.0)
        self.assertAlmostEqual(summary["final_position_error_km"], 1e-4)
        shift = _shift(prediction, truth, 100.0, 10.0)
        self.assertAlmostEqual(shift["final_position_shift_km"], 1e-4)
        self.assertTrue(all(math.isfinite(value) for value in (*summary.values(), *shift.values())))

    def test_protocol_declares_fixed_grid_and_no_future_inputs(self) -> None:
        config = json.loads((ROOT / "configs/apophis_solver_audit.json").read_text())
        self.assertEqual(config["rk4_step_scales"], [0.5, 0.25, 0.125])
        self.assertEqual([item["label"] for item in config["dopri54_tolerances"]], ["loose", "medium", "tight", "tighter", "extreme"])
        self.assertIn("same old daily-plus-refined TDB grid", config["protocol"]["evaluation"])
        self.assertIn("no future asteroid state", config["protocol"]["no_future_inputs"])


if __name__ == "__main__":
    unittest.main()

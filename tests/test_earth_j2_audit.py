from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from run_earth_j2_audit import _variant_plan, merge_ephemeris_rows  # noqa: E402


class EarthJ2AuditTests(unittest.TestCase):
    def test_refined_rows_replace_duplicate_epochs_and_remain_sorted(self):
        daily = [
            {"epoch_jd_tdb": 2.0, "value": "daily-2"},
            {"epoch_jd_tdb": 1.0, "value": "daily-1"},
        ]
        refined = [
            {"epoch_jd_tdb": 1.5, "value": "refined-15"},
            {"epoch_jd_tdb": 2.0, "value": "refined-2"},
        ]
        merged = merge_ephemeris_rows(daily, refined)
        self.assertEqual([row["epoch_jd_tdb"] for row in merged], [1.0, 1.5, 2.0])
        self.assertEqual(merged[-1]["value"], "refined-2")

    def test_variant_plan_contains_both_ng_branches_and_j2_poles(self):
        variants = _variant_plan()
        self.assertEqual(len(variants), 8)
        ids = {variant["variant_id"] for variant in variants}
        for branch in ("baseGR+SB16", "baseGR+SB16+NG"):
            self.assertIn(f"{branch}:daily", ids)
            self.assertIn(f"{branch}:merged", ids)
            self.assertIn(f"{branch}:merged+J2fixed_j2000", ids)
            self.assertIn(f"{branch}:merged+J2iau", ids)


if __name__ == "__main__":
    unittest.main()

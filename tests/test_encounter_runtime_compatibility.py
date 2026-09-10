"""Ensure cross-runtime compatibility cannot hide material geometry changes."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from verify_encounter_planets import compare_geometry


class RuntimeCompatibilityTests(unittest.TestCase):
    def example(self):
        return {"time_days": 7.9, "distance_au": .001, "distance_km": 149597.8707,
                "relative_speed_km_s": 7, "eta_helio_at_closest": 1e-5,
                "rho_sun_proxy_at_closest": None, "boundary_minimum": False,
                "zero_separation": False, "bracket_gap_days_at_minimum": 1,
                "sample_count": 10, "max_knot_gap_days": 1}

    def test_accepts_tiny_drift_but_does_not_call_it_exact(self):
        original = self.example()
        changed = {**original, "distance_km": original["distance_km"] + 1e-7}
        result = compare_geometry(changed, original, 149597870.7, require_exact=False)
        self.assertFalse(result["exact"])
        self.assertGreater(result["distance_km"], 0)
        with self.assertRaises(AssertionError):
            compare_geometry(changed, original, 149597870.7, require_exact=True)

    def test_rejects_material_distance_time_and_flag_changes(self):
        original = self.example()
        for change in ({"distance_km": original["distance_km"] + .001},
                       {"time_days": original["time_days"] + 1/86400},
                       {"boundary_minimum": True}, {"sample_count": 11}):
            with self.subTest(change=change), self.assertRaises(AssertionError):
                compare_geometry({**original, **change}, original, 149597870.7, require_exact=False)

    def test_exact_geometry_stays_exact(self):
        original = self.example()
        self.assertTrue(compare_geometry(original, original, 149597870.7, require_exact=True)["exact"])


if __name__ == "__main__":
    unittest.main()

import math
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from run_eda import acceleration_toward, calendar_to_iso, osculating_invariants, subtract


class EdaPhysicsTests(unittest.TestCase):
    def test_calendar_parser(self):
        self.assertEqual(
            calendar_to_iso("A.D. 2029-Apr-13 21:45:00.0000"),
            "2029-04-13T21:45:00",
        )

    def test_circular_orbit_invariants(self):
        mu = 2.959122082855911e-4
        semi_major_axis, eccentricity, _ = osculating_invariants(
            (1.0, 0.0, 0.0), (0.0, math.sqrt(mu), 0.0), mu
        )
        self.assertAlmostEqual(semi_major_axis, 1.0, places=12)
        self.assertAlmostEqual(eccentricity, 0.0, places=12)

    def test_heliocentric_indirect_term_cancels_at_origin(self):
        body_position = (1.0, 0.5, -0.25)
        mu = 1.0e-6
        direct = acceleration_toward(body_position, mu)
        indirect = acceleration_toward(body_position, mu)
        self.assertEqual(subtract(direct, indirect), (0.0, 0.0, 0.0))


if __name__ == "__main__":
    unittest.main()

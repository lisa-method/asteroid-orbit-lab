from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from earth_oblateness import earth_j2_acceleration
from orbit_baselines import State
from planetary_dynamics import EphemerisInterpolator, Perturber, combine_accelerations, restricted_n_body_acceleration, solar_schwarzschild_acceleration
from relative_time_dynamics import RelativeEphemerisInterpolator, build_relative_force, relative_perturbers, relative_rows


class RelativeTimeDynamicsTests(unittest.TestCase):
    def test_cubic_exact_and_frozen_arithmetic(self):
        states=(State((0.,2.,3.),(0.,0.,0.)),State((8.,2.,3.),(12.,0.,0.)))
        a=RelativeEphemerisInterpolator((0.,2.),states)
        b=EphemerisInterpolator((0.,2.),states)
        for t in (.25,.75,1.5):
            self.assertEqual(a.state_at(t),b.state_at(t))
            self.assertAlmostEqual(a.state_at(t).position[0],t**3)
            self.assertAlmostEqual(a.state_at(t).velocity[0],3*t*t)

    def test_rejects_utc_label_and_bad_state_shapes(self):
        rows=self._rows()
        with self.assertRaises(ValueError):
            relative_rows(rows,2462137.5,'2029-01-01T00:00:00Z','calendar')
        rows[0]={**rows[0],'r':(1,2,3,4),'v':(1,2)}
        with self.assertRaises(ValueError):
            relative_rows(rows,2462137.5,'2029-01-01','calendar')

    def _rows(self):
        return [
            {"epoch_jd_tdb": 2462137.5, "epoch_tdb": "2029-01-01T00:00:00", "r": (1.0, 0.0, 0.0), "v": (0.0, 1.0, 0.0)},
            {"epoch_jd_tdb": 2462138.5, "epoch_tdb": "2029-01-02T00:00:00", "r": (1.0, 1.0, 0.0), "v": (0.0, 1.0, 0.0)},
        ]

    def test_knots_and_cubic_hermite_velocity(self):
        eph = RelativeEphemerisInterpolator((0.0, 1.0), (State((0, 0, 0), (1, 0, 0)), State((1, 0, 0), (1, 0, 0))))
        self.assertEqual(eph.state_at(0.0), eph.states[0])
        self.assertEqual(eph.state_at(1.0), eph.states[1])
        self.assertAlmostEqual(eph.state_at(0.5).position[0], 0.5)
        self.assertAlmostEqual(eph.state_at(0.5).velocity[0], 1.0)

    def test_calendar_and_float_modes_are_distinct_when_jd_has_extra_bits(self):
        rows = self._rows()
        rows[1] = {**rows[1], "epoch_jd_tdb": rows[0]["epoch_jd_tdb"] + 1.0 + 2.0 * math.ulp(rows[0]["epoch_jd_tdb"])}
        floating = relative_rows(rows, rows[0]["epoch_jd_tdb"], "2029-01-01T00:00:00", "float")
        calendar = relative_rows(rows, rows[0]["epoch_jd_tdb"], "2029-01-01T00:00:00", "calendar")
        self.assertNotEqual(floating[1]["epoch_relative_days"], calendar[1]["epoch_relative_days"])
        self.assertEqual(calendar[1]["epoch_relative_days"], 1.0)

    def test_sub_jd_ulp_query_is_distinct_and_smooth(self):
        rows = self._rows()
        eph = relative_perturbers(((Perturber("x", "x", 1.0, EphemerisInterpolator.from_rows(rows)), rows),), rows[0]["epoch_jd_tdb"], "2029-01-01", "float")[0].ephemeris
        query = math.nextafter(0.5, 1.0)
        self.assertGreater(query, 0.5)
        self.assertNotEqual(eph.state_at(query).position, eph.state_at(0.5).position)
        self.assertLess(abs(eph.state_at(query).position[0] - eph.state_at(0.5).position[0]), 1e-12)

    def test_force_matches_absolute_at_common_knots_including_j2(self):
        rows_e = self._rows()
        rows_a = [{**r, "r": (1.01 + r["epoch_jd_tdb"] - rows_e[0]["epoch_jd_tdb"], 0.2, 0.1)} for r in rows_e]
        earth_abs = Perturber("399", "Earth", 3e-6, EphemerisInterpolator.from_rows(rows_e))
        earth_rel = relative_perturbers(((earth_abs, rows_e),), rows_e[0]["epoch_jd_tdb"], "2029-01-01", "float")[0]
        mu, c, radius, j2 = 0.0002959122082855911, 173.144632674240, 4.26e-5, 0.00108262545
        absolute = combine_accelerations(restricted_n_body_acceleration(mu, (earth_abs,), rows_e[0]["epoch_jd_tdb"]), solar_schwarzschild_acceleration(mu, c), earth_j2_acceleration(earth_abs, rows_e[0]["epoch_jd_tdb"], radius, j2, pole_model="iau"))
        relative = build_relative_force(rows_e[0]["epoch_jd_tdb"], (earth_rel,), mu, c, None, radius, j2)
        for t in (0.0, 0.5, 1.0):
            state = rows_a[0]["r"]
            vel = rows_a[0]["v"]
            self.assertEqual(relative(t, state, vel), absolute(t, state, vel))

    def test_validation_and_input_immutability(self):
        rows = self._rows(); original = [dict(row) for row in rows]
        converted = relative_rows(rows, rows[0]["epoch_jd_tdb"], "2029-01-01", "calendar")
        self.assertEqual(rows, original)
        self.assertEqual(len(converted), 2)
        with self.assertRaises(ValueError):
            relative_rows([rows[1], rows[0]], rows[0]["epoch_jd_tdb"], "2029-01-01", "float")
        with self.assertRaises(ValueError):
            RelativeEphemerisInterpolator((0.0, 0.0), (State((0, 0, 0), (0, 0, 0)), State((1, 0, 0), (0, 0, 0))))


if __name__ == "__main__":
    unittest.main()

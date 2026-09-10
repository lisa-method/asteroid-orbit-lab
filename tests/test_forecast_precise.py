from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ng_inputs_v2 import NGInput
from orbit_baselines import State
from planetary_dynamics import NonGravitationalParameters, Perturber
from relative_time_dynamics import RelativeEphemerisInterpolator
from forecast_precise import forecast_precise


class ForecastPreciseTests(unittest.TestCase):
    ORIGIN = 2462000.5
    CONSTANTS = {"mu_sun_au3_d2": 1.0e-12, "speed_of_light_au_d": 1.0e20}
    J2 = {"reference_radius_au": 1.0e-4, "j2": 0.0}
    DP = {"rtol": 1.0e-8, "atol_position": 1.0e-10, "atol_velocity": 1.0e-10, "min_step": 1.0e-8, "max_step": 0.2, "max_steps": 10000}
    RK4 = {"default_step_days": 0.125, "step_scale": 1.0, "max_steps": 10000}

    @classmethod
    def setUpClass(cls):
        eph = RelativeEphemerisInterpolator(
            (-0.5, 0.0, 1.0, 2.0, 3.0),
            tuple(State((10.0, 0.0, 0.0), (0.0, 0.0, 0.0)) for _ in range(5)),
        )
        cls.earth = Perturber("399", "Earth", 1.0e-15, eph)
        cls.planets = (cls.earth,)
        cls.initial = State((0.2, 0.1, 0.0), (0.001, -0.002, 0.0005))

    def test_dopri54_returns_uniform_daily_states_and_native_trace(self):
        predictions, metadata = forecast_precise(self.initial, self.ORIGIN, 2.5, self.planets, self.CONSTANTS, {"earth_j2": self.J2, "dopri54": self.DP}, solver="dopri54")
        self.assertEqual(len(predictions), 4)
        self.assertEqual(metadata["daily_times_days"], [0.0, 1.0, 2.0, 2.5])
        self.assertEqual(metadata["daily_epochs_jd_tdb"], [self.ORIGIN, self.ORIGIN + 1.0, self.ORIGIN + 2.0, self.ORIGIN + 2.5])
        self.assertEqual(metadata["native_trace"][0][0], 0.0)
        self.assertEqual(metadata["accepted_time_basis"], "relative_days_since_origin")
        self.assertEqual(metadata["solver"], "dopri54")
        self.assertFalse(metadata["accuracy_guaranteed"])
        self.assertFalse(metadata["reference_states_used"])
        self.assertFalse(metadata["selector_or_retraining_used"])
        self.assertEqual(metadata["frame_contract"]["frame"], "ICRF")
        self.assertEqual(metadata["frame_contract"]["time_scale"], "TDB")
        for day, state in zip((0.0, 1.0, 2.0, 2.5), predictions):
            for actual, expected in zip(state.position, (self.initial.position[i] + day * self.initial.velocity[i] for i in range(3))):
                self.assertAlmostEqual(actual, expected, delta=1.0e-8)

    def test_compensated_rk4_uses_precise_relative_api(self):
        predictions, metadata = forecast_precise(self.initial, self.ORIGIN, 1.25, self.planets, self.CONSTANTS, {"earth_j2": self.J2, "compensated_rk4": self.RK4}, solver="compensated_rk4")
        self.assertEqual(len(predictions), 3)
        self.assertEqual(metadata["daily_times_days"], [0.0, 1.0, 1.25])
        self.assertEqual(metadata["solver"], "compensated_rk4")
        self.assertTrue(metadata["solver_stats"]["compensated"])
        self.assertGreaterEqual(len(metadata["native_trace"]), 2)
        self.assertEqual(metadata["native_trace"][-1][0], 1.25)

    def test_ng_availability_and_j3_j4_flags_are_forwarded(self):
        params = NonGravitationalParameters(a1_au_d2=1.0e-12)
        ng = NGInput(parameters=params, source="fixture", available_from_jd_tdb=self.ORIGIN + 1.0, availability_basis="fixture")
        zonals = {"reference_radius_au": 1.0e-4, "mu_au3_d2": 1.0e-15, "c30_j2000": 1.0e-8, "c40_j2000": 2.0e-8, "c30_rate_per_julian_year": 0.0, "c40_rate_per_julian_year": 0.0}
        _, metadata = forecast_precise(self.initial, self.ORIGIN, 1.0, self.planets, self.CONSTANTS, {"earth_j2": self.J2, "earth_zonals": zonals, "dopri54": self.DP}, ng=ng, enabled=(3, 4), solver="dopri54")
        self.assertEqual(metadata["force_metadata"]["ng_status"], "not_yet_available")
        self.assertFalse(metadata["force_metadata"]["ng_applied"])
        self.assertEqual(metadata["force_metadata"]["earth_zonals"]["enabled_degrees"], [3, 4])
        self.assertTrue(metadata["force_metadata"]["tdb_iers_scaling_approx"])

    def test_rejects_reference_like_inputs_and_coverage_gaps(self):
        with self.assertRaises(ValueError):
            forecast_precise(self.initial, self.ORIGIN, 0.0, self.planets, self.CONSTANTS, {"earth_j2": self.J2, "dopri54": self.DP})
        with self.assertRaises(ValueError):
            forecast_precise(self.initial, self.ORIGIN, 4.0, self.planets, self.CONSTANTS, {"earth_j2": self.J2, "dopri54": self.DP})
        with self.assertRaises(ValueError):
            forecast_precise(self.initial, self.ORIGIN, 1.0, self.planets, self.CONSTANTS, {"earth_j2": self.J2})
        with self.assertRaises(TypeError):
            forecast_precise(self.initial, self.ORIGIN, 1.0, (object(),), self.CONSTANTS, {"earth_j2": self.J2, "dopri54": self.DP})
        with self.assertRaises(TypeError):
            forecast_precise(self.initial, self.ORIGIN, 1.0, (Perturber("399", "Earth", 1.0e-15, self.earth.ephemeris),), self.CONSTANTS, {"earth_j2": self.J2, "dopri54": self.DP}, reference=None)  # type: ignore[call-arg]

    def test_dopri_requires_explicit_tolerance_settings(self):
        with self.assertRaises(ValueError):
            forecast_precise(self.initial, self.ORIGIN, 1.0, self.planets, self.CONSTANTS, {"earth_j2": self.J2, "dopri54": {"max_step": 0.2}}, solver="dopri54")
        for invalid in (3.9, True):
            config = {"earth_j2": self.J2, "dopri54": {**self.DP, "max_steps": invalid}}
            with self.assertRaises(ValueError):
                forecast_precise(self.initial, self.ORIGIN, 1.0, self.planets, self.CONSTANTS, config, solver="dopri54")


if __name__ == "__main__":
    unittest.main()

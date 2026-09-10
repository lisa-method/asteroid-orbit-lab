import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from development_features import build_features
from orbit_baselines import State
from planetary_dynamics import EphemerisInterpolator, Perturber


class DevelopmentFeatureTests(unittest.TestCase):
    def _body(self, body_id, mu, *, radius=2.0, future_shift=0.0):
        epochs = (0.0, 1.0, 2.0)
        states = tuple(
            State((radius + future_shift * max(0.0, epoch - 1.0), 0.1 * epoch, 0.0), (0.0, 0.1, 0.0))
            for epoch in epochs
        )
        return Perturber(
            body_id,
            "synthetic",
            mu,
            EphemerisInterpolator(epochs, states),
        )

    def _context(self, planets=(), small=()):
        return {
            "mu": 0.0003,
            "au_km": 1.0,
            "day_s": 1.0,
            "c_au_d": 1.0e12,
            "planets": tuple(planets),
            "small": tuple(small),
        }

    def _config(self):
        return {"default_step_days": 0.25, "step_scale": 1.0}

    def test_zero_perturbers_have_zero_planet_and_small_proxies(self):
        result = build_features(
            State((1.0, 0.0, 0.0), (0.0, 0.017, 0.0)),
            0.0,
            1.5,
            self._context(planets=(self._body("399", 0.0),)),
            self._config(),
        )
        self.assertEqual(result["planet_proxy_km"], 0.0)
        self.assertEqual(result["small_body_proxy_km"], 0.0)
        self.assertEqual(result["planet_max_eta"], 0.0)
        self.assertEqual(result["max_eta_by_body"]["399"], 0.0)
        self.assertGreaterEqual(result["gr_proxy_km"], 0.0)
        self.assertGreaterEqual(result["runtime_seconds"], result["propagation_seconds"])
        self.assertEqual(result["steps"], result["rk4_steps"])

    def test_small_body_proxy_scales_with_a_single_small_body_mass(self):
        initial = State((1.0, 0.0, 0.0), (0.0, 0.017, 0.0))
        one = build_features(initial, 0.0, 1.5, self._context(small=(self._body("A", 1.0e-7),)), self._config())
        two = build_features(initial, 0.0, 1.5, self._context(small=(self._body("A", 2.0e-7),)), self._config())
        self.assertAlmostEqual(two["small_body_proxy_km"], 2.0 * one["small_body_proxy_km"], places=14)
        self.assertAlmostEqual(two["min_solar_distance_au"], one["min_solar_distance_au"], places=14)

    def test_future_ephemeris_change_after_horizon_does_not_change_prefix(self):
        initial = State((1.0, 0.0, 0.0), (0.0, 0.017, 0.0))
        prefix_body = self._body("399", 1.0e-6, future_shift=0.0)
        changed_body = self._body("399", 1.0e-6, future_shift=10.0)
        first = build_features(initial, 0.0, 0.5, self._context(planets=(prefix_body,)), self._config())
        second = build_features(initial, 0.0, 0.5, self._context(planets=(changed_body,)), self._config())
        for key in ("horizon_days", "min_solar_distance_au", "planet_proxy_km", "gr_proxy_km", "small_body_proxy_km", "planet_max_eta"):
            self.assertAlmostEqual(first[key], second[key], places=14, msg=key)
        self.assertAlmostEqual(first["per_body"]["399"]["distance_au"], second["per_body"]["399"]["distance_au"], places=14)

    def test_moon_keeps_null_heliocentric_hill_proxy(self):
        result = build_features(
            State((1.0, 0.0, 0.0), (0.0, 0.017, 0.0)),
            0.0,
            1.0,
            self._context(planets=(self._body("301", 1.0e-8, radius=1.01),)),
            self._config(),
        )
        self.assertIsNone(result["per_body"]["301"]["rho_sun_proxy_at_closest"])
        self.assertEqual(result["per_body"]["301"]["body_id"], "301")
        self.assertTrue(math.isfinite(result["per_body"]["301"]["planet_proxy_km"]))


if __name__ == "__main__":
    unittest.main()

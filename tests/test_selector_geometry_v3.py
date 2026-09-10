import math
import unittest
from unittest.mock import patch

from ng_inputs_v2 import NGInput
from orbit_baselines import State
from planetary_dynamics import EphemerisInterpolator, NonGravitationalParameters, Perturber
from selector_v3 import features_v3


class GeometryHierarchyTests(unittest.TestCase):
    def test_moon_solar_hill_would_spuriously_trigger_guard(self):
        start = 2460000.
        def body(identifier, x, mu):
            s = State((x, 0., 0.), (0., 0., 0.))
            return Perturber(identifier, identifier, mu, EphemerisInterpolator((start, start + 1), (s, s)))
        earth, moon = body("399", 1., 8.8877e-10), body("301", 1.00257, 1.093e-11)
        ctx = {"mu": 0.0002959122082855911, "au_km": 149597870.7, "day_s": 86400., "planets": (earth, moon)}
        base = {"per_body": {"301": {"distance_au": .00015, "relative_speed_km_s": .001 * ctx["au_km"] / 86400., "time_days": .5}},
                "planet_proxy_km": 1., "gr_proxy_km": 1., "small_body_proxy_km": 1.}
        initial = State((1., 0., 0.), (0., math.sqrt(ctx["mu"]), 0.))
        with patch("selector_v3.build_features", return_value=base):
            result = features_v3(initial, start, 1., ctx, {})
        self.assertGreater(result["max_scattering_strength"], .01)
        self.assertGreater(result["min_planet_distance_over_hill"], .25)
        self.assertFalse(result["strong_encounter"])
        solar_hill = 1.00257 * (moon.mu_au3_d2 / (3 * ctx["mu"])) ** (1 / 3)
        self.assertLess(.00015 / solar_hill, .25)

    def test_future_fitted_ng_cannot_enter_features(self):
        from selector_v3 import _ng_metadata
        ng = NGInput(parameters=NonGravitationalParameters(a2_au_d2=1e-12),
                     source="synthetic", available_from_jd_tdb=2460001., availability_basis="test")
        amplitude, metadata, _ = _ng_metadata(ng, 2460000.)
        self.assertEqual(amplitude, 0.)
        self.assertFalse(metadata["applied"])
        self.assertIsNone(metadata["parameters"])


if __name__ == "__main__":
    unittest.main()

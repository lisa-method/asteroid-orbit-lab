from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from orbit_baselines import State, norm, subtract
from planetary_dynamics import (
    EphemerisInterpolator,
    NonGravitationalParameters,
    Perturber,
    combine_accelerations,
    non_gravitational_acceleration,
    restricted_n_body_acceleration,
    solar_schwarzschild_acceleration,
)


class PlanetaryDynamicsTests(unittest.TestCase):
    def test_hermite_interpolation_reproduces_linear_motion(self):
        ephemeris = EphemerisInterpolator(
            (0.0, 1.0),
            (
                State((1.0, 2.0, 3.0), (0.1, -0.2, 0.3)),
                State((1.1, 1.8, 3.3), (0.1, -0.2, 0.3)),
            ),
        )
        middle = ephemeris.state_at(0.5)
        self.assertLess(norm(subtract(middle.position, (1.05, 1.9, 3.15))), 1.0e-14)
        self.assertLess(norm(subtract(middle.velocity, (0.1, -0.2, 0.3))), 1.0e-14)

    def test_direct_and_indirect_terms_cancel_at_solar_origin(self):
        ephemeris = EphemerisInterpolator(
            (0.0, 1.0),
            (
                State((1.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
                State((1.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
            ),
        )
        body = Perturber("test", "test", 1.0e-6, ephemeris)
        acceleration = restricted_n_body_acceleration(1.0, (body,), 0.0)
        result = acceleration(0.5, (1.0e-3, 0.0, 0.0), (0.0, 0.0, 0.0))
        sun_only = (-1.0e6, 0.0, 0.0)
        planetary_residual = norm(subtract(result, sun_only))
        self.assertLess(planetary_residual, 3.0e-9)

    def test_solar_schwarzschild_circular_orbit_limit(self):
        mu = 2.959122082855911e-4
        speed_of_light = 173.144632674240
        acceleration = solar_schwarzschild_acceleration(mu, speed_of_light)
        result = acceleration(0.0, (1.0, 0.0, 0.0), (0.0, mu**0.5, 0.0))
        expected_x = 3.0 * mu**2 / speed_of_light**2
        self.assertAlmostEqual(result[0], expected_x, places=20)
        self.assertAlmostEqual(result[1], 0.0, places=20)
        self.assertAlmostEqual(result[2], 0.0, places=20)

    def test_non_gravitational_acceleration_uses_rtn_basis_and_distance_law(self):
        parameters = NonGravitationalParameters(
            a1_au_d2=1.0,
            a2_au_d2=2.0,
            a3_au_d2=3.0,
            exponent_m=2.0,
        )
        acceleration = non_gravitational_acceleration(parameters)
        at_one_au = acceleration(0.0, (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
        at_two_au = acceleration(0.0, (2.0, 0.0, 0.0), (0.0, 1.0, 0.0))
        self.assertEqual(at_one_au, (1.0, 2.0, 3.0))
        self.assertEqual(at_two_au, (0.25, 0.5, 0.75))

    def test_combine_accelerations_sums_terms(self):
        def first(_time, _position, _velocity):
            return (1.0, 2.0, 3.0)

        def second(_time, _position, _velocity):
            return (-0.5, 0.25, 2.0)

        combined = combine_accelerations(first, second)
        self.assertEqual(
            combined(0.0, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
            (0.5, 2.25, 5.0),
        )


if __name__ == "__main__":
    unittest.main()

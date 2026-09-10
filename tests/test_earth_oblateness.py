import math
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from earth_oblateness import (  # noqa: E402
    earth_j2_acceleration,
    earth_pole_iau,
    j2_acceleration,
)
from orbit_baselines import State, norm, subtract  # noqa: E402
from planetary_dynamics import EphemerisInterpolator, Perturber  # noqa: E402


class EarthOblatenessTests(unittest.TestCase):
    MU = 1.0
    RADIUS = 0.1
    J2 = 0.01

    def test_equatorial_and_polar_analytic_signs_and_magnitudes(self):
        equatorial = j2_acceleration((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), self.MU, self.RADIUS, self.J2)
        polar = j2_acceleration((0.0, 0.0, 1.0), (0.0, 0.0, 1.0), self.MU, self.RADIUS, self.J2)
        scale = self.MU * self.J2 * self.RADIUS**2
        self.assertAlmostEqual(equatorial[0], -1.5 * scale, places=15)
        self.assertAlmostEqual(polar[2], 3.0 * scale, places=15)
        self.assertAlmostEqual(abs(polar[2] / equatorial[0]), 2.0, places=15)

    def test_common_rotation_covariance(self):
        angle = 0.37
        cos_angle, sin_angle = math.cos(angle), math.sin(angle)

        def rotate(vector):
            # A proper rotation about a non-coordinate axis (Rodrigues form).
            axis = (1.0, -2.0, 3.0)
            axis_norm = norm(axis)
            ux, uy, uz = (component / axis_norm for component in axis)
            c, s = cos_angle, sin_angle
            x, y, z = vector
            return (
                (c + ux * ux * (1 - c)) * x + (ux * uy * (1 - c) - uz * s) * y + (ux * uz * (1 - c) + uy * s) * z,
                (uy * ux * (1 - c) + uz * s) * x + (c + uy * uy * (1 - c)) * y + (uy * uz * (1 - c) - ux * s) * z,
                (uz * ux * (1 - c) - uy * s) * x + (uz * uy * (1 - c) + ux * s) * y + (c + uz * uz * (1 - c)) * z,
            )

        displacement = (0.8, -0.4, 1.2)
        pole = (0.2, 0.7, -0.1)
        original = j2_acceleration(displacement, pole, self.MU, self.RADIUS, self.J2)
        rotated = j2_acceleration(rotate(displacement), rotate(pole), self.MU, self.RADIUS, self.J2)
        self.assertLess(norm(subtract(rotated, rotate(original))), 1.0e-15)

    def test_matches_independent_numerical_gradient_of_j2_potential(self):
        displacement = (0.8, -0.4, 1.2)
        pole = (0.2, 0.7, -0.1)
        step = 1.0e-6

        def potential(vector):
            radius = norm(vector)
            unit_pole = tuple(component / norm(pole) for component in pole)
            q = sum(a * b for a, b in zip(vector, unit_pole)) / radius
            return self.MU * self.J2 * self.RADIUS**2 * (3.0 * q * q - 1.0) / (2.0 * radius**3)

        numerical = []
        for index in range(3):
            plus = list(displacement)
            minus = list(displacement)
            plus[index] += step
            minus[index] -= step
            numerical.append(-(potential(tuple(plus)) - potential(tuple(minus))) / (2.0 * step))
        actual = j2_acceleration(displacement, pole, self.MU, self.RADIUS, self.J2)
        for expected, value in zip(numerical, actual):
            self.assertAlmostEqual(expected, value, delta=2.0e-12)

    def test_iau_pole_is_unit_and_changes_with_epoch(self):
        pole_j2000 = earth_pole_iau(2451545.0)
        pole_later = earth_pole_iau(2451545.0 + 36525.0)
        self.assertAlmostEqual(norm(pole_j2000), 1.0, places=15)
        self.assertAlmostEqual(norm(pole_later), 1.0, places=15)
        self.assertAlmostEqual(pole_j2000[0], 0.0, places=15)
        self.assertAlmostEqual(pole_j2000[1], 0.0, places=15)
        self.assertAlmostEqual(pole_j2000[2], 1.0, places=15)
        self.assertGreater(norm(subtract(pole_j2000, pole_later)), 1.0e-3)

    def test_zero_j2_is_zero(self):
        self.assertEqual(
            j2_acceleration((0.8, -0.4, 1.2), (0.0, 0.0, 1.0), self.MU, self.RADIUS, 0.0),
            (0.0, 0.0, 0.0),
        )

    def test_heliocentric_indirect_term_cancels_at_solar_origin(self):
        ephemeris = EphemerisInterpolator(
            (0.0, 1.0),
            (State((1.0, 0.2, -0.1), (0.0, 0.0, 0.0)), State((1.0, 0.2, -0.1), (0.0, 0.0, 0.0))),
        )
        earth = Perturber("399", "Earth", self.MU, ephemeris)
        acceleration = earth_j2_acceleration(earth, 0.0, self.RADIUS, self.J2)
        result = acceleration(0.5, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        self.assertLess(norm(result), 1.0e-15)

    def test_scaled_pole_is_normalized(self):
        displacement = (0.8, -0.4, 1.2)
        unit_result = j2_acceleration(displacement, (0.0, 0.0, 1.0), self.MU, self.RADIUS, self.J2)
        scaled_result = j2_acceleration(displacement, (0.0, 0.0, 5.0), self.MU, self.RADIUS, self.J2)
        self.assertLess(norm(subtract(unit_result, scaled_result)), 1.0e-15)


if __name__ == "__main__":
    unittest.main()

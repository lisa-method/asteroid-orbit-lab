import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from earth_oblateness import j2_acceleration  # noqa: E402
from extended_body_forces import (  # noqa: E402
    extended_body_quadrupole_acceleration,
    zonal_acceleration,
)
from orbit_baselines import norm, subtract  # noqa: E402


def rotate(vector):
    angle = 0.41
    axis = (1.0, -2.0, 3.0)
    axis_norm = norm(axis)
    ux, uy, uz = (component / axis_norm for component in axis)
    c, s = math.cos(angle), math.sin(angle)
    x, y, z = vector
    return (
        (c + ux * ux * (1 - c)) * x + (ux * uy * (1 - c) - uz * s) * y + (ux * uz * (1 - c) + uy * s) * z,
        (uy * ux * (1 - c) + uz * s) * x + (c + uy * uy * (1 - c)) * y + (uy * uz * (1 - c) - ux * s) * z,
        (uz * ux * (1 - c) - uy * s) * x + (uz * uy * (1 - c) + ux * s) * y + (c + uz * uz * (1 - c)) * z,
    )


class ExtendedBodyForceTests(unittest.TestCase):
    MU = 2.3
    RADIUS = 0.17
    POLE = (0.2, 0.7, -0.1)
    DISPLACEMENT = (0.8, -0.4, 1.2)

    def test_degree_two_matches_frozen_j2_for_arbitrary_axis(self):
        generic = zonal_acceleration(self.DISPLACEMENT, self.POLE, self.MU, self.RADIUS, 2, 0.013)
        existing = j2_acceleration(self.DISPLACEMENT, self.POLE, self.MU, self.RADIUS, 0.013)
        self.assertLess(norm(subtract(generic, existing)), 1.0e-15)

    def test_zonal_terms_match_finite_difference_potential(self):
        for degree in (2, 3, 4):
            jn = 0.013 if degree == 2 else (-0.002 if degree == 3 else 0.004)
            step = 2.0e-6
            pole_norm = norm(self.POLE)
            unit_pole = tuple(component / pole_norm for component in self.POLE)

            def potential(vector):
                radius = norm(vector)
                cosine = sum(a * b for a, b in zip(vector, unit_pole)) / radius
                if degree == 2:
                    polynomial = (3.0 * cosine**2 - 1.0) / 2.0
                elif degree == 3:
                    polynomial = (5.0 * cosine**3 - 3.0 * cosine) / 2.0
                else:
                    polynomial = (35.0 * cosine**4 - 30.0 * cosine**2 + 3.0) / 8.0
                return self.MU * jn * self.RADIUS**degree * polynomial / radius ** (degree + 1)

            numerical = []
            for index in range(3):
                plus, minus = list(self.DISPLACEMENT), list(self.DISPLACEMENT)
                plus[index] += step
                minus[index] -= step
                numerical.append(-(potential(tuple(plus)) - potential(tuple(minus))) / (2.0 * step))
            actual = zonal_acceleration(self.DISPLACEMENT, self.POLE, self.MU, self.RADIUS, degree, jn)
            for expected, value in zip(numerical, actual):
                self.assertAlmostEqual(expected, value, delta=2.0e-10)

    def test_pole_and_equator_signs_for_odd_and_even_terms(self):
        j3_pole = zonal_acceleration((0.0, 0.0, 1.0), (0.0, 0.0, 1.0), 1.0, 0.1, 3, 1.0)
        j3_equator = zonal_acceleration((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), 1.0, 0.1, 3, 1.0)
        j4_equator = zonal_acceleration((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), 1.0, 0.1, 4, 1.0)
        self.assertGreater(j3_pole[2], 0.0)
        self.assertGreater(j3_equator[2], 0.0)
        self.assertGreater(j4_equator[0], 0.0)

    def test_zonal_rotation_covariance(self):
        original = zonal_acceleration(self.DISPLACEMENT, self.POLE, self.MU, self.RADIUS, 4, 0.004)
        rotated = zonal_acceleration(rotate(self.DISPLACEMENT), rotate(self.POLE), self.MU, self.RADIUS, 4, 0.004)
        self.assertLess(norm(subtract(rotated, rotate(original))), 2.0e-15)

    def test_sphere_and_point_moment_are_zero(self):
        vector = (0.8, -0.4, 1.2)
        sphere = ((0.03, 0.0, 0.0), (0.0, 0.03, 0.0), (0.0, 0.0, 0.03))
        self.assertEqual(extended_body_quadrupole_acceleration(vector, 1.0, sphere), (0.0, 0.0, 0.0))
        self.assertEqual(extended_body_quadrupole_acceleration(vector, 1.0, ((0.0, 0.0, 0.0),) * 3), (0.0, 0.0, 0.0))

    def test_dumbbell_matches_exact_average_with_fourth_order_remainder(self):
        radius_vector = (1.0, 0.2, -0.1)
        axis = (1.0, 0.0, 0.0)

        def point_acceleration(vector):
            radius = norm(vector)
            return tuple(-component / radius**3 for component in vector)

        def error_for(offset):
            left = tuple(radius_vector[i] - offset * axis[i] for i in range(3))
            right = tuple(radius_vector[i] + offset * axis[i] for i in range(3))
            central = point_acceleration(radius_vector)
            exact = tuple((point_acceleration(left)[i] + point_acceleration(right)[i]) / 2.0 - central[i] for i in range(3))
            moment = ((offset**2, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
            approximation = extended_body_quadrupole_acceleration(radius_vector, 1.0, moment)
            return norm(subtract(exact, approximation))

        small, large = error_for(0.001), error_for(0.002)
        self.assertGreater(small, 0.0)
        self.assertGreater(large / small, 10.0)
        self.assertLess(large / small, 22.0)

    def test_second_moment_rotation_covariance_and_no_mutation(self):
        vector = (0.8, -0.4, 1.2)
        matrix = ((0.04, 0.01, 0.0), (0.01, 0.02, 0.0), (0.0, 0.0, 0.01))
        original_matrix = tuple(tuple(row) for row in matrix)
        # Build R S R^T using the same proper rotation used for vectors.
        rotation = tuple(tuple(rotate(tuple(1.0 if k == j else 0.0 for k in range(3)))[i] for j in range(3)) for i in range(3))
        rotated_matrix = tuple(tuple(sum(rotation[i][k] * matrix[k][l] * rotation[j][l] for k in range(3) for l in range(3)) for j in range(3)) for i in range(3))
        expected = rotate(extended_body_quadrupole_acceleration(vector, 1.0, matrix))
        actual = extended_body_quadrupole_acceleration(rotate(vector), 1.0, rotated_matrix)
        self.assertLess(norm(subtract(actual, expected)), 2.0e-14)
        self.assertEqual(matrix, original_matrix)

    def test_invalid_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            zonal_acceleration((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 1.0, 0.1, 2, 1.0)
        with self.assertRaises(ValueError):
            zonal_acceleration((1.0, 0.0, 0.0), (0.0, 0.0, 0.0), 1.0, 0.1, 2, 1.0)
        with self.assertRaises(ValueError):
            zonal_acceleration((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), 1.0, 0.1, 5, 1.0)
        with self.assertRaises(ValueError):
            zonal_acceleration((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), 1.0, 0.1, 2, math.nan)
        with self.assertRaises(ValueError):
            extended_body_quadrupole_acceleration((1.0, 0.0, 0.0), 1.0, ((1.0, 2.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))
        with self.assertRaises(ValueError):
            extended_body_quadrupole_acceleration((1.0, 0.0, 0.0), 1.0, ((1.0, 0.0, 0.0), (0.0, -0.1, 0.0), (0.0, 0.0, 1.0)))
        with self.assertRaises(ValueError):
            extended_body_quadrupole_acceleration((1.0, 0.0, 0.0), 1.0, ((True, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))


if __name__ == "__main__":
    unittest.main()

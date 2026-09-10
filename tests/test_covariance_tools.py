from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from covariance_tools import (  # noqa: E402
    jacobi_eigh_3x3,
    principal_position_sigmas,
    scaled_cholesky,
    sigma_point_moments,
    sigma_points,
    transform_covariance,
    validate_covariance,
)


def matmul(left, right):
    return tuple(tuple(math.fsum(left[i][k] * right[k][j] for k in range(len(right)))
                       for j in range(len(right[0]))) for i in range(len(left)))


class CovarianceToolTests(unittest.TestCase):
    def test_rotated_correlated_spd_cholesky_and_eigen_residual(self):
        angle = 0.37
        c, s = math.cos(angle), math.sin(angle)
        rotation = ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))
        diagonal = ((9.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 0.25))
        covariance = matmul(matmul(rotation, diagonal), tuple(zip(*rotation)))
        factor = scaled_cholesky(covariance)
        self.assertLess(factor.residual, 1e-13)
        eigen = jacobi_eigh_3x3(covariance)
        self.assertLess(eigen.residual, 1e-13)
        self.assertEqual(tuple(round(value, 10) for value in eigen.eigenvalues), (9.0, 1.0, 0.25))
        self.assertEqual(len(principal_position_sigmas(covariance, units="km").offsets), 3)

    def test_relative_symmetry_and_eigensolver_work_below_absolute_scale(self):
        with self.assertRaises(ValueError):
            validate_covariance(((1e-30, 1e-40), (0.0, 1e-30)))
        angle = 0.41
        c, s = math.cos(angle), math.sin(angle)
        rotation = ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))
        diagonal = ((9e-25, 0.0, 0.0), (0.0, 1e-25, 0.0), (0.0, 0.0, 0.25e-25))
        covariance = matmul(matmul(rotation, diagonal), tuple(zip(*rotation)))
        eigen = jacobi_eigh_3x3(covariance)
        self.assertLess(eigen.residual, 1e-13)
        for actual, expected in zip(eigen.eigenvalues, (9e-25, 1e-25, 0.25e-25)):
            self.assertAlmostEqual(actual / expected, 1.0, places=12)

    def test_linear_sigma_points_recover_transformed_covariance(self):
        mean = (1.0, -2.0, 0.5)
        covariance = ((4.0, 1.0, 0.2), (1.0, 2.0, -0.3), (0.2, -0.3, 1.0))
        jacobian = ((2.0, -1.0, 0.5), (0.0, 3.0, -2.0))
        points, weights = sigma_points(mean, covariance)
        propagated = [tuple(math.fsum(jacobian[i][j] * point[j] for j in range(3))
                            for i in range(2)) for point in points]
        sigma_mean, sigma_covariance = sigma_point_moments(propagated, weights)
        expected_mean = tuple(math.fsum(jacobian[i][j] * mean[j] for j in range(3)) for i in range(2))
        expected_covariance = transform_covariance(jacobian, covariance)
        for actual, expected in zip(sigma_mean, expected_mean):
            self.assertAlmostEqual(actual, expected, places=12)
        for actual_row, expected_row in zip(sigma_covariance, expected_covariance):
            for actual, expected in zip(actual_row, expected_row):
                self.assertAlmostEqual(actual, expected, places=11)

    def test_scale_disparity_and_eight_dimensional_sigma_points(self):
        covariance = tuple(tuple(1e-20 if i == j == 0 else 1e10 if i == j == 1 else (0.0 if i != j else 1.0)
                                 for j in range(8)) for i in range(8))
        result = scaled_cholesky(covariance)
        self.assertLess(result.residual, 1e-13)
        points, weights = sigma_points((0.0,) * 8, covariance)
        self.assertEqual((len(points), len(weights)), (16, 16))
        _, recovered = sigma_point_moments(points, weights)
        self.assertAlmostEqual(recovered[0][0], 1e-20, places=30)
        self.assertAlmostEqual(recovered[1][1], 1e10, places=3)

    def test_invalid_symmetry_notfinite_and_non_spd_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_covariance(((1.0, 2.0), (2.1, 1.0)))
        with self.assertRaises(ValueError):
            validate_covariance(((1.0, math.nan), (math.nan, 1.0)))
        with self.assertRaises(ValueError):
            scaled_cholesky(((1.0, 1.0), (1.0, 1.0)))
        with self.assertRaises(ValueError):
            sigma_points((0.0, 0.0), ((1.0, 2.0), (2.0, 1.0)))

    def test_principal_position_sigmas_reject_negative_spectrum_and_preserve_units(self):
        with self.assertRaises(ValueError):
            principal_position_sigmas(((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0)))
        with self.assertRaises(ValueError):
            principal_position_sigmas(((1.0, 0.0, 0.0), (0.0, -1e-16, 0.0), (0.0, 0.0, 1.0)))
        result = principal_position_sigmas(((4.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 0.0)), units="m")
        self.assertEqual(result.units, "m")
        self.assertEqual(tuple(round(value, 12) for value in result.eigenvalues), (4.0, 1.0, 0.0))
        self.assertTrue(any(abs(offset[0] - 2.0) < 1e-12 for offset in result.offsets))


if __name__ == "__main__":
    unittest.main()

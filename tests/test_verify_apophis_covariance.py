import math
import unittest
from verify_apophis_covariance import _check_step_bound, _assert_matrix_close


class CovarianceVerificationTests(unittest.TestCase):
    def test_endpoint_representation_and_real_step_violation(self):
        _check_step_bound(255.1, math.nextafter(255.35, math.inf), .25)
        with self.assertRaises(ValueError):
            _check_step_bound(255.1, 255.351, .25)

    def test_tiny_ng_covariance_cannot_hide_in_absolute_epsilon(self):
        with self.assertRaises(ValueError):
            _assert_matrix_close([[1e-25]], [[2e-25]], 'NG covariance')


if __name__ == '__main__':
    unittest.main()

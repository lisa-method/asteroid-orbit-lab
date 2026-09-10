import math
import unittest

from covariance_tools import scaled_cholesky, sigma_point_moments
from orbit_baselines import State
from run_apophis_covariance import ShiftedEphemeris, specs


class CovarianceDesignTests(unittest.TestCase):
    def test_joint_probe_covariance_including_ng_correlations(self):
        factor = [[(i+1)*1e-5 if i == j else 2e-6 if i > j else 0. for j in range(8)] for i in range(8)]
        covariance = [[sum(factor[i][k]*factor[j][k] for k in range(8)) for j in range(8)] for i in range(8)]
        config = {'nominal_settings': ['extreme', 'ultra'], 'sigma_scales': [.25, 1.], 'probe_setting': 'ultra'}
        runs = specs(config, scaled_cholesky(covariance).factor)
        self.assertEqual(len(runs), 34)
        for scale in [.25, 1.]:
            points = [r['delta_elements'] for r in runs if r.get('scale') == scale]
            mean, actual = sigma_point_moments(points, [1/16]*16)
            self.assertEqual(mean, (0.,)*8)
            for i in range(8):
                for j in range(8):
                    self.assertTrue(math.isclose(actual[i][j]/scale**2, covariance[i][j], rel_tol=1e-14, abs_tol=1e-25))

    def test_time_origin_adapter(self):
        class Old:
            def state_at(self, t):
                return State((t, 0., 0.), (1., 0., 0.))
        shifted = ShiftedEphemeris(Old(), 2922.)
        self.assertEqual(shifted.state_at(0.).position[0], -2922.)
        self.assertEqual(shifted.state_at(2922.).position[0], 0.)
        self.assertEqual(shifted.state_at(3287.).position[0], 365.)


if __name__ == '__main__':
    unittest.main()

import unittest
from export_apophis_covariance import covariance_export, STATE_LABELS


class CovarianceExportTests(unittest.TestCase):
    def test_numerical_failure_is_not_hidden_by_linear_cubature_success(self):
        origin = {'nominal_state': [0.]*6, 'nominal_ng': {'a1_au_d2': 1e-13, 'a2_au_d2': -1e-14},
                  'linear_augmented': {'native': [[0.]*8 for _ in range(8)]},
                  'full_scale_augmented': {'native': [[0.]*8 for _ in range(8)]},
                  'parameter_labels': ['e','q','tp','node','peri','i','A1','A2']}
        matrix = {'fingerprint': 'test', 'analysis': {'forecast_origin_joint_covariance': origin,
            'forecast_epoch_jd_tdb': 2462137.5, 'covariance_epoch_jd_tdb': 2459215.5,
            'nominal_dp_comparison': {'within_criterion': False}, 'diagnostic_days': [
                {'covariance_relative_passes': True, 'mean_shift': {'passes': True},
                 'principal_position': {'linear': {'unresolved_negative_eigenvalues': False}}}]}}
        result = covariance_export(matrix)
        self.assertEqual(result['status'], 'numerically_unresolved')
        self.assertEqual(result['coordinate_labels'], STATE_LABELS)
        self.assertNotEqual(result['coordinate_labels'], result['input_orbital_parameter_labels'])
        self.assertFalse(result['calibrated_forecast_uncertainty'])
        self.assertEqual(result['nominal_state'][-2:], [1e-13,-1e-14])


if __name__ == '__main__':
    unittest.main()

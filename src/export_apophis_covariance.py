"""Export verified Cartesian covariance with explicit numerical status.

The frozen analysis retains input parameter labels alongside Cartesian state
matrices. This separate export gives each coordinate system an explicit name;
it never alters the frozen experiment or treats a failed numerical gate as an
accuracy certificate.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from prepare_fresh_holdout import immutable_json, sha


STATE_LABELS = ['x', 'y', 'z', 'vx', 'vy', 'vz', 'A1', 'A2']
STATE_UNITS = ['AU', 'AU', 'AU', 'AU/day', 'AU/day', 'AU/day', 'AU/day^2', 'AU/day^2']


def covariance_export(matrix):
    analysis = matrix['analysis']
    origin = analysis['forecast_origin_joint_covariance']
    numerical = analysis['nominal_dp_comparison']
    diagnostics = analysis['diagnostic_days']
    covariance_pass = all(d['covariance_relative_passes'] for d in diagnostics)
    mean_pass = all(d['mean_shift']['passes'] is True for d in diagnostics)
    positive = all(not p['unresolved_negative_eigenvalues'] for d in diagnostics for p in d['principal_position'].values())
    numerical_pass = numerical['within_criterion'] is True
    status = ('numerically_unresolved' if not numerical_pass else
              'nonlinear_or_unresolved' if not (covariance_pass and mean_pass and positive) else
              'formal_linear_approximation_only')
    return {'schema_version': 1, 'fingerprint': matrix['fingerprint'],
        'epoch_jd_tdb': analysis['forecast_epoch_jd_tdb'],
        'frame': 'heliocentric ICRF, geometric, TDB', 'coordinate_labels': STATE_LABELS,
        'coordinate_units': STATE_UNITS, 'matrix_units': 'coordinate_units[i] * coordinate_units[j]',
        'nominal_state': origin['nominal_state'] + [origin['nominal_ng']['a1_au_d2'], origin['nominal_ng']['a2_au_d2']],
        'linear_covariance': origin['linear_augmented']['native'],
        'full_scale_cubature_covariance': origin['full_scale_augmented']['native'],
        'input_orbital_parameter_labels': origin['parameter_labels'],
        'input_covariance_epoch_jd_tdb': analysis['covariance_epoch_jd_tdb'],
        'status': status, 'diagnostic_only': True, 'calibrated_forecast_uncertainty': False,
        'validated_no_candidate_rule': False, 'nominal_dp_comparison': numerical,
        'covariance_relative_gate_passed': covariance_pass, 'mean_shift_gate_passed': mean_pass,
        'all_position_eigenvalues_nonnegative': positive,
        'interpretation': 'This covariance belongs only to the included propagated nominal state. '
            'Do not attach it to the old 2029 Horizons start or interpret it as calibrated confidence/impact probability.'}


def run(root):
    output = root/'outputs/apophis_covariance'
    matrix_path = output/'matrix.json'; verification_path = output/'verification.json'
    verification = json.loads(verification_path.read_text())
    if verification['matrix_sha256'] != sha(matrix_path):
        raise ValueError('Completed verification must identify the current matrix')
    result = covariance_export(json.loads(matrix_path.read_text()))
    result.update(matrix_sha256=sha(matrix_path), verification_sha256=sha(verification_path),
                  exporter_sha256=sha(root/'src/export_apophis_covariance.py'))
    immutable_json(output/'forecast_origin_covariance.json', result)
    print(json.dumps({'status': result['status'], 'path': str(output/'forecast_origin_covariance.json')}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--root', type=Path, default=Path('.'))
    run(p.parse_args().root.resolve())

"""Post-hoc matched-reference diagnostic for the new nine-year nominal.

The frozen matrix also retains a legacy mixed-source reference diagnostic.
This separate artifact compares only identical daily epochs of the already
frozen annual_daily reference; it neither propagates nor replaces old scores.
"""
import argparse
import json
import math
from pathlib import Path

from prepare_fresh_holdout import immutable_json, sha
from run_eda import parse_horizons
from run_physics_baselines import state_from_row


def run(root):
    output = root/'outputs/apophis_covariance'
    matrix_path = output/'matrix.json'
    verification = json.loads((output/'verification.json').read_text())
    if verification['matrix_sha256'] != sha(matrix_path):
        raise ValueError('Verified covariance matrix required')
    matrix = json.loads(matrix_path.read_text())
    nominal = next(r for r in matrix['records'] if r['id'] == 'nominal_ultra')
    states = {s['day']: s['state'] for s in nominal['samples']}
    raw = root/'data/raw/apophis_reference_time/asteroid_99942_annual_daily.json'
    header, rows = parse_horizons(raw, '99942', 'Apophis')
    if len(rows) != 366 or 'JPL#220' not in header['target'] or header['reference_frame'] != 'ICRF':
        raise ValueError('Unexpected matched annual reference')
    values = []
    for row in rows:
        t = row['epoch_jd_tdb'] - 2459215.5
        s, reference = states[t], state_from_row(row)
        values.append({'forecast_day': t-2922., 'position_km': math.dist(s[:3], reference.position)*149597870.7,
                       'velocity_m_s': math.dist(s[3:], reference.velocity)*149597870700/86400})
    old_path = root/'outputs/apophis_spk_audit/checkpoints/all_direct__ultra.json'
    old = json.loads(old_path.read_text())
    difference = [{'forecast_day': t, 'position_km': math.dist(states[t+2922.][:3], s[:3])*149597870.7}
                  for t, s in zip(old['requested_times_relative_days'], old['requested_states'], strict=True)]
    result = {'matrix_sha256': sha(matrix_path), 'checker_sha256': sha(root/'src/check_apophis_covariance_reference.py'),
        'reference': str(raw.relative_to(root)), 'reference_sha256': sha(raw), 'header': header,
        'old_baseline_sha256': sha(old_path), 'matched_daily': values,
        'difference_from_old_2029_start_nominal': difference,
        'scope': 'Post-hoc comparison of a NEW 2021-start nominal, not the old 2029-start score or uncertainty calibration. '
                 'The frozen matrix legacy mixed-reference diagnostic uses a different reference and must not be combined.'}
    immutable_json(output/'matched_reference_diagnostic.json', result)
    print(json.dumps({'matched_count': len(values), 'first': values[0], 'last': values[-1]}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--root', type=Path, default=Path('.'))
    run(p.parse_args().root.resolve())

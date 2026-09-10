"""Offline verification of compensated DP and frozen RK4 cross-comparisons."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_eda import load_json
from run_physics_baselines import state_from_row
from verify_precise_propagation import (
    _sha, _state, _shift, _write_immutable, _check_freeze, _load_teachers,
)
import verify_apophis_reference_time as rv
import verify_apophis_shape_weak_force as sv


def vector_effect_difference(a, b, c, d, au, day):
    """Difference of two force-effect vectors, never of their norms."""
    left = [_state([x-y for x,y in zip(sa,sb)])
        for sa,sb in zip(a['requested_states'],b['requested_states'])]
    right = [_state([x-y for x,y in zip(sc,sd)])
        for sc,sd in zip(c['requested_states'],d['requested_states'])]
    return _shift(left, right, au, day)


def verify(root):
    output = root / 'outputs/precise_dp'
    matrix = load_json(output / 'matrix.json')
    freeze = _check_freeze(root, matrix, output / 'freeze.json')
    manifest = sv._manifest_inventory(root)
    config = load_json(root / 'configs/precise_dp.json')
    previous = load_json(root / 'outputs/apophis_reference_time/matrix.json')
    rk4 = load_json(root / 'outputs/precise_propagation/matrix.json')
    annual, references, data = _load_teachers(root, previous)
    au, day = data['constants']['au_km'], data['constants']['day_s']
    initial = {'old': state_from_row(annual[0]),
        'annual_hourly': state_from_row(references['annual_hourly'][0])}
    expected = {f"precise_dp__{arm}__{source}__{tol['label']}": (arm, source, tol)
        for source in config['sources'] for arm in config['arms']
        for tol in config['tolerances']}
    records = matrix['records']
    if len(records) != 12 or {r['key'] for r in records} != set(expected):
        raise ValueError('DP matrix specification mismatch')
    if {p.stem for p in (output/'checkpoints').glob('*.json')} != set(expected):
        raise ValueError('Checkpoint set differs')
    comparisons, checkpoint_hashes = [], {}
    scalar_count = 0
    for row in records:
        arm, source, setting = expected[row['key']]
        if (row['arm'],row['initial_source'],row['setting']) != (arm,source,setting):
            raise ValueError('DP record setting mismatch')
        if row['solver'] != 'dopri54' or row['numerical_mode'] != 'precise_dp':
            raise ValueError('Wrong algorithm')
        if row['fingerprint'] != freeze['fingerprint']:
            raise ValueError('Record fingerprint mismatch')
        if row['solver_stats'].get('compensated') is not True or row['solver_stats'].get('endpoint_consistent') is not True:
            raise ValueError('Numerical mode not recorded')
        rv._verify_record(row, annual, references, initial, au, day)
        scalar_count += 2 * (len(annual) + sum(r['samples'] for r in row['reference_comparisons'].values()))
        path = output / 'checkpoints' / (row['key'] + '.json')
        if load_json(path) != row:
            raise ValueError('Checkpoint differs from matrix')
        checkpoint_hashes[path.name] = _sha(path)
        for scale in ([.125,.0625] if source == 'old' and arm == 'baseline' else [.125]):
            other = next(r for r in rk4['records'] if r['key'] == f'compensated__{arm}__{source}__{scale}')
            shift = _shift([_state(s) for s in row['requested_states']],
                [_state(s) for s in other['requested_states']], au, day)
            comparisons.append({'dp_key': row['key'], 'rk4_key': other['key'],
                **shift, 'within_1m': shift['max_position_shift_km'] <= .001})
    pairs = []
    for source in config['sources']:
        for arm in config['arms']:
            branch = [r for r in records if r['initial_source']==source and r['arm']==arm]
            for a,b in zip(branch,branch[1:]):
                pairs.append({'left': a['key'], 'right': b['key'],
                    **_shift([_state(s) for s in a['requested_states']], [_state(s) for s in b['requested_states']],au,day)})
    effects=[]
    for source in config['sources']:
        a=next(r for r in records if r['key']==f'precise_dp__j3j4__{source}__ultra')
        b=next(r for r in records if r['key']==f'precise_dp__baseline__{source}__ultra')
        c=next(r for r in rk4['records'] if r['key']==f'compensated__j3j4__{source}__0.125')
        d=next(r for r in rk4['records'] if r['key']==f'compensated__baseline__{source}__0.125')
        effects.append({'initial_source':source,
            'j3j4_shift':_shift([_state(s) for s in a['requested_states']],[_state(s) for s in b['requested_states']],au,day),
            'effect_vector_solver_difference':vector_effect_difference(a,b,c,d,au,day)})
    criterion=[p for p in comparisons if p['dp_key'].endswith('__ultra')]
    analysis={'rk4_comparisons':comparisons,'dp_adjacent_tolerances':pairs,'force_effects':effects,
        'criterion_all_passed':all(p['within_1m'] for p in criterion),
        'empirical_only':True,'matched_errors':[
            {'key':r['key'],'error':r['reference_comparisons']['old_daily' if r['initial_source']=='old' else 'annual_daily']}
            for r in records]}
    provenance={'matrix_sha256':_sha(output/'matrix.json'), 'freeze_sha256':_sha(output/'freeze.json'),
        'verifier_sha256':_sha(root/'src/verify_precise_dp.py'),
        'helper_sha256':{name:_sha(root/'src'/name) for name in (
            'verify_precise_propagation.py','verify_apophis_reference_time.py','verify_apophis_shape_weak_force.py')}}
    _write_immutable(output/'analysis.json',{**analysis,**provenance})
    result={**provenance,'run_count':12,'scalar_error_count':scalar_count,
        'accepted_endpoint_count':sum(len(r['accepted_endpoints']) for r in records),
        'checkpoint_sha256':checkpoint_hashes,'manifest':manifest,
        'criterion_all_passed':analysis['criterion_all_passed'],'ultra_comparisons':criterion}
    _write_immutable(output/'verification.json',result)
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('.'))
    verify(parser.parse_args().root.resolve())

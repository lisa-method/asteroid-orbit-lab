"""Frozen Apophis ephemeris-cadence matrix with source and force gates."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import datetime as dt
import hashlib
import json
from pathlib import Path
import time

from barycentric_eih import build_barycentric_eih
from check_apophis_spk_inputs import check_ephemerides, check_forces
from de441_subset import DE441Subset
from planetary_dynamics import Perturber, encounter_aware_step_selector
from precise_dopri import integrate_precise_dopri54
from precise_rk4 import advance
from prepare_fresh_holdout import immutable_json, check_hashes, sha
from relative_time_dynamics import RelativeEphemerisInterpolator
from run_apophis_eih_barycentric import context as previous_context, NATIVE_BASIS, OUTPUT_BASIS
from run_apophis_reference_time import source_closure, evaluate
from run_apophis_solver_audit import _flat, _state
from run_physics_baselines import state_from_row

CONFIG = 'configs/apophis_spk_audit.json'


def context(root):
    ctx, _, previous_config, solar, _, _, _ = previous_context(root)
    config = json.loads((root / CONFIG).read_text())
    prior = json.loads((root / config['previous_freeze']).read_text())
    check_hashes(root, prior['hashes'])
    backend = DE441Subset(root, ctx['au'], ctx['day'])
    input_config = json.loads((root / config['input_config']).read_text())
    paths = [CONFIG, config['contract'], config['previous_freeze'], config['previous_matrix'], config['input_config'],
             input_config['index'], input_config['manifest'], input_config['design'],
             'tests/test_spk_chebyshev.py', 'tests/test_independent_force_terms.py']
    paths += [r['path'] for r in backend.manifest['files']] + source_closure(root, ['run_apophis_spk_audit'])
    hashes = {**prior['hashes'], **{p: sha(root / p) for p in sorted(set(paths))}}
    payload = {'hashes': hashes, 'runtime': prior['runtime']}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    output = root / config['output_directory']
    freeze = output / 'freeze.json'
    if freeze.exists():
        old = json.loads(freeze.read_text())
        if old['fingerprint'] != fingerprint or any(old[k] != payload[k] for k in payload):
            raise ValueError('SPK audit freeze changed')
    else:
        immutable_json(freeze, {**payload, 'fingerprint': fingerprint,
                               'created_utc': dt.datetime.now(dt.timezone.utc).isoformat()})
    immutable_json(output / 'inputs.json', {
        'fingerprint': fingerprint, 'previous_matrix_sha256': sha(root / config['previous_matrix']),
        'input_index_sha256': sha(root / input_config['index']), 'input_manifest_sha256': sha(root / input_config['manifest']),
        'native_state_basis': NATIVE_BASIS, 'reported_state_basis': OUTPUT_BASIS,
        'initial_source': 'annual_hourly', 'target_data_unchanged': True,
        'gr': config['gr'], 'solar_j2': config['solar_j2'], 'earth_pole': 'fixed_j2000',
        'sb16_ephemerides_unchanged': True, 'sun_origin_acceleration_subtraction': False,
        'rk4_step_selection_state_basis': OUTPUT_BASIS,
        'source_knot_epoch_counts': {p.body_id: sum(-1 <= t <= 366 for t in p.ephemeris.epochs_days)
                                    for p in ctx['planets'] + (ctx['pluto'],) if not p.body_id.startswith('sb:')}})
    return ctx, config, previous_config, backend, output, freeze, fingerprint


def ephemerides(ctx, backend, arm):
    if arm not in ('baseline', 'same_knots', 'hourly', 'earth_moon_direct', 'other_major_direct', 'all_direct'):
        raise ValueError('Unknown prespecified ephemeris arm')
    def replacement(target, center, old):
        if arm == 'baseline' or (arm == 'earth_moon_direct' and target not in (399, 301)) or (arm == 'other_major_direct' and target in (399, 301)):
            return old
        direct = backend.ephemeris(target, center)
        if arm in ('all_direct', 'earth_moon_direct', 'other_major_direct'):
            return direct
        times = tuple(t for t in old.epochs_days if -1. <= t <= 366.) if arm == 'same_knots' else tuple(k / 24 for k in range(-24, 8785))
        return RelativeEphemerisInterpolator(times, tuple(direct.state_at(t) for t in times))
    planets = tuple(p if p.body_id.startswith('sb:') else Perturber(p.body_id, p.name, p.mu_au3_d2,
                        replacement(int(p.body_id), 10, p.ephemeris)) for p in ctx['planets'] + (ctx['pluto'],))
    return planets, replacement(10, 0, ctx['sun_barycentric'])


def force_spec(arm):
    return {'gr': 'eih_sun', 'solar_j2': False, 'earth_pole': 'fixed_j2000', 'ephemeris': arm,
            'newton_sources': 'Sun + 10 major + SB16', 'sb16_interpolation': 'unchanged'}


def gates(ctx, config, backend, output, previous, fingerprint):
    paths = [output / 'input_checks.json', output / 'force_checks.json']
    results = []
    for kind, path in zip(('inputs', 'force'), paths, strict=True):
        if path.exists():
            value = json.loads(path.read_text())
            if value['fingerprint'] != fingerprint:
                raise ValueError('Check fingerprint changed')
        else:
            value = check_ephemerides(ctx, backend, config) if kind == 'inputs' else check_forces(
                ctx, next(r for r in previous['records'] if r['key'] == 'pn_sun__ultra'), config)
            value['fingerprint'] = fingerprint
            immutable_json(path, value)
        results.append(value)
        print(json.dumps({'gate': kind, 'passed': value.get('source_gate_passed', value.get('force_gate_passed'))}), flush=True)
    if not results[0]['source_gate_passed'] or not results[1]['force_gate_passed']:
        raise ValueError('Prespecified input/force gate failed; dependent propagation paused')


def run(root, solver='all', prepare_only=False):
    ctx, config, previous_config, backend, output, freeze, fingerprint = context(root)
    if prepare_only:
        print(json.dumps({'frozen': fingerprint}), flush=True)
        return
    previous = json.loads((root / config['previous_matrix']).read_text())
    gates(ctx, config, backend, output, previous, fingerprint)
    initial = state_from_row(ctx['refs']['annual_hourly'][0])
    settings = [s for s in previous_config['solver_settings'] if s['label'] in config['solver_settings']]
    if len(settings) != 3:
        raise ValueError('Three solver settings required')
    for setting in settings:
        if solver != 'all' and solver != setting['solver']:
            continue
        for arm in config['arms']:
            key = arm + '__' + setting['label']
            path = output / 'checkpoints' / (key + '.json')
            if path.exists():
                if json.loads(path.read_text())['fingerprint'] != fingerprint:
                    raise ValueError('Checkpoint fingerprint differs')
                continue
            if arm == 'baseline':
                old = next(r for r in previous['records'] if r['key'] == 'pn_sun__' + setting['label'])
                row = {**old, 'key': key, 'arm': arm, 'fingerprint': fingerprint,
                       'force_specification': force_spec(arm),
                       'reused_from': {'matrix': config['previous_matrix'], 'key': old['key'], 'sha256': sha(root / config['previous_matrix'])}}
            else:
                started = time.perf_counter()
                planets, sun = ephemerides(ctx, backend, arm)
                force, convert = build_barycentric_eih(ctx['origin'], planets, ctx['mu'], ctx['c'], ctx['ng'],
                    ctx['base']['earth_j2']['reference_radius_km'] / ctx['au'], ctx['base']['earth_j2']['j2'], sun, gr=config['gr'])
                construction_seconds = time.perf_counter() - started
                if convert(0., initial) != initial:
                    raise ValueError('Initial-state conversion changed')
                started = time.perf_counter()
                if setting['solver'] == 'dopri54':
                    result = integrate_precise_dopri54(force, 0., _flat(initial), ctx['times'],
                        **{k: v for k, v in setting.items() if k not in ('solver', 'label')}, max_step=previous_config['max_dp_step_days'])
                    native_states = [_state(s) for t, s in result.samples]
                    native_endpoints = [[t, list(s)] for t, s in result.accepted_endpoints]
                    stats = dict(result.stats)
                else:
                    native_endpoints = [[0., list(_flat(initial))]]
                    helio_selector = encounter_aware_step_selector(planets, 0., default_step_days=previous_config['rk4_default_step_days'], scale_factor=setting['scale'])
                    def selector(t, native):
                        return helio_selector(t, convert(t, native))
                    native_states, meta = advance(initial, ctx['times'], force, selector, compensated=True,
                        on_step=lambda _t, _s, right, state: native_endpoints.append([right, list(_flat(state))]))
                    stats = asdict(meta)
                propagation_elapsed = time.perf_counter() - started
                states = [convert(t, s) for t, s in zip(ctx['times'], native_states, strict=True)]
                endpoints = [[t, list(_flat(convert(t, _state(s))))] for t, s in native_endpoints]
                elapsed = time.perf_counter() - started
                primary, refs = evaluate(states, ctx['annual'], ctx['refs'], ctx['au'], ctx['day'])
                row = {'key': key, 'arm': arm, 'initial_source': 'annual_hourly', 'solver': setting['solver'], 'setting': setting,
                    'numerical_mode': 'precise_dp' if setting['solver'] == 'dopri54' else 'compensated',
                    'mode': 'relative_calendar_knots', 'origin_jd_tdb': ctx['origin'], 'origin_calendar_tdb': ctx['calendar'],
                    'initial_state': list(_flat(initial)), 'native_initial_state': list(_flat(initial)),
                    'requested_times_relative_days': ctx['times'], 'requested_states': [list(_flat(s)) for s in states],
                    'native_solver_requested_states': [list(_flat(s)) for s in native_states],
                    'accepted_time_basis': 'relative_days_since_start', 'accepted_endpoints': endpoints, 'native_solver_endpoints': native_endpoints,
                    'native_state_basis': NATIVE_BASIS, 'reported_state_basis': OUTPUT_BASIS,
                    'primary_old_grid': primary, 'reference_comparisons': refs, 'solver_stats': stats,
                    'runtime_seconds': elapsed, 'propagation_runtime_seconds': propagation_elapsed,
                    'ephemeris_construction_seconds': construction_seconds, 'fingerprint': fingerprint,
                    'force_specification': force_spec(arm), 'sun_origin_acceleration_subtraction': False,
                    'rk4_step_selection_state_basis': OUTPUT_BASIS}
            immutable_json(path, row)
            print(json.dumps({'completed': key, 'reused': 'reused_from' in row,
                'matched_error_km': row['reference_comparisons']['annual_daily']['max_grid_position_error_km'],
                'seconds': row['runtime_seconds']}), flush=True)
    paths = [output / 'checkpoints' / (a + '__' + s['label'] + '.json') for a in config['arms'] for s in settings]
    if all(p.exists() for p in paths):
        records = [json.loads(p.read_text()) for p in paths]
        if any(r['fingerprint'] != fingerprint for r in records):
            raise ValueError('Matrix fingerprint mismatch')
        immutable_json(output / 'matrix.json', {'fingerprint': fingerprint, 'freeze_sha256': sha(freeze), 'records': records,
            'input_checks_sha256': sha(output / 'input_checks.json'), 'force_checks_sha256': sha(output / 'force_checks.json'),
            'scope': 'Post-hoc DE441 interpolation and independent force audit; no selector validation'})
        print(json.dumps({'complete': len(records), 'new_propagations': 15}), flush=True)
    else:
        print(json.dumps({'completed_checkpoints': sum(p.exists() for p in paths), 'expected': len(paths)}), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path('.'))
    p.add_argument('--solver', choices=('all', 'dopri54', 'rk4'), default='all')
    p.add_argument('--prepare-only', action='store_true')
    args = p.parse_args()
    run(args.root.resolve(), args.solver, args.prepare_only)

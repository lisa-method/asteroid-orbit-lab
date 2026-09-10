"""Frozen direct-barycentric follow-up to the heliocentric PPN audit."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import datetime as dt
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

from barycentric_eih import build_barycentric_eih
from planetary_dynamics import encounter_aware_step_selector
from precise_dopri import integrate_precise_dopri54
from precise_rk4 import advance
from prepare_fresh_holdout import check_hashes, immutable_json, sha
from run_apophis_eih import context as eih_context
from run_apophis_reference_time import source_closure, evaluate
from run_apophis_solver_audit import _flat, _state
from run_physics_baselines import state_from_row

CONFIG = 'configs/apophis_eih_barycentric.json'
NATIVE_BASIS = 'uniform_inertial_q=rb-rSun0-t*vSun0,w=vb-vSun0'
OUTPUT_BASIS = 'heliocentric_ICRF_geometric_AU_AU-per-day'


def context(root):
    config = json.loads((root / CONFIG).read_text())
    prior = json.loads((root / config['previous_freeze']).read_text())
    check_hashes(root, prior['hashes'])
    ctx, previous_config, solar, _, _, _ = eih_context(root)
    runtime = {'executable': sys.executable, 'version': sys.version, 'platform': platform.platform()}
    if runtime != prior['runtime']:
        raise ValueError('Barycentric follow-up requires frozen runtime')
    paths = [CONFIG, config['contract'], config['previous_freeze'], config['previous_matrix'],
             'tests/test_barycentric_eih.py']
    paths += source_closure(root, ['run_apophis_eih_barycentric'])
    hashes = {**prior['hashes'], **{p: sha(root / p) for p in sorted(set(paths))}}
    payload = {'hashes': hashes, 'runtime': runtime}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    output = root / config['output_directory']
    freeze = output / 'freeze.json'
    if freeze.exists():
        old = json.loads(freeze.read_text())
        if old['fingerprint'] != fingerprint or any(old[k] != payload[k] for k in payload):
            raise ValueError('Barycentric freeze changed')
    else:
        immutable_json(freeze, {**payload, 'fingerprint': fingerprint,
                               'created_utc': dt.datetime.now(dt.timezone.utc).isoformat()})
    immutable_json(output / 'inputs.json', {
        'fingerprint': fingerprint, 'previous_matrix_sha256': sha(root / config['previous_matrix']),
        'previous_inputs_sha256': sha(root / previous_config['output_directory'] / 'inputs.json'),
        'native_state_basis': NATIVE_BASIS, 'reported_state_basis': OUTPUT_BASIS,
        'initial_source': 'annual_hourly', 'target_data_unchanged': True,
        'solar_j2': solar, 'sun_origin_acceleration_subtraction': False,
        'rk4_step_selection_state_basis': OUTPUT_BASIS,
        'pn_velocity_basis': 'true_barycentric_v=w+vSun0',
        'ephemeris_inputs': 'unchanged hourly Sun-SSB and Pluto-Sun from preceding PPN audit'})
    return ctx, config, previous_config, solar, output, freeze, fingerprint


def run(root, solver='all', prepare_only=False):
    ctx, config, previous_config, solar, output, freeze, fingerprint = context(root)
    if prepare_only:
        print(json.dumps({'frozen': fingerprint}), flush=True)
        return
    initial = state_from_row(ctx['refs']['annual_hourly'][0])
    arms = [a for a in previous_config['arms'] if a['id'] in config['arms']]
    settings = [s for s in previous_config['solver_settings'] if s['label'] in config['settings']]
    if len(arms) != 4 or len(settings) != 3:
        raise ValueError('Prespecified four-arm/three-setting matrix changed')
    planets = ctx['planets'] + (ctx['pluto'],)
    for setting in settings:
        if solver != 'all' and setting['solver'] != solver:
            continue
        for arm in arms:
            key = arm['id'] + '__' + setting['label']
            path = output / 'checkpoints' / (key + '.json')
            if path.exists():
                if json.loads(path.read_text())['fingerprint'] != fingerprint:
                    raise ValueError('Checkpoint fingerprint mismatch')
                continue
            force, convert = build_barycentric_eih(
                ctx['origin'], planets, ctx['mu'], ctx['c'], ctx['ng'],
                ctx['base']['earth_j2']['reference_radius_km'] / ctx['au'],
                ctx['base']['earth_j2']['j2'], ctx['sun_barycentric'],
                gr=arm['gr'], solar_j2=solar if arm['solar_j2'] else None)
            if convert(0., initial) != initial:
                raise ValueError('Initial-state frame conversion changed the forecast input')
            started = time.perf_counter()
            if setting['solver'] == 'dopri54':
                result = integrate_precise_dopri54(
                    force, 0., _flat(initial), ctx['times'],
                    **{k: v for k, v in setting.items() if k not in ('solver', 'label')},
                    max_step=previous_config['max_dp_step_days'])
                native_states = [_state(s) for t, s in result.samples]
                native_endpoints = [[t, list(s)] for t, s in result.accepted_endpoints]
                stats = dict(result.stats)
            else:
                native_endpoints = [[0., list(_flat(initial))]]
                helio_selector = encounter_aware_step_selector(
                    planets, 0., default_step_days=previous_config['rk4_default_step_days'],
                    scale_factor=setting['scale'])
                # Screening geometry needs instantaneous heliocentric target states.
                def selector(t, native):
                    return helio_selector(t, convert(t, native))
                native_states, meta = advance(
                    initial, ctx['times'], force, selector, compensated=True,
                    on_step=lambda _t, _s, right, state:
                        native_endpoints.append([right, list(_flat(state))]))
                stats = asdict(meta)
            propagation_elapsed = time.perf_counter() - started
            states = [convert(t, s) for t, s in zip(ctx['times'], native_states, strict=True)]
            endpoints = [[t, list(_flat(convert(t, _state(s))))] for t, s in native_endpoints]
            elapsed = time.perf_counter() - started
            primary, refs = evaluate(states, ctx['annual'], ctx['refs'], ctx['au'], ctx['day'])
            row = {
                'key': key, 'arm': arm['id'], 'initial_source': 'annual_hourly',
                'solver': setting['solver'], 'setting': setting,
                'numerical_mode': 'precise_dp' if setting['solver'] == 'dopri54' else 'compensated',
                'mode': 'relative_calendar_knots', 'origin_jd_tdb': ctx['origin'],
                'origin_calendar_tdb': ctx['calendar'], 'initial_state': list(_flat(initial)),
                'native_initial_state': list(_flat(initial)), 'requested_times_relative_days': ctx['times'],
                'requested_states': [list(_flat(s)) for s in states],
                'native_solver_requested_states': [list(_flat(s)) for s in native_states],
                'accepted_time_basis': 'relative_days_since_start', 'accepted_endpoints': endpoints,
                'native_solver_endpoints': native_endpoints,
                'native_state_basis': NATIVE_BASIS, 'reported_state_basis': OUTPUT_BASIS,
                'primary_old_grid': primary, 'reference_comparisons': refs, 'solver_stats': stats,
                'runtime_seconds': elapsed, 'propagation_runtime_seconds': propagation_elapsed,
                'fingerprint': fingerprint, 'force_specification': arm,
                'sun_origin_acceleration_subtraction': False,
                'rk4_step_selection_state_basis': OUTPUT_BASIS}
            immutable_json(path, row)
            print(json.dumps({'completed': key,
                'matched_error_km': refs['annual_daily']['max_grid_position_error_km'],
                'propagation_seconds': propagation_elapsed, 'seconds_including_trace_conversion': elapsed}), flush=True)
    paths = [output / 'checkpoints' / (a['id'] + '__' + s['label'] + '.json')
             for a in arms for s in settings]
    if all(p.exists() for p in paths):
        records = [json.loads(p.read_text()) for p in paths]
        if any(r['fingerprint'] != fingerprint for r in records):
            raise ValueError('Matrix fingerprint mismatch')
        immutable_json(output / 'matrix.json', {'fingerprint': fingerprint, 'freeze_sha256': sha(freeze),
            'records': records, 'scope': 'Post-hoc direct barycentric PPN frame audit; not operational validation'})
        print(json.dumps({'complete': len(records), 'new_propagations': 12}), flush=True)
    else:
        print(json.dumps({'completed_checkpoints': sum(p.exists() for p in paths), 'expected': len(paths)}), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path('.'))
    p.add_argument('--solver', choices=('all', 'dopri54', 'rk4'), default='all')
    p.add_argument('--prepare-only', action='store_true')
    args = p.parse_args()
    run(args.root.resolve(), args.solver, args.prepare_only)

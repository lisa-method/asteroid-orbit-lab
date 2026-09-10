"""Frozen comparison of legacy, endpoint-consistent and compensated RK4."""
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

from orbit_baselines import State
from planetary_dynamics import encounter_aware_step_selector
from precise_rk4 import advance
from prepare_fresh_holdout import check_hashes, immutable_json, sha
from run_apophis_shape_weak_force import context as shape_context, make_force
from run_apophis_reference_time import evaluate, source_closure
from run_apophis_solver_audit import _flat, _state, _shift, _run_dp, _run_rk4
from run_physics_baselines import state_from_row

CONFIG = 'configs/precise_propagation.json'


def specification(config):
    runs = []
    for mode, scales in config['old_baseline_modes'].items():
        runs.extend((mode, 'baseline', 'old', scale) for scale in scales)
    for row in config['additional_compensated']:
        runs.extend(('compensated', row['arm'], row['initial_source'], scale) for scale in row['scales'])
    for arm in config['dp_arms']:
        for source in config['dp_initial_sources']:
            runs.extend(('dopri54', arm, source, label) for label in config['dp_labels'])
    return runs


def context(root, freeze=True):
    config = json.loads((root / CONFIG).read_text())
    prior = json.loads((root / config['previous_freeze']).read_text())
    check_hashes(root, prior['hashes'])
    ctx = shape_context(root, freeze=False)
    matrix = json.loads((root / config['previous_matrix']).read_text())
    if matrix['freeze_sha256'] != sha(root / config['previous_freeze']):
        raise ValueError('Previous matrix/freeze mismatch')
    paths = [CONFIG, config['contract'], config['previous_matrix'], config['previous_freeze']]
    paths += source_closure(root, ['run_precise_propagation'])
    hashes = {**prior['hashes'], **{p: sha(root / p) for p in sorted(set(paths))}}
    runtime = {'executable': sys.executable, 'version': sys.version, 'platform': platform.platform()}
    payload = {'hashes': hashes, 'runtime': runtime}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    output = root / config['output_directory']
    freeze_path = output / 'freeze.json'
    if freeze:
        if freeze_path.exists():
            old = json.loads(freeze_path.read_text())
            if old['fingerprint'] != fingerprint or any(old[k] != payload[k] for k in payload):
                raise ValueError('Precise propagation freeze changed')
        else:
            immutable_json(freeze_path, {**payload, 'fingerprint': fingerprint,
                'created_utc': dt.datetime.now(dt.timezone.utc).isoformat()})
    ctx.update(audit_config=config, prior_shape=matrix, output=output,
        audit_fingerprint=fingerprint, audit_freeze=freeze_path)
    return ctx


def run_one(ctx, mode, arm, source, setting):
    key = f'{mode}__{arm}__{source}__{setting}'
    path = ctx['output'] / 'checkpoints' / (key + '.json')
    if path.exists():
        saved = json.loads(path.read_text())
        if saved['fingerprint'] != ctx['audit_fingerprint']:
            raise ValueError('Checkpoint fingerprint mismatch')
        return saved
    initial = state_from_row(ctx['annual'][0] if source == 'old' else ctx['refs']['annual_hourly'][0])
    force = make_force(ctx, arm)
    trace = [[0., list(_flat(initial))]]
    started = time.perf_counter()
    if mode == 'dopri54':
        tolerance = next(t for t in ctx['base']['dopri54_tolerances'] if t['label'] == setting)
        predictions, meta = _run_dp(initial, 0., ctx['times'], force, tolerance, ctx['base']['dopri54_max_step_days'])
        trace = meta['accepted_endpoints']
        stored_setting = tolerance
    elif mode == 'legacy':
        predictions, meta = _run_rk4(initial, 0., ctx['times'], force, ctx['planets'], setting, .0625)
        trace += meta['accepted_endpoints']
        stored_setting = setting
    else:
        selector = encounter_aware_step_selector(ctx['planets'], 0., default_step_days=.0625, scale_factor=setting)
        predictions, stats = advance(initial, ctx['times'], force, selector,
            compensated=(mode == 'compensated'),
            on_step=lambda _t, _s, right, state: trace.append([right, list(_flat(state))]))
        meta = {**asdict(stats), 'rk4_steps': stats.steps, 'force_evaluations': 4 * stats.steps}
        stored_setting = setting
    elapsed = time.perf_counter() - started
    states = [list(_flat(s)) for s in predictions]
    if mode == 'legacy':
        previous = next(r for r in ctx['previous']['records'] if r['mode'] == 'relative_calendar_knots'
            and r['solver'] == 'rk4' and r['initial_source'] == source and r['setting'] == setting)
        if states != previous['requested_states']:
            raise ValueError('Legacy RK4 reproduction not exact')
    if mode == 'dopri54':
        previous = next(r for r in ctx['prior_shape']['records'] if r['key'] == f'{arm}__{source}__{setting}')
        if states != previous['requested_states']:
            raise ValueError('DP reproduction not exact')
    primary, references = evaluate(predictions, ctx['annual'], ctx['refs'], ctx['au'], ctx['day'])
    record = dict(key=key, numerical_mode=mode, arm=arm, initial_source=source,
        solver='dopri54' if mode == 'dopri54' else 'rk4', setting=stored_setting,
        mode='relative_calendar_knots', origin_jd_tdb=ctx['origin'], origin_calendar_tdb=ctx['calendar'],
        initial_state=list(_flat(initial)), requested_times_relative_days=ctx['times'],
        requested_states=states, accepted_time_basis='relative_days_since_start',
        accepted_endpoints=trace, primary_old_grid=primary, reference_comparisons=references,
        solver_stats={k:v for k,v in meta.items() if k not in ('requested_states','accepted_endpoints')},
        runtime_seconds=elapsed, fingerprint=ctx['audit_fingerprint'])
    immutable_json(path, record)
    teacher = 'old_daily' if source == 'old' else 'annual_daily'
    print(json.dumps({'completed':key, 'matched_error_km':references[teacher]['max_grid_position_error_km'],
        'steps':len(trace)-1, 'seconds':elapsed}), flush=True)
    return record


def run(root):
    ctx = context(root)
    records = [run_one(ctx, *spec) for spec in specification(ctx['audit_config'])]
    pairs = []
    for i, left in enumerate(records):
        for right in records[i+1:]:
            if left['initial_source'] == right['initial_source'] and left['arm'] == right['arm']:
                pairs.append({'left':left['key'], 'right':right['key'], **_shift(
                    [_state(s) for s in left['requested_states']], [_state(s) for s in right['requested_states']],
                    ctx['au'],ctx['day'])})
    result = {'fingerprint':ctx['audit_fingerprint'], 'freeze_sha256':sha(ctx['audit_freeze']),
        'records':records, 'paired_shifts':pairs,
        'scope':'post-hoc numerical regression; independent algorithms share physical RHS; no selector validation'}
    immutable_json(ctx['output'] / 'matrix.json', result)
    print(json.dumps({'complete':len(records),'pairs':len(pairs)}), flush=True)
    return result


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('.'))
    run(parser.parse_args().root.resolve())

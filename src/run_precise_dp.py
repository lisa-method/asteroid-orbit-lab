"""Versioned compensated-DP follow-up; never rewrites completed artifacts."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import time

from precise_dopri import integrate_precise_dopri54
from prepare_fresh_holdout import immutable_json, sha
from run_precise_propagation import context as previous_context
from run_apophis_shape_weak_force import make_force
from run_apophis_reference_time import source_closure, evaluate
from run_apophis_solver_audit import _flat, _state
from run_physics_baselines import state_from_row


def context(root):
    ctx = previous_context(root)
    config_path = 'configs/precise_dp.json'
    config = json.loads((root / config_path).read_text())
    previous = json.loads(ctx['audit_freeze'].read_text())
    paths = source_closure(root, ['run_precise_dp']) + [config_path, config['contract'],
        'outputs/precise_propagation/matrix.json', 'outputs/precise_propagation/freeze.json']
    hashes = {**previous['hashes'], **{p: sha(root / p) for p in sorted(set(paths))}}
    payload = {'hashes': hashes, 'runtime': previous['runtime']}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    output = root / config['output_directory']
    freeze = output / 'freeze.json'
    if freeze.exists():
        old = json.loads(freeze.read_text())
        if old['fingerprint'] != fingerprint or any(old[k] != payload[k] for k in payload):
            raise ValueError('Precise DP freeze changed')
    else:
        immutable_json(freeze, {**payload, 'fingerprint': fingerprint,
            'created_utc': dt.datetime.now(dt.timezone.utc).isoformat()})
    return ctx, config, output, freeze, fingerprint


def run(root):
    ctx, config, output, freeze, fingerprint = context(root)
    records = []
    for source in config['sources']:
        initial = state_from_row(ctx['annual'][0] if source == 'old' else ctx['refs']['annual_hourly'][0])
        for arm in config['arms']:
            force = make_force(ctx, arm)
            for tolerance in config['tolerances']:
                key = f"precise_dp__{arm}__{source}__{tolerance['label']}"
                path = output / 'checkpoints' / (key + '.json')
                if path.exists():
                    row = json.loads(path.read_text())
                    if row['fingerprint'] != fingerprint:
                        raise ValueError('Checkpoint fingerprint differs')
                else:
                    started = time.perf_counter()
                    result = integrate_precise_dopri54(force, 0., _flat(initial), ctx['times'],
                        **{k: v for k, v in tolerance.items() if k != 'label'},
                        max_step=config['max_step_days'])
                    elapsed = time.perf_counter() - started
                    states = [_state(s) for t, s in result.samples]
                    primary, references = evaluate(states, ctx['annual'], ctx['refs'], ctx['au'], ctx['day'])
                    row = dict(key=key, numerical_mode='precise_dp', solver='dopri54',
                        arm=arm, initial_source=source, setting=tolerance,
                        mode='relative_calendar_knots', origin_jd_tdb=ctx['origin'],
                        origin_calendar_tdb=ctx['calendar'], initial_state=list(_flat(initial)),
                        requested_times_relative_days=ctx['times'],
                        requested_states=[list(_flat(s)) for s in states],
                        accepted_time_basis='relative_days_since_start',
                        accepted_endpoints=[[t, list(s)] for t, s in result.accepted_endpoints],
                        primary_old_grid=primary, reference_comparisons=references,
                        solver_stats=dict(result.stats), runtime_seconds=elapsed, fingerprint=fingerprint)
                    immutable_json(path, row)
                    matched = 'old_daily' if source == 'old' else 'annual_daily'
                    print(json.dumps({'completed': key, 'matched_error_km':
                        references[matched]['max_grid_position_error_km'], 'seconds': elapsed}), flush=True)
                records.append(row)
    matrix = {'fingerprint': fingerprint, 'freeze_sha256': sha(freeze), 'records': records,
        'scope': 'post-hoc numerical sensitivity; same RHS; not independent physical validation'}
    immutable_json(output / 'matrix.json', matrix)
    print(json.dumps({'complete': len(records)}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    run(parser.parse_args().root.resolve())

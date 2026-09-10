"""Immutable 2x2 Earth pole / solar J2 diagnostic; no fitted parameters."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import time

from gravity_conventions import build_convention_force
from ng_inputs_v2 import load_ng_input
from planetary_dynamics import encounter_aware_step_selector
from precise_dopri import integrate_precise_dopri54
from precise_rk4 import advance
from prepare_fresh_holdout import check_hashes, immutable_json, sha
from run_apophis_reference_time import evaluate, source_closure
from run_apophis_solver_audit import _flat, _state
from run_physics_baselines import state_from_row
from run_precise_propagation import context as previous_context

CONFIG = 'configs/apophis_gravity_conventions.json'


def de_constants(text):
    groups = text.split('GROUP')
    names = next(x for x in groups if x.strip().startswith('1040')).split()[1:]
    values = next(x for x in groups if x.strip().startswith('1041')).split()[1:]
    if int(names[0]) != len(names)-1 or int(values[0]) != len(values)-1 or names[0] != values[0]:
        raise ValueError('Malformed DE constants groups')
    return dict(zip(names[1:], values[1:], strict=True))


def header_inventory(root, hashes):
    result = []
    for name in sorted(hashes):
        if not name.startswith('data/raw/') or not name.endswith('.json'):
            continue
        document = json.loads((root/name).read_text())
        header = document.get('result', '').split('$$SOE')[0]
        if 'Apophis' not in header or '99942' not in header:
            continue
        models = re.findall(r'EOBL_MOD\s*=\s*([^\n]+)', header)
        limits = re.findall(r'EOBL_LIM\s*=\s*([0-9.]+)', header)
        if not models or any(m.strip() != '2x0 (J2 only)' for m in models):
            raise ValueError(f'Unexpected Apophis Earth model in {name}')
        result.append({'path': name, 'sha256': sha(root/name),
                       'earth_models': sorted(set(m.strip() for m in models)),
                       'earth_cutoffs_au': sorted(set(map(float, limits)))})
    if not result:
        raise ValueError('No Apophis force headers inspected')
    return result


def context(root):
    config = json.loads((root/CONFIG).read_text())
    prior = json.loads((root/config['previous_freeze']).read_text())
    check_hashes(root, prior['hashes'])
    ctx = previous_context(root, freeze=False)
    de = de_constants((root/config['header']).read_text())
    solar = {'j2':float(de['J2SUN'].replace('D','E')),
             'radius_au':float(de['ASUN'].replace('D','E'))/ctx['au'],
             'pole_ra_deg':config['solar_pole_ra_deg'], 'pole_dec_deg':config['solar_pole_dec_deg']}
    paths = [CONFIG, config['contract'], config['header'], config['manifest'], config['previous_freeze'],
             'outputs/precise_dp/matrix.json', 'outputs/precise_propagation/matrix.json']
    paths += source_closure(root, ['run_apophis_gravity_conventions'])
    hashes = {**prior['hashes'], **{p:sha(root/p) for p in sorted(set(paths))}}
    manifest = json.loads((root/config['manifest']).read_text())
    for row in manifest['files']:
        if sha(root/row['path']) != row['sha256'] or (root/row['path']).stat().st_size != row['bytes']:
            raise ValueError('Constants provenance changed')
    inventory = header_inventory(root, hashes)
    payload = {'hashes':hashes, 'runtime':json.loads(ctx['audit_freeze'].read_text())['runtime']}
    # previous_context records the actual runtime; it must match the previous freeze.
    if payload['runtime'] != prior['runtime']:
        raise ValueError('Frozen runtime differs')
    import platform, sys
    actual = {'executable':sys.executable, 'version':sys.version, 'platform':platform.platform()}
    if actual != payload['runtime']:
        raise ValueError('Current runtime differs')
    fingerprint = hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    output = root/config['output_directory']; freeze = output/'freeze.json'
    if freeze.exists():
        old = json.loads(freeze.read_text())
        if old['fingerprint'] != fingerprint or any(old[k] != payload[k] for k in payload):
            raise ValueError('Gravity-convention freeze changed')
    else:
        immutable_json(freeze, {**payload,'fingerprint':fingerprint,
            'created_utc':dt.datetime.now(dt.timezone.utc).isoformat()})
    immutable_json(output/'inputs.json', {'headers':inventory,'solar_constants':solar,
        'de_original_strings':{k:de[k] for k in ('J2SUN','ASUN','J2E','RE','GMS','AU','CLIGHT')},
        'retained_earth_constants':ctx['base']['earth_j2'], 'fingerprint':fingerprint})
    fc = json.loads((root/ctx['base']['force_config']).read_text())
    ctx.update(c=fc['speed_of_light_km_s']*ctx['day']/ctx['au'],
               ng=load_ng_input(root/'data/raw/horizons/asteroid_99942.json').parameters)
    return ctx,config,solar,output,freeze,fingerprint


def force_for(ctx, arm, solar):
    return build_convention_force(ctx['origin'],ctx['planets'],ctx['mu'],ctx['c'],ctx['ng'],
        ctx['base']['earth_j2']['reference_radius_km']/ctx['au'],ctx['base']['earth_j2']['j2'],
        earth_pole=arm['earth_pole'],solar_j2=solar if arm['solar_j2'] else None)


def run(root):
    ctx,config,solar,output,freeze,fingerprint = context(root)
    initial = state_from_row(ctx['refs']['annual_hourly'][0])
    dp_path = 'outputs/precise_dp/matrix.json'; rk_path = 'outputs/precise_propagation/matrix.json'
    previous = {p:json.loads((root/p).read_text()) for p in (dp_path,rk_path)}
    records = []
    for arm in config['arms']:
        force = force_for(ctx,arm,solar)
        for setting in config['solver_settings']:
            key = arm['id']+'__'+setting['label']; path = output/'checkpoints'/(key+'.json')
            if path.exists():
                row = json.loads(path.read_text())
                if row['fingerprint'] != fingerprint:
                    raise ValueError('Checkpoint freeze differs')
                records.append(row); continue
            if arm['id'] == 'baseline':
                p = dp_path if setting['solver'] == 'dopri54' else rk_path
                old_key = ('precise_dp__baseline__annual_hourly__'+setting['label'] if p == dp_path
                           else 'compensated__baseline__annual_hourly__0.125')
                old = next(r for r in previous[p]['records'] if r['key'] == old_key)
                for t, state in zip(old['requested_times_relative_days'],old['requested_states'],strict=True):
                    if force(t,tuple(state[:3]),tuple(state[3:])) != ctx['force'](t,tuple(state[:3]),tuple(state[3:])):
                        raise ValueError('Baseline RHS changed')
                row = {**old,'key':key,'setting':setting,'fingerprint':fingerprint,
                       'reused_from':{'matrix':p,'key':old_key,'sha256':sha(root/p)}}
            else:
                started = time.perf_counter()
                if setting['solver'] == 'dopri54':
                    result = integrate_precise_dopri54(force,0.,_flat(initial),ctx['times'],
                        **{k:v for k,v in setting.items() if k not in ('solver','label')},
                        max_step=config['max_dp_step_days'])
                    states = [_state(s) for t,s in result.samples]
                    endpoints = [[t,list(s)] for t,s in result.accepted_endpoints]
                    stats = dict(result.stats)
                else:
                    endpoints = [[0.,list(_flat(initial))]]
                    selector = encounter_aware_step_selector(ctx['planets'],0.,
                        default_step_days=config['rk4_default_step_days'],scale_factor=setting['scale'])
                    states, stats_raw = advance(initial,ctx['times'],force,selector,compensated=True,
                        on_step=lambda _t,_s,right,state:endpoints.append([right,list(_flat(state))]))
                    stats = asdict(stats_raw)
                elapsed = time.perf_counter()-started
                primary,refs = evaluate(states,ctx['annual'],ctx['refs'],ctx['au'],ctx['day'])
                row = {'key':key,'arm':arm['id'],'initial_source':'annual_hourly',
                    'solver':setting['solver'],'setting':setting,
                    'numerical_mode':'precise_dp' if setting['solver']=='dopri54' else 'compensated',
                    'mode':'relative_calendar_knots','origin_jd_tdb':ctx['origin'],
                    'origin_calendar_tdb':ctx['calendar'],'initial_state':list(_flat(initial)),
                    'requested_times_relative_days':ctx['times'],
                    'requested_states':[list(_flat(s)) for s in states],
                    'accepted_time_basis':'relative_days_since_start','accepted_endpoints':endpoints,
                    'primary_old_grid':primary,'reference_comparisons':refs,
                    'solver_stats':stats,'runtime_seconds':elapsed,'fingerprint':fingerprint}
            row['force_specification'] = arm
            immutable_json(path,row); records.append(row)
            print(json.dumps({'completed':key,'reused':'reused_from' in row,
                'matched_error_km':row['reference_comparisons']['annual_daily']['max_grid_position_error_km']}),flush=True)
    immutable_json(output/'matrix.json',{'fingerprint':fingerprint,'freeze_sha256':sha(freeze),
        'records':records,'scope':'post-hoc Earth pole / solar J2 factorial audit; no accuracy guarantee'})
    print(json.dumps({'complete':len(records),'new_propagations':9}),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('.'))
    run(parser.parse_args().root.resolve())

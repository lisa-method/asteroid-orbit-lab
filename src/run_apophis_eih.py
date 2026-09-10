"""Frozen relativistic Apophis audit with separate DP/RK4 scheduling."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import datetime as dt
import hashlib
import json
from pathlib import Path
import time

from eih_force_model import build_eih_force_model
from planetary_dynamics import Perturber, encounter_aware_step_selector
from precise_dopri import integrate_precise_dopri54
from precise_rk4 import advance
from prepare_apophis_eih import prepare
from prepare_fresh_holdout import check_hashes,immutable_json,sha
from relative_time_dynamics import RelativeEphemerisInterpolator,relative_rows
from run_apophis_gravity_conventions import context as gravity_context
from run_apophis_reference_time import source_closure,evaluate
from run_apophis_solver_audit import _flat,_state
from run_eda import parse_horizons
from run_physics_baselines import state_from_row

CONFIG='configs/apophis_eih.json'


def context(root):
    config=json.loads((root/CONFIG).read_text())
    prior=json.loads((root/config['previous_freeze']).read_text());check_hashes(root,prior['hashes'])
    ctx,_,solar,_,_,_=gravity_context(root)
    manifest=prepare(root)
    ic=json.loads((root/config['input_config']).read_text())
    ephems={}
    for row in manifest['files']:
        _,rows=parse_horizons(root/row['path'],row['target_id'],row['target_name'])
        relative=relative_rows(rows,ctx['origin'],ctx['calendar'],'calendar')
        ephems[row['name']]=RelativeEphemerisInterpolator(
            tuple(r['epoch_relative_days'] for r in relative),tuple(state_from_row(r) for r in relative))
    ctx.update(sun_barycentric=ephems['sun_ssb_hourly'],
        pluto=Perturber('9','Pluto system',config['pluto_mu_km3_s2']*ctx['day']**2/ctx['au']**3,ephems['pluto_sun_hourly']))
    paths=[CONFIG,config['contract'],config['previous_freeze'],config['previous_matrix'],
        config['input_config'],ic['manifest'],ic['design'],
        'tests/test_relativistic_eih.py','tests/test_eih_force_model.py']
    paths+=source_closure(root,['run_apophis_eih'])+[r['path'] for r in manifest['files']]
    hashes={**prior['hashes'],**{p:sha(root/p) for p in sorted(set(paths))}}
    payload={'hashes':hashes,'runtime':prior['runtime']}
    fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    output=root/config['output_directory'];freeze=output/'freeze.json'
    if freeze.exists():
        old=json.loads(freeze.read_text())
        if old['fingerprint']!=fingerprint or any(old[k]!=payload[k] for k in payload):raise ValueError('EIH freeze changed')
    else:immutable_json(freeze,{**payload,'fingerprint':fingerprint,'created_utc':dt.datetime.now(dt.timezone.utc).isoformat()})
    immutable_json(output/'inputs.json',{'fingerprint':fingerprint,'manifest_sha256':sha(root/ic['manifest']),
        'raw_paths':[r['path'] for r in manifest['files']],
        'pn_source_ids':['10']+[p.body_id for p in ctx['planets'] if not p.body_id.startswith('sb:')]+['9'],
        'small_body_physics':'16 Newtonian only','solar_j2':solar,
        'frame':'Heliocentric differences, barycentric velocities, full major-source PN Sun-origin subtraction',
        'initial_source':'annual_hourly','target_data_unchanged':True,
        'reference_model':'Horizons nominal teacher; Sun-only PPN motivated by Holman2023, internals not certified'})
    return ctx,config,solar,output,freeze,fingerprint


def force_for(ctx,arm,solar):
    planets=ctx['planets']+((ctx['pluto'],) if arm['pluto'] else ())
    force=build_eih_force_model(ctx['origin'],planets,ctx['mu'],ctx['c'],ctx['ng'],
        ctx['base']['earth_j2']['reference_radius_km']/ctx['au'],ctx['base']['earth_j2']['j2'],
        ctx['sun_barycentric'],gr=arm['gr'],solar_j2=solar if arm['solar_j2'] else None)
    return force,planets


def run(root,solver='all',prepare_only=False):
    ctx,config,solar,output,freeze,fingerprint=context(root)
    if prepare_only:
        print(json.dumps({'frozen':fingerprint}),flush=True);return
    initial=state_from_row(ctx['refs']['annual_hourly'][0])
    previous=json.loads((root/config['previous_matrix']).read_text())
    # Settings outermost: expensive RK4 is schedulable independently of DP.
    for setting in config['solver_settings']:
        if solver!='all' and setting['solver']!=solver:continue
        for arm in config['arms']:
            key=arm['id']+'__'+setting['label'];path=output/'checkpoints'/(key+'.json')
            if path.exists():
                row=json.loads(path.read_text())
                if row['fingerprint']!=fingerprint:raise ValueError('Checkpoint fingerprint mismatch')
                continue
            force,planets=force_for(ctx,arm,solar)
            if arm['id']=='baseline':
                old_key='fixed_pole__'+setting['label']
                old=next(r for r in previous['records'] if r['key']==old_key)
                from run_apophis_gravity_conventions import force_for as old_force_for
                old_force=old_force_for(ctx,{'earth_pole':'fixed_j2000','solar_j2':False},solar)
                for t,s in zip(old['requested_times_relative_days'],old['requested_states'],strict=True):
                    if force(t,tuple(s[:3]),tuple(s[3:]))!=old_force(t,tuple(s[:3]),tuple(s[3:])):
                        raise ValueError('Baseline force not preserved')
                row={**old,'key':key,'arm':'baseline','fingerprint':fingerprint,
                    'reused_from':{'matrix':config['previous_matrix'],'key':old_key,'sha256':sha(root/config['previous_matrix'])}}
            else:
                started=time.perf_counter()
                if setting['solver']=='dopri54':
                    result=integrate_precise_dopri54(force,0.,_flat(initial),ctx['times'],
                        **{k:v for k,v in setting.items() if k not in ('solver','label')},max_step=config['max_dp_step_days'])
                    states=[_state(s) for t,s in result.samples]
                    endpoints=[[t,list(s)] for t,s in result.accepted_endpoints];stats=dict(result.stats)
                else:
                    endpoints=[[0.,list(_flat(initial))]]
                    selector=encounter_aware_step_selector(planets,0.,default_step_days=config['rk4_default_step_days'],scale_factor=setting['scale'])
                    states,meta=advance(initial,ctx['times'],force,selector,compensated=True,
                        on_step=lambda _t,_s,right,state:endpoints.append([right,list(_flat(state))]))
                    stats=asdict(meta)
                elapsed=time.perf_counter()-started
                primary,refs=evaluate(states,ctx['annual'],ctx['refs'],ctx['au'],ctx['day'])
                row={'key':key,'arm':arm['id'],'initial_source':'annual_hourly','solver':setting['solver'],
                    'setting':setting,'numerical_mode':'precise_dp' if setting['solver']=='dopri54' else 'compensated',
                    'mode':'relative_calendar_knots','origin_jd_tdb':ctx['origin'],'origin_calendar_tdb':ctx['calendar'],
                    'initial_state':list(_flat(initial)),'requested_times_relative_days':ctx['times'],
                    'requested_states':[list(_flat(s)) for s in states],
                    'accepted_time_basis':'relative_days_since_start','accepted_endpoints':endpoints,
                    'primary_old_grid':primary,'reference_comparisons':refs,'solver_stats':stats,
                    'runtime_seconds':elapsed,'fingerprint':fingerprint}
            row['force_specification']=arm
            immutable_json(path,row)
            print(json.dumps({'completed':key,'reused':'reused_from' in row,
                'matched_error_km':row['reference_comparisons']['annual_daily']['max_grid_position_error_km'],
                'seconds':row['runtime_seconds']}),flush=True)
    paths=[output/'checkpoints'/(a['id']+'__'+s['label']+'.json') for a in config['arms'] for s in config['solver_settings']]
    if all(p.exists() for p in paths):
        records=[json.loads(p.read_text()) for p in paths]
        if any(r['fingerprint']!=fingerprint for r in records):raise ValueError('Matrix record fingerprint mismatch')
        immutable_json(output/'matrix.json',{'fingerprint':fingerprint,'freeze_sha256':sha(freeze),
            'records':records,'scope':'Post-hoc 1PN/EIH model and frame audit; not operational validation'})
        print(json.dumps({'complete':len(records),'new_propagations':15}),flush=True)
    else:print(json.dumps({'completed_checkpoints':sum(p.exists() for p in paths),'expected':len(paths)}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path('.'));p.add_argument('--solver',choices=('all','dopri54','rk4'),default='all')
    p.add_argument('--prepare-only',action='store_true');args=p.parse_args()
    run(args.root.resolve(),args.solver,args.prepare_only)

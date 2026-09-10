"""Offline all-record audit of the general relative-force regression."""
from __future__ import annotations

import argparse
import collections
import json
import math
from pathlib import Path

from ng_inputs_v2 import load_ng_input
from run_eda import load_json, parse_horizons
from run_physics_baselines import state_from_row
from verify_precise_propagation import _check_freeze, _sha, _state, _write_immutable
from verify_apophis_shape_weak_force import _manifest_inventory


def close(a, b, name):
    if isinstance(a, dict):
        if not isinstance(b, dict) or set(a) != set(b):
            raise ValueError(f'{name}: different keys')
        for key in a:
            close(a[key], b[key], name+'.'+key)
    elif isinstance(a, (int, float)) and not isinstance(a, bool):
        if not math.isfinite(b) or not math.isclose(a, b, rel_tol=2e-12, abs_tol=1e-13):
            raise ValueError(f'{name}: {a} != {b}')
    elif a != b:
        raise ValueError(f'{name}: values differ')


def distances(a, b, au, day):
    if len(a) != len(b) or not a:
        raise ValueError('Incompatible traces')
    position=[math.sqrt(sum((x-y)**2 for x,y in zip(sa[:3],sb[:3]))) * au for sa,sb in zip(a,b)]
    velocity=[math.sqrt(sum((x-y)**2 for x,y in zip(sa[3:],sb[3:]))) * au * 1000 / day for sa,sb in zip(a,b)]
    return position, velocity


def shift(a,b,au,day):
    p,v=distances(a,b,au,day)
    return {'max_position_shift_km':max(p),'final_position_shift_km':p[-1],
        'max_velocity_shift_m_s':max(v),'final_velocity_shift_m_s':v[-1]}


def verify(root):
    out=root/'outputs/relative_force_regression'
    matrix=load_json(out/'matrix.json')
    freeze=_check_freeze(root,matrix,out/'freeze.json')
    manifest=_manifest_inventory(root)
    config=load_json(root/'configs/relative_force_regression.json')
    base=load_json(root/config['base_config'])
    data=load_json(root/base['data_config'])
    force=load_json(root/base['force_config'])
    solver=load_json(root/config['solver_config'])
    sample=load_json(root/config['sample_path'])['objects']
    au,day=data['constants']['au_km'],data['constants']['day_s']
    tolerances={r['label']:r for r in solver['dopri54_tolerances']}
    expected={f"{o['id']}__{arm}__dopri54__{label}":(o,arm,'dopri54',
        {**tolerances[label],'max_step':solver['dopri54_max_step_days']})
        for o in sample for arm in config['arms'] for label in config['dp_labels']}
    for o in sample:
        if str(o['id']) in config['rk4_object_ids']:
            for arm in config['arms']:
                expected[f"{o['id']}__{arm}__rk4__{config['rk4_step_scale']}"]=(o,arm,'compensated_rk4',
                    {'default_step_days':base['default_step_days'],'step_scale':config['rk4_step_scale'],'max_steps':1000000})
    compact=matrix['records']
    if len(compact)!=132 or {r['key'] for r in compact}!=set(expected):
        raise ValueError('Matrix differs from 132-run contract')
    if {p.stem for p in (out/'checkpoints').glob('*.json')}!=set(expected):
        raise ValueError('Checkpoint set differs')
    if freeze.get('force_check')!={'baseline':0.,'j3j4':0.}:
        raise ValueError('Exact generic force check missing')
    records={}
    raw_cache={}
    scalar_count=endpoint_count=0
    for index in compact:
        path=root/index['checkpoint']
        if path.resolve()!=(out/'checkpoints'/(index['key']+'.json')).resolve() or _sha(path)!=index['checkpoint_sha256']:
            raise ValueError('Checkpoint path/hash mismatch')
        r=load_json(path)
        for key,value in index.items():
            if key not in {'checkpoint','checkpoint_sha256','requested_count','accepted_count'} and r.get(key)!=value:
                raise ValueError(f'Index differs from checkpoint: {key}')
        obj,arm,algorithm,setting=expected[r['key']]
        if r['fingerprint']!=freeze['fingerprint'] or r['object_id']!=str(obj['id']) or r['arm']!=arm or r['solver']!=algorithm or r['setting']!=setting:
            raise ValueError('Forecast specification mismatch')
        if r['start_date']!=obj['start_date'] or r['stratum']!=obj['stratum'] or r['split']!=obj['split']:
            raise ValueError('Object metadata changed')
        if r['horizon_days']!=365 or r['requested_times_relative_days']!=list(range(366)) or len(r['requested_states'])!=366 or index['requested_count']!=366:
            raise ValueError('Daily grid mismatch')
        for values in r['requested_states']:
            _state(values)
        raw_path=root/base['raw_directory']/'asteroids'/f"asteroid_{obj['id']}_daily.json"
        if str(obj['id']) not in raw_cache:
            rows=parse_horizons(raw_path,str(obj['id']),str(obj['name']))[1]
            rows=[row for row in rows if str(row['epoch_tdb'])[:10]>=obj['start_date']][:366]
            ng=load_ng_input(raw_path)
            if ng is None:
                raise ValueError('NG availability adapter failed')
            raw_cache[str(obj['id'])]=(rows,ng)
        rows,ng=raw_cache[str(obj['id'])]
        if len(rows)!=366 or str(rows[0]['epoch_tdb'])[:10]!=obj['start_date']:
            raise ValueError('Wrong teacher window')
        origin=rows[0]['epoch_jd_tdb']
        if any(row['epoch_jd_tdb']-origin!=i for i,row in enumerate(rows)):
            raise ValueError('Teacher is not uniform daily')
        truth=[[*state_from_row(row).position,*state_from_row(row).velocity] for row in rows]
        if r['initial_state']!=truth[0] or r['requested_states'][0]!=truth[0]:
            raise ValueError('Initial state differs')
        trace=r['accepted_endpoints']
        if len(trace)!=index['accepted_count'] or trace[0]!=[0.,truth[0]] or trace[-1][0]!=365.:
            raise ValueError('Native endpoint contract differs')
        for t,s in trace:
            if not isinstance(t,(int,float)) or not math.isfinite(t):
                raise ValueError('Nonfinite native time')
            _state(s)
        if any(b[0]<=a[0] for a,b in zip(trace,trace[1:])):
            raise ValueError('Native times not increasing')
        native=dict(trace)
        if any(native.get(i)!=s for i,s in enumerate(r['requested_states'])):
            raise ValueError('Requested state differs from native endpoint')
        steps=r['solver_stats']['accepted_steps' if algorithm=='dopri54' else 'steps']
        if len(trace)!=steps+1 or (algorithm=='compensated_rk4' and r['solver_stats'].get('compensated') is not True):
            raise ValueError('Solver trace/count mismatch')
        if r['accepted_time_basis']!='relative_days_since_start' or r['frame_contract']['frame']!='ICRF' or r['frame_contract']['time_scale']!='TDB':
            raise ValueError('Frame/time contract mismatch')
        if r['phase_info']['future_event_inputs'] is not False or r['phase_info']['forecast_origin']!=rows[0]['epoch_tdb']:
            raise ValueError('Forecast input contract mismatch')
        meta=r['force_metadata']
        status=ng.status(origin)
        if r['ng_status']!=status or meta['ng_status']!=status or meta['ng_applied']!=(status=='available') or meta['ng_policy']!='available':
            raise ValueError('NG causal gate mismatch')
        if r['ng_source']!=ng.source or r['ng_source_sha256']!=ng.source_sha256:
            raise ValueError('NG provenance differs')
        planet_ids=[str(b['id']) for b in data['perturbers']]
        if meta['perturber_ids'][:9]!=planet_ids or len(meta['small_body_ids'])!=16 or len(meta['perturber_ids'])!=25:
            raise ValueError('Incomplete physical perturber set')
        if meta['earth_zonals']['enabled_degrees']!=([3,4] if arm=='j3j4' else []) or meta['accuracy_guaranteed'] is not False:
            raise ValueError('Force option/guarantee metadata mismatch')
        p,v=distances(r['requested_states'],truth,au,day)
        summary={'max_position_error_km':max(p),'final_position_error_km':p[-1],
            'max_velocity_error_m_s':max(v),'final_velocity_error_m_s':v[-1],'samples':366}
        close(summary,r['primary_daily'],r['key'])
        close({'daily':summary},r['reference_comparisons'],r['key']+'.references')
        if not math.isfinite(r['runtime_seconds']) or r['runtime_seconds']<=0:
            raise ValueError('Invalid measured runtime')
        records[r['key']]=r
        scalar_count+=732
        endpoint_count+=len(trace)
    pairs=[]
    for obj in sample:
        for label in config['dp_labels']:
            a=records[f"{obj['id']}__baseline__dopri54__{label}"]
            b=records[f"{obj['id']}__j3j4__dopri54__{label}"]
            pairs.append({'left':a['key'],'right':b['key'],**shift(a['requested_states'],b['requested_states'],au,day)})
        if str(obj['id']) in config['rk4_object_ids']:
            a=records[f"{obj['id']}__baseline__rk4__{config['rk4_step_scale']}"]
            b=records[f"{obj['id']}__j3j4__rk4__{config['rk4_step_scale']}"]
            pairs.append({'left':a['key'],'right':b['key'],**shift(a['requested_states'],b['requested_states'],au,day)})
    if len(matrix['paired_shifts'])!=66:
        raise ValueError('Force pair count mismatch')
    for a,b in zip(pairs,matrix['paired_shifts']):
        close(a,b,'paired_shift')
    analyses=[]
    for obj in sample:
        branches={}
        for arm in config['arms']:
            a=records[f"{obj['id']}__{arm}__dopri54__tighter"]
            b=records[f"{obj['id']}__{arm}__dopri54__extreme"]
            dp=shift(a['requested_states'],b['requested_states'],au,day)
            entry={'error':b['primary_daily'],'dp_setting_shift':dp,'ng_status':b['ng_status']}
            control=records.get(f"{obj['id']}__{arm}__rk4__{config['rk4_step_scale']}")
            if control:
                entry['rk4_vs_dp_extreme']=shift(control['requested_states'],b['requested_states'],au,day)
            branches[arm]=entry
        bt=records[f"{obj['id']}__baseline__dopri54__tighter"]['requested_states']
        be=records[f"{obj['id']}__baseline__dopri54__extreme"]['requested_states']
        jt=records[f"{obj['id']}__j3j4__dopri54__tighter"]['requested_states']
        je=records[f"{obj['id']}__j3j4__dopri54__extreme"]['requested_states']
        effect_t=[[x-y for x,y in zip(a,b)] for a,b in zip(jt,bt)]
        effect_e=[[x-y for x,y in zip(a,b)] for a,b in zip(je,be)]
        analyses.append({'object_id':str(obj['id']),'stratum':obj['stratum'],'branches':branches,
            'j3j4_shift':shift(je,be,au,day),'effect_setting_difference':shift(effect_t,effect_e,au,day),
            'j3j4_minus_baseline_max_error_km':branches['j3j4']['error']['max_position_error_km']-branches['baseline']['error']['max_position_error_km']})
    coverage=[]
    for arm in config['arms']:
        for tolerance in config['tolerances_km']:
            eligible=[]
            for a in analyses:
                b=a['branches'][arm]
                numerical=max(b['dp_setting_shift']['max_position_shift_km'],
                    b.get('rk4_vs_dp_extreme',{}).get('max_position_shift_km',0.))
                if b['error']['max_position_error_km']<=tolerance and numerical<=tolerance*config['numerical_budget_fraction']:
                    eligible.append(a['object_id'])
            coverage.append({'arm':arm,'tolerance_km':tolerance,'count':len(eligible),'objects':eligible,
                'gate':'DP setting difference for all30; also cross-solver for6 controls; empirical only'})
    provenance={'matrix_sha256':_sha(out/'matrix.json'),'freeze_sha256':_sha(out/'freeze.json'),
        'verifier_sha256':_sha(root/'src/verify_relative_force_regression.py'),
        'helper_sha256':{name:_sha(root/'src'/name) for name in (
            'verify_precise_propagation.py','verify_apophis_shape_weak_force.py')}}
    analysis={**provenance,'objects':analyses,'coverage':coverage,
        'ng_status_counts':dict(collections.Counter(a['branches']['baseline']['ng_status'] for a in analyses)),
        'scope':'30 inspected objects, annual daily grid; no new selector or holdout score'}
    _write_immutable(out/'analysis.json',analysis)
    result={**provenance,'records':len(records),'scalar_error_count':scalar_count,
        'native_endpoints':endpoint_count,'force_pairs':len(pairs),'manifest':manifest,
        'coverage':coverage,'ng_status_counts':analysis['ng_status_counts']}
    _write_immutable(out/'verification.json',result)
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('.'))
    verify(parser.parse_args().root.resolve())

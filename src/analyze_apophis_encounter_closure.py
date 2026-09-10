"""Offline paired errors and Earth-frame diagnostics; no target fitting."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path

from encounter_geometry import segment_closest_approach
from prepare_fresh_holdout import immutable_json, check_hashes, sha
from relative_time_dynamics import relative_rows
from run_apophis_encounter_closure import build_context, digest, OUT, scenario
from run_apophis_solver_audit import _flat, _state
from run_physics_baselines import state_from_row

DAYS=(0,30,60,90,99,102,103,106,180,365)

def delta(a,b,au,day):
    dr=[(x-y)*au*1000 for x,y in zip(a[:3],b[:3])]
    dv=[(x-y)*au*1000/day for x,y in zip(a[3:],b[3:])]
    return dict(position_m=math.sqrt(math.fsum(x*x for x in dr)),
        velocity_m_s=math.sqrt(math.fsum(x*x for x in dv)),delta_position_m=dr,delta_velocity_m_s=dv)

def comparison(left,right,au,day,expected_count=None):
    common=sorted(set(left)&set(right))
    if expected_count is not None and len(common)!=expected_count:
        raise ValueError(f'Expected {expected_count} aligned nodes, got {len(common)}')
    rows=[dict(day=t,**delta(left[t],right[t],au,day)) for t in common]
    if not rows: raise ValueError('No common sample nodes')
    worst=max(rows,key=lambda r:r['position_m'])
    return dict(count=len(rows),left_count=len(left),right_count=len(right),
        pairing='exact common times; daily teacher grids intentionally contain only integer days',
        max_position_m=worst['position_m'],worst_day=worst['day'],
        max_velocity_m_s=max(r['velocity_m_s'] for r in rows),final=rows[-1],
        diagnostics=[r for r in rows if r['day'] in DAYS],numerical_1m_gate=max(r['position_m'] for r in rows)<=1.)

def relative(state,earth):
    return _state(tuple(a-b for a,b in zip(state,_flat(earth))))

def minimum(endpoints,earth,au,day):
    near=[(t,relative(s,earth.state_at(t))) for t,s in endpoints if 98<=t<=107]
    if len(near)<2: raise ValueError('Encounter is not covered')
    candidates=[]
    for (t0,s0),(t1,s1) in zip(near,near[1:]):
        u,s=segment_closest_approach(s0,s1,t1-t0)
        candidates.append((math.dist(s.position,(0,0,0)), t0+u*(t1-t0), s,t1-t0))
    d,t,s,h=min(candidates,key=lambda v:(v[0],v[1]))
    return dict(distance_km=d*au,forecast_day=t,relative_speed_km_s=math.dist(s.velocity,(0,0,0))*au/day,
        containing_segment_seconds=h*day,method='cubic Hermite between saved endpoint states')

def analyze(root):
    frozen=json.loads((root/OUT/'freeze.json').read_text());check_hashes(root,frozen['hashes'])
    cctx,ctx,planets,sun,rt,parent=build_context(root)
    records={}
    for name in ('annual','long','local'):
        for setting in ('coarse','fine'):
            key=name+'__'+setting;path=root/OUT/'checkpoints'/(key+'.json')
            row=json.loads(path.read_text())
            if row['fingerprint']!=frozen['fingerprint'] or digest({k:v for k,v in row.items() if k!='payload_sha256'})!=row['payload_sha256']:
                raise ValueError('Frozen checkpoint mismatch')
            force,convert,initial,times,offset,origin=scenario(cctx,ctx,planets,sun,name)
            if [t for t,s in row['samples']]!=times or list(_flat(initial))!=row['initial_native_state']:
                raise ValueError('Initial/sample alignment failed')
            if len(row['native_endpoints'])!=len(row['endpoints']):raise ValueError('Endpoint length mismatch')
            for (t,s),(u,h) in zip(row['native_endpoints'],row['endpoints'],strict=True):
                if t!=u or list(_flat(convert(t,_state(s))))!=h:raise ValueError('Native conversion mismatch')
            row['day_map']={t+row['forecast_day_offset']:s for t,s in row['samples']}
            row['day_endpoints']=[(t+row['forecast_day_offset'],s) for t,s in row['endpoints']]
            records[key]=row
    au,day=ctx['au'],ctx['day']
    result={'fingerprint':frozen['fingerprint'],'comparisons':{},'teacher':{},'encounters':{},'earth_frames':{},
        'scope':'inspected Apophis numerical diagnosis; local restart is evaluator-only',
        'covariance_ensemble_convergence_checked':False,'calibrated_uncertainty':False}
    for name in ('annual','long','local'):
        left,right=records[name+'__coarse']['day_map'],records[name+'__fine']['day_map']
        result['comparisons'][name+'_coarse_fine']=comparison(left,right,au,day,len(left))
    oldspk=json.loads((root/'outputs/apophis_spk_audit/matrix.json').read_text())
    for setting in ('ultra','extreme','rk4_fine'):
        old=next(r for r in oldspk['records'] if r['key']=='all_direct__'+setting)
        oldmap=dict(zip(old['requested_times_relative_days'],old['requested_states'],strict=True))
        result['comparisons']['annual_fine_vs_'+setting]=comparison(records['annual__fine']['day_map'],oldmap,au,day,797)
    for setting in ('ultra','extreme'):
        old=json.loads((root/'outputs/apophis_covariance/checkpoints'/('nominal_'+setting+'.json')).read_text())
        oldmap={s['day']-2922.:s['state'] for s in old['samples']}
        result['comparisons']['long_fine_vs_'+setting]=comparison(records['long__fine']['day_map'],oldmap,au,day,895)
    teacher={r['epoch_jd_tdb']-ctx['origin']:_flat(state_from_row(r)) for r in ctx['refs']['annual_daily']}
    earth=next(p.ephemeris for p in planets if p.body_id=='399')
    for key,row in records.items():
        result['teacher'][key]=comparison(row['day_map'],teacher,au,day,267 if row['scenario']=='local' else 366)
        result['teacher'][key].pop('numerical_1m_gate')
        result['encounters'][key]=minimum(row['day_endpoints'],earth,au,day)
        rel={t:relative(row['day_map'][t],earth.state_at(t)) for t in (99,102,103,106) if t in row['day_map']}
        frames={str(t):dict(position_km=[v*au for v in s.position],velocity_km_s=[v*au/day for v in s.velocity],
                          asteroid_teacher_difference_same_earth_source=delta(row['day_map'][t],teacher[t],au,day)) for t,s in rel.items()}
        if 99 in rel and 106 in rel:
            vi,vo=rel[99].velocity,rel[106].velocity
            cosine=math.fsum(a*b for a,b in zip(vi,vo))/(math.dist(vi,(0,0,0))*math.dist(vo,(0,0,0)))
            frames['finite_interval_turn_angle_deg']=math.degrees(math.acos(max(-1,min(1,cosine))))
            frames['angle_is_asymptotic_scattering_angle']=False
        result['earth_frames'][key]=frames
    ref=relative_rows(ctx['refs']['recent_refined'],ctx['origin'],ctx['calendar'],'calendar')
    result['encounters']['matched_recent_refined']=minimum([(r['epoch_relative_days'],_flat(state_from_row(r))) for r in ref],earth,au,day)
    result['comparisons']['annual_local_fine']=comparison(records['annual__fine']['day_map'],records['local__fine']['day_map'],au,day)
    result['verified_requested_states']=sum(len(r['samples']) for r in records.values())
    result['verified_native_endpoints']=sum(len(r['endpoints']) for r in records.values())
    result['checkpoint_hashes']={p.relative_to(root).as_posix():sha(p) for p in sorted((root/OUT/'checkpoints').glob('*.json'))}
    result['analyzer_sha256']=sha(Path(__file__))
    immutable_json(root/OUT/'analysis.json',result)
    print(json.dumps({k:{name:row['max_position_m'] for name,row in result[k].items()} for k in ('comparisons','teacher')},indent=2))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('.'));a=p.parse_args();analyze(a.root.resolve())

"""Frozen, bounded Earth-J3/J4 and target finite-size rollout audit."""
import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import platform
import sys
import time

from extended_body_forces import zonal_acceleration, extended_body_quadrupole_acceleration
from ng_inputs_v2 import load_ng_input
from orbit_baselines import add, subtract, norm
from planetary_dynamics import combine_accelerations
from prepare_fresh_holdout import immutable_json, sha, check_hashes
from relative_time_dynamics import relative_rows, relative_perturbers, build_relative_force
from run_apophis_reference_time import source_closure, evaluate
from run_apophis_solver_audit import _old_inputs, _old_planets, _target_rows, _merge_rows, _run_dp, _flat, _state, _shift
from run_apophis_moon_venus_v1_1 import _new_rows, _make_arm_planets, _normalize_trace
from run_b3plus_ablation import load_small_body_perturbers
from run_eda import parse_horizons
from run_physics_baselines import state_from_row

CONFIG='configs/apophis_shape_weak_force.json'


def context(root,freeze=True):
    config=json.loads((root/CONFIG).read_text())
    prior_freeze=json.loads((root/config['previous_freeze']).read_text());check_hashes(root,prior_freeze['hashes'])
    previous=json.loads((root/config['previous_matrix']).read_text())
    if previous['freeze_sha256']!=sha(root/config['previous_freeze']):raise ValueError('Previous freeze mismatch')
    base=json.loads((root/'configs/apophis_solver_audit.json').read_text())
    data=json.loads((root/base['data_config']).read_text());fc=json.loads((root/base['force_config']).read_text());event=json.loads((root/base['event_config']).read_text())
    source=json.loads((root/'configs/apophis_moon_venus_v1_1.json').read_text())
    daily,refined=_old_inputs(root,data,event);constants=data['constants'];au,day=constants['au_km'],constants['day_s']
    mu=constants['mu_sun_km3_s2']*day**2/au**3
    absolute=_old_planets(root,data,daily,refined)+load_small_body_perturbers(root,fc,mu)
    new=_new_rows(root,source);arm=next(a for a in source['arms'] if a['id']=='earthmoon_venus15m')
    absolute=_make_arm_planets(arm,absolute,new,daily,constants);entries=[]
    for p in absolute:
        if p.body_id.startswith('sb:'):rows=parse_horizons(root/fc['small_body_ephemeris_directory']/f'asteroid_{p.body_id[3:]}.json',p.body_id[3:],p.name)[1]
        elif p.body_id in ('399','301'):rows=_merge_rows(daily[p.body_id],new[p.body_id])
        elif p.body_id=='299':rows=new['299']
        else:rows=daily[p.body_id]
        if tuple(state_from_row(r) for r in rows)!=p.ephemeris.states:raise ValueError('Changed force states')
        if tuple(r['epoch_jd_tdb'] for r in rows)!=p.ephemeris.epochs_jd_tdb:raise ValueError('Changed force knots')
        entries.append((p,rows))
    annual,_=_target_rows(root,data,event,365);origin=annual[0]['epoch_jd_tdb'];calendar=annual[0]['epoch_tdb']
    planets=relative_perturbers(entries,origin,calendar,'calendar')
    refs={k:parse_horizons(root/v,'99942','Apophis')[1] for k,v in previous['input_validation']['paths'].items()}
    refs['long_repeat']=parse_horizons(root/'data/raw/apophis_reference_time/asteroid_99942_long_repeat.json','99942','Apophis')[1]
    moments=json.loads((root/config['shape_moments']).read_text());manifest=json.loads((root/config['shape_manifest']).read_text())
    for r in manifest['files']:
        if sha(root/r['path'])!=r['sha256'] or (root/r['path']).stat().st_size!=r['bytes']:raise ValueError('Shape raw changed')
    if moments['manifest_sha256']!=sha(root/config['shape_manifest']):raise ValueError('Shape moments provenance')
    if moments['producer_sha256']!=sha(root/'src/prepare_apophis_shape.py'):raise ValueError('Shape moment producer changed')
    ng=load_ng_input(root/'data/raw/horizons/asteroid_99942.json')
    if ng is None:raise ValueError('Nominal NG missing')
    force=build_relative_force(origin,planets,mu,fc['speed_of_light_km_s']*day/au,ng.parameters,base['earth_j2']['reference_radius_km']/au,base['earth_j2']['j2'])
    paths=[CONFIG,config['contract'],config['previous_freeze'],config['previous_matrix'],config['shape_moments'],config['shape_manifest'],'src/prepare_apophis_shape.py']
    paths+=source_closure(root,['run_apophis_shape_weak_force']);paths +=[r['path'] for r in manifest['files']]
    hashes={**prior_freeze['hashes'],**{p:sha(root/p) for p in sorted(set(paths))}}
    runtime={'executable':sys.executable,'version':sys.version,'platform':platform.platform()}
    payload={'hashes':hashes,'runtime':runtime};fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    freeze_path=root/config['output_directory']/'freeze.json'
    if freeze:
        if freeze_path.exists():
            old=json.loads(freeze_path.read_text())
            if old['fingerprint']!=fingerprint or any(old[k]!=payload[k] for k in payload):raise ValueError('New audit freeze changed')
        else:immutable_json(freeze_path,{**payload,'fingerprint':fingerprint,'created_utc':dt.datetime.now(dt.timezone.utc).isoformat()})
    times=[r['epoch_relative_days'] for r in relative_rows(annual,origin,calendar,'calendar')]
    S=tuple(tuple(x/au**2 for x in row) for row in moments['second_moment_km2'])
    return dict(config=config,base=base,previous=previous,annual=annual,refs=refs,origin=origin,calendar=calendar,planets=planets,
        force=force,au=au,day=day,mu=mu,times=times,S=S,moments=moments,fingerprint=fingerprint,freeze_path=freeze_path)


def pole(origin,t):
    T=((origin-2451545.)+t)/36525.;ra=math.radians(-.641*T);dec=math.radians(90.-.557*T)
    return (math.cos(dec)*math.cos(ra),math.cos(dec)*math.sin(ra),math.sin(dec))


def make_force(ctx,arm):
    if arm=='baseline':return ctx['force']
    if arm in ('j3','j4','j3j4'):
        degrees={'j3':(3,),'j4':(4,),'j3j4':(3,4)}[arm];z=ctx['config']['earth_zonals']
        earth=next(p for p in ctx['planets'] if p.body_id=='399');mu=z['mu_km3_s2']*ctx['day']**2/ctx['au']**3;radius=z['reference_radius_km']/ctx['au']
        def correction(t,r,v):
            e=earth.ephemeris.state_at(t).position;axis=pole(ctx['origin'],t);years=((ctx['origin']-2451545.)+t)/365.25
            a=(0.,0.,0.)
            for degree in degrees:
                c=z[f'c{degree}0_j2000']+z[f'c{degree}0_rate_per_julian_year']*years;jn=-math.sqrt(2*degree+1)*c
                direct=zonal_acceleration(subtract(r,e),axis,mu,radius,degree,jn)
                indirect=zonal_acceleration(tuple(-x for x in e),axis,mu,radius,degree,jn)
                a=add(a,subtract(direct,indirect))
            return a
    else:
        order={'shape_xyz':(0,1,2),'shape_yzx':(1,2,0),'shape_zxy':(2,0,1)}
        if arm=='shape_sphere':
            k=sum(ctx['S'][i][i] for i in range(3))/3;S=tuple(tuple(k if i==j else 0. for j in range(3)) for i in range(3))
        elif arm in order:
            p=order[arm];S=tuple(tuple(ctx['S'][i][j] for j in p) for i in p)
        else:raise ValueError('Unknown force arm')
        def correction(t,r,v):
            a=extended_body_quadrupole_acceleration(r,ctx['mu'],S)
            for p in ctx['planets']:
                a=add(a,extended_body_quadrupole_acceleration(subtract(r,p.ephemeris.state_at(t).position),p.mu_au3_d2,S))
            return a
    return combine_accelerations(ctx['force'],correction)


def run_one(root,ctx,arm,source,label):
    key=f'{arm}__{source}__{label}';path=root/ctx['config']['output_directory']/'checkpoints'/(key+'.json')
    if path.exists():
        r=json.loads(path.read_text())
        if r['fingerprint']!=ctx['fingerprint']:raise ValueError('Checkpoint mismatch')
        return r
    tolerance=next(t for t in ctx['base']['dopri54_tolerances'] if t['label']==label)
    initial_row=ctx['annual'][0] if source=='old' else ctx['refs']['annual_hourly'][0]
    if initial_row['epoch_tdb']!=ctx['calendar']:raise ValueError('Initial epoch mismatch')
    initial=state_from_row(initial_row);force=make_force(ctx,arm);start=time.perf_counter()
    predictions,meta=_run_dp(initial,0.,ctx['times'],force,tolerance,ctx['base']['dopri54_max_step_days'])
    elapsed=time.perf_counter()-start;trace=_normalize_trace(meta,0.,initial,'dopri54')
    if len(predictions)!=797 or _flat(predictions[0])!=_flat(initial):raise ValueError('Bad prediction dimensions/initial')
    if any(not math.isfinite(x) for s in predictions for x in _flat(s)):raise ValueError('Nonfinite prediction')
    primary,comparisons=evaluate(predictions,ctx['annual'],ctx['refs'],ctx['au'],ctx['day'])
    states=[list(_flat(s)) for s in predictions]
    if arm in ('baseline','shape_sphere'):
        old=next(r for r in ctx['previous']['records'] if r['mode']=='relative_calendar_knots' and r['solver']=='dopri54' and r['initial_source']==source and r['setting']['label']==label)
        if states!=old['requested_states']:raise ValueError('Baseline/zero control not exact')
    record={'key':key,'arm':arm,'initial_source':source,'setting':tolerance,'solver':'dopri54','mode':'relative_calendar_knots',
        'origin_jd_tdb':ctx['origin'],'origin_calendar_tdb':ctx['calendar'],'initial_state':list(_flat(initial)),
        'requested_times_relative_days':ctx['times'],'requested_states':states,'accepted_time_basis':'relative_days_since_start',
        'accepted_endpoints':trace,'primary_old_grid':primary,'reference_comparisons':comparisons,
        'solver_stats':{k:v for k,v in meta.items() if k not in ('requested_states','accepted_endpoints')},
        'runtime_seconds':elapsed,'fingerprint':ctx['fingerprint']}
    immutable_json(path,record)
    print(json.dumps({'completed':key,'old_km':comparisons['old_daily']['max_grid_position_error_km'],'new_km':comparisons['annual_daily']['max_grid_position_error_km']}),flush=True)
    return record


def shape_force_bound(ctx,record):
    traceS=sum(ctx['S'][i][i] for i in range(3));peak=(0.,0.);min_distance=math.inf
    for t,values in zip(ctx['times'],record['requested_states']):
        r=values[:3];bound=3*traceS*ctx['mu']/norm(r)**4
        for p in ctx['planets']:
            d=norm(subtract(r,p.ephemeris.state_at(t).position));min_distance=min(min_distance,d)
            bound+=3*traceS*p.mu_au3_d2/d**4
        if bound>peak[1]:peak=(t,bound)
    return {'max_sampled_acceleration_bound_m_s2':peak[1]*ctx['au']*1000/ctx['day']**2,
        'peak_relative_day':peak[0],'minimum_sampled_point_source_distance_km':min_distance*ctx['au'],
        'radius_over_minimum_distance':ctx['moments']['max_radius_from_centroid_km']/(min_distance*ctx['au']),
        'scope':'orientation-independent quadrupole force bound on baseline output grid; not a propagated error bound'}


def run(root):
    ctx=context(root);records=[];config=ctx['config']
    for arm in config['geopotential_arms']:
        for source in config['initial_sources']:
            for label in config['dp_labels']:records.append(run_one(root,ctx,arm,source,label))
    for arm in config['shape_arms']:
        for label in config['dp_labels']:records.append(run_one(root,ctx,arm,config['shape_initial_source'],label))
    pairs=[]
    for i,a in enumerate(records):
        for b in records[i+1:]:
            if a['initial_source']==b['initial_source'] and (a['arm']==b['arm'] or a['setting']==b['setting']):
                pairs.append({'left':a['key'],'right':b['key'],**_shift([_state(s) for s in a['requested_states']],[_state(s) for s in b['requested_states']],ctx['au'],ctx['day'])})
    baseline=next(r for r in records if r['key']=='baseline__annual_hourly__extreme')
    result={'fingerprint':ctx['fingerprint'],'freeze_sha256':sha(ctx['freeze_path']),'records':records,'paired_shifts':pairs,
        'shape_force_bound':shape_force_bound(ctx,baseline),'scope':'post-hoc force and fixed-attitude shape sensitivity; not actual spin prediction or selector validation'}
    immutable_json(root/config['output_directory']/'matrix.json',result)
    print(json.dumps({'complete':len(records),'paired_shifts':len(pairs),'shape_force_bound':result['shape_force_bound']}),flush=True)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=Path('.'));run(p.parse_args().root.resolve())

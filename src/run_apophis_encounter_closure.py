"""Versioned independent numerical Apophis encounter diagnosis."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

from barycentric_eih import build_barycentric_eih
from extrapolation_integrator import integrate_extrapolation
from orbit_baselines import State
from prepare_fresh_holdout import immutable_json, sha, check_hashes
from run_apophis_covariance import build_context as covariance_context, force_for
from run_apophis_reference_time import source_closure
from run_apophis_solver_audit import _flat, _state
from run_apophis_spk_audit import context as spk_context, ephemerides
from run_physics_baselines import state_from_row
from sbdb_covariance import state_from_covariance

CONTRACT = 'docs/APOPHIS_ENCOUNTER_CLOSURE_CONTRACT.md'
OUT = 'outputs/apophis_encounter_closure'
SETTINGS = {
    'coarse':dict(rtol=1e-15, atol_position=1e-18, atol_velocity=1e-19, max_step=.5),
    'fine':dict(rtol=2e-16, atol_position=2e-19, atol_velocity=2e-20, max_step=.25)}

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

def build_context(root):
    cctx = covariance_context(root)
    ctx, config, previous, backend, output, freeze, fingerprint = spk_context(root)
    parent = json.loads((root/'outputs/apophis_covariance/freeze.json').read_text())
    check_hashes(root,parent['hashes'])
    runtime = dict(executable=sys.executable,version=sys.version,platform=platform.platform())
    if runtime != parent['runtime']:
        raise ValueError('Frozen runtime mismatch')
    planets, sun = ephemerides(ctx, backend, 'all_direct')
    return cctx,ctx,planets,sun,runtime,parent

def freeze(root, runtime, parent):
    paths = source_closure(root,['run_apophis_encounter_closure']) + [CONTRACT,
        'tests/test_extrapolation_integrator.py', 'outputs/apophis_spk_audit/matrix.json',
        'outputs/apophis_covariance/matrix.json', 'outputs/apophis_covariance/freeze.json']
    hashes = {**parent['hashes'], **{p:sha(root/p) for p in paths}}
    payload = dict(hashes=hashes,runtime=runtime,settings=SETTINGS,scenarios=['annual','long','local'])
    fingerprint = digest(payload)
    path = root/OUT/'freeze.json'
    if path.exists():
        old = json.loads(path.read_text())
        if old['fingerprint'] != fingerprint:
            raise ValueError('Experiment changed after freeze')
    else:
        immutable_json(path,{**payload,'fingerprint':fingerprint,'created_utc':dt.datetime.now(dt.timezone.utc).isoformat()})
    return fingerprint

def scenario(cctx,ctx,planets,sun,name):
    if name == 'long':
        force,convert,ng = force_for(cctx,[0.]*8)
        return force,convert,state_from_covariance(cctx['parsed'],ctx['mu']), cctx['times'],0.,2459215.5
    force,convert = build_barycentric_eih(ctx['origin'], planets,ctx['mu'],ctx['c'],ctx['ng'],
        ctx['base']['earth_j2']['reference_radius_km']/ctx['au'],ctx['base']['earth_j2']['j2'],sun,gr='eih_sun')
    start = 99. if name == 'local' else 0.
    reference = next(row for row in ctx['refs']['annual_hourly'] if row['epoch_jd_tdb'] == ctx['origin']+start)
    rh = state_from_row(reference)
    offset = convert(start,State((0.,)*3,(0.,)*3))
    initial = _state(tuple(a-b for a,b in zip(_flat(rh),_flat(offset))))
    def shifted_force(t,r,v): return force(start+t,r,v)
    def shifted_convert(t,s): return convert(start+t,s)
    return shifted_force,shifted_convert,initial,[t-start for t in ctx['times'] if t>=start],start,ctx['origin']+start

def run(root,chosen='all',prepare=False):
    cctx,ctx,planets,sun,runtime,parent = build_context(root)
    fingerprint = freeze(root,runtime,parent)
    if prepare:
        print(json.dumps({'frozen':fingerprint,'runs':6}),flush=True);return
    for name in ('annual','long','local'):
        for setting,kwargs in SETTINGS.items():
            key = name+'__'+setting
            if chosen not in ('all',key,name): continue
            path = root/OUT/'checkpoints'/(key+'.json')
            if path.exists():
                row = json.loads(path.read_text())
                if row['fingerprint'] != fingerprint or row['payload_sha256'] != digest({k:v for k,v in row.items() if k!='payload_sha256'}):
                    raise ValueError('Checkpoint mismatch')
                continue
            force,convert,initial,times,offset,origin = scenario(cctx,ctx,planets,sun,name)
            print(json.dumps({'starting':key}),flush=True)
            started = time.perf_counter()
            result = integrate_extrapolation(force,_flat(initial),times,
                breakpoints=range(1,int(times[-1])+1),**kwargs)
            elapsed = time.perf_counter()-started
            row = dict(id=key,scenario=name,setting=kwargs,origin_jd_tdb=origin,
                forecast_day_offset=offset if name!='long' else -2922.,initial_native_state=_flat(initial),
                native_samples=result.samples,native_endpoints=result.accepted_endpoints,
                samples=[(t,_flat(convert(t,_state(s)))) for t,s in result.samples],
                endpoints=[(t,_flat(convert(t,_state(s)))) for t,s in result.accepted_endpoints],
                solver_stats=result.stats,runtime_seconds=elapsed,fingerprint=fingerprint,
                diagnostic_only_local_restart=name=='local',created_utc=dt.datetime.now(dt.timezone.utc).isoformat())
            row['payload_sha256']=digest(row)
            immutable_json(path,row)
            print(json.dumps({'complete':key,'seconds':elapsed,'steps':result.stats['accepted_steps']}),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('.'))
    p.add_argument('--scenario',default='all');p.add_argument('--prepare-only',action='store_true')
    a=p.parse_args();run(a.root.resolve(),a.scenario,a.prepare_only)

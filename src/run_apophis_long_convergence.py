"""Two prespecified tighter controls for the covariance-epoch nominal."""
import argparse
import datetime as dt
import json
from pathlib import Path
import time

from extrapolation_integrator import integrate_extrapolation
from precise_dopri import integrate_precise_dopri54
from prepare_fresh_holdout import immutable_json, sha, check_hashes
from run_apophis_encounter_closure import build_context, scenario, digest
from run_apophis_reference_time import source_closure
from run_apophis_solver_audit import _flat,_state

OUT='outputs/apophis_long_convergence'
SETTINGS={'extrap':dict(rtol=1e-16,atol_position=1e-19,atol_velocity=1e-20,max_step=.125),
          'dp':dict(rtol=1e-16,atol_position=1e-18,atol_velocity=1e-19,max_step=.03125)}

def run(root,solver):
    cctx,ctx,planets,sun,runtime,parent=build_context(root)
    p=json.loads((root/'outputs/apophis_encounter_closure/freeze.json').read_text())
    check_hashes(root,p['hashes'])
    paths=source_closure(root,['run_apophis_long_convergence'])+['docs/APOPHIS_LONG_CONVERGENCE_CONTRACT.md',
        'outputs/apophis_encounter_closure/freeze.json','outputs/apophis_encounter_closure/analysis.json']
    payload=dict(hashes={**p['hashes'],**{k:sha(root/k) for k in paths}},runtime=runtime,settings=SETTINGS)
    fingerprint=digest(payload);fp=root/OUT/'freeze.json'
    if fp.exists():
        if json.loads(fp.read_text())['fingerprint']!=fingerprint:raise ValueError('Freeze changed')
    else:immutable_json(fp,{**payload,'fingerprint':fingerprint,'created_utc':dt.datetime.now(dt.timezone.utc).isoformat()})
    for key,kwargs in SETTINGS.items():
        if solver not in ('all',key):continue
        path=root/OUT/(key+'.json')
        if path.exists():
            old=json.loads(path.read_text())
            if old['fingerprint']!=fingerprint or old['payload_sha256']!=digest({k:v for k,v in old.items() if k!='payload_sha256'}):raise ValueError('Checkpoint mismatch')
            continue
        force,convert,initial,times,offset,origin=scenario(cctx,ctx,planets,sun,'long')
        print(json.dumps({'starting':key}),flush=True);start=time.perf_counter()
        if key=='extrap':r=integrate_extrapolation(force,_flat(initial),times,breakpoints=range(1,3287),**kwargs)
        else:r=integrate_precise_dopri54(force,0.,_flat(initial),times,breakpoints=range(1,3287),**kwargs)
        elapsed=time.perf_counter()-start
        row=dict(solver=key,setting=kwargs,fingerprint=fingerprint,origin_jd_tdb=origin,
            initial_native_state=_flat(initial),native_samples=r.samples,native_endpoints=r.accepted_endpoints,
            samples=[(t,_flat(convert(t,_state(s)))) for t,s in r.samples],
            solver_stats=r.stats,runtime_seconds=elapsed)
        row['payload_sha256']=digest(row);immutable_json(path,row)
        print(json.dumps({'completed':key,'seconds':elapsed,'steps':r.stats['accepted_steps']}),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('.'));p.add_argument('--solver',choices=['all','extrap','dp'],default='all')
    a=p.parse_args();run(a.root.resolve(),a.solver)

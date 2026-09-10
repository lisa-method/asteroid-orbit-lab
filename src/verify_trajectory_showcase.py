"""Audit displayed coordinates against saved forecast and raw reference nodes."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path

from orbit_baselines import norm, subtract
from run_eda import parse_horizons
from build_trajectory_showcase import AU_KM, PLOT_ASPECT


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(root, directory):
    audit=json.loads((directory/'audit.json').read_text())
    if audit['builder_sha256'] != sha(root/'src/build_trajectory_showcase.py'):
        raise ValueError('builder changed; rebuild presentation scenes')
    for path,digest in audit['source_hashes'].items():
        if sha(root/path) != digest:
            raise ValueError('source hash mismatch: '+path)
    refs={}
    for path in audit['source_hashes']:
        if '/data/raw/' in '/'+path and ('asteroid_' in path or 'apophis_earth_2029_99942' in path) and path.endswith('.json'):
            for r in parse_horizons(root/path,audit['object_id'],audit['object_id'])[1]:
                jd=r['epoch_jd_tdb']
                if jd in refs and refs[jd]['r'] != r['r']:
                    raise ValueError('reference sources disagree at a displayed-compatible epoch')
                refs[jd]=r
    if audit['source']=='confirmation100':
        from run_development_benchmark import _interpolate_endpoints
        from run_selector_confirmation import _trace_states
        path=next(p for p in audit['source_hashes'] if p.endswith('_production.json'))
        at=_interpolate_endpoints(_trace_states(json.loads((root/path).read_text())))
        pred_at=lambda t: at(t).position
    else:
        path=next(p for p in audit['source_hashes'] if p.endswith('annual__fine.json'))
        states=dict(json.loads((root/path).read_text())['samples'])
        pred_at=lambda t: states[t][:3]
    scenes={}
    for name,details in audit['scenes'].items():
        path=directory/(name+'.json')
        if sha(path) != audit['scene_sha256'][name]:
            raise ValueError('scene hash mismatch')
        scene=json.loads(path.read_text())
        basis=details['basis']; gain=details['amplification_gain']; scale=details['unit_scale']
        for i in range(3):
            for j in range(3):
                if abs(math.fsum(a*b for a,b in zip(basis[i],basis[j]))-float(i==j)) > 1e-12:
                    raise ValueError('projection is not orthonormal')
        bounds=scene['limits']
        if abs((bounds[1]-bounds[0])/(bounds[3]-bounds[2])-PLOT_ASPECT)>1e-10:
            raise ValueError('unequal spatial axis scale')
        count=len(scene['times_days'])
        for i,t in enumerate(scene['times_days']):
            jd=details['raw_epochs_jd_tdb'][i]
            r=refs[jd]['r']; p=pred_at(t)
            if list(p)!=list(details['prediction_heliocentric_au'][i]) or list(r)!=list(details['reference_heliocentric_au'][i]):
                raise ValueError('frame differs from raw reference or stored prediction')
            error=norm(subtract(p,r))*AU_KM
            if abs(error-scene['error_km'][i])>1e-9:
                raise ValueError('displayed true error differs')
            center=details['common_center_au'][i]; origin=details['local_origin_au']
            for k in range(2):
                ref=math.fsum((r[a]-center[a]-origin[a])*basis[k][a] for a in range(3))*scale
                residual=math.fsum((p[a]-r[a])*basis[k][a] for a in range(3))*scale
                expected=ref+gain*residual
                # A tiny arithmetic-order tolerance, far below one display pixel.
                if abs(ref-scene['reference_xy'][i][k])>1e-6 or abs(expected-scene['prediction_xy'][i][k])>1e-6:
                    raise ValueError('incorrect coordinate transform or residual magnification')
            if scene['dates'][i] != refs[jd]['epoch_tdb'].replace('T',' ')+' TDB':
                raise ValueError('display date differs from raw epoch')
        if name=='overview' and gain!=1:
            raise ValueError('overview must preserve physical separation')
        scenes[name]=dict(frames=count,gain=gain,first_day=scene['times_days'][0],last_day=scene['times_days'][-1],
                          maximum_displayed_true_error_km=max(scene['error_km']),scene_sha256=sha(path))
    result=dict(passed=True,source=audit['source'],object_id=audit['object_id'],scenes=scenes,
                source_files_checked=len(audit['source_hashes']),audit_sha256=sha(directory/'audit.json'),
                verifier_sha256=sha(Path(__file__)),scope='presentation audit; no new propagation or validation score')
    (directory/'verification.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path);a=p.parse_args()
    print(json.dumps(verify(Path.cwd(),a.directory),indent=2))

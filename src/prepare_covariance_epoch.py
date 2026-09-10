"""Immutable old-epoch Horizons control for SBDB orbital-element conversion."""
from __future__ import annotations
import argparse
import datetime as dt
import json
from pathlib import Path
from download_jpl_pilot import build_horizons_url, download_one
from prepare_fresh_holdout import immutable_json, check_hashes, sha
from run_eda import parse_horizons

CONFIG='configs/covariance_epoch_check.json'

def prepare(root, download=False):
    config=json.loads((root/CONFIG).read_text())
    data=json.loads((root/'configs/eda_pilot_6.json').read_text())
    design=root/config['design']; raw=root/config['raw']; manifest=root/config['manifest']
    paths=[CONFIG,'configs/eda_pilot_6.json','src/prepare_covariance_epoch.py','src/download_jpl_pilot.py','src/run_eda.py']
    hashes={p:sha(root/p) for p in paths}
    if design.exists():
        old=json.loads(design.read_text());check_hashes(root,old['hashes'])
        if hashes!=old['hashes']:raise ValueError('Epoch download design changed')
    else:immutable_json(design,{'hashes':hashes,'created_utc':dt.datetime.now(dt.timezone.utc).isoformat()})
    url=build_horizons_url({**data,'time_range':config['time_range']},'99942',small_body=True)
    if manifest.exists():
        old=json.loads(manifest.read_text());row=old['files'][0]
        if row['source_url']!=url or row['path']!=config['raw'] or old['design_sha256']!=sha(design):raise ValueError('Epoch input provenance mismatch')
        if sha(raw)!=row['sha256'] or raw.stat().st_size!=row['bytes']:raise ValueError('Epoch raw changed')
    else:
        if raw.exists() or not download:raise ValueError('Missing/orphan epoch raw; explicit --download required')
        row=download_one({'kind':'horizons_asteroid','target_id':'99942','target_name':'Apophis','path':raw,'url':url})
        row.pop('reused',None);row.update(path=config['raw'],retrieved_at_utc=dt.datetime.now(dt.timezone.utc).isoformat())
        immutable_json(manifest,{'design_sha256':sha(design),'files':[row]})
    header,rows=parse_horizons(raw,'99942','Apophis')
    if len(rows)!=2 or rows[0]['epoch_jd_tdb']!=2459215.5 or rows[-1]['epoch_jd_tdb']!=2459216.5:raise ValueError('Epoch control dates mismatch')
    if not(header['center'].startswith('Sun (10)') and header['reference_frame']=='ICRF' and header['units']=='AU-D' and header['geometric'] and header['tdb']):raise ValueError('Epoch reference coordinate mismatch')
    return header,rows

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=Path('.'));p.add_argument('--download',action='store_true');a=p.parse_args()
    header,rows=prepare(a.root.resolve(),a.download);print(json.dumps({'header':header,'rows':len(rows)}))

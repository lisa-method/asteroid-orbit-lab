"""Two immutable anonymous ephemerides for a barycentric-velocity PN audit."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path

from download_jpl_pilot import build_horizons_url, download_one
from prepare_development_sample import atomic_json
from prepare_fresh_holdout import check_hashes, immutable_json, sha
from run_eda import parse_horizons

CONFIG = 'configs/apophis_eih_inputs.json'


def prepare(root, download=False):
    config = json.loads((root/CONFIG).read_text())
    design = root/config['design']
    paths = [CONFIG,'src/prepare_apophis_eih.py','src/download_jpl_pilot.py','src/run_eda.py']
    hashes = {p:sha(root/p) for p in paths}
    if design.exists():
        old = json.loads(design.read_text()); check_hashes(root,old['hashes'])
        if hashes != old['hashes']: raise ValueError('Input design changed')
    else:
        immutable_json(design,{'hashes':hashes,'created_utc':dt.datetime.now(dt.timezone.utc).isoformat()})
    manifest_path = root/config['manifest']
    old_manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {'files':[]}
    records = {r['name']:r for r in old_manifest['files']}
    created = old_manifest.get('created_utc',dt.datetime.now(dt.timezone.utc).isoformat())
    for query in config['queries']:
        name = query['name']; path = root/config['raw_directory']/(name+'.json')
        query_config = {**config,'coordinates':{**config['coordinates'],'center':query['center']}}
        url = build_horizons_url(query_config,query['id'],small_body=False)
        if name not in records:
            if path.exists(): raise ValueError('Orphan raw file')
            if not download: raise ValueError('Missing ephemeris; explicit --download required')
            row = download_one({'path':path,'kind':'horizons_body','target_id':query['id'],
                'target_name':query['target_name'],'url':url})
            row.pop('reused',None)
            row.update(name=name,path=path.relative_to(root).as_posix(),
                       retrieved_at_utc=dt.datetime.now(dt.timezone.utc).isoformat())
            records[name]=row
            atomic_json(manifest_path,{'created_utc':created,'complete':False,'files':list(records.values()),'design_sha256':sha(design)})
        row = records[name]
        if sha(path)!=row['sha256'] or path.stat().st_size!=row['bytes'] or row['source_url']!=url:
            raise ValueError('Input provenance changed')
        header, rows = parse_horizons(path,query['id'],query['target_name'])
        expected_center = 'Solar System Barycenter (0)' if query['center']=='500@0' else 'Sun (10)'
        if not (header['center'].startswith(expected_center) and header['reference_frame']=='ICRF'
                and header['units']=='AU-D' and header['geometric'] and header['tdb']):
            raise ValueError(f'Unexpected ephemeris conventions: {header}')
        if f"({query['id']})" not in header['target'] or len(rows)!=query['rows']:
            raise ValueError('Target or row count differs')
        dates = [dt.datetime.fromisoformat(r['epoch_tdb']) for r in rows]
        if dates[0] != dt.datetime.fromisoformat(config['time_range']['start']) or dates[-1] != dt.datetime.fromisoformat(config['time_range']['stop']):
            raise ValueError('Coverage mismatch')
        if any((b-a).total_seconds()!=3600 for a,b in zip(dates,dates[1:])):
            raise ValueError('Ephemeris cadence differs')
        if any(not math.isfinite(x) for r in rows for x in (*r['r'],*r['v'],r['epoch_jd_tdb'])):
            raise ValueError('Nonfinite ephemeris')
        row.update(header=header,rows=len(rows),coordinates=query_config['coordinates'],time_scale='TDB')
        if not old_manifest.get('complete'):
            atomic_json(manifest_path,{'created_utc':created,'complete':False,'files':list(records.values()),'design_sha256':sha(design)})
        print(json.dumps({'input':name,'rows':len(rows),'bytes':row['bytes']}),flush=True)
    result = {'created_utc':created,'complete':True,'files':list(records.values()),'design_sha256':sha(design)}
    if old_manifest.get('complete'):
        if result != old_manifest: raise ValueError('Completed manifest differs')
    else: atomic_json(manifest_path,result)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path('.')); p.add_argument('--download',action='store_true')
    args=p.parse_args(); prepare(args.root.resolve(),args.download)

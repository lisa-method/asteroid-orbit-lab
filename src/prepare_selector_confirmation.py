"""Metadata-only, object-disjoint confirmation cohort; anonymous serial JPL IO.

The existing v4 selector is immutable. Selection uses catalogue geometry and
orbit-quality metadata, never a target vector or measured forecast error.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
import urllib.parse

from download_jpl_pilot import fetch, write_immutable, build_horizons_url, download_one
from prepare_development_sample import atomic_json, rows
from prepare_selector_v4_holdout24 import _excluded_ids, check_hashes, _check_header, _regular_grid, _check_ng_pair
from run_eda import parse_horizons
from run_force_models_v2 import _runtime_environment

RAW = 'data/raw/selector_confirmation100'
OUT = 'outputs/selector_confirmation100'
SAMPLE = 'data/processed/selector_confirmation100/sample.json'
META_MANIFEST = 'data/checksums/selector_confirmation100_metadata_manifest.json'
MANIFEST = 'data/checksums/selector_confirmation100_manifest.json'
PARENT = 'outputs/selector_v4/method_freeze.json'
SEED = 'selector-confirmation100-2026-09-10-v1'
QUOTAS = {'Earth_tight':4, 'Earth_moderate':16, 'Venus':12, 'Mars':12,
          'Jupiter':12, 'inner_controls':16, 'outer_controls':16, 'NG':6, 'low_q':6}
TIMING_QUOTAS = {k: (2 if k == 'NG' else 1 if k == 'low_q' else 3) for k in QUOTAS}
BODIES = {'Earth':'399', 'Venus':'299', 'Mars':'4', 'Jupiter':'5'}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def immutable(path: Path, value: object) -> None:
    write_immutable(path, (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)+'\n').encode())


def score(purpose: str, value: str) -> str:
    return hashlib.sha256(f'{SEED}:{purpose}:{value}'.encode()).hexdigest()


def safe_id(designation: str) -> str:
    value = designation.strip().replace(' ', '_')
    if not re.fullmatch(r'[A-Za-z0-9_]+', value):
        raise ValueError(f'Unsupported asteroid designation: {designation}')
    return value


def queries() -> list[tuple[str,str,str]]:
    result=[]
    for body, query_body in [('Earth','Earth'),('Venus','Venus'),('Mars','Mars'),('Jupiter','Juptr')]:
        params={'body':query_body,'date-min':'2027-01-31','date-max':'2029-12-31',
                'dist-max':'1.0' if body == 'Jupiter' else '0.2','neo':'false',
                'kind':'a','fullname':'true','sort':'date'}
        result.append((body,'https://ssd-api.jpl.nasa.gov/cad.api?'+urllib.parse.urlencode(params),'1.5'))
    fields='spkid,pdes,full_name,a,e,q,i,epoch,orbit_id,condition_code,data_arc,A1,A2,A3,last_obs'
    filters={
        'inner_controls':['a|RG|2.1|2.5','q|GT|1.8','e|LT|0.2'],
        'outer_controls':['a|RG|3.0|3.4','q|GT|2.5','e|LT|0.2'],
        'NG':['A2|DF'],
        'low_q':['q|LT|0.5','q|GT|0.08','e|LT|0.99'],
    }
    for name, constraints in filters.items():
        params={'fields':fields,'sb-kind':'a','sort':'spkid','full-prec':'true',
                'sb-cdata':json.dumps({'AND':constraints+['condition_code|LE|3','data_arc|GE|365']},separators=(',',':'))}
        # No limit: hash selection spans the entire eligible catalogue.
        result.append((name,'https://ssd-api.jpl.nasa.gov/sbdb_query.api?'+urllib.parse.urlencode(params),'1.0'))
    return result


def cached(root: Path, relative: str, url: str, version: str) -> dict:
    path=root/relative
    mp=root/META_MANIFEST
    manifest=json.loads(mp.read_text()) if mp.exists() else {'files':[], 'created_utc':dt.datetime.now(dt.timezone.utc).isoformat()}
    by={x['path']:x for x in manifest['files']}
    if relative in by:
        rec=by[relative]
        if rec['source_url'] != url or not path.exists() or sha(path)!=rec['sha256'] or path.stat().st_size!=rec['bytes']:
            raise ValueError('Metadata provenance mismatch: '+relative)
        doc=json.loads(path.read_text())
    else:
        if path.exists():
            raise ValueError('Orphan metadata file: '+relative)
        payload=fetch(url); doc=json.loads(payload)
        if doc.get('signature',{}).get('version')!=version or 'error' in doc:
            raise ValueError(f'JPL metadata schema: {doc}')
        digest=write_immutable(path,payload)
        manifest['files'].append({'path':relative,'source_url':url,'sha256':digest,
           'bytes':len(payload),'retrieved_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
           'signature':doc['signature']})
        atomic_json(mp,manifest)
    return doc


def catalogues(root: Path, network: bool=False) -> dict:
    result={}
    for name,url,version in queries():
        relative=f'{RAW}/catalogues/{name}.json'
        doc=cached(root,relative,url,version) if network else json.loads((root/relative).read_text())
        if 'fields' not in doc or 'data' not in doc or int(doc['count'])!=len(doc['data']):
            raise ValueError('Incomplete catalogue: '+name)
        result[name]=doc
        if network: print(json.dumps({'catalogue':name,'rows':len(doc['data'])}),flush=True)
    return result


def excluded(root: Path) -> set[str]:
    blocked=_excluded_ids(root)
    blocked.update(str(o['id']) for o in json.loads((root/'data/processed/selector_v4_holdout24/sample.json').read_text())['objects'])
    return blocked


def event_candidates(doc: dict, stratum: str, blocked: set[str]) -> list[dict]:
    unique={}
    for r in rows(doc):
        des=str(r['des']).strip()
        if des in blocked: continue
        d=float(r['dist'])
        if stratum=='Earth_tight' and not 0 < d <= .01: continue
        if stratum=='Earth_moderate' and not .01 < d <= .05: continue
        day=dt.datetime.strptime(r['cd'].split()[0],'%Y-%b-%d').date()
        if not dt.date(2027,1,1) <= day-dt.timedelta(days=30) <= dt.date(2029,12,31): continue
        old=unique.get(des)
        if old is None or (d,float(r['jd']))<(float(old['dist']),float(old['jd'])): unique[des]=r
    # Cover distance and speed, without always selecting only the nearest.
    ordered=sorted(unique.values(),key=lambda r:(float(r['dist']),float(r['jd']),r['des']))
    bins=[ordered[len(ordered)*i//4:len(ordered)*(i+1)//4] for i in range(4)]
    for b in bins: b.sort(key=lambda r:score(stratum,r['des']))
    interleaved=[]
    for i in range(max(map(len,bins),default=0)):
        for b in bins:
            if i<len(b): interleaved.append(b[i])
    # Tight challenge includes the nearest two by metadata before quantile draw.
    if stratum=='Earth_tight':
        front=ordered[:2]
        return front+[r for r in interleaved if r['des'] not in {x['des'] for x in front}]
    return interleaved


def quality(doc: dict) -> tuple[bool,str]:
    try:
        orbit=doc['orbit']
        if int(orbit['condition_code'])>3: return False,'condition_code>3'
        if float(orbit['data_arc'])<365: return False,'data_arc<365d'
        if orbit.get('two_body') in ('T',True): return False,'two_body_orbit_fit'
        if doc['object'].get('kind')=='cu' or doc['object'].get('kind')=='cn': return False,'comet'
    except (KeyError,TypeError,ValueError): return False,'missing_quality_metadata'
    return True,'eligible'


def select(root: Path, network: bool=False) -> dict:
    parent=json.loads((root/PARENT).read_text()); check_hashes(root,parent['hashes'])
    if parent['runtime'] != _runtime_environment(): raise ValueError('Runtime differs from frozen v4')
    docs=catalogues(root,network=False); blocked=excluded(root); used=set(blocked)
    selected=[]; audit=[]; counts={}
    for stratum,quota in QUOTAS.items():
        body='Earth' if stratum.startswith('Earth') else stratum
        events=body in BODIES
        candidates=event_candidates(docs[body],stratum,blocked) if events else sorted(rows(docs[stratum]),key=lambda r:score(stratum,str(r['pdes'])))
        counts[stratum]=len(candidates)
        group=[]
        for r in candidates:
            des=str(r['des'] if events else r['pdes']).strip()
            if des in used: continue
            key=safe_id(des)
            rel=f'{RAW}/metadata/object_{key}.json'
            url='https://ssd-api.jpl.nasa.gov/sbdb.api?'+urllib.parse.urlencode({'sstr':des,'full-prec':'true'})
            doc=cached(root,rel,url,'1.3') if network else json.loads((root/rel).read_text())
            if 'object' not in doc or 'orbit' not in doc: raise ValueError('Ambiguous SBDB identity: '+des)
            canonical=str(doc['object']['des']).strip(); canonical_key=safe_id(canonical)
            ok,reason=quality(doc)
            if canonical in used: ok,reason=False,'duplicate_or_inspected_identity'
            audit.append({'requested_designation':des,'canonical':canonical,'stratum':stratum,'accepted':ok,'reason':reason,'metadata_path':rel})
            if not ok: continue
            if events:
                event_day=dt.datetime.strptime(r['cd'].split()[0],'%Y-%b-%d').date()
                event={'body_id':BODIES[body],'body_name':body,'jd':float(r['jd']),
                       'cd':r['cd'],'dist_au':float(r['dist']),'catalogue_row':r}
                start=(event_day-dt.timedelta(days=30)).isoformat()
            else: event=None; start='2029-01-01'
            obj={'id':canonical_key,'designation':canonical,'spkid':str(doc['object']['spkid']),
                 'name':doc['object']['fullname'],'stratum':stratum,'split':'confirmation100',
                 'start_date':start,'event':event,'metadata_path':rel,'catalogue_row':r,
                 'condition_code':doc['orbit']['condition_code'],'data_arc_days':doc['orbit']['data_arc']}
            group.append(obj); used.update((des,canonical))
            print(json.dumps({'selected_count':len(selected)+len(group),'stratum':stratum,'id':canonical_key}),flush=True)
            if len(group)==quota: break
        if len(group)!=quota: raise ValueError(f'Insufficient quality objects {stratum}: {len(group)}/{quota}')
        selected.extend(group)
    if len(selected)!=100 or len({x['spkid'] for x in selected})!=100: raise ValueError('Expected100 unique physical objects')
    timing=[]
    for stratum,n in TIMING_QUOTAS.items():
        timing.extend(x['id'] for x in sorted([x for x in selected if x['stratum']==stratum],key=lambda o:score('timing',o['id']))[:n])
    sample={'schema_version':1,'scope':'frozen-v4 independent confirmation100; stratified challenge, not population prevalence',
       'seed':SEED,'objects':selected,'quotas':QUOTAS,'timing_object_ids':timing,'timing_quotas':TIMING_QUOTAS,
       'selection_audit':audit,'candidate_counts':counts,'excluded_object_ids':sorted(blocked),
       'coordinates':json.loads((root/'configs/eda_pilot_6.json').read_text())['coordinates'],
       'time_scale':'TDB','daily_rows':366,'refined_rows':1153,'method_freeze_sha256':sha(root/PARENT),
       'metadata_sha256':{r['path']:r['sha256'] for r in json.loads((root/META_MANIFEST).read_text())['files']}}
    immutable(root/SAMPLE,sample)
    return sample


def jobs(root: Path,sample: dict) -> list[dict]:
    base=json.loads((root/'configs/eda_pilot_6.json').read_text()); result=[]
    for obj in sample['objects']:
        start=dt.date.fromisoformat(obj['start_date']); periods=[('daily',start,start+dt.timedelta(days=365),'1 d')]
        if obj['event']:
            day=dt.datetime.strptime(obj['event']['cd'].split()[0],'%Y-%b-%d').date()
            periods.append(('refined',day-dt.timedelta(days=2),day+dt.timedelta(days=2),'5 m'))
        for label,left,right,step in periods:
            cfg={**base,'time_range':{'start':left.isoformat(),'stop':right.isoformat(),'step':step,'time_scale':'TDB'}}
            result.append({'kind':'horizons_asteroid','target_id':obj['id'],'target_name':obj['name'],
              'path':root/RAW/'asteroids'/f"asteroid_{obj['id']}_{label}.json",
              'url':build_horizons_url(cfg,obj['designation'] if obj['designation'].isdigit() else 'DES='+obj['designation'],small_body=True),'label':label})
    return result


def validate(root: Path,sample: dict,all_jobs: list[dict],records: list[dict]) -> dict:
    if len(records)!=len(all_jobs): raise ValueError('Incomplete raw downloads')
    by_id={o['id']:o for o in sample['objects']}; parsed={}; common=0; ng={}
    for job,rec in zip(all_jobs,records,strict=True):
        path=job['path']; obj=by_id[job['target_id']]
        if rec['path']!=path.relative_to(root).as_posix() or rec['source_url']!=job['url'] or rec['sha256']!=sha(path) or rec['bytes']!=path.stat().st_size:
            raise ValueError('Raw manifest mismatch')
        doc=_check_header(path); header=doc['result'].split('$$SOE')[0]
        target=next(line for line in header.splitlines() if line.startswith('Target body name:'))
        # Match canonical number at start or exact provisional designation in parentheses.
        des=obj['designation']
        if des.isdigit():
            good=bool(re.search(r'^Target body name:\s*'+re.escape(des)+r'\b',target))
        else: good=('('+des+')') in target
        if not good: raise ValueError(f'Identity mismatch {des}: {target}')
        if any(not any(line.startswith(label) and line.rstrip().endswith('TDB') for line in header.splitlines()) for label in ('Start time','Stop  time')):
            raise ValueError('Time scale mismatch')
        rr=parse_horizons(path,obj['id'],obj['name'])[1]
        expected=366 if job['label']=='daily' else 1153
        if len(rr)!=expected or not _regular_grid(rr,1. if expected==366 else 5./1440.): raise ValueError('Grid mismatch')
        if rec['rows']!=len(rr) or rec['first_jd_tdb']!=rr[0]['epoch_jd_tdb'] or rec['last_jd_tdb']!=rr[-1]['epoch_jd_tdb']:
            raise ValueError('Manifest epoch/row metadata mismatch')
        if job['label']=='daily':
            left=dt.date.fromisoformat(obj['start_date']);right=left+dt.timedelta(days=365)
        else:
            day=dt.datetime.strptime(obj['event']['cd'].split()[0],'%Y-%b-%d').date()
            left=day-dt.timedelta(days=2);right=day+dt.timedelta(days=2)
        if rr[0]['epoch_jd_tdb'] != left.toordinal()+1721424.5 or rr[-1]['epoch_jd_tdb'] != right.toordinal()+1721424.5:
            raise ValueError('Reference interval differs from frozen request')
        parsed[(obj['id'],job['label'])]=rr
    for obj in sample['objects']:
        key=obj['id']; daily=root/RAW/'asteroids'/f'asteroid_{key}_daily.json'
        refined=root/RAW/'asteroids'/f'asteroid_{key}_refined.json' if obj['event'] else None
        dr=parsed[(key,'daily')]
        if refined:
            lookup={r['epoch_jd_tdb']:r for r in dr}; shared=0
            for row in parsed[(key,'refined')]:
                other=lookup.get(row['epoch_jd_tdb'])
                if other:
                    if row['r']!=other['r'] or row['v']!=other['v']: raise ValueError('Shared state mismatch')
                    shared+=1
            if shared!=5: raise ValueError('Expected five shared nodes')
            common+=shared
        ng[key]=_check_ng_pair(root,obj,daily,refined,dr[0]['epoch_jd_tdb'])
    return {'passed':True,'objects':len(by_id),'shared_daily_refined_nodes':common,'ng':ng}


def freeze(root: Path) -> dict:
    sample=json.loads((root/SAMPLE).read_text()); parent=json.loads((root/PARENT).read_text())
    check_hashes(root,parent['hashes']); check_hashes(root,sample['metadata_sha256'])
    if parent['runtime'] != _runtime_environment(): raise ValueError('Runtime differs from frozen v4')
    paths=[PARENT,SAMPLE,'src/prepare_selector_confirmation.py','src/run_selector_confirmation.py',
           'tests/test_prepare_selector_confirmation.py','tests/test_selector_confirmation.py',
           'docs/SELECTOR_CONFIRMATION100_CONTRACT.md','src/verify_selector_confirmation_preflight.py',
           OUT+'/runner_preflight.json']
    fp=root/OUT/'experiment_freeze.json'
    previous=json.loads(fp.read_text()) if fp.exists() else {}
    value={'schema_version':1,'runtime':parent['runtime'],
           'frozen_at_utc':previous.get('frozen_at_utc',dt.datetime.now(dt.timezone.utc).isoformat()),
           'hashes':{**parent['hashes'],**{p:sha(root/p) for p in paths},**sample['metadata_sha256']},
           'scope':'confirmation100; fixed selector v4; sample/runner frozen before target vectors'}
    immutable(fp,value)
    return {'frozen':True,'files':len(value['hashes'])}


def download(root: Path) -> dict:
    fr=json.loads((root/OUT/'experiment_freeze.json').read_text()); check_hashes(root,fr['hashes'])
    sample=json.loads((root/SAMPLE).read_text()); all_jobs=jobs(root,sample)
    mp=root/MANIFEST; old=json.loads(mp.read_text()) if mp.exists() else {'files':[]}
    known={r['path']:r for r in old['files']}; records=[]
    expected={j['path'].relative_to(root).as_posix() for j in all_jobs}
    if set(known)-expected: raise ValueError('Unexpected manifest files')
    if old.get('complete'):
        return validate(root,sample,all_jobs,old['files'])
    for j in all_jobs:
        relative=j['path'].relative_to(root).as_posix()
        if relative in known:
            rec=known[relative]
            if rec['sha256']!=sha(j['path']) or rec['source_url']!=j['url']: raise ValueError('Changed raw input')
        else:
            if j['path'].exists(): raise ValueError('Orphan raw target file')
            rec=download_one(j);rec.pop('reused',None);rec['path']=relative
            rec['retrieved_at_utc']=dt.datetime.now(dt.timezone.utc).isoformat()
            rr=parse_horizons(j['path'],j['target_id'],j['target_name'])[1]
            rec.update(rows=len(rr),first_jd_tdb=rr[0]['epoch_jd_tdb'],last_jd_tdb=rr[-1]['epoch_jd_tdb'],
                       coordinates=sample['coordinates'],time_scale='TDB')
        records.append(rec)
        manifest={'files':records,'complete':False,'sample_sha256':sha(root/SAMPLE),
                  'method_freeze_sha256':sample['method_freeze_sha256'],
                  'experiment_freeze_sha256':sha(root/OUT/'experiment_freeze.json')}
        atomic_json(mp,manifest)
        print(json.dumps({'downloaded':len(records),'total':len(all_jobs),'id':j['target_id'],'grid':j['label']}),flush=True)
    validation=validate(root,sample,all_jobs,records)
    manifest.update(complete=True,validation=validation);atomic_json(mp,manifest)
    return {'complete':True,'files':len(records),'objects':100}


def main() -> None:
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['catalogues','select','freeze','download','verify']);p.add_argument('--root',type=Path,default=Path('.'))
    a=p.parse_args();root=a.root.resolve()
    if a.phase=='catalogues': result={k:len(v['data']) for k,v in catalogues(root,True).items()}
    elif a.phase=='select': result={'objects':len(select(root,True)['objects'])}
    elif a.phase=='freeze': result=freeze(root)
    elif a.phase=='download': result=download(root)
    else:
        fr=json.loads((root/OUT/'experiment_freeze.json').read_text());check_hashes(root,fr['hashes'])
        sample=json.loads((root/SAMPLE).read_text());manifest=json.loads((root/MANIFEST).read_text())
        if not manifest.get('complete') or manifest.get('sample_sha256')!=sha(root/SAMPLE) or manifest.get('experiment_freeze_sha256')!=sha(root/OUT/'experiment_freeze.json'):
            raise ValueError('Incomplete or mismatched manifest')
        result=validate(root,sample,jobs(root,sample),manifest['files'])
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__': main()

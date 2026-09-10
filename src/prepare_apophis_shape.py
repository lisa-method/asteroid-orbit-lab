"""Retrieve three public PDS files; derive uniform-density mesh moments."""
import argparse
import collections
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import subprocess

from prepare_fresh_holdout import immutable_json, sha
from prepare_development_sample import atomic_json

BASE="https://sbnarchive.psi.edu/pds4/non_mission/gbo.ast-apophis.jpl.radar.shape_model_v1.0/"
FILES={"apophis_v233s7.obj":"data/apophis_v233s7.obj",
       "apophis_v233s7.xml":"data/apophis_v233s7.xml",
       "bundle_description.txt":"document/bundle_description.txt"}
MANIFEST="data/checksums/apophis_shape_manifest.json"
MODEL="apophis_v233s7.obj"


def dot(a,b):return sum(x*y for x,y in zip(a,b))
def cross(a,b):return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])


def mesh_moments(text):
    vertices=[];faces=[]
    for line in text.splitlines():
        fields=line.split()
        if not fields or fields[0].startswith('#'):continue
        if fields[0]=='v':
            if len(fields)!=4:raise ValueError('Expected 3D vertex')
            v=tuple(map(float,fields[1:]))
            if not all(math.isfinite(x) for x in v):raise ValueError('Nonfinite mesh')
            vertices.append(v)
        elif fields[0]=='f':
            if len(fields)!=4:raise ValueError('Expected triangles')
            faces.append(tuple(int(x.split('/')[0])-1 for x in fields[1:]))
        else:raise ValueError('Unsupported OBJ record '+fields[0])
    if not vertices or not faces:raise ValueError('Empty mesh')
    edges=collections.Counter();directed=collections.Counter()
    volumes=[];first=[[],[],[]];second=[[[] for _ in range(3)] for _ in range(3)]
    for f in faces:
        if min(f)<0 or max(f)>=len(vertices) or len(set(f))!=3:raise ValueError('Invalid face')
        for i,j in zip(f,(*f[1:],f[0])):
            edges[tuple(sorted((i,j)))]+=1;directed[(i,j)]+=1
        a,b,c=[vertices[i] for i in f];vol=dot(a,cross(b,c))/6
        volumes.append(vol);s=tuple(a[i]+b[i]+c[i] for i in range(3))
        for i in range(3):
            first[i].append(vol*s[i]/4)
            for j in range(3):second[i][j].append(vol*(s[i]*s[j]+sum(v[i]*v[j] for v in (a,b,c)))/20)
    if any(n!=2 for n in edges.values()) or any(directed[(i,j)]!=directed[(j,i)] for i,j in edges):raise ValueError('Mesh is not closed and consistently oriented')
    volume=math.fsum(volumes)
    if volume==0:raise ValueError('Zero volume')
    centroid=tuple(math.fsum(x)/volume for x in first)
    S=tuple(tuple(math.fsum(second[i][j])/volume-centroid[i]*centroid[j] for j in range(3)) for i in range(3))
    return {'vertices':len(vertices),'facets':len(faces),'edges':len(edges),
        'signed_volume_km3':volume,'volume_km3':abs(volume),'equivalent_diameter_km':(6*abs(volume)/math.pi)**(1/3),
        'bbox_extents_km':[max(v[i] for v in vertices)-min(v[i] for v in vertices) for i in range(3)],
        'uniform_density_centroid_km':centroid,'second_moment_km2':S,
        'max_radius_from_centroid_km':max(math.dist(v,centroid) for v in vertices)},vertices,faces


def prepare(root,download=False):
    manifest_path=root/MANIFEST
    manifest=json.loads(manifest_path.read_text()) if manifest_path.exists() else {'complete':False,'files':[]}
    records={r['name']:r for r in manifest['files']}
    for name,suffix in FILES.items():
        path=root/'data/raw/apophis_shape'/name;url=BASE+suffix
        if name in records:
            r=records[name]
            if r['sha256']!=sha(path) or r['bytes']!=path.stat().st_size or r['source_url']!=url:raise ValueError('PDS provenance mismatch')
        else:
            if path.exists():raise ValueError('Orphan PDS raw')
            if not download:raise ValueError('Missing input: explicit --download required')
            data=subprocess.run(['curl','--fail','--silent','--show-error','--location','--max-time','60',url],capture_output=True,check=True).stdout
            if not 0<len(data)<2_000_000:raise ValueError('Unexpected file size')
            path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('xb') as out:out.write(data)
            records[name]={'name':name,'path':path.relative_to(root).as_posix(),'sha256':sha(path),'bytes':len(data),'source_url':url,
                'retrieved_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'kind':'PDS derived radar shape',
                'doi':'10.26033/ydyq-5756','model_status':'preliminary; Brozovic et al. 2018'}
            atomic_json(manifest_path,{'complete':False,'files':list(records.values())})
    completed={'complete':True,'files':list(records.values())}
    if manifest.get('complete'):
        if completed!=manifest:raise ValueError('Completed PDS manifest changed')
    else:atomic_json(manifest_path,completed)
    model_path=root/'data/raw/apophis_shape'/MODEL
    moments,vertices,faces=mesh_moments(model_path.read_text())
    output={**moments,'obj_sha256':sha(model_path),'manifest_sha256':sha(manifest_path),
        'assumption':'homogeneous density; shape body frame, not ICRF attitude',
        'producer_sha256':sha(root/'src/prepare_apophis_shape.py')}
    immutable_json(root/'data/processed/apophis_shape/moments.json',output)
    print(json.dumps(output,indent=2),flush=True)
    return output,vertices,faces


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=Path('.'));p.add_argument('--download',action='store_true')
    a=p.parse_args();prepare(a.root.resolve(),a.download)

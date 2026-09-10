"""Preserve one anonymous SBDB covariance response with a frozen input design."""
from __future__ import annotations
import argparse
import datetime as dt
import json
from pathlib import Path
from urllib.request import urlopen, Request
from prepare_fresh_holdout import immutable_json, check_hashes, sha

CONFIG = 'configs/apophis_covariance_inputs.json'

def prepare(root, download=False):
    config = json.loads((root / CONFIG).read_text())
    design = root / config['design']
    hashes = {p: sha(root / p) for p in (CONFIG, 'src/prepare_apophis_covariance.py')}
    if design.exists():
        old = json.loads(design.read_text())
        check_hashes(root, old['hashes'])
        if old['hashes'] != hashes:
            raise ValueError('Covariance download design changed')
    else:
        immutable_json(design, {'hashes': hashes, 'created_utc': dt.datetime.now(dt.timezone.utc).isoformat()})
    raw, manifest = root / config['raw'], root / config['manifest']
    if manifest.exists():
        data = json.loads(manifest.read_text())
        row = data['files'][0]
        if data['design_sha256'] != sha(design) or row['path'] != config['raw'] or row['url'] != config['url']:
            raise ValueError('Covariance input provenance mismatch')
        if raw.stat().st_size != row['bytes'] or sha(raw) != row['sha256']:
            raise ValueError('Covariance raw changed')
    else:
        if raw.exists() or not download:
            raise ValueError('Missing/orphan covariance input; explicit --download required')
        with urlopen(Request(config['url'], headers={'Accept': 'application/json'}), timeout=40) as response:
            if response.status != 200:
                raise ValueError('SBDB response was not HTTP 200')
            body = response.read(config['max_bytes'] + 1)
        if len(body) > config['max_bytes']:
            raise ValueError('SBDB response exceeds frozen size limit')
        document = json.loads(body)
        if document.get('object', {}).get('des') != config['object_designation']:
            raise ValueError('SBDB object identity mismatch')
        raw.parent.mkdir(parents=True, exist_ok=True)
        with raw.open('xb') as stream:
            stream.write(body)
        immutable_json(manifest, {'design_sha256': sha(design), 'files': [{
            'path': config['raw'], 'url': config['url'], 'bytes': len(body), 'sha256': sha(raw),
            'retrieved_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'source': 'NASA/JPL SBDB API'}]})
    document = json.loads(raw.read_text())
    if document.get('signature', {}).get('version') != config['expected_api_version']:
        raise ValueError('SBDB schema version differs; retain raw and review before use')
    return document

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path('.'))
    p.add_argument('--download', action='store_true')
    args = p.parse_args()
    result = prepare(args.root.resolve(), args.download)
    orbit = result['orbit']; cov = orbit.get('covariance') or {}
    print(json.dumps({'solution': orbit['orbit_id'], 'epoch': orbit['epoch'], 'cov_epoch': cov.get('epoch'),
        'labels': cov.get('labels'), 'soln_date': orbit.get('soln_date'), 'last_obs': orbit.get('last_obs'),
        'pe_used': orbit.get('pe_used'), 'sb_used': orbit.get('sb_used'), 'model_pars': orbit.get('model_pars')}))

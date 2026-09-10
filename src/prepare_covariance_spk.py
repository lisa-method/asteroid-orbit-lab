"""Versioned DE441 coefficient window for covariance propagation from 2021."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path
import struct
from urllib.request import Request, urlopen

from prepare_development_sample import atomic_json
from prepare_fresh_holdout import immutable_json, check_hashes, sha

CONFIG = 'configs/covariance_spk_inputs.json'


def et_calendar(label):
    value = dt.datetime.fromisoformat(label)
    if value.tzinfo is not None:
        raise ValueError('TDB label must have no UTC timezone')
    return (value - dt.datetime(2000, 1, 1, 12)).total_seconds()


def exact_int(value, label):
    if not math.isfinite(value) or int(value) != value:
        raise ValueError(f'Invalid integer {label}')
    return int(value)


def prepare(root, download=False):
    config = json.loads((root / CONFIG).read_text())
    design = root / config['design']
    hashes = {p: sha(root / p) for p in [CONFIG, 'src/prepare_covariance_spk.py']}
    if design.exists():
        prior = json.loads(design.read_text())
        check_hashes(root, prior['hashes'])
        if hashes != prior['hashes']:
            raise ValueError('SPK download design changed')
    else:
        immutable_json(design, {'hashes': hashes, 'created_utc': dt.datetime.now(dt.timezone.utc).isoformat()})
    manifest_path = root / config['manifest']
    old = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    rows = {r['name']: r for r in old.get('files', [])}
    created = old.get('created_utc', dt.datetime.now(dt.timezone.utc).isoformat())

    def write_manifest(complete):
        value = {'created_utc': created, 'complete': complete, 'files': list(rows.values()),
                 'design_sha256': sha(design), 'source_url': config['source_url'],
                 'source_bytes': config['source_bytes'], 'scope': 'Unmodified byte ranges, not a complete SPK file'}
        if old.get('complete'):
            if value != old:
                raise ValueError('Complete SPK manifest differs')
        else:
            atomic_json(manifest_path, value)
        return value

    def read_range(name, first, last):
        if not (0 <= first <= last < config['source_bytes']):
            raise ValueError('Requested range outside source kernel')
        count = last - first + 1
        path = root / config['raw_directory'] / (name + '.bin')
        if name in rows:
            r = rows[name]
            if r['path'] != path.relative_to(root).as_posix() or r['range'] != [first, last] or r['source_url'] != config['source_url']:
                raise ValueError('Saved range provenance differs')
            if path.stat().st_size != count or sha(path) != r['sha256']:
                raise ValueError('Saved raw range changed')
            return path.read_bytes()
        if old.get('complete') or path.exists() or not download:
            raise ValueError(f'Missing or orphan range {name}; explicit --download required')
        if count + sum(r['bytes'] for r in rows.values()) > config['max_download_bytes']:
            raise ValueError('Prespecified 2 MiB download ceiling exceeded')
        request = Request(config['source_url'], headers={'Range': f'bytes={first}-{last}', 'Accept-Encoding': 'identity'})
        with urlopen(request, timeout=30) as response:
            if response.status != 206:
                raise ValueError('Server did not honor Range; full kernel download refused')
            expected = f"bytes {first}-{last}/{config['source_bytes']}"
            if response.headers.get('Content-Range') != expected:
                raise ValueError('Server Content-Range mismatch')
            if response.headers.get('Last-Modified') != config['source_last_modified']:
                raise ValueError('Source kernel changed since design')
            raw = response.read(count + 1)
            if len(raw) != count:
                raise ValueError('Truncated or oversized range response')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(raw)
        rows[name] = {'name': name, 'path': path.relative_to(root).as_posix(), 'sha256': sha(path),
                      'bytes': len(raw), 'source_url': config['source_url'], 'range': [first, last],
                      'source_bytes': config['source_bytes'], 'last_modified': config['source_last_modified'],
                      'retrieved_utc': dt.datetime.now(dt.timezone.utc).isoformat()}
        write_manifest(False)
        print(json.dumps({'downloaded': name, 'bytes': count}), flush=True)
        return raw

    header = read_range('file_record', 0, 1023)
    if header[:8] != b'DAF/SPK ' or header[88:96] not in (b'LTL-IEEE', b'BIG-IEEE'):
        raise ValueError('Not an IEEE DAF/SPK kernel')
    endian = '<' if header[88:96] == b'LTL-IEEE' else '>'
    nd, ni = struct.unpack_from(endian + '2i', header, 8)
    forward, backward, free = struct.unpack_from(endian + '3i', header, 76)
    if (nd, ni) != (2, 6) or forward <= 1 or backward < forward or free * 8 > config['source_bytes'] + 8:
        raise ValueError('Unexpected SPK file descriptor')
    # Preserve the original comment area as provenance, plus summary/name records.
    metadata = read_range('metadata', 1024, (backward + 1) * 1024 - 1)
    record_number, previous, seen = forward, 0, set()
    summaries = []
    while record_number:
        if record_number in seen or not forward <= record_number <= backward:
            raise ValueError('DAF summary chain is cyclic/outside metadata')
        seen.add(record_number)
        offset = (record_number - 2) * 1024
        raw = metadata[offset:offset + 1024]
        nxt, prev, count = [exact_int(x, 'summary control') for x in struct.unpack_from(endian + '3d', raw)]
        if prev != previous or not 0 <= count <= 25:
            raise ValueError('DAF summary chain/count mismatch')
        for i in range(count):
            start, stop, target, center, frame, kind, first, last = struct.unpack_from(endian + '2d6i', raw, 24 + i * 40)
            summaries.append(dict(start_et=start, end_et=stop, target=target, center=center,
                                  frame=frame, type=kind, first_address=first, last_address=last))
        previous, record_number = record_number, nxt
    if previous != backward:
        raise ValueError('DAF final summary mismatch')
    start_et, stop_et = et_calendar(config['start_tdb']), et_calendar(config['stop_tdb'])
    selected = []
    for target in config['target_ids']:
        candidates = [s for s in summaries if s['target'] == target and s['start_et'] <= start_et and stop_et <= s['end_et']]
        if len(candidates) != 1:
            raise ValueError(f'Expected one covering segment for {target}, found {len(candidates)}')
        segment = candidates[0]
        if segment['frame'] != 1 or segment['type'] != 2:
            raise ValueError('Only DE441 J2000 type-2 segments allowed')
        first, last = segment['first_address'], segment['last_address']
        directory = read_range(f'{target}_directory', (last - 4) * 8, last * 8 - 1)
        init, interval, size_float, n_float = struct.unpack(endian + '4d', directory)
        size, n = exact_int(size_float, 'record size'), exact_int(n_float, 'record count')
        if interval <= 0 or n <= 0 or size < 5 or (size - 2) % 3 or last - first + 1 != n * size + 4:
            raise ValueError('Malformed type-2 directory or data addresses')
        lo = max(0, math.floor((start_et - init) / interval))
        hi = min(n - 1, math.floor((stop_et - init) / interval))
        if lo > hi or start_et < init or stop_et > init + interval * n:
            raise ValueError('SPK requested window outside type-2 data')
        record_first = (first - 1 + lo * size) * 8
        record_last = (first - 1 + (hi + 1) * size) * 8 - 1
        data = read_range(f'{target}_records', record_first, record_last)
        # Independent structural validation; propagation decoder also checks these.
        for k in range(hi - lo + 1):
            numbers = struct.unpack_from(endian + str(size) + 'd', data, k * size * 8)
            midpoint, radius = numbers[:2]
            if any(not math.isfinite(x) for x in numbers) or radius != interval / 2 or midpoint != init + (lo + k + .5) * interval:
                raise ValueError('Malformed type-2 record constants')
        selected.append({**segment, 'directory': dict(init_et=init, intlen=interval, record_size=size, count=n),
                         'first_record_index': lo, 'last_record_index': hi,
                         'directory_path': rows[f'{target}_directory']['path'],
                         'records_path': rows[f'{target}_records']['path']})
    manifest = write_manifest(True)
    index = {'endian': endian, 'nd': nd, 'ni': ni, 'forward': forward, 'backward': backward,
             'source_url': config['source_url'], 'manifest_sha256': sha(manifest_path),
             'start_et': start_et, 'stop_et': stop_et, 'origin_et': et_calendar(config['origin_tdb']),
             'segments': selected, 'input_role': 'Exogenous planetary coefficients only; target states unchanged'}
    immutable_json(root / config['index'], index)
    print(json.dumps({'complete': len(selected), 'raw_files': len(rows), 'downloaded_bytes': sum(r['bytes'] for r in rows.values())}), flush=True)
    return index, manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    prepare(args.root.resolve(), args.download)

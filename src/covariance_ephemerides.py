"""Versioned 2021-2030 covariance ephemerides; original DE441 loader stays frozen."""
from __future__ import annotations

import json
import math
from pathlib import Path

from orbit_baselines import State
from prepare_covariance_spk import prepare
from spk_chebyshev import Type2Segment, parse_file_record, parse_summary_record, parse_type2_directory

ZERO = State((0., 0., 0.), (0., 0., 0.))


class CovarianceDE441:
    def __init__(self, root: Path, au_km: float, day_s: float):
        self.index, self.manifest = prepare(root)
        self.au, self.day = float(au_km), float(day_s)
        if not all(math.isfinite(v) and v > 0 for v in (self.au, self.day)):
            raise ValueError('Positive finite units required')
        self.origin = self.index['origin_et']
        by_name = {r['name']: root / r['path'] for r in self.manifest['files']}
        header = parse_file_record(by_name['file_record'].read_bytes())
        if header['endian'] != self.index['endian'] or (header['ND'], header['NI']) != (2, 6):
            raise ValueError('Independent DAF parser disagrees with frozen index')
        metadata = by_name['metadata'].read_bytes()
        seen, descriptors = set(), []
        recno = header['forward']
        previous = 0
        while recno:
            if recno in seen:
                raise ValueError('Cyclic summary chain')
            seen.add(recno)
            offset = (recno - 2) * 1024
            parsed = parse_summary_record(metadata[offset:offset + 1024], header['endian'])
            if parsed['prev'] != previous:
                raise ValueError('Broken summary chain')
            descriptors.extend(parsed['segments'])
            previous, recno = recno, parsed['next']
        if previous != header['backward']:
            raise ValueError('Final summary differs')
        self.segments = {}
        fields = ('start_et', 'end_et', 'target', 'center', 'frame', 'type', 'first_address', 'last_address')
        for entry in self.index['segments']:
            desc = {k: entry[k] for k in fields}
            if desc not in descriptors or entry['target'] in self.segments:
                raise ValueError('Selected segment not in raw summaries or duplicate target')
            directory = parse_type2_directory((root / entry['directory_path']).read_bytes(), header['endian'])
            expected_directory = tuple(entry['directory'][k] for k in ('init_et', 'intlen', 'record_size', 'count'))
            if directory != expected_directory:
                raise ValueError('Raw directory differs from index')
            self.segments[entry['target']] = Type2Segment.from_records(
                desc, (root / entry['records_path']).read_bytes(), header['endian'], entry['first_record_index'], directory)
        for target in self.segments:
            chain, cursor = set(), target
            while cursor:
                if cursor in chain or cursor not in self.segments:
                    raise ValueError('Cyclic or incomplete ephemeris center graph')
                chain.add(cursor)
                cursor = self.segments[cursor].metadata['center']
        self.cached_t = None
        self.cached_states = {}

    def barycentric_km(self, target: int, t: float) -> State:
        if isinstance(t, bool) or not math.isfinite(t):
            raise ValueError('Finite relative TDB days required')
        delta = t * self.day
        if delta < self.index['start_et'] - self.origin or delta > self.index['stop_et'] - self.origin:
            raise ValueError('Requested epoch outside retained DE441 window')
        if self.cached_t != t:
            self.cached_t, self.cached_states = t, {0: ZERO}
        if target in self.cached_states:
            return self.cached_states[target]
        if target not in self.segments:
            raise ValueError(f'Unsupported DE441 target {target}')
        segment = self.segments[target]
        parent = self.barycentric_km(segment.metadata['center'], t)
        relative = segment.state_at_split(self.origin, delta)
        result = State(tuple(a + b for a, b in zip(parent.position, relative.position)),
                       tuple(a + b for a, b in zip(parent.velocity, relative.velocity)))
        self.cached_states[target] = result
        return result

    def state_au(self, target: int, center: int, t: float) -> State:
        target_state = self.barycentric_km(target, t)
        center_state = self.barycentric_km(center, t)
        return State(tuple((a - b) / self.au for a, b in zip(target_state.position, center_state.position)),
                     tuple((a - b) * self.day / self.au for a, b in zip(target_state.velocity, center_state.velocity)))

    def ephemeris(self, target: int, center: int = 10):
        return CovarianceEphemeris(self, target, center)


class CovarianceEphemeris:
    def __init__(self, backend: CovarianceDE441, target: int, center: int):
        self.backend, self.target, self.center = backend, target, center

    def state_at(self, t: float) -> State:
        return self.backend.state_au(self.target, self.center, t)

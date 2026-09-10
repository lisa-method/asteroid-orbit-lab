"""Frozen joint orbit/NG covariance experiment; no fit or recentering."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import platform
import sys
import time

from barycentric_eih import build_barycentric_eih
from covariance_ephemerides import CovarianceDE441
from covariance_tools import scaled_cholesky, sigma_point_moments
from planetary_dynamics import Perturber
from precise_dopri import integrate_precise_dopri54
from prepare_apophis_covariance import prepare as prepare_covariance
from prepare_covariance_epoch import prepare as prepare_epoch
from prepare_fresh_holdout import check_hashes, immutable_json, sha
from run_apophis_reference_time import source_closure
from run_apophis_solver_audit import _flat, _state
from run_apophis_spk_audit import context as previous_context
from run_physics_baselines import errors, state_from_row
from run_eda import parse_horizons
from relative_time_dynamics import RelativeEphemerisInterpolator
from sbdb_covariance import load_sbdb_covariance, state_from_covariance

CONFIG = 'configs/apophis_covariance.json'
NATIVE_BASIS = 'uniform_inertial_q=rb-rSun0-t*vSun0,w=vb-vSun0'
OUTPUT_BASIS = 'heliocentric_ICRF_geometric_AU_AU-per-day'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class ShiftedEphemeris:
    """Translate 2021-relative arguments to the unchanged 2029-relative SB16."""
    def __init__(self, old, shift):
        self.old, self.shift = old, shift

    def state_at(self, t):
        return self.old.state_at(t - self.shift)


def specs(config, factor):
    result = [{'id': 'nominal_' + label, 'kind': 'nominal', 'solver_label': label,
               'delta_elements': [0.] * 8} for label in config['nominal_settings']]
    for scale in config['sigma_scales']:
        for column in range(8):
            for sign in (-1, 1):
                result.append({'id': f"probe_s{scale:g}_j{column}_{'plus' if sign > 0 else 'minus'}".replace('.', 'p'),
                    'kind': 'probe', 'solver_label': config['probe_setting'], 'scale': scale,
                    'column': column, 'sign': sign,
                    'delta_elements': [sign * math.sqrt(8) * scale * factor[i][column] for i in range(8)]})
    if len(result) != 34 or len({s['id'] for s in result}) != 34:
        raise ValueError('Exactly 34 prescribed runs required')
    return result


def build_context(root):
    config = json.loads((root / CONFIG).read_text())
    if config['sigma_scales'] != [.25, 1.] or config['nominal_settings'] != ['extreme', 'ultra'] or config['expected_new_propagations'] != 34:
        raise ValueError('Unexpected covariance matrix design')
    ctx, _, old_config, _, _, _, _ = previous_context(root)
    parent = json.loads((root / config['parent_freeze']).read_text())
    check_hashes(root, parent['hashes'])
    verification = json.loads((root / config['parent_verification']).read_text())
    if verification['matrix_sha256'] != sha(root / config['parent_matrix']) or verification['freeze_sha256'] != sha(root / config['parent_freeze']):
        raise ValueError('Parent verification does not match immutable parent artifacts')
    runtime = {'executable': sys.executable, 'version': sys.version, 'platform': platform.platform()}
    if runtime != parent['runtime']:
        raise ValueError('Runtime differs from frozen parent')
    document = prepare_covariance(root)
    parsed = load_sbdb_covariance(root / 'data/raw/apophis_covariance/sbdb_cov_vec.json')
    factor = scaled_cholesky(parsed.covariance)
    orbit = document['orbit']
    if document['object']['des'] != '99942' or orbit['orbit_id'] != '220' or orbit['pe_used'] != 'DE441' or orbit['sb_used'] != 'SB441-N16':
        raise ValueError('Object/solution/source identity differs')
    if parsed.covariance_epoch_jd_tdb != config['covariance_epoch_jd_tdb'] or list(parsed.labels) != config['labels']:
        raise ValueError('Covariance epoch or coordinate order differs')
    if not (orbit['last_obs'] < config['forecast_epoch_calendar_tdb'] and orbit['soln_date'] < config['forecast_epoch_calendar_tdb']):
        raise ValueError('Orbit-fit information was not available before forecast origin')
    pars = {p['name']: p for p in orbit['model_pars']}
    if any(float(pars[k]['value']) != v or pars[k]['kind'] != 'SET' for k, v in {'ALN': 1., 'NK': 0., 'NM': 2., 'R0': 1.}.items()):
        raise ValueError('Unexpected nongravitational law')
    if (ctx['ng'].alpha, ctx['ng'].exponent_m, ctx['ng'].exponent_k, ctx['ng'].r0_au, ctx['ng'].a3_au_d2) != (1., 2., 0., 1., 0.):
        raise ValueError('Prior NG law differs from joint covariance model')
    backend = CovarianceDE441(root, ctx['au'], ctx['day'])
    shift = config['forecast_epoch_jd_tdb'] - config['covariance_epoch_jd_tdb']
    if shift != 2922. or ctx['origin'] != config['forecast_epoch_jd_tdb']:
        raise ValueError('Time origins differ from contract')
    times = sorted(set([float(t) for t in range(0, int(shift), config['early_output_step_days'])] + [shift + t for t in ctx['times']]))
    if len(ctx['times']) != 797 or times[0] != 0. or times[-1] != 3287.:
        raise ValueError('Unexpected propagation grid')
    planets = tuple(Perturber(p.body_id, p.name, p.mu_au3_d2,
        ShiftedEphemeris(p.ephemeris, shift) if p.body_id.startswith('sb:') else backend.ephemeris(int(p.body_id), 10))
        for p in ctx['planets'] + (ctx['pluto'],))
    for p in planets:
        p.ephemeris.state_at(0.); p.ephemeris.state_at(times[-1])
    return {'config': config, 'old': ctx, 'settings': {s['label']: s for s in old_config['solver_settings'] if s['solver'] == 'dopri54'},
        'parent': parent, 'runtime': runtime, 'document': document, 'parsed': parsed, 'factor': factor,
        'backend': backend, 'shift': shift, 'times': times, 'planets': planets, 'sun': backend.ephemeris(10, 0)}


def input_gates(root, context):
    c, ctx, parsed = context['config'], context['old'], context['parsed']
    header, rows = prepare_epoch(root)
    if 'JPL#220' not in header['target']:
        raise ValueError('Epoch conversion control uses a different orbit solution')
    initial = state_from_covariance(parsed, ctx['mu'])
    pos, vel = errors(initial, state_from_row(rows[0]), ctx['au'], ctx['day'])
    epoch_config = json.loads((root / 'configs/covariance_epoch_check.json').read_text())
    epoch_pass = pos <= epoch_config['position_gate_km'] and vel <= epoch_config['velocity_gate_m_s']
    sources = [(p.body_id, p.ephemeris, 10) for p in ctx['planets'] + (ctx['pluto'],) if not p.body_id.startswith('sb:')]
    sources.append(('10', ctx['sun_barycentric'], 0))
    # The previous Venus audit replaced its daily backbone with a 2029 dense
    # table. Retain the old daily control too, to check the extended interval.
    _, venus_rows = parse_horizons(root/'data/raw/horizons/body_299.json', '299', 'Venus')
    sources.append(('299', RelativeEphemerisInterpolator(
        tuple(r['epoch_jd_tdb']-ctx['origin'] for r in venus_rows),
        tuple(state_from_row(r) for r in venus_rows)), 10))
    checks = []
    for target, old, center in sources:
        count, pmax, vmax = 0, 0., 0.
        direct = context['backend'].ephemeris(int(target), center)
        for t, state in zip(old.epochs_days, old.states, strict=True):
            tau = t + context['shift']
            if -1. <= tau <= 3288.:
                p, v = errors(direct.state_at(tau), state, ctx['au'], ctx['day'])
                count += 1; pmax = max(pmax, p); vmax = max(vmax, v)
        checks.append({'target': target, 'center': center, 'count': count, 'max_position_km': pmax, 'max_velocity_m_s': vmax,
                       'passed': count > 0 and pmax <= c['source_knot_position_budget_km'] and vmax <= c['source_knot_velocity_budget_m_s']})
    full = [s['delta_elements'] for s in specs(c, context['factor'].factor) if s.get('scale') == 1.]
    mean, reconstructed = sigma_point_moments(full, [1/16] * 16)
    residue = max(abs(reconstructed[i][j] - parsed.covariance[i][j]) /
        (context['factor'].scales[i] * context['factor'].scales[j]) for i in range(8) for j in range(8))
    return {'epoch_position_difference_km': pos, 'epoch_velocity_difference_m_s': vel, 'epoch_passed': epoch_pass,
        'epoch_header': header, 'source_knots': checks, 'source_knot_count': sum(x['count'] for x in checks),
        'input_cholesky_residual': context['factor'].residual, 'input_sigma_covariance_residual': residue,
        'input_sigma_mean': mean, 'passed': epoch_pass and all(x['passed'] for x in checks) and residue <= 1e-12,
        'sbdb_ng_nominal': list(parsed.ng_nominal), 'rounded_prior_ng_nominal': [ctx['ng'].a1_au_d2, ctx['ng'].a2_au_d2],
        'availability': {'fit_date': context['document']['orbit']['soln_date'], 'last_obs': context['document']['orbit']['last_obs'],
                         'forecast_origin': c['forecast_epoch_calendar_tdb'], 'forecast_issued_in_2021': False}}


def freeze(root, context):
    c = context['config']; paths = set(context['parent']['hashes'])
    paths.update([CONFIG, c['contract'], c['parent_freeze'], c['parent_matrix'], c['parent_verification']])
    paths.update(source_closure(root, ['run_apophis_covariance', 'analyze_apophis_covariance']))
    paths.update(p.relative_to(root).as_posix() for pattern in ('test_covariance*.py', 'test_sbdb_covariance.py', 'test_analyze_apophis_covariance.py', 'test_run_apophis_covariance.py') for p in (root/'tests').glob(pattern))
    for cfg_name in ('apophis_covariance_inputs', 'covariance_epoch_check', 'covariance_spk_inputs'):
        cfg_path = f'configs/{cfg_name}.json'; cfg = json.loads((root/cfg_path).read_text()); paths.add(cfg_path)
        paths.update(cfg[k] for k in ('raw', 'manifest', 'design', 'index') if k in cfg)
        paths.update(r['path'] for r in json.loads((root/cfg['manifest']).read_text())['files'])
    hashes = {p: sha(root/p) for p in sorted(paths)}
    payload = {'hashes': hashes, 'runtime': context['runtime']}; fingerprint = digest(payload)
    output = root/c['output_directory']; path = output/'freeze.json'
    if path.exists():
        prior = json.loads(path.read_text())
        if prior['fingerprint'] != fingerprint or any(prior[k] != payload[k] for k in payload):
            raise ValueError('Covariance experiment changed after freeze')
    else:
        immutable_json(path, {**payload, 'fingerprint': fingerprint, 'created_utc': dt.datetime.now(dt.timezone.utc).isoformat()})
    return output, fingerprint


def force_for(context, delta):
    ctx, parsed, c = context['old'], context['parsed'], context['config']
    ng = replace(ctx['ng'], a1_au_d2=parsed.ng_nominal[0]+delta[6], a2_au_d2=parsed.ng_nominal[1]+delta[7])
    force, convert = build_barycentric_eih(c['covariance_epoch_jd_tdb'], context['planets'], ctx['mu'], ctx['c'], ng,
        ctx['base']['earth_j2']['reference_radius_km']/ctx['au'], ctx['base']['earth_j2']['j2'], context['sun'], gr='eih_sun')
    return force, convert, ng


def verify_record(row, spec, context, fingerprint):
    if any(row[k] != v for k, v in spec.items()) or row['fingerprint'] != fingerprint:
        raise ValueError('Checkpoint specification/fingerprint changed')
    saved = row['payload_sha256']
    if digest({k: v for k, v in row.items() if k != 'payload_sha256'}) != saved:
        raise ValueError('Checkpoint payload changed')
    if [s['day'] for s in row['samples']] != context['times'] or row['solver_stats']['status'] != 'finished':
        raise ValueError('Incomplete checkpoint')


def run(root, group='all', prepare_only=False):
    context = build_context(root); c, ctx = context['config'], context['old']
    gates = input_gates(root, context)
    print(json.dumps({'input_gates_passed': gates['passed'], 'epoch_difference_m': 1000*gates['epoch_position_difference_km'],
                      'source_knots': gates['source_knot_count']}), flush=True)
    if not gates['passed']:
        print(json.dumps(gates), flush=True)
        raise ValueError('Frozen input gate failed; no dependent propagation')
    output, fingerprint = freeze(root, context)
    immutable_json(output/'input_checks.json', {**gates, 'fingerprint': fingerprint})
    matrix_specs = specs(c, context['factor'].factor)
    immutable_json(output/'inputs.json', {'fingerprint': fingerprint, 'specs': matrix_specs, 'times': context['times'],
        'parameter_labels': list(context['parsed'].labels), 'covariance_units': list(context['parsed'].covariance_units),
        'parameter_covariance': context['parsed'].covariance, 'cholesky_factor': context['factor'].factor,
        'covariance_epoch_jd_tdb': c['covariance_epoch_jd_tdb'], 'forecast_epoch_jd_tdb': c['forecast_epoch_jd_tdb'],
        'mean_elements_text': dict(context['parsed'].mean_element_text), 'ng_nominal': list(context['parsed'].ng_nominal),
        'au_km': ctx['au'], 'day_seconds': ctx['day'], 'native_basis': NATIVE_BASIS, 'output_basis': OUTPUT_BASIS})
    if prepare_only:
        print(json.dumps({'frozen': fingerprint, 'expected_propagations': len(matrix_specs)}), flush=True); return
    for spec in matrix_specs:
        selected = group == 'all' or (group == 'nominal' and spec['kind'] == 'nominal') or (group == 'small' and spec.get('scale') == .25) or (group == 'full' and spec.get('scale') == 1.)
        if not selected: continue
        path = output/'checkpoints'/(spec['id']+'.json')
        if path.exists():
            verify_record(json.loads(path.read_text()), spec, context, fingerprint); continue
        delta = spec['delta_elements']; initial = state_from_covariance(context['parsed'], ctx['mu'], delta=delta)
        force, convert, ng = force_for(context, delta)
        if convert(0., initial) != initial:
            raise ValueError('Native initial state is not heliocentric at epoch')
        setting = context['settings'][spec['solver_label']]
        print(json.dumps({'starting': spec['id']}), flush=True)
        started = time.perf_counter()
        result = integrate_precise_dopri54(force, 0., _flat(initial), context['times'],
            **{k: v for k, v in setting.items() if k not in ('label', 'solver')}, max_step=c['max_dp_step_days'])
        elapsed = time.perf_counter()-started
        native = [{'day': t, 'state': list(s)} for t, s in result.samples]
        samples = [{'day': t, 'state': list(_flat(convert(t, _state(s))))} for t, s in result.samples]
        endpoints = [[t, list(s)] for t, s in result.accepted_endpoints]
        converted = [[t, list(_flat(convert(t, _state(s))))] for t, s in result.accepted_endpoints]
        row = {**spec, 'fingerprint': fingerprint, 'initial_state': list(_flat(initial)), 'ng': asdict(ng), 'setting': setting,
            'samples': samples, 'native_samples': native, 'native_endpoints': endpoints, 'endpoints': converted,
            'solver_stats': result.stats, 'runtime_seconds': elapsed, 'native_basis': NATIVE_BASIS, 'output_basis': OUTPUT_BASIS,
            'origin_jd_tdb': c['covariance_epoch_jd_tdb'], 'created_utc': dt.datetime.now(dt.timezone.utc).isoformat()}
        row['payload_sha256'] = digest(row)
        verify_record(row, spec, context, fingerprint); immutable_json(path, row)
        print(json.dumps({'completed': spec['id'], 'seconds': elapsed, 'accepted_steps': result.stats['accepted_steps']}), flush=True)
    paths = [output/'checkpoints'/(s['id']+'.json') for s in matrix_specs]
    if all(p.exists() for p in paths):
        records = [json.loads(p.read_text()) for p in paths]
        for row, spec in zip(records, matrix_specs, strict=True): verify_record(row, spec, context, fingerprint)
        from analyze_apophis_covariance import analyze_records
        analysis = analyze_records(records, c, ctx['au'], ctx['day'])
        nominal = next(r for r in records if r['id'] == 'nominal_ultra')
        teacher = {context['shift']+t: row for t, row in zip(ctx['times'], ctx['annual'], strict=True)}
        comparisons = [{'forecast_day': s['day']-context['shift'], 'position_km': errors(_state(s['state']), state_from_row(teacher[s['day']]), ctx['au'], ctx['day'])[0]} for s in nominal['samples'] if s['day'] in teacher]
        immutable_json(output/'matrix.json', {'fingerprint': fingerprint, 'freeze_sha256': sha(output/'freeze.json'),
            'input_checks_sha256': sha(output/'input_checks.json'), 'records': [{k:v for k,v in r.items() if k not in ('native_endpoints','endpoints')} for r in records],
            'checkpoint_hashes': {p.relative_to(root).as_posix(): sha(p) for p in paths},
            'analysis': analysis, 'new_nominal_teacher_diagnostic': comparisons,
            'scope': 'Formal joint orbit-fit covariance under a fixed propagator; inspected Apophis event, no calibration or selector validation'})
        print(json.dumps({'complete': 34}), flush=True)
    else:
        print(json.dumps({'completed_checkpoints': sum(p.exists() for p in paths), 'expected': 34}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--group', choices=['all', 'nominal', 'small', 'full'], default='all')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args(); run(args.root.resolve(), args.group, args.prepare_only)

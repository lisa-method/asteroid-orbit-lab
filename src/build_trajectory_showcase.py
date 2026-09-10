"""Build scientific animation scenes from verified, already computed forecasts.

Reference coordinates are used only at their raw epochs. This is a read-only
presentation transform: no new propagation, fitting, or reference interpolation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from orbit_baselines import State, norm, subtract
from run_eda import parse_horizons

AU_KM = 149597870.7
PLOT_ASPECT = 820 / 590
OUT = 'outputs/selector_confirmation100'


def _dot(a, b):
    return math.fsum(x * y for x, y in zip(a, b, strict=True))


def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def _unit(v):
    n = math.sqrt(_dot(v, v))
    if not math.isfinite(n) or n == 0:
        raise ValueError('degenerate projection basis')
    return tuple(x/n for x in v)


def orthonormal_basis(position, velocity):
    e1 = _unit(position)
    e3 = _unit(_cross(position, velocity))
    return e1, _unit(_cross(e3, e1)), e3


def project_vector(vector, basis, origin=(0., 0., 0.)):
    local = subtract(vector, origin)
    return [_dot(local, basis[0]), _dot(local, basis[1])]


def select_indices(times, count=120):
    if len(times) < 2 or count < 2 or any(not math.isfinite(t) for t in times):
        raise ValueError('at least two finite epochs and frames required')
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError('epochs must be strictly increasing')
    if len(times) <= count:
        return list(range(len(times)))
    return sorted({round(i*(len(times)-1)/(count-1)) for i in range(count)})


def choose_amplification(prediction, reference):
    maximum = max(math.dist(p, r) for p, r in zip(prediction, reference, strict=True))
    span = max(math.dist(r, reference[0]) for r in reference)
    if maximum == 0 or span == 0:
        return 1.
    return max(1., min(1e12, 10.**round(math.log10(.1*span/maximum))))


def _limits(points):
    if any(len(p) != 2 or not all(math.isfinite(v) for v in p) for p in points):
        raise ValueError('finite two-dimensional display positions required')
    lo = [min(p[i] for p in points) for i in range(2)]
    hi = [max(p[i] for p in points) for i in range(2)]
    sx, sy = (max(b-a, 1e-12)*1.12 for a, b in zip(lo, hi))
    sx = max(sx, sy*PLOT_ASPECT)
    sy = sx/PLOT_ASPECT
    cx, cy = ((a+b)/2 for a, b in zip(lo, hi))
    return [cx-sx/2, cx+sx/2, cy-sy/2, cy+sy/2]


def build_scene(title, model, times, prediction, reference, error_km, *,
                mode='overview', unit='AU', dates=None, limits=None,
                context_bodies=None, amplification=None, frame_label=None):
    if not len(times) == len(prediction) == len(reference) == len(error_km):
        raise ValueError('scene arrays must have equal lengths')
    if dates is not None and len(dates) != len(times):
        raise ValueError('date count differs')
    if any(not math.isfinite(e) or e < 0 for e in error_km):
        raise ValueError('finite nonnegative true errors required')
    ix = select_indices(times)
    bodies = []
    for body in context_bodies or []:
        b = dict(body)
        if b.get('trajectory_xy') is not None:
            if len(b['trajectory_xy']) != len(times):
                raise ValueError('context body epochs differ')
            b['trajectory_xy'] = [b['trajectory_xy'][i] for i in ix]
        bodies.append(b)
    return dict(title=title, model=model, mode=mode, unit=unit,
                subtitle='Prediction and Horizons reference at the same TDB epochs',
                frame_label=frame_label or 'Fixed initial orbital-plane projection',
                times_days=[times[i] for i in ix], dates=[dates[i] for i in ix] if dates else None,
                prediction_xy=[prediction[i] for i in ix], reference_xy=[reference[i] for i in ix],
                error_km=[error_km[i] for i in ix], context_bodies=bodies,
                limits=limits or _limits(prediction+reference), amplification=amplification,
                footer='True 3D error is unscaled. Projection hides the out-of-plane component. Markers are schematic.')


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(root, path):
    return json.loads((root/path).read_text())


def _checked(root, path, hashes):
    expected = hashes.get(path)
    if not isinstance(expected, str) or _sha(root/path) != expected:
        raise ValueError(f'missing or mismatched source hash: {path}')
    return _read(root, path)


def _unique(items, **criteria):
    found = [x for x in items if all(x.get(k) == v for k, v in criteria.items())]
    if len(found) != 1:
        raise ValueError(f'expected exactly one record: {criteria}')
    return found[0]


def _render_pair(name, model, rows, predictions, basis, *, body_positions=None,
                 body_name=None, zoom=False, amplify=False, scope=''):
    """Apply one shared frame transform and optional residual-only magnification."""
    times = [r['display_time_days'] for r in rows]
    ix = select_indices(times)
    rows = [rows[i] for i in ix]
    predictions = [predictions[i] for i in ix]
    references = [r['r'] for r in rows]
    errors = [norm(subtract(p, r))*AU_KM for p, r in zip(predictions, references, strict=True)]
    centers = [body_positions[i] for i in ix] if body_positions is not None else [(0.,)*3]*len(ix)
    local_ref = [subtract(r, b) for r, b in zip(references, centers, strict=True)]
    local_pred = [subtract(p, b) for p, b in zip(predictions, centers, strict=True)]
    origin = local_ref[len(local_ref)//2] if zoom else (0.,)*3
    unit_scale = AU_KM if zoom else 1.
    ref = [[v*unit_scale for v in project_vector(r, basis, origin)] for r in local_ref]
    pred = [[v*unit_scale for v in project_vector(p, basis, origin)] for p in local_pred]
    # Compute the displacement before removing large coordinates; keep unscaled
    # prediction for a direct independent audit of the display transformation.
    residual = [[v*unit_scale for v in project_vector(subtract(p,r), basis)]
                for p, r in zip(predictions, references, strict=True)]
    gain = choose_amplification(pred, ref) if amplify else 1.
    displayed = [[r[k]+gain*d[k] for k in range(2)] for r, d in zip(ref, residual, strict=True)] if gain > 1 else pred
    context = []
    frame = 'Heliocentric; fixed initial orbital plane'
    if zoom:
        frame = (f'{body_name}-centric' if body_positions is not None else 'Heliocentric') + '; fixed plane, local offset'
        if body_positions is not None:
            context.append(dict(name=body_name, xy=[v*unit_scale for v in project_vector((0.,)*3,basis,origin)], radius=5))
    else:
        context.append(dict(name='Sun', xy=[0.,0.], radius=6))
    text = f'Separation x{gain:g}; not physical spacing' if gain > 1 else 'Physical spacing; separation x1'
    scene = build_scene(name, model, [r['display_time_days'] for r in rows], displayed, ref, errors,
                        mode='zoom' if zoom else 'overview', unit='km' if zoom else 'AU',
                        dates=[r['epoch_tdb'].replace('T',' ')+' TDB' for r in rows],
                        limits=_limits(displayed+ref+([[0.,0.]] if not zoom else [])),
                        context_bodies=context, amplification=text, frame_label=frame)
    scene['subtitle'] = scope
    audit = dict(raw_epochs_jd_tdb=[r['epoch_jd_tdb'] for r in rows],
                 relative_days=[r['display_time_days'] for r in rows], basis=basis,
                 local_origin_au=origin, common_center_au=centers, common_center_name=body_name or 'Sun',
                 prediction_heliocentric_au=predictions, reference_heliocentric_au=references,
                 unamplified_prediction_xy=pred, projected_residual_xy=residual,
                 amplification_gain=gain, unit_scale=unit_scale,
                 true_3d_error_km=errors, exact_reference_epochs=True)
    return scene, audit


def detail_rows_near_max_error(rows, error_at, horizon, width=90.):
    """Post-hoc illustration window; never used for forecast-time decisions."""
    peak = max(rows, key=lambda row: error_at[row['display_time_days']])['display_time_days']
    start = max(0., min(max(0., horizon-width), peak-width/2.))
    return [row for row in rows if start <= row['display_time_days'] <= min(horizon, start+width)]


def assemble_confirmation(root, object_id, method, horizon, tolerance=1.):
    from run_selector_confirmation import _trace_states, _v2_context_config
    from run_development_benchmark import _interpolate_endpoints, load_development_context
    ver_path, matrix_path = OUT+'/verification.json', OUT+'/matrix.json'
    ver = _read(root, ver_path)
    if ver.get('passed') is not True or ver.get('matrix_sha256') != _sha(root/matrix_path):
        raise ValueError('verified current confirmation matrix required')
    matrix = _read(root, matrix_path)
    hashes = matrix['provenance']['source_hashes']
    from prepare_selector_confirmation import check_hashes
    check_hashes(root, hashes)
    sample_path = 'data/processed/selector_confirmation100/sample.json'
    sample = _checked(root, sample_path, hashes)
    obj = _unique(sample['objects'], id=object_id)
    choice = _unique(matrix['choices'], object_id=object_id, method=method,
                     horizon_days=horizon, tolerance_km=tolerance)
    case = _unique(matrix['records'], object_id=object_id, model_id=choice['model_id'], horizon_days=horizon)
    trace_path = case['trace_paths'][0]
    trace = _checked(root, trace_path, case['trace_hashes'])
    at = _interpolate_endpoints(_trace_states(trace))
    used = {sample_path:hashes[sample_path], matrix_path:_sha(root/matrix_path),
            ver_path:_sha(root/ver_path), trace_path:case['trace_hashes'][trace_path]}
    def read_rows(kind):
        path = f'data/raw/selector_confirmation100/asteroids/asteroid_{object_id}_{kind}.json'
        _checked(root,path,hashes)
        used[path] = hashes[path]
        return [dict(r, display_time_days=r['epoch_jd_tdb']-case['start_jd_tdb'])
                for r in parse_horizons(root/path,object_id,obj['name'])[1]
                if 0 <= r['epoch_jd_tdb']-case['start_jd_tdb'] <= horizon]
    daily = read_rows('daily')
    initial = case['initial_state']
    basis = orthonormal_basis(initial['r'], initial['v'])
    error_at = {r['time_days']:r['position_error_km'] for r in case['record']['error_rows']}
    def predictions(rows):
        result = [at(r['display_time_days']).position for r in rows]
        for r,p in zip(rows,result,strict=True):
            actual = norm(subtract(p,r['r']))*AU_KM
            if abs(actual-error_at[r['display_time_days']]) > 1e-9:
                raise ValueError('animation error differs from frozen benchmark')
        return result
    label = f'{method}: {choice["model_id"]}'
    status_label = {'outside_training_support':'outside training range',
                    'predicted_feasible':'predicted within budget',
                    'fixed_full':'fixed full model'}.get(choice['status'],choice['status'].replace('_',' '))
    outcome = 'passed' if choice['actual_eligible'] else 'failed'
    scope = f'Confirmation100; {tolerance:g} km request; evaluation {outcome}; {status_label}; post-hoc illustration'
    overview, oa = _render_pair(obj['name']+f' | {horizon:g} d forecast',label,daily,predictions(daily),basis,scope=scope)
    ref_path = f'data/raw/selector_confirmation100/asteroids/asteroid_{object_id}_refined.json'
    body_positions = body_name = None
    refined = read_rows('refined') if ref_path in hashes else []
    if len(refined) >= 2:
        rows = refined
        config = _checked(root,'configs/force_models_v2.json',hashes)
        context = load_development_context(root,_v2_context_config(config))
        body_id = str(obj['event']['body_id'])
        body = next(p for p in context['planets'] if p.body_id == body_id)
        body_positions = [body.ephemeris.state_at(r['epoch_jd_tdb']).position for r in rows]
        body_name = body.name
    else:
        rows = detail_rows_near_max_error(daily, error_at, horizon)
    zoom, za = _render_pair(obj['name']+' | divergence detail',label,rows,predictions(rows),basis,
                           zoom=True,amplify=True,body_positions=body_positions,body_name=body_name,scope=scope)
    scenes = {'overview':overview,'zoom':zoom}
    scene_audits = {'overview':oa,'zoom':za}
    if len(refined) >= 2:
        late = [r for r in daily if r['display_time_days'] >= max(0.,horizon-90.)]
        scenes['divergence'], scene_audits['divergence'] = _render_pair(
            obj['name']+' | late forecast separation',label,late,predictions(late),basis,
            zoom=True,amplify=True,scope=scope)
    audit = dict(source='confirmation100',object_id=object_id,source_hashes=used,method=method,model_id=choice['model_id'],
                 horizon_days=horizon,tolerance_km=tolerance,choice_status=choice['status'],warning=choice['warning'],
                 benchmark_max_error_km=case['record']['max_position_error_km'],
                 detail_selection='catalogued event' if len(refined) >= 2 else '90-day window around maximum daily error; post-hoc only',
                 scope='Post-hoc illustration, not a new validation score; no propagation performed',
                 prediction_interpolation='frozen native-endpoint Hermite; errors match record within 1e-9 km',
                 scenes=scene_audits)
    return scenes, audit


def assemble_apophis(root):
    from relative_time_dynamics import relative_rows
    from de441_subset import DE441Subset
    from prepare_selector_confirmation import check_hashes
    freeze_path = 'outputs/apophis_encounter_closure/freeze.json'
    analysis_path = 'outputs/apophis_encounter_closure/analysis.json'
    ver_path = 'outputs/apophis_long_convergence/final_verification.json'
    trace_path = 'outputs/apophis_encounter_closure/checkpoints/annual__fine.json'
    ver, frozen = _read(root,ver_path), _read(root,freeze_path)
    if ver.get('verification') != 'passed' or ver['freeze_hashes']['closure'] != _sha(root/freeze_path):
        raise ValueError('verified Apophis closure required')
    if ver['prior_analysis_sha256'] != _sha(root/analysis_path):
        raise ValueError('Apophis analysis changed')
    check_hashes(root,frozen['hashes'])
    analysis = _read(root,analysis_path)
    trace = _checked(root,trace_path,analysis['checkpoint_hashes'])
    samples = dict(trace['samples'])
    used = {p:_sha(root/p) for p in [freeze_path,analysis_path,ver_path,trace_path]}
    def rows_for(path):
        _checked(root,path,frozen['hashes'])
        used[path] = frozen['hashes'][path]
        rows = relative_rows(parse_horizons(root/path,'99942','Apophis')[1],trace['origin_jd_tdb'],'2029-01-01T00:00:00','calendar')
        return [dict(r,display_time_days=r['epoch_relative_days']) for r in rows]
    daily = rows_for('data/raw/apophis_reference_time/asteroid_99942_annual_daily.json')
    refined = [r for r in rows_for('data/raw/apophis_moon_venus/asteroid_99942.json') if 102 <= r['display_time_days'] <= 103.5]
    def predictions(rows):
        if any(r['display_time_days'] not in samples for r in rows):
            raise ValueError('Apophis image requires exact stored prediction sample epochs')
        return [samples[r['display_time_days']][:3] for r in rows]
    annual_pred = predictions(daily)
    maximum = max(norm(subtract(p,r['r']))*AU_KM for p,r in zip(annual_pred,daily,strict=True))
    if abs(maximum-analysis['teacher']['annual__fine']['max_position_m']/1000) > 1e-9:
        raise ValueError('Apophis annual error reproduction failed')
    basis = orthonormal_basis(daily[0]['r'],daily[0]['v'])
    backend = DE441Subset(root,AU_KM,86400.)
    for f in backend.manifest['files']:
        if f['path'] not in frozen['hashes']:
            raise ValueError('unfrozen Apophis context')
        used[f['path']] = frozen['hashes'][f['path']]
    earth = [backend.state_au(399,10,r['display_time_days']).position for r in refined]
    label = 'Apophis diagnostic backend; annual fine'
    scope = 'Inspected stress case; not selector v4; annual 1 km target remains unmet'
    overview,oa = _render_pair('Apophis | annual forecast',label,daily,annual_pred,basis,scope=scope)
    encounter,ea = _render_pair('Apophis | Earth encounter',label,refined,predictions(refined),basis,
                               zoom=True,body_positions=earth,body_name='Earth',scope=scope)
    zoom,za = _render_pair('Apophis | encounter separation',label,refined,predictions(refined),basis,
                          zoom=True,amplify=True,body_positions=earth,body_name='Earth',scope=scope)
    late = [r for r in daily if r['display_time_days'] >= 275.]
    divergence, da = _render_pair('Apophis | late forecast separation',label,late,predictions(late),basis,
                                  zoom=True,amplify=True,scope=scope)
    return {'overview':overview,'encounter':encounter,'zoom':zoom,'divergence':divergence}, dict(
        source='apophis_encounter_closure',object_id='99942',source_hashes=used,benchmark_max_error_km=maximum,
        scope=scope,prediction_interpolation='none; exact saved requested states',
        reference_time='TDB Gregorian labels converted to relative days, as in frozen audit',
        scenes={'overview':oa,'encounter':ea,'zoom':za,'divergence':da})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',choices=('confirmation','apophis'),required=True)
    parser.add_argument('--object',required=True)
    parser.add_argument('--method',default='physics_v4')
    parser.add_argument('--horizon',type=float,default=365.)
    parser.add_argument('--tolerance',type=float,choices=(.1,1.,10.),default=1.)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    if args.source == 'apophis':
        if args.object != '99942' or args.horizon != 365.:
            parser.error('Apophis illustration is the inspected annual 99942 case')
        scenes,audit = assemble_apophis(Path.cwd())
    else:
        scenes,audit = assemble_confirmation(Path.cwd(),args.object,args.method,args.horizon,args.tolerance)
    args.output.mkdir(parents=True,exist_ok=True)
    for name,scene in scenes.items():
        (args.output/(name+'.json')).write_text(json.dumps(scene,indent=2,allow_nan=False)+'\n')
    audit['builder_sha256'] = _sha(Path(__file__))
    audit['scene_sha256'] = {name:_sha(args.output/(name+'.json')) for name in scenes}
    (args.output/'audit.json').write_text(json.dumps(audit,indent=2,allow_nan=False)+'\n')
    print(json.dumps(dict(output=str(args.output),scenes=list(scenes),frames={k:len(v['times_days']) for k,v in scenes.items()})))


if __name__ == '__main__':
    main()

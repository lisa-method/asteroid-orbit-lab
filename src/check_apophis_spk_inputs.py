"""Frozen source-knot and independently implemented force checks."""
from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal, localcontext
import math

from barycentric_eih import build_barycentric_eih
from earth_oblateness import j2_acceleration
from independent_force_terms import decimal_force_terms
from orbit_baselines import State
from planetary_dynamics import non_gravitational_acceleration
from relativistic_eih import prepare_sources, eih_correction


def distance(a, b):
    return math.sqrt(math.fsum((x - y)**2 for x, y in zip(a, b)))


def check_ephemerides(ctx, backend, config):
    sources = [(p.body_id, p.ephemeris, 10) for p in ctx['planets'] if not p.body_id.startswith('sb:')]
    sources += [('9', ctx['pluto'].ephemeris, 10), ('10', ctx['sun_barycentric'], 0)]
    results = []
    for target, old, center in sources:
        direct = backend.ephemeris(int(target), center)
        position_max, velocity_max, count = 0., 0., 0
        for t, reference in zip(old.epochs_days, old.states, strict=True):
            if not -1. <= t <= 366.:
                continue
            state = direct.state_at(t)
            position_max = max(position_max, distance(state.position, reference.position) * ctx['au'])
            velocity_max = max(velocity_max, distance(state.velocity, reference.velocity) * ctx['au'] / ctx['day'] * 1000)
            count += 1
        interior_pos, interior_vel, interior_count, worst_t = 0., 0., 0, None
        for left, right in zip(old.epochs_days, old.epochs_days[1:]):
            if left < 0 or right > 365:
                continue
            for fraction in (.25, .5, .75):
                t = left + (right - left) * fraction
                state, reference = direct.state_at(t), old.state_at(t)
                pos = distance(state.position, reference.position) * ctx['au']
                if pos > interior_pos:
                    interior_pos, worst_t = pos, t
                interior_vel = max(interior_vel, distance(state.velocity, reference.velocity) * ctx['au'] / ctx['day'] * 1000)
                interior_count += 1
        if not count or not interior_count:
            raise ValueError('No in-window source comparisons')
        results.append({'target_id': target, 'center_id': center, 'knot_count': count,
            'max_knot_position_difference_km': position_max, 'max_knot_velocity_difference_m_s': velocity_max,
            'within_knot_budget': position_max <= config['knot_agreement_position_km'] and velocity_max <= config['knot_agreement_velocity_m_s'],
            'interior_count': interior_count, 'max_interior_position_difference_km': interior_pos,
            'max_interior_velocity_difference_m_s': interior_vel, 'worst_position_t_days': worst_t})
    return {'bodies': results, 'source_gate_passed': all(r['within_knot_budget'] for r in results),
            'position_budget_km': config['knot_agreement_position_km'],
            'velocity_budget_m_s': config['knot_agreement_velocity_m_s'],
            'scope': 'Exogenous interpolation only; no target reference comparison'}


def check_forces(ctx, baseline, config):
    planets = ctx['planets'] + (ctx['pluto'],)
    earth_index = 1 + next(i for i, p in enumerate(planets) if p.body_id == '399')
    masses = (ctx['mu'],) + tuple(p.mu_au3_d2 for p in planets)
    major = (0,) + tuple(i + 1 for i, p in enumerate(planets) if not p.body_id.startswith('sb:'))
    radius = ctx['base']['earth_j2']['reference_radius_km'] / ctx['au']
    j2 = ctx['base']['earth_j2']['j2']
    sun = ctx['sun_barycentric']
    sun0 = sun.state_at(0.)
    force, convert = build_barycentric_eih(ctx['origin'], planets, ctx['mu'], ctx['c'], ctx['ng'], radius, j2, sun, gr='eih_sun')
    ng_force = non_gravitational_acceleration(ctx['ng'])
    samples = []
    maxima = {k: 0. for k in ('newton', 'ppn', 'earth_j2', 'ng', 'total')}
    for t, native in zip(baseline['requested_times_relative_days'], baseline['native_solver_requested_states'], strict=True):
        s = State(tuple(native[:3]), tuple(native[3:]))
        h = convert(t, s)
        if list((*h.position, *h.velocity)) != baseline['requested_states'][len(samples)]:
            raise ValueError('Baseline native conversion changed before force audit')
        sun_now = sun.state_at(t)
        states = tuple(p.ephemeris.state_at(t) for p in planets)
        positions = ((0., 0., 0.),) + tuple(p.position for p in states)
        velocities = (sun_now.velocity,) + tuple(tuple(a + b for a, b in zip(p.velocity, sun_now.velocity)) for p in states)
        vb = tuple(a + b for a, b in zip(s.velocity, sun0.velocity))
        decimal = decimal_force_terms(h.position, vb, masses, positions, velocities, ctx['c'], (0,),
            major_indices=major, sun_index=0, earth_index=earth_index,
            earth_j2={'mu': masses[earth_index], 'radius_au': radius, 'j2': j2}, ng=asdict(ctx['ng']))
        prepared = prepare_sources([masses[i] for i in major], [positions[i] for i in major], [velocities[i] for i in major])
        newton = (0., 0., 0.)
        for mu, pos in zip(masses, positions, strict=True):
            delta = tuple(a - b for a, b in zip(pos, h.position))
            r = math.sqrt(sum(x*x for x in delta))
            newton = tuple(a + mu * b / r**3 for a, b in zip(newton, delta))
        floating = {'newton': newton,
            'ppn': eih_correction(h.position, vb, prepared, ctx['c'], outer_indices=(0,)),
            'earth_j2': j2_acceleration(tuple(a-b for a,b in zip(h.position, positions[earth_index])), (0.,0.,1.), masses[earth_index], radius, j2),
            'ng': ng_force(t, h.position, h.velocity), 'total': force(t, s.position, s.velocity)}
        with localcontext() as context:
            context.prec = 50
            decimal['ppn'] = tuple(sum((decimal[k][i] for k in ('ppn_position','ppn_velocity','ppn_source_acceleration')), Decimal(0)) for i in range(3))
            differences = {key: float(sum((Decimal.from_float(v) - ref)**2 for v, ref in zip(floating[key], decimal[key])).sqrt()) for key in maxima}
        for key, value in differences.items():
            maxima[key] = max(maxima[key], value)
        samples.append({'t_days': t, 'difference_norm_au_d2': differences,
                        'decimal_terms_au_d2': {k: [str(v) for v in vec] for k, vec in decimal.items()}})
    return {'samples': samples, 'sample_count': len(samples), 'max_difference_au_d2': maxima,
            'force_gate_passed': maxima['total'] <= config['force_absolute_budget_au_d2'],
            'total_budget_au_d2': config['force_absolute_budget_au_d2'], 'decimal_precision': 50,
            'major_indices': list(major), 'newton_source_count': len(masses),
            'scope': 'Independent algebra implementation at shared saved predicted states; not JPL source-code validation'}

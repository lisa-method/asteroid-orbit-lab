"""Explicit heliocentric composition of barycentric-velocity 1PN forces.

The source accelerations used for the Sun-origin subtraction belong to the
stated force model, not a numerical derivative of an ephemeris table.
"""
from __future__ import annotations

import math

from earth_oblateness import earth_j2_acceleration, j2_acceleration
from gravity_conventions import build_convention_force
from planetary_dynamics import (Perturber, combine_accelerations,
    non_gravitational_acceleration, restricted_n_body_acceleration)
from relativistic_eih import prepare_sources, eih_correction


class _EpochCache:
    """Cache only exogenous interpolation, never a target-dependent force."""
    def __init__(self, ephemeris):
        self.ephemeris = ephemeris
        self.time = None
        self.state = None

    def state_at(self, time):
        if time != self.time:
            state = self.ephemeris.state_at(time)
            self.time, self.state = time, state
        return self.state


def build_eih_force_model(origin, planets, mu_sun, c, ng, earth_radius, earth_j2,
                          sun_barycentric_ephemeris, *, gr='eih_all', solar_j2=None):
    """Return relative-TDB-days, AU/day² acceleration; fixed Earth J2000 pole.

    All non-``sb:`` perturbers enter the relativistic inner sums; SB bodies
    retain Newtonian gravity. No asteroid identity selects the force model.
    ``eih_sun`` restricts only the target's outer PN sum to the Sun.
    In both PN modes the origin acceleration uses all major sources.
    """
    if gr not in {'schwarzschild','eih_sun','eih_all'}:
        raise ValueError('Unknown relativity convention')
    if gr == 'schwarzschild':
        return build_convention_force(origin,planets,mu_sun,c,ng,earth_radius,earth_j2,
                                       earth_pole='fixed_j2000',solar_j2=solar_j2)
    if not math.isfinite(c) or c <= 0 or not math.isfinite(mu_sun) or mu_sun <= 0:
        raise ValueError('Invalid relativistic constants')
    ids = [p.body_id for p in planets]
    if len(set(ids)) != len(ids) or '10' in ids or '399' not in ids:
        raise ValueError('Unique perturbers with Earth and without duplicated Sun required')
    bodies = tuple(Perturber(p.body_id,p.name,p.mu_au3_d2,_EpochCache(p.ephemeris)) for p in planets)
    major = tuple(p for p in bodies if not p.body_id.startswith('sb:'))
    masses = (mu_sun,*(p.mu_au3_d2 for p in major))
    earth = next(p for p in bodies if p.body_id=='399')
    cached_time = None
    sources = sun_velocity = sun_correction = None

    def relative_pn(time, position, velocity):
        nonlocal cached_time, sources, sun_velocity, sun_correction
        if time != cached_time:
            vs = sun_barycentric_ephemeris.state_at(time).velocity
            states = [p.ephemeris.state_at(time) for p in major]
            positions = ((0.,0.,0.),*(s.position for s in states))
            velocities = (vs,*(tuple(a+b for a,b in zip(s.velocity,vs)) for s in states))
            prepared = prepare_sources(masses,positions,velocities)
            origin_pn = eih_correction((0.,0.,0.),vs,prepared,c,exclude_index=0)
            cached_time, sources, sun_velocity, sun_correction = time,prepared,vs,origin_pn
        target_velocity = tuple(a+b for a,b in zip(velocity,sun_velocity))
        target_pn = eih_correction(position,target_velocity,sources,c,
                                   outer_indices=(0,) if gr=='eih_sun' else None)
        return tuple(a-b for a,b in zip(target_pn,sun_correction))

    terms = [restricted_n_body_acceleration(mu_sun,bodies,0.),relative_pn]
    if ng is not None:
        terms.append(non_gravitational_acceleration(ng))
    terms.append(earth_j2_acceleration(earth,0.,earth_radius,earth_j2,pole_model='fixed_j2000'))
    if solar_j2 is not None:
        values = {k:float(solar_j2[k]) for k in ('j2','radius_au','pole_ra_deg','pole_dec_deg')}
        if not all(math.isfinite(v) for v in values.values()) or values['radius_au']<=0:
            raise ValueError('Invalid solar J2')
        ra,dec = (math.radians(values[k]) for k in ('pole_ra_deg','pole_dec_deg'))
        pole = (math.cos(dec)*math.cos(ra),math.cos(dec)*math.sin(ra),math.sin(dec))
        def solar_term(_t,r,_v):
            return j2_acceleration(r,pole,mu_sun,values['radius_au'],values['j2'])
        terms.append(solar_term)
    return combine_accelerations(*terms)

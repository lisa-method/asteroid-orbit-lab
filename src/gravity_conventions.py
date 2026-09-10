"""Explicit gravity conventions for isolated audits; no target-ID routing.

Uses this project's existing vector J2 formula. Earth fixed-J2000 and the
solar pole are hypotheses motivated by ASSIST's documented conventions;
this module does not implement ASSIST or its full relativistic equations.
"""
from __future__ import annotations

import math

from earth_oblateness import earth_j2_acceleration, j2_acceleration
from planetary_dynamics import (combine_accelerations, non_gravitational_acceleration,
    restricted_n_body_acceleration, solar_schwarzschild_acceleration)
from relative_time_dynamics import build_relative_force


def build_convention_force(origin, planets, mu_sun, c, ng, earth_radius, earth_j2,
                           *, earth_pole="iau", solar_j2=None):
    """Sun-centred ICRF, relative TDB days, AU/day units; massless target.

    solar_j2, if supplied, requires j2, radius_au, pole_ra_deg, pole_dec_deg.
    Earth uses direct-minus-indirect J2 without a distance cutoff. The Sun
    quadrupole is an isolated direct test-particle term, not full DE441.
    """
    if earth_pole not in {"iau", "fixed_j2000"}:
        raise ValueError("Unsupported Earth pole")
    if earth_pole == "iau":
        base = build_relative_force(origin, planets, mu_sun, c, ng, earth_radius, earth_j2)
    else:
        earth = next(p for p in planets if p.body_id == "399")
        terms = [restricted_n_body_acceleration(mu_sun, planets, 0.),
                 solar_schwarzschild_acceleration(mu_sun, c)]
        if ng is not None:
            terms.append(non_gravitational_acceleration(ng))
        terms.append(earth_j2_acceleration(earth, 0., earth_radius, earth_j2,
                                          pole_model="fixed_j2000"))
        base = combine_accelerations(*terms)
    if solar_j2 is None:
        return base
    values = {key: float(solar_j2[key]) for key in
              ("j2", "radius_au", "pole_ra_deg", "pole_dec_deg")}
    if not all(math.isfinite(v) for v in values.values()) or values["radius_au"] <= 0:
        raise ValueError("Invalid solar quadrupole constants")
    ra, dec = (math.radians(values[k]) for k in ("pole_ra_deg", "pole_dec_deg"))
    pole = (math.cos(dec)*math.cos(ra), math.cos(dec)*math.sin(ra), math.sin(dec))

    def solar_term(_t, position, _velocity):
        return j2_acceleration(position, pole, mu_sun, values["radius_au"], values["j2"])

    return combine_accelerations(base, solar_term)

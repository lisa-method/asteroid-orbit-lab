"""Earth's isolated oblateness (J2) acceleration correction.

The correction uses AU, days, and AU/day**2, matching the project's canonical
heliocentric dynamics convention.  ``earth_pole_iau`` implements the
epoch-dependent IAU mean-pole coefficients published in the NAIF text PCK
documentation, with ``T`` measured in TDB Julian centuries from J2000:

https://naif.jpl.nasa.gov/pub/naif/toolkit_docs/C/req/pck.html

This is an approximate IAU mean pole.  It is not a high-precision ITRF
orientation, a precession-nutation/EOP solution, or a guarantee of exact
Horizons Earth-pole matching.  The caller supplies Earth's ``mu``, reference
radius, and J2 so that those experiment constants remain explicit.
"""

from __future__ import annotations

import math

from orbit_baselines import Acceleration, Vector, norm, scale, subtract
from planetary_dynamics import Perturber


_J2000_JD_TDB = 2451545.0
_DAYS_PER_JULIAN_CENTURY = 36525.0
_FIXED_J2000_POLE: Vector = (0.0, 0.0, 1.0)


def _finite_vector(vector: Vector, *, name: str) -> None:
    """Validate a 3-vector without imposing a coordinate scale."""

    if len(vector) != 3 or any(not math.isfinite(component) for component in vector):
        raise ValueError(f"{name} must contain three finite components")


def earth_pole_iau(jd_tdb: float) -> Vector:
    """Return the approximate IAU mean Earth pole as an ICRF unit vector.

    The NAIF ``BODY399_POLE_RA`` and ``BODY399_POLE_DEC`` coefficients are
    ``RA = 0 - 0.641*T`` and ``DEC = 90 - 0.557*T`` degrees, respectively.
    ``T = (JD_TDB - 2451545) / 36525`` is in TDB Julian centuries.
    """

    if not math.isfinite(jd_tdb):
        raise ValueError("jd_tdb must be finite")
    centuries = (jd_tdb - _J2000_JD_TDB) / _DAYS_PER_JULIAN_CENTURY
    right_ascension = math.radians(-0.641 * centuries)
    declination = math.radians(90.0 - 0.557 * centuries)
    cos_declination = math.cos(declination)
    return (
        cos_declination * math.cos(right_ascension),
        cos_declination * math.sin(right_ascension),
        math.sin(declination),
    )


def j2_acceleration(
    displacement: Vector,
    pole: Vector,
    mu_au3_d2: float,
    reference_radius_au: float,
    j2: float,
) -> Vector:
    """Return only the quadrupole ``J2`` acceleration correction.

    ``displacement`` is the target position relative to Earth's center.  The
    supplied pole is normalized internally, which makes equivalent scaled
    representations of the same axis produce the same result.  The returned
    vector is the correction to the point-mass acceleration; it does not
    include Earth's monopole term.
    """

    _finite_vector(displacement, name="displacement")
    _finite_vector(pole, name="pole")
    if not math.isfinite(mu_au3_d2) or mu_au3_d2 <= 0.0:
        raise ValueError("mu_au3_d2 must be finite and positive")
    if not math.isfinite(reference_radius_au) or reference_radius_au <= 0.0:
        raise ValueError("reference_radius_au must be finite and positive")
    if not math.isfinite(j2):
        raise ValueError("j2 must be finite")

    radius = norm(displacement)
    pole_norm = norm(pole)
    if not math.isfinite(radius) or radius == 0.0:
        raise ValueError("displacement must be nonzero and finite")
    if not math.isfinite(pole_norm) or pole_norm == 0.0:
        raise ValueError("pole must be nonzero and finite")

    unit_pole = scale(pole, 1.0 / pole_norm)
    pole_projection = sum(component * axis for component, axis in zip(displacement, unit_pole))
    coefficient = 1.5 * mu_au3_d2 * j2 * reference_radius_au**2 / radius**5
    bracket = (
        (5.0 * (pole_projection / radius) ** 2 - 1.0) * displacement[0]
        - 2.0 * pole_projection * unit_pole[0],
        (5.0 * (pole_projection / radius) ** 2 - 1.0) * displacement[1]
        - 2.0 * pole_projection * unit_pole[1],
        (5.0 * (pole_projection / radius) ** 2 - 1.0) * displacement[2]
        - 2.0 * pole_projection * unit_pole[2],
    )
    return scale(bracket, coefficient)


def earth_j2_acceleration(
    earth: Perturber,
    start_jd_tdb: float,
    reference_radius_au: float,
    j2: float,
    *,
    pole_model: str = "iau",
) -> Acceleration:
    """Build a heliocentric Earth-J2 correction acceleration.

    At each rollout epoch, the direct correction uses the asteroid-to-Earth
    displacement ``r_asteroid - r_earth``.  The matching indirect correction
    uses the Sun-to-Earth displacement ``-r_earth`` and is subtracted, as
    required by a Sun-centered non-inertial frame.  ``fixed_j2000`` is a
    diagnostic pole choice; ``iau`` uses the epoch-dependent approximate mean
    pole above.  No distance cutoff is applied.
    """

    if not math.isfinite(start_jd_tdb):
        raise ValueError("start_jd_tdb must be finite")
    if not math.isfinite(reference_radius_au) or reference_radius_au <= 0.0:
        raise ValueError("reference_radius_au must be finite and positive")
    if not math.isfinite(j2):
        raise ValueError("j2 must be finite")
    if pole_model not in {"iau", "fixed_j2000"}:
        raise ValueError("pole_model must be 'iau' or 'fixed_j2000'")

    def acceleration(time_days: float, position: Vector, _velocity: Vector) -> Vector:
        if not math.isfinite(time_days):
            raise ValueError("time_days must be finite")
        epoch = start_jd_tdb + time_days
        earth_position = earth.ephemeris.state_at(epoch).position
        pole = earth_pole_iau(epoch) if pole_model == "iau" else _FIXED_J2000_POLE
        direct = j2_acceleration(
            subtract(position, earth_position),
            pole,
            earth.mu_au3_d2,
            reference_radius_au,
            j2,
        )
        indirect = j2_acceleration(
            scale(earth_position, -1.0),
            pole,
            earth.mu_au3_d2,
            reference_radius_au,
            j2,
        )
        return subtract(direct, indirect)

    return acceleration

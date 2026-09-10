"""Relative-time ephemerides and force callbacks for the reference-time audit.

The relative interpolator stores only days from the declared origin.  This
avoids adding a local integration time to a seven-digit Julian date inside
``state_at`` while preserving the project's cubic Hermite interpolation.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from earth_oblateness import j2_acceleration
from orbit_baselines import State, Vector, add, scale
from planetary_dynamics import (
    NonGravitationalParameters,
    Perturber,
    combine_accelerations,
    non_gravitational_acceleration,
    restricted_n_body_acceleration,
    solar_schwarzschild_acceleration,
)


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _calendar(value: Any, name: str) -> dt.datetime:
    if isinstance(value, dt.datetime):
        parsed = value
    elif isinstance(value, dt.date):
        parsed = dt.datetime.combine(value, dt.time())
    elif isinstance(value, str):
        text = value.strip().replace(" TDB", "").replace("Z", "+00:00")
        try:
            parsed = dt.datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{name} must be Gregorian ISO calendar time") from exc
    else:
        raise ValueError(f"{name} must be Gregorian ISO calendar time")
    if parsed.tzinfo is not None:
        raise ValueError(f"{name} must be a TDB calendar label without UTC timezone")
    return parsed


def _row_calendar(row: dict[str, Any]) -> dt.datetime:
    if "epoch_tdb" not in row:
        raise ValueError("calendar mode requires epoch_tdb in every row")
    return _calendar(row["epoch_tdb"], "row epoch_tdb")


def _validate_rows(rows: Sequence[dict[str, Any]]) -> None:
    if len(rows) < 2:
        raise ValueError("at least two ephemeris rows are required")
    epochs = [_finite(row["epoch_jd_tdb"], "epoch_jd_tdb") for row in rows]
    if any(right <= left for left, right in zip(epochs, epochs[1:])):
        raise ValueError("epoch_jd_tdb rows must be strictly increasing")
    for row in rows:
        if len(row.get("r",())) != 3 or len(row.get("v",())) != 3:
            raise ValueError("rows require separate three-component position and velocity")
        state = (*row.get("r", ()), *row.get("v", ()))
        if len(state) != 6 or not all(_finite(value, "state") == float(value) for value in state):
            raise ValueError("rows must contain six finite state components")


def relative_rows(rows: Sequence[dict[str, Any]], origin_jd_tdb: float, origin_calendar: Any, mode: str) -> list[dict[str, Any]]:
    """Copy rows and add ``epoch_relative_days`` without mutating input rows.

    ``float`` subtracts the original floating-point JD.  ``calendar`` computes
    an integer Gregorian timedelta in microseconds first, then divides by the
    number of microseconds per day.
    """
    _validate_rows(rows)
    origin_jd = _finite(origin_jd_tdb, "origin_jd_tdb")
    origin = _calendar(origin_calendar, "origin_calendar")
    if mode not in {"float", "calendar"}:
        raise ValueError("mode must be 'float' or 'calendar'")
    output: list[dict[str, Any]] = []
    previous = -math.inf
    for row in rows:
        if mode == "float":
            relative = _finite(row["epoch_jd_tdb"], "epoch_jd_tdb") - origin_jd
        else:
            delta = _row_calendar(row) - origin
            integer_microseconds = ((delta.days * 86400 + delta.seconds) * 1_000_000) + delta.microseconds
            relative = integer_microseconds / (86400.0 * 1_000_000.0)
        relative = _finite(relative, "epoch_relative_days")
        if relative <= previous:
            raise ValueError("relative epochs must be strictly increasing")
        previous = relative
        copied = dict(row)
        copied["epoch_relative_days"] = relative
        copied["epoch_days"] = relative
        output.append(copied)
    return output


@dataclass(frozen=True)
class RelativeEphemerisInterpolator:
    epochs_days: tuple[float, ...]
    states: tuple[State, ...]

    def __post_init__(self) -> None:
        if len(self.epochs_days) != len(self.states) or len(self.states) < 2:
            raise ValueError("relative ephemeris needs matching epochs and at least two states")
        if any(not math.isfinite(value) for value in self.epochs_days):
            raise ValueError("relative epochs must be finite")
        if any(right <= left for left, right in zip(self.epochs_days, self.epochs_days[1:])):
            raise ValueError("relative epochs must be strictly increasing")
        for state in self.states:
            if len(state.position) != 3 or len(state.velocity) != 3 or not all(math.isfinite(value) for value in (*state.position, *state.velocity)):
                raise ValueError("relative ephemeris states must be finite six-vectors")

    def state_at(self, time_days: float) -> State:
        time = _finite(time_days, "time_days")
        if time < self.epochs_days[0] or time > self.epochs_days[-1]:
            raise ValueError("requested relative time lies outside the ephemeris")
        if time == self.epochs_days[-1]:
            return self.states[-1]
        import bisect
        index = bisect.bisect_right(self.epochs_days, time) - 1
        left_time, right_time = self.epochs_days[index:index + 2]
        left, right = self.states[index:index + 2]
        h = right_time - left_time
        u = (time - left_time) / h
        u2, u3 = u * u, u * u * u
        h00, h10 = 2*u3 - 3*u2 + 1, u3 - 2*u2 + u
        h01, h11 = -2*u3 + 3*u2, u3 - u2
        # Preserve the frozen interpolator's arithmetic grouping. This axis
        # changes time coordinates, not floating-point summation of the state.
        position = add(add(scale(left.position,h00),scale(left.velocity,h10*h)),
                       add(scale(right.position,h01),scale(right.velocity,h11*h)))
        velocity = add(add(scale(left.position,(6*u2-6*u)/h),scale(left.velocity,3*u2-4*u+1)),
                       add(scale(right.position,(-6*u2+6*u)/h),scale(right.velocity,3*u2-2*u)))
        return State(position, velocity)


def relative_perturbers(
    entries: Iterable[tuple[Perturber, Sequence[dict[str, Any]]]],
    origin_jd_tdb: float,
    origin_calendar: Any,
    mode: str,
) -> tuple[Perturber, ...]:
    """Build independent relative ephemerides while preserving entry identity."""
    result: list[Perturber] = []
    for perturber, rows in entries:
        converted = relative_rows(rows, origin_jd_tdb, origin_calendar, mode)
        result.append(Perturber(perturber.body_id, perturber.name, perturber.mu_au3_d2, RelativeEphemerisInterpolator(tuple(row["epoch_relative_days"] for row in converted), tuple(State(tuple(row["r"]), tuple(row["v"])) for row in converted))))
    return tuple(result)


def build_relative_force(
    origin_jd_tdb: float,
    perturbers: tuple[Perturber, ...],
    mu_sun_au3_d2: float,
    speed_of_light_au_d: float,
    non_gravitational: NonGravitationalParameters | None,
    reference_radius_au: float,
    j2: float,
):
    """Compose the old Sun+planet+GR+NG+Earth-J2 force in relative time."""
    origin = _finite(origin_jd_tdb, "origin_jd_tdb")
    mu = _finite(mu_sun_au3_d2, "mu_sun_au3_d2")
    c = _finite(speed_of_light_au_d, "speed_of_light_au_d")
    radius = _finite(reference_radius_au, "reference_radius_au")
    j2_value = _finite(j2, "j2")
    if mu <= 0.0 or c <= 0.0 or radius <= 0.0:
        raise ValueError("force constants must be positive")
    earth = next((body for body in perturbers if body.body_id == "399"), None)
    if earth is None:
        raise ValueError("relative force requires Earth body_id 399 for J2")
    terms = [restricted_n_body_acceleration(mu, perturbers, 0.0), solar_schwarzschild_acceleration(mu, c)]
    if non_gravitational is not None:
        terms.append(non_gravitational_acceleration(non_gravitational))

    def j2_term(time_days: float, position: Vector, _velocity: Vector) -> Vector:
        time = _finite(time_days, "time_days")
        earth_position = earth.ephemeris.state_at(time).position
        centuries = ((origin - 2451545.0) + time) / 36525.0
        right_ascension = math.radians(-0.641 * centuries)
        declination = math.radians(90.0 - 0.557 * centuries)
        pole = (math.cos(declination) * math.cos(right_ascension), math.cos(declination) * math.sin(right_ascension), math.sin(declination))
        direct = j2_acceleration(tuple(a - b for a, b in zip(position, earth_position)), pole, earth.mu_au3_d2, radius, j2_value)
        indirect = j2_acceleration(tuple(-a for a in earth_position), pole, earth.mu_au3_d2, radius, j2_value)
        return tuple(a - b for a, b in zip(direct, indirect))

    terms.append(j2_term)
    return combine_accelerations(*terms)


__all__ = ["RelativeEphemerisInterpolator", "build_relative_force", "relative_perturbers", "relative_rows"]

"""Heliocentric restricted N-body forces from tabulated body ephemerides."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from orbit_baselines import (
    Acceleration,
    State,
    StepSelector,
    Vector,
    add,
    cross,
    norm,
    scale,
    subtract,
)


@dataclass(frozen=True)
class EphemerisInterpolator:
    epochs_jd_tdb: tuple[float, ...]
    states: tuple[State, ...]

    def __post_init__(self) -> None:
        if len(self.epochs_jd_tdb) != len(self.states) or len(self.states) < 2:
            raise ValueError("Ephemeris needs matching epochs and at least two states")
        if any(right <= left for left, right in zip(self.epochs_jd_tdb, self.epochs_jd_tdb[1:])):
            raise ValueError("Ephemeris epochs must be strictly increasing")

    @classmethod
    def from_rows(cls, rows: list[dict]) -> "EphemerisInterpolator":
        return cls(
            tuple(row["epoch_jd_tdb"] for row in rows),
            tuple(State(tuple(row["r"]), tuple(row["v"])) for row in rows),  # type: ignore[arg-type]
        )

    def state_at(self, epoch_jd_tdb: float) -> State:
        if not self.epochs_jd_tdb[0] <= epoch_jd_tdb <= self.epochs_jd_tdb[-1]:
            raise ValueError("Requested epoch lies outside the ephemeris")
        if epoch_jd_tdb == self.epochs_jd_tdb[-1]:
            return self.states[-1]
        left_index = bisect_right(self.epochs_jd_tdb, epoch_jd_tdb) - 1
        left_epoch = self.epochs_jd_tdb[left_index]
        right_epoch = self.epochs_jd_tdb[left_index + 1]
        duration = right_epoch - left_epoch
        fraction = (epoch_jd_tdb - left_epoch) / duration
        left = self.states[left_index]
        right = self.states[left_index + 1]
        s2 = fraction * fraction
        s3 = s2 * fraction
        h00 = 2.0 * s3 - 3.0 * s2 + 1.0
        h10 = s3 - 2.0 * s2 + fraction
        h01 = -2.0 * s3 + 3.0 * s2
        h11 = s3 - s2
        position = add(
            add(scale(left.position, h00), scale(left.velocity, h10 * duration)),
            add(scale(right.position, h01), scale(right.velocity, h11 * duration)),
        )
        velocity = add(
            add(
                scale(left.position, (6.0 * s2 - 6.0 * fraction) / duration),
                scale(left.velocity, 3.0 * s2 - 4.0 * fraction + 1.0),
            ),
            add(
                scale(right.position, (-6.0 * s2 + 6.0 * fraction) / duration),
                scale(right.velocity, 3.0 * s2 - 2.0 * fraction),
            ),
        )
        return State(position, velocity)


@dataclass(frozen=True)
class Perturber:
    body_id: str
    name: str
    mu_au3_d2: float
    ephemeris: EphemerisInterpolator


@dataclass(frozen=True)
class NonGravitationalParameters:
    """Marsden-style radial/transverse/normal acceleration parameters.

    Accelerations are in AU/day^2 and ``r0_au`` is in AU. The distance law is
    ``alpha * (r/r0)^(-m) * (1 + (r/r0)^n)^(-k)``.
    """

    a1_au_d2: float = 0.0
    a2_au_d2: float = 0.0
    a3_au_d2: float = 0.0
    alpha: float = 1.0
    exponent_m: float = 2.0
    exponent_n: float = 0.0
    exponent_k: float = 0.0
    r0_au: float = 1.0

    def __post_init__(self) -> None:
        if self.alpha <= 0.0 or self.r0_au <= 0.0:
            raise ValueError("Non-gravitational scale factors must be positive")


def inverse_cube_acceleration(displacement: Vector, mu: float) -> Vector:
    distance = norm(displacement)
    if distance == 0.0:
        raise ValueError("Point-mass acceleration is singular at zero separation")
    return scale(displacement, mu / distance**3)


def restricted_n_body_acceleration(
    mu_sun_au3_d2: float,
    perturbers: tuple[Perturber, ...],
    start_jd_tdb: float,
) -> Acceleration:
    """Use direct and indirect terms in a Sun-centered non-inertial frame."""

    def acceleration(time_days: float, position: Vector, _velocity: Vector) -> Vector:
        epoch = start_jd_tdb + time_days
        total = scale(position, -mu_sun_au3_d2 / norm(position) ** 3)
        for body in perturbers:
            body_position = body.ephemeris.state_at(epoch).position
            direct = inverse_cube_acceleration(subtract(body_position, position), body.mu_au3_d2)
            indirect = inverse_cube_acceleration(body_position, body.mu_au3_d2)
            total = add(total, subtract(direct, indirect))
        return total

    return acceleration


def solar_schwarzschild_acceleration(
    mu_sun_au3_d2: float,
    speed_of_light_au_d: float,
) -> Acceleration:
    """Return the leading solar Schwarzschild correction for a test particle.

    This is the common heliocentric two-body 1PN term. It is an interpretable
    ablation, not the full Einstein-Infeld-Hoffmann Solar-system model used by
    high-precision ephemerides.
    """

    if mu_sun_au3_d2 <= 0.0 or speed_of_light_au_d <= 0.0:
        raise ValueError("Solar GM and the speed of light must be positive")

    def acceleration(_time: float, position: Vector, velocity: Vector) -> Vector:
        radius = norm(position)
        if radius == 0.0:
            raise ValueError("Relativistic acceleration is singular at the origin")
        speed_squared = sum(component * component for component in velocity)
        radial_velocity_product = sum(a * b for a, b in zip(position, velocity))
        bracket = add(
            scale(position, 4.0 * mu_sun_au3_d2 / radius - speed_squared),
            scale(velocity, 4.0 * radial_velocity_product),
        )
        return scale(
            bracket,
            mu_sun_au3_d2 / (speed_of_light_au_d**2 * radius**3),
        )

    return acceleration


def non_gravitational_acceleration(
    parameters: NonGravitationalParameters,
) -> Acceleration:
    """Return a Marsden-style acceleration in the orbital RTN basis."""

    def acceleration(_time: float, position: Vector, velocity: Vector) -> Vector:
        radius = norm(position)
        if radius == 0.0:
            raise ValueError("Non-gravitational acceleration is singular at the origin")
        radial_hat = scale(position, 1.0 / radius)
        angular_momentum = cross(position, velocity)
        angular_momentum_norm = norm(angular_momentum)
        if angular_momentum_norm == 0.0:
            raise ValueError("RTN basis is undefined for zero angular momentum")
        normal_hat = scale(angular_momentum, 1.0 / angular_momentum_norm)
        transverse_hat = cross(normal_hat, radial_hat)
        radius_ratio = radius / parameters.r0_au
        distance_law = (
            parameters.alpha
            * radius_ratio ** (-parameters.exponent_m)
            * (1.0 + radius_ratio**parameters.exponent_n)
            ** (-parameters.exponent_k)
        )
        rtn = add(
            add(
                scale(radial_hat, parameters.a1_au_d2),
                scale(transverse_hat, parameters.a2_au_d2),
            ),
            scale(normal_hat, parameters.a3_au_d2),
        )
        return scale(rtn, distance_law)

    return acceleration


def combine_accelerations(*accelerations: Acceleration) -> Acceleration:
    """Sum independently testable acceleration terms."""

    if not accelerations:
        raise ValueError("At least one acceleration term is required")

    def acceleration(time: float, position: Vector, velocity: Vector) -> Vector:
        total: Vector = (0.0, 0.0, 0.0)
        for term in accelerations:
            total = add(total, term(time, position, velocity))
        return total

    return acceleration


def encounter_aware_step_selector(
    perturbers: tuple[Perturber, ...],
    start_jd_tdb: float,
    *,
    default_step_days: float,
    scale_factor: float = 1.0,
) -> StepSelector:
    """Reduce RK4 steps near the Sun and close planetary approaches."""

    if default_step_days <= 0.0 or scale_factor <= 0.0:
        raise ValueError("Step sizes must be positive")

    def select(time_days: float, state: State) -> float:
        epoch = start_jd_tdb + time_days
        step = default_step_days
        solar_distance = norm(state.position)
        if solar_distance < 0.25:
            step = min(step, 5.0 / 1440.0)
        elif solar_distance < 0.5:
            step = min(step, 15.0 / 1440.0)
        for body in perturbers:
            distance = norm(subtract(body.ephemeris.state_at(epoch).position, state.position))
            if distance < 0.002:
                step = min(step, 1.0 / 1440.0)
            elif distance < 0.01:
                step = min(step, 5.0 / 1440.0)
            elif distance < 0.05:
                step = min(step, 15.0 / 1440.0)
            elif distance < 0.2:
                step = min(step, 1.0 / 24.0)
        return step * scale_factor

    return select

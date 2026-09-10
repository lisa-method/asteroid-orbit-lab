"""First post-Newtonian Einstein--Infeld--Hoffmann test-particle term.

The implementation evaluates only the :math:`O(c^{-2})` correction from
Holman et al. (2023), Eq. (2), with ``beta = gamma = 1``; the Newtonian
acceleration is deliberately not included.  See the primary source:
https://arxiv.org/html/2303.16246v1 (Sec. III.2, Eq. 2).

Coordinates may be translated together, while velocities are interpreted as
barycentric velocities and are never shifted by a position-frame origin.
``PreparedSources`` stores immutable source data and pair quantities so a
forecast can evaluate the correction repeatedly without rebuilding them.
Massive asteroid sources can be included for Newtonian source accelerations,
while callers use ``outer_indices`` to restrict the PPN outer sum to the Sun,
planets, Moon, and Pluto as required by their force model.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable


Vector = tuple[float, float, float]


def _number(value: object, name: str, *, positive: bool = False, nonnegative: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    if positive and number <= 0.0:
        raise ValueError(f"{name} must be positive")
    if nonnegative and number < 0.0:
        raise ValueError(f"{name} must be non-negative")
    return number


def _vector(value: Iterable[object], name: str) -> Vector:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must contain three finite components")
    try:
        components = tuple(_number(component, f"{name}[{index}]") for index, component in enumerate(value))
    except TypeError as exc:
        raise ValueError(f"{name} must contain three finite components") from exc
    if len(components) != 3:
        raise ValueError(f"{name} must contain three finite components")
    return components  # type: ignore[return-value]


def _sub(left: Vector, right: Vector) -> Vector:
    return tuple(a - b for a, b in zip(left, right))  # type: ignore[return-value]


def _scale(vector: Vector, factor: float) -> Vector:
    return tuple(factor * component for component in vector)  # type: ignore[return-value]


def _add(left: Vector, right: Vector) -> Vector:
    return tuple(a + b for a, b in zip(left, right))  # type: ignore[return-value]


def _dot(left: Vector, right: Vector) -> float:
    return math.fsum(a * b for a, b in zip(left, right))


def _norm(vector: Vector) -> float:
    return math.sqrt(_dot(vector, vector))


@dataclass(frozen=True)
class EIHSource:
    """One massive source in the stable order supplied by the caller."""

    mu: float
    position: Vector
    velocity: Vector


@dataclass(frozen=True)
class PreparedSources:
    """Immutable sources and pair terms reused by :func:`eih_correction`.

    ``pair_potentials[i][j]`` is ``mu_j / |r_j-r_i|`` for ``i != j`` and zero
    on the diagonal.  ``newtonian_accelerations[j]`` includes every other
    prepared source; callers can still omit asteroid indices from the PPN
    outer loop with ``outer_indices``.
    """

    sources: tuple[EIHSource, ...]
    pair_distances: tuple[tuple[float, ...], ...]
    pair_potentials: tuple[tuple[float, ...], ...]
    newtonian_accelerations: tuple[Vector, ...]

    def __post_init__(self) -> None:
        count = len(self.sources)
        if count == 0:
            raise ValueError("at least one EIH source is required")
        if len(self.pair_distances) != count or len(self.pair_potentials) != count or len(self.newtonian_accelerations) != count:
            raise ValueError("prepared source arrays must match source count")
        for matrix in (self.pair_distances, self.pair_potentials):
            if any(len(row) != count for row in matrix):
                raise ValueError("prepared pair arrays must be square")
        if any(len(source.position) != 3 or len(source.velocity) != 3 for source in self.sources):
            raise ValueError("prepared source vectors must have three components")

    @property
    def mus(self) -> tuple[float, ...]:
        return tuple(source.mu for source in self.sources)

    @property
    def positions(self) -> tuple[Vector, ...]:
        return tuple(source.position for source in self.sources)

    @property
    def velocities(self) -> tuple[Vector, ...]:
        return tuple(source.velocity for source in self.sources)


def prepare_sources(
    mus: Iterable[object],
    positions: Iterable[Iterable[object]],
    velocities: Iterable[Iterable[object]],
) -> PreparedSources:
    """Prepare immutable source pair distances, potentials, and accelerations.

    The three iterables are consumed once and retain their input order.  A
    strictly positive ``mu`` is required for every source, because this object
    represents massive bodies in the EIH equation.
    """

    try:
        raw_mus = tuple(mus)
        raw_positions = tuple(positions)
        raw_velocities = tuple(velocities)
    except TypeError as exc:
        raise ValueError("mus, positions, and velocities must be iterables") from exc
    if not raw_mus or len(raw_mus) != len(raw_positions) or len(raw_mus) != len(raw_velocities):
        raise ValueError("mus, positions, and velocities must be non-empty and have equal length")
    sources = tuple(
        EIHSource(
            _number(mu, f"mus[{index}]", positive=True),
            _vector(position, f"positions[{index}]"),
            _vector(velocity, f"velocities[{index}]"),
        )
        for index, (mu, position, velocity) in enumerate(zip(raw_mus, raw_positions, raw_velocities))
    )
    count = len(sources)
    distances: list[list[float]] = [[0.0] * count for _ in range(count)]
    potentials: list[list[float]] = [[0.0] * count for _ in range(count)]
    accelerations: list[Vector] = []
    for i, source_i in enumerate(sources):
        acceleration = (0.0, 0.0, 0.0)
        for j, source_j in enumerate(sources):
            if i == j:
                continue
            displacement = _sub(source_j.position, source_i.position)
            distance = _norm(displacement)
            if distance == 0.0:
                raise ValueError(f"coincident source positions at indices {i} and {j}")
            distances[i][j] = distance
            potentials[i][j] = source_j.mu / distance
            acceleration = _add(acceleration, _scale(displacement, source_j.mu / distance**3))
        accelerations.append(acceleration)
    return PreparedSources(
        sources,
        tuple(tuple(row) for row in distances),
        tuple(tuple(row) for row in potentials),
        tuple(accelerations),
    )


def _indices(value: Iterable[int] | None, count: int, name: str) -> tuple[int, ...]:
    if value is None:
        return tuple(range(count))
    try:
        result = tuple(value)
    except TypeError as exc:
        raise ValueError(f"{name} must be an iterable of source indices") from exc
    if any(isinstance(index, bool) or not isinstance(index, int) for index in result):
        raise ValueError(f"{name} must contain integer source indices")
    if len(set(result)) != len(result) or any(index < 0 or index >= count for index in result):
        raise ValueError(f"{name} must contain unique in-range source indices")
    return result


def eih_correction(
    position: Iterable[object],
    velocity: Iterable[object],
    sources: PreparedSources,
    c: object,
    *,
    exclude_index: int | None = None,
    outer_indices: Iterable[int] | None = None,
) -> Vector:
    """Return the 1PN EIH acceleration correction, excluding Newtonian terms.

    ``exclude_index`` identifies a target that is itself one prepared source;
    it removes that source from ``U_i`` and from the outer ``j`` sum while
    leaving it in every other source's ``U_j``.  For a massless test particle,
    leave it as ``None``.  ``outer_indices`` defaults to every prepared source;
    pass e.g. ``(0,)`` for a Sun-only PPN outer sum.  Source velocities are
    always used as supplied (barycentric by contract).
    """

    if not isinstance(sources, PreparedSources):
        raise TypeError("sources must be PreparedSources from prepare_sources")
    target_position = _vector(position, "position")
    target_velocity = _vector(velocity, "velocity")
    speed_of_light = _number(c, "c", positive=True)
    count = len(sources.sources)
    if exclude_index is not None and (isinstance(exclude_index, bool) or not isinstance(exclude_index, int) or not 0 <= exclude_index < count):
        raise ValueError("exclude_index must be an in-range source index or None")
    selected = _indices(outer_indices, count, "outer_indices")
    if exclude_index is not None:
        selected = tuple(index for index in selected if index != exclude_index)

    if exclude_index is not None:
        source = sources.sources[exclude_index]
        if target_position != source.position or target_velocity != source.velocity:
            raise ValueError("excluded massive target must match its prepared source state")

    target_distances: list[float] = []
    ui_terms: list[float] = []
    for index, source in enumerate(sources.sources):
        distance = _norm(_sub(source.position, target_position))
        if distance == 0.0 and index != exclude_index:
            raise ValueError(f"target coincides with source index {index}")
        target_distances.append(distance)
        if index != exclude_index:
            if distance == 0.0:
                raise ValueError("target-source distance is singular")
            ui_terms.append(source.mu / distance)
    ui = math.fsum(ui_terms)
    speed_squared = _dot(target_velocity, target_velocity)
    inverse_c_squared = 1.0 / speed_of_light**2
    total = (0.0, 0.0, 0.0)
    for j in selected:
        source = sources.sources[j]
        distance = target_distances[j]
        if distance == 0.0:
            raise ValueError(f"target coincides with source index {j}")
        rj_minus_ri = _sub(source.position, target_position)
        ri_minus_rj = _scale(rj_minus_ri, -1.0)
        velocity_dot = _dot(target_velocity, source.velocity)
        radial_velocity = _dot(ri_minus_rj, source.velocity) / distance
        uj = math.fsum(sources.pair_potentials[j][k] for k in range(count) if k != j)
        bracket = (
            -4.0 * ui
            - uj
            + speed_squared
            + 2.0 * _dot(source.velocity, source.velocity)
            - 4.0 * velocity_dot
            - 1.5 * radial_velocity**2
            + 0.5 * _dot(rj_minus_ri, sources.newtonian_accelerations[j])
        )
        coefficient = source.mu / distance**3 * inverse_c_squared
        first = _scale(rj_minus_ri, coefficient * bracket)
        second_scalar = _dot(ri_minus_rj, _sub(_scale(target_velocity, 4.0), _scale(source.velocity, 3.0)))
        second = _scale(_sub(target_velocity, source.velocity), coefficient * second_scalar)
        third = _scale(sources.newtonian_accelerations[j], 3.5 * source.mu / distance * inverse_c_squared)
        total = _add(total, _add(_add(first, second), third))
    return total


__all__ = ["EIHSource", "PreparedSources", "prepare_sources", "eih_correction"]

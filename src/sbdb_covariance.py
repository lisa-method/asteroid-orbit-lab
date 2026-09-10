"""Strict SBDB covariance parsing and an element-to-state adapter.

The adapter is intentionally separate from the propagation and force modules.
It accepts the SBDB ``cov=vec`` representation used by the frozen Apophis
input.  SBDB stores the upper triangle in column-major order::

    (00, 01, 11, 02, 12, 22, ...)

The parsed mean is the six-element solution bundled with the covariance, at
the covariance epoch.  It is *not* silently replaced with the newer
``orbit.elements`` solution epoch.  The orbital elements are heliocentric
ecliptic J2000 (angles in degrees, ``q`` in AU, and ``tp`` in TDB days), and
the returned :class:`orbit_baselines.State` is heliocentric ICRF in AU and
AU/day.  The ecliptic-to-ICRF rotation uses the IAU 1976/1980 conventional
mean J2000 obliquity, 84381.448 arcsec.

``elements_to_state(..., delta=...)`` accepts a perturbation in exactly the
SBDB covariance label order ``(e, q, tp, node, peri, i, A1, A2)``.  The NG
components are deliberately ignored by the Kepler mapper and are returned
separately by :func:`load_sbdb_covariance`; this prevents applying A1/A2
twice when a caller constructs a non-gravitational force.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Sequence
from decimal import Decimal, InvalidOperation

from orbit_baselines import State


SBDB_LABELS = ("e", "q", "tp", "node", "peri", "i", "A1", "A2")
EXPECTED_SIGNATURE_VERSION = "1.3"
EXPECTED_SIGNATURE_SOURCE = "NASA/JPL Small-Body Database (SBDB) API"
MEAN_OBLIQUITY_ARCSEC = 84381.448
MEAN_OBLIQUITY_RAD = math.radians(MEAN_OBLIQUITY_ARCSEC / 3600.0)
_ELEMENT_UNITS = ("dimensionless", "au", "TDB", "deg", "deg", "deg")
_NG_UNITS = "au/d^2"

Vector = tuple[float, float, float]


def _number(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite real number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite real number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _vector(value: Sequence[object], name: str) -> Vector:
    if isinstance(value, (str, bytes)) or len(value) != 3:
        raise ValueError(f"{name} must have three components")
    result = tuple(_number(item, f"{name}[{index}]") for index, item in enumerate(value))
    return result  # type: ignore[return-value]


@dataclass(frozen=True)
class SBDBCovariance:
    """Immutable parsed covariance and NG provenance.

    ``mean_elements`` contains canonical keys ``e, q, tp, node, peri, i``.
    ``non_grav_nominal`` is the SBDB A1/A2 estimate, while
    ``non_grav_sigma`` is the pair of marginal 1-sigma values from the
    covariance diagonal.  Neither is folded into the Kepler state by this
    module.
    """

    schema_version: str
    schema_source: str
    source_sha256: str
    object_id: str
    object_name: str
    covariance_epoch_jd_tdb: float
    covariance_epoch_text: str
    covariance_epoch_cd: str | None
    mean_elements: Mapping[str, float]
    mean_element_text: Mapping[str, str]
    labels: tuple[str, ...]
    covariance: tuple[tuple[float, ...], ...]
    covariance_units: tuple[str, ...]
    solution_epoch_jd_tdb: float
    solution_epoch_cd: str | None
    solution_date: str | None
    non_grav_nominal: tuple[float, float]
    non_grav_sigma: tuple[float, float]
    non_grav_units: str
    non_grav_availability_basis: str
    frame: str
    obliquity_arcsec: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "mean_elements", MappingProxyType(dict(self.mean_elements)))
        object.__setattr__(self, "mean_element_text", MappingProxyType(dict(self.mean_element_text)))

    @property
    def ng_nominal(self) -> tuple[float, float]:
        """Alias for the separate nominal ``(A1, A2)`` values."""
        return self.non_grav_nominal

    @property
    def ng_sigma(self) -> tuple[float, float]:
        """Alias for the marginal covariance ``(A1, A2)`` sigmas."""
        return self.non_grav_sigma


def _parse_element_values(covariance: Mapping[str, object]) -> tuple[dict[str, float], dict[str, str]]:
    raw = covariance.get("elements")
    if not isinstance(raw, list) or not raw:
        raise ValueError("SBDB covariance.elements is required")
    aliases = {"e": "e", "q": "q", "tp": "tp", "om": "node", "w": "peri", "i": "i"}
    result: dict[str, float] = {}
    original_text: dict[str, str] = {}
    for index, item in enumerate(raw):
        entry = _mapping(item, f"covariance.elements[{index}]")
        name = entry.get("name")
        if not isinstance(name, str) or name not in aliases:
            raise ValueError(f"unsupported covariance element {name!r}")
        canonical = aliases[name]
        if canonical in result:
            raise ValueError(f"duplicate covariance element {canonical}")
        expected = _ELEMENT_UNITS[("e", "q", "tp", "node", "peri", "i").index(canonical)]
        units = entry.get("units")
        if canonical == "e":
            if units not in (None, ""):
                raise ValueError("eccentricity must be dimensionless")
        elif units != expected:
            raise ValueError(f"unsupported units for {canonical}: {units!r}")
        raw_value = entry.get("value")
        result[canonical] = _number(raw_value, f"covariance.elements[{name}].value")
        original_text[canonical] = raw_value if isinstance(raw_value, str) else repr(raw_value)
    required = ("e", "q", "tp", "node", "peri", "i")
    if tuple(sorted(result)) != tuple(sorted(required)):
        raise ValueError("covariance.elements must contain exactly the six orbital elements")
    return result, original_text


def _parse_covariance_matrix(covariance: Mapping[str, object]) -> tuple[tuple[float, ...], ...]:
    labels = covariance.get("labels")
    if not isinstance(labels, list) or tuple(labels) != SBDB_LABELS:
        raise ValueError("unsupported or incorrectly ordered SBDB covariance labels")
    data = covariance.get("data")
    if not isinstance(data, list) or len(data) != 36:
        raise ValueError("SBDB covariance data must contain 36 upper-triangle values")
    matrix = [[0.0] * 8 for _ in range(8)]
    index = 0
    for column in range(8):
        for row in range(column + 1):
            value = _number(data[index], f"covariance.data[{index}]")
            matrix[row][column] = value
            matrix[column][row] = value
            index += 1
    # A covariance variance cannot be negative.  Keep off-diagonal signs and
    # all cross-covariances exactly as supplied; PSD is a separate caller policy.
    if any(matrix[index][index] < 0.0 for index in range(8)):
        raise ValueError("covariance diagonal must be non-negative")
    return tuple(tuple(row) for row in matrix)


def _parse_model_parameters(orbit: Mapping[str, object]) -> tuple[tuple[float, float], str]:
    raw = orbit.get("model_pars")
    if not isinstance(raw, list):
        raise ValueError("SBDB orbit.model_pars is required for NG provenance")
    values: dict[str, float] = {}
    for index, item in enumerate(raw):
        entry = _mapping(item, f"model_pars[{index}]")
        name = entry.get("name")
        if name not in ("A1", "A2"):
            continue
        if name in values:
            raise ValueError(f"duplicate model parameter {name}")
        if entry.get("kind") != "EST" or entry.get("units") != _NG_UNITS:
            raise ValueError(f"unsupported kind or units for model parameter {name}")
        values[name] = _number(entry.get("value"), f"model_pars[{name}].value")
    if set(values) != {"A1", "A2"}:
        raise ValueError("SBDB model_pars must provide estimated A1 and A2")
    return (values["A1"], values["A2"]), _NG_UNITS


def load_sbdb_covariance(path: Path) -> SBDBCovariance:
    """Read and strictly validate one SBDB ``cov=vec`` JSON response."""

    if not isinstance(path, Path):
        raise TypeError("path must be a pathlib.Path")
    body = path.read_bytes()
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid SBDB JSON document") from exc
    root = _mapping(document, "SBDB response")
    signature = _mapping(root.get("signature"), "signature")
    if signature.get("version") != EXPECTED_SIGNATURE_VERSION:
        raise ValueError("unsupported SBDB schema version")
    if signature.get("source") != EXPECTED_SIGNATURE_SOURCE:
        raise ValueError("unsupported SBDB response source")
    obj = _mapping(root.get("object"), "object")
    object_id = obj.get("des")
    if not isinstance(object_id, str) or not object_id.strip():
        raise ValueError("SBDB object designation is required")
    orbit = _mapping(root.get("orbit"), "orbit")
    if orbit.get("equinox") != "J2000":
        raise ValueError("SBDB orbit.equinox must be exactly J2000")
    cov = _mapping(orbit.get("covariance"), "orbit.covariance")
    cov_epoch_token = cov.get("epoch")
    cov_epoch = _number(cov_epoch_token, "covariance.epoch")
    cov_epoch_text = cov_epoch_token if isinstance(cov_epoch_token, str) else repr(cov_epoch_token)
    solution_epoch = _number(orbit.get("epoch"), "orbit.epoch")
    mean, mean_text = _parse_element_values(cov)
    matrix = _parse_covariance_matrix(cov)
    nominal, ng_units = _parse_model_parameters(orbit)
    sigma = (math.sqrt(max(matrix[6][6], 0.0)), math.sqrt(max(matrix[7][7], 0.0)))
    solution_date = orbit.get("soln_date")
    if solution_date is not None and not isinstance(solution_date, str):
        raise ValueError("orbit.soln_date must be a string or null")
    return SBDBCovariance(
        schema_version=signature["version"],
        schema_source=signature["source"],
        source_sha256=hashlib.sha256(body).hexdigest(),
        object_id=object_id,
        object_name=str(obj.get("fullname") or obj.get("shortname") or object_id),
        covariance_epoch_jd_tdb=cov_epoch,
        covariance_epoch_text=cov_epoch_text,
        covariance_epoch_cd=cov.get("epoch_cd") if isinstance(cov.get("epoch_cd"), str) else None,
        mean_elements=mean,
        mean_element_text=mean_text,
        labels=SBDB_LABELS,
        covariance=matrix,
        covariance_units=_ELEMENT_UNITS + (_NG_UNITS, _NG_UNITS),
        solution_epoch_jd_tdb=solution_epoch,
        solution_epoch_cd=orbit.get("epoch_cd") if isinstance(orbit.get("epoch_cd"), str) else None,
        solution_date=solution_date,
        non_grav_nominal=nominal,
        non_grav_sigma=sigma,
        non_grav_units=ng_units,
        non_grav_availability_basis="SBDB soln_date provenance; forecast availability is caller-defined",
        frame="heliocentric ecliptic J2000",
        obliquity_arcsec=MEAN_OBLIQUITY_ARCSEC,
    )


def _check_delta(delta: Sequence[object] | None) -> tuple[float, ...]:
    if delta is None:
        return (0.0,) * 8
    if isinstance(delta, (str, bytes)) or len(delta) != 8:
        raise ValueError("delta must have the eight SBDB covariance components")
    return tuple(_number(value, f"delta[{index}]") for index, value in enumerate(delta))


def _validate_elements(elements: Mapping[str, object], epoch: object, mu_sun: object) -> tuple[dict[str, float], float, float]:
    if not isinstance(elements, Mapping):
        raise TypeError("elements must be a mapping")
    required = ("e", "q", "tp", "node", "peri", "i")
    if any(key not in elements for key in required):
        raise ValueError("elements must contain e, q, tp, node, peri, and i")
    converted = {key: _number(elements[key], f"elements.{key}") for key in required}
    epoch_value = _number(epoch, "epoch_jd_tdb")
    mu_value = _number(mu_sun, "mu_sun")
    if not 0.0 <= converted["e"] < 1.0:
        raise ValueError("elliptic eccentricity must satisfy 0 <= e < 1")
    if converted["q"] <= 0.0 or mu_value <= 0.0:
        raise ValueError("q and mu_sun must be positive")
    return converted, epoch_value, mu_value


def _decimal_literal(value: object, name: str) -> Decimal:
    """Parse a finite decimal token without first rounding a long JD string."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite real number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a finite real number") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _solve_kepler(mean_anomaly: float, eccentricity: float) -> float:
    reduced = math.remainder(mean_anomaly, 2.0 * math.pi)
    if eccentricity == 0.0:
        return reduced
    lo, hi = -math.pi, math.pi
    estimate = reduced if eccentricity < 0.8 else math.copysign(math.pi, reduced or 1.0)
    for _ in range(48):
        value = estimate - eccentricity * math.sin(estimate) - reduced
        if abs(value) <= 2.0e-15:
            return estimate
        if value > 0.0:
            hi = estimate
        else:
            lo = estimate
        derivative = 1.0 - eccentricity * math.cos(estimate)
        candidate = estimate - value / derivative
        if not lo < candidate < hi or not math.isfinite(candidate):
            candidate = 0.5 * (lo + hi)
        estimate = candidate
    raise ArithmeticError("elliptic Kepler solver did not converge")


def _rotate_perifocal(vector: Vector, node: float, inclination: float, peri: float) -> Vector:
    x, y, z = vector
    co, so = math.cos(node), math.sin(node)
    ci, si = math.cos(inclination), math.sin(inclination)
    cw, sw = math.cos(peri), math.sin(peri)
    return (
        (co * cw - so * sw * ci) * x + (-co * sw - so * cw * ci) * y,
        (so * cw + co * sw * ci) * x + (-so * sw + co * cw * ci) * y,
        (sw * si) * x + (cw * si) * y,
    )


def ecliptic_to_icrf(vector: Sequence[object]) -> Vector:
    """Rotate a J2000 ecliptic vector into ICRF using the fixed obliquity."""
    x, y, z = _vector(vector, "ecliptic vector")
    c, s = math.cos(MEAN_OBLIQUITY_RAD), math.sin(MEAN_OBLIQUITY_RAD)
    return (x, c * y - s * z, s * y + c * z)


def icrf_to_ecliptic(vector: Sequence[object]) -> Vector:
    """Inverse of :func:`ecliptic_to_icrf`."""
    x, y, z = _vector(vector, "ICRF vector")
    c, s = math.cos(MEAN_OBLIQUITY_RAD), math.sin(MEAN_OBLIQUITY_RAD)
    return (x, c * y + s * z, -s * y + c * z)


def elements_to_state(
    elements: Mapping[str, object],
    epoch_jd_tdb: object,
    mu_sun: object,
    *,
    delta: Sequence[object] | None = None,
) -> State:
    """Map elliptic SBDB elements to a heliocentric ICRF state.

    ``delta`` follows :data:`SBDB_LABELS`.  In particular, ``delta[2]`` is
    applied as a relative time offset in ``(epoch - tp) - delta[2]``; it is
    never added to a large absolute ``tp`` Julian date.
    """
    base, epoch, mu = _validate_elements(elements, epoch_jd_tdb, mu_sun)
    offsets = _check_delta(delta)
    e = base["e"] + offsets[0]
    q = base["q"] + offsets[1]
    if not 0.0 <= e < 1.0 or q <= 0.0:
        raise ValueError("delta produces invalid elliptic elements")
    # Keep this subtraction grouped: it preserves the intended small tp
    # perturbation without replacing the well-resolved epoch difference.
    # SBDB's long ``tp`` token contains more information than a binary float.
    # Keep the subtraction in Decimal and apply the perturbation only after
    # forming the epoch difference.  For ordinary float callers, Decimal(str)
    # preserves the decimal representation they supplied without inventing
    # binary-rounding noise.
    elapsed = float(_decimal_literal(epoch_jd_tdb, "epoch_jd_tdb") -
                    _decimal_literal(elements["tp"], "elements.tp")) - offsets[2]
    node = math.radians(base["node"] + offsets[3])
    peri = math.radians(base["peri"] + offsets[4])
    inclination = math.radians(base["i"] + offsets[5])
    semimajor = q / (1.0 - e)
    mean_motion = math.sqrt(mu / (semimajor * semimajor * semimajor))
    eccentric_anomaly = _solve_kepler(mean_motion * elapsed, e)
    cos_e, sin_e = math.cos(eccentric_anomaly), math.sin(eccentric_anomaly)
    root = math.sqrt(1.0 - e * e)
    denominator = 1.0 - e * cos_e
    position_pf = (semimajor * (cos_e - e), semimajor * root * sin_e, 0.0)
    speed_factor = mean_motion * semimajor / denominator
    velocity_pf = (-speed_factor * sin_e, speed_factor * root * cos_e, 0.0)
    return State(
        ecliptic_to_icrf(_rotate_perifocal(position_pf, node, inclination, peri)),
        ecliptic_to_icrf(_rotate_perifocal(velocity_pf, node, inclination, peri)),
    )


def state_from_covariance(
    parsed: SBDBCovariance,
    mu_sun: object,
    *,
    epoch_jd_tdb: object | None = None,
    delta: Sequence[object] | None = None,
) -> State:
    """Map parsed covariance elements, defaulting to their covariance epoch."""
    if not isinstance(parsed, SBDBCovariance):
        raise TypeError("parsed must be an SBDBCovariance")
    epoch = parsed.covariance_epoch_text if epoch_jd_tdb is None else epoch_jd_tdb
    elements = dict(parsed.mean_elements)
    # Keep the exact SBDB tp token on the state-mapping path while exposing
    # numeric mean_elements for callers and serialization.
    elements["tp"] = parsed.mean_element_text["tp"]
    return elements_to_state(elements, epoch, mu_sun, delta=delta)

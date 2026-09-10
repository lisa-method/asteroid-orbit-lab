"""Forecast-time availability and validation for Horizons NG parameters.

The raw Horizons response is the source of the physical parameters.  Download
timestamps are provenance only: an osculating epoch and an orbit solution date
do not establish when the fitted parameters were available to a forecaster.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

from planetary_dynamics import NonGravitationalParameters


_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][-+]?\d+)?"
_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _validate_finite(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _validate_parameters(parameters: NonGravitationalParameters) -> None:
    values = {
        "a1_au_d2": parameters.a1_au_d2,
        "a2_au_d2": parameters.a2_au_d2,
        "a3_au_d2": parameters.a3_au_d2,
        "alpha": parameters.alpha,
        "exponent_m": parameters.exponent_m,
        "exponent_n": parameters.exponent_n,
        "exponent_k": parameters.exponent_k,
        "r0_au": parameters.r0_au,
    }
    for label, value in values.items():
        _validate_finite(value, label)
    if parameters.alpha <= 0.0 or parameters.r0_au <= 0.0:
        raise ValueError("NG alpha and r0_au must be positive")
    if any(value < 0.0 for value in (parameters.exponent_m, parameters.exponent_n, parameters.exponent_k)):
        raise ValueError("NG exponents must be non-negative")


def _calendar_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        pass
    match = re.match(r"^(\d{4})-([A-Za-z]{3})-(\d{2})", text)
    if not match or match.group(2).title() not in _MONTHS:
        return None
    try:
        return dt.date(int(match.group(1)), _MONTHS[match.group(2).title()], int(match.group(3)))
    except ValueError:
        return None


def _midnight_jd(date: dt.date) -> float:
    # Gregorian midnight JD; the two-day margin is deliberately conservative
    # and avoids pretending that UTC-to-TDB conversion is exact at a boundary.
    return float(date.toordinal() + 1721424.5 + 2.0)


def _utc_date(value: Any) -> dt.date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(dt.timezone.utc).date()


def _header(document: dict[str, Any]) -> str:
    result = document.get("result")
    if not isinstance(result, str):
        raise ValueError("Raw response has no string result")
    return result.split("$$SOE", 1)[0]


def _extract_values(header: str, label: str) -> float:
    # Capture the complete non-whitespace token, then validate it as a whole.
    # This rejects malformed values such as ``A2=1e`` and ``A2=1junk`` rather
    # than silently accepting their numeric prefixes.
    tokens = re.findall(rf"\b{label}\s*=\s*([^\s,;]+)", header, re.IGNORECASE)
    matches = [token for token in tokens if re.fullmatch(_NUMBER, token)]
    if len(tokens) != 1:
        raise ValueError(f"Duplicate or malformed non-gravitational header: {label}")
    if not matches:
        raise ValueError(f"Incomplete non-gravitational header: {label}")
    return _validate_finite(matches[0].replace("D", "E").replace("d", "e"), label)


def _explicit_sigma(document: dict[str, Any]) -> tuple[float, float, float] | None:
    metadata = document.get("metadata")
    candidates: list[Any] = [document.get("sigma_au_d2"), document.get("non_gravitational_sigma")]
    if isinstance(metadata, dict):
        candidates.extend((metadata.get("sigma_au_d2"), metadata.get("non_gravitational_sigma")))
    raw = next((candidate for candidate in candidates if candidate is not None), None)
    if raw is None:
        return None
    if isinstance(raw, dict):
        raw = (raw.get("a1"), raw.get("a2"), raw.get("a3"))
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise ValueError("sigma_au_d2 must contain three values")
    sigma = tuple(_validate_finite(value, "sigma_au_d2") for value in raw)
    if any(value < 0.0 for value in sigma):
        raise ValueError("sigma_au_d2 must be non-negative")
    return sigma  # type: ignore[return-value]


def _path_matches(record_path: Any, path: Path, manifest: Path) -> bool:
    if not isinstance(record_path, str):
        return False
    candidate = Path(record_path)
    if not candidate.is_absolute():
        candidate = manifest.parent.parent.parent / candidate
    try:
        return candidate.resolve() == path.resolve()
    except OSError:
        return False


def _manifest_timestamps(path: Path, actual_sha256: str, actual_bytes: int) -> list[tuple[dt.date, str]]:
    """Return matching manifest timestamps, if a local manifest records one."""
    found: list[tuple[dt.date, str]] = []
    seen: set[Path] = set()
    for ancestor in (path.resolve(), *path.resolve().parents):
        directory = ancestor / "data" / "checksums"
        if not directory.is_dir():
            continue
        for manifest in sorted(directory.glob("*manifest.json")):
            manifest = manifest.resolve()
            if manifest in seen:
                continue
            seen.add(manifest)
            try:
                document = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            records = document.get("files")
            if not isinstance(records, list):
                continue
            matching = next(
                (record for record in records
                 if isinstance(record, dict)
                 and _path_matches(record.get("path"), path, manifest)
                 and str(record.get("sha256", "")).lower() == actual_sha256
                 and ("bytes" not in record or record.get("bytes") == actual_bytes)),
                None,
            )
            if matching is None:
                continue
            timestamp = matching.get("retrieved_at_utc") or matching.get("downloaded_at_utc")
            key = "files[].retrieved_at_utc"
            if timestamp is None:
                timestamp = document.get("retrieved_at_utc")
                key = "retrieved_at_utc"
            if timestamp is None:
                timestamp = document.get("created_utc")
                key = "created_utc"
            date = _utc_date(timestamp)
            if date is not None:
                try:
                    display_path = manifest.relative_to(path.resolve().parents[3])
                except (ValueError, IndexError):
                    display_path = manifest
                manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()
                found.append((date, f"{display_path}:{key}; manifest_sha256={manifest_sha256}"))
    return found


def _downloaded_dates(document: dict[str, Any], path: Path, actual_sha256: str, actual_bytes: int) -> list[tuple[dt.date, str]]:
    found: list[tuple[dt.date, str]] = []
    candidates: list[tuple[Any, str]] = [
        (document.get("retrieved_at_utc"), "raw:retrieved_at_utc"),
        (document.get("downloaded_at_utc"), "raw:downloaded_at_utc"),
    ]
    metadata = document.get("metadata")
    if isinstance(metadata, dict):
        candidates.extend((metadata.get(key), f"raw:metadata.{key}") for key in ("retrieved_at_utc", "downloaded_at_utc"))
    for timestamp, basis in candidates:
        date = _utc_date(timestamp)
        if date is not None:
            found.append((date, f"{basis}; raw_sha256={actual_sha256}"))
    found.extend(_manifest_timestamps(path, actual_sha256, actual_bytes))
    return found


@dataclass(frozen=True)
class NGInput:
    parameters: NonGravitationalParameters | None = None
    source: str = "unknown"
    source_sha256: str | None = None
    available_from_jd_tdb: float | None = None
    availability_basis: str | None = None
    sigma_au_d2: tuple[float, float, float] | None = None
    solution_date: str | None = None

    def __post_init__(self) -> None:
        if self.parameters is not None:
            if not isinstance(self.parameters, NonGravitationalParameters):
                raise ValueError("parameters must be NonGravitationalParameters or None")
            _validate_parameters(self.parameters)
        elif self.sigma_au_d2 is not None:
            raise ValueError("sigma_au_d2 requires NG parameters")
        if self.available_from_jd_tdb is not None:
            _validate_finite(self.available_from_jd_tdb, "available_from_jd_tdb")
            if not self.source.strip() or self.source.strip().lower() == "unknown":
                raise ValueError("known availability requires a non-empty source")
            if not self.availability_basis or not self.availability_basis.strip():
                raise ValueError("known availability requires availability_basis")
        if self.sigma_au_d2 is not None:
            if len(self.sigma_au_d2) != 3:
                raise ValueError("sigma_au_d2 must contain three values")
            for value in self.sigma_au_d2:
                if _validate_finite(value, "sigma_au_d2") < 0.0:
                    raise ValueError("sigma_au_d2 must be non-negative")

    def status(self, start_jd: float) -> str:
        start = _validate_finite(start_jd, "start_jd_tdb")
        if self.parameters is None:
            return "not_provided"
        if self.available_from_jd_tdb is None:
            return "availability_unknown"
        return "available" if start >= self.available_from_jd_tdb else "not_yet_available"


def load_ng_input(path: Path) -> NGInput:
    """Load and validate only the NG header and local provenance metadata."""
    path = Path(path)
    payload = path.read_bytes()
    source_sha256 = hashlib.sha256(payload).hexdigest()
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid raw JSON: {path}") from exc
    if not isinstance(document, dict):
        raise ValueError("Raw response must be a JSON object")
    header = _header(document)
    solution_match = re.search(r"Soln\.date:\s*([^\s]+)", header, re.IGNORECASE)
    solution_date = solution_match.group(1) if solution_match else None
    marker = re.search(r"Asteroid non-gravitational force model", header, re.IGNORECASE)
    if marker is None:
        return NGInput(parameters=None, source="Horizons header", source_sha256=source_sha256, solution_date=solution_date)

    section_end = header.find("\n*******************************************************************************", marker.end())
    section = header[marker.start():section_end if section_end >= 0 else len(header)]
    # The marker line itself contains unit notation such as ``A3=au/d^2``;
    # parse assignments only from the following parameter lines.
    first_value_line = section.find("\n")
    section = section[first_value_line + 1:] if first_value_line >= 0 else ""
    values = {
        "a1_au_d2": _extract_values(section, "A1"),
        "a2_au_d2": _extract_values(section, "A2"),
        "a3_au_d2": _extract_values(section, "A3"),
        "alpha": _extract_values(section, "ALN"),
        "exponent_k": _extract_values(section, "NK"),
        "exponent_m": _extract_values(section, "NM"),
        "exponent_n": _extract_values(section, "NN"),
        "r0_au": _extract_values(section, "R0"),
    }
    parameters = NonGravitationalParameters(**values)
    _validate_parameters(parameters)
    sigma = _explicit_sigma(document)
    dates = _downloaded_dates(document, path, source_sha256, len(payload))
    availability = None
    basis = None
    if dates:
        latest_date, latest_basis = max(dates, key=lambda item: item[0])
        bounds = [("downloaded UTC date + 2 d", _midnight_jd(latest_date))]
        solution = _calendar_date(solution_date)
        if solution is not None:
            bounds.append(("solution calendar date + 2 d", _midnight_jd(solution)))
        availability = max(value for _, value in bounds)
        basis = "; ".join(label for label, _ in bounds) + f"; timestamp source={latest_basis}"
    else:
        basis = "downloaded UTC timestamp unavailable; solution date is descriptive only"
    return NGInput(parameters=parameters, source="Horizons header", source_sha256=source_sha256,
                   available_from_jd_tdb=availability, availability_basis=basis,
                   sigma_au_d2=sigma, solution_date=solution_date)

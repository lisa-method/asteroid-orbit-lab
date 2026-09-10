"""Read-only SPK Type 2 Chebyshev records, without a SPICE dependency.

The binary layouts follow the official NAIF DAF and SPK specifications:
https://naif.jpl.nasa.gov/pub/naif/toolkit_docs/C/req/daf.html and
https://naif.jpl.nasa.gov/pub/naif/toolkit_docs/C/req/spk.html (Type 2).
This small backend parses bytes supplied by a caller; it performs no file or
network I/O.  It supports J2000/ICRF frame code 1 and SPK type 2 only.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
import struct
from collections.abc import Iterable, Mapping

from orbit_baselines import State


_RECORD_BYTES = 1024
_SUPPORTED_FRAME = 1  # J2000, the ICRF-compatible frame used by DE441.


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _integral(value: object, name: str, *, positive: bool = False, nonnegative: bool = False) -> int:
    result = _finite(value, name)
    if result != math.trunc(result):
        raise ValueError(f"{name} must be integral")
    integer = int(result)
    if positive and integer <= 0:
        raise ValueError(f"{name} must be positive")
    if nonnegative and integer < 0:
        raise ValueError(f"{name} must be non-negative")
    return integer


def _fmt(endian: str) -> str:
    if endian not in {"<", ">"}:
        raise ValueError("endian must be '<' or '>'")
    return endian


def parse_file_record(data: bytes) -> dict[str, object]:
    """Parse a 1024-byte DAF/SPK file record and detect byte order."""

    if not isinstance(data, (bytes, bytearray, memoryview)) or len(data) < _RECORD_BYTES:
        raise ValueError("DAF file record must contain at least 1024 bytes")
    raw = bytes(data[:_RECORD_BYTES])
    if raw[:8] not in (b"DAF/SPK ", b"DAF/SPK"):
        raise ValueError("file record is not a DAF/SPK record")
    candidates = []
    for endian in ("<", ">"):
        nd, ni = struct.unpack_from(endian + "ii", raw, 8)
        if 1 <= nd <= 10 and 1 <= ni <= 20:
            candidates.append((endian, nd, ni))
    if len(candidates) != 1:
        raise ValueError("unable to determine unique DAF byte order")
    endian, nd, ni = candidates[0]
    if (nd, ni) != (2, 6):
        raise ValueError("only standard SPK summaries ND=2, NI=6 are supported")
    expected_format = b"LTL-IEEE" if endian == "<" else b"BIG-IEEE"
    if raw[88:96] != expected_format:
        raise ValueError("DAF LOCFMT does not match detected byte order")
    forward, backward, free = struct.unpack_from(endian + "iii", raw, 76)
    if forward < 0 or backward < 0 or free < 0:
        raise ValueError("DAF record pointers must be non-negative")
    return {
        "idword": raw[:8].decode("ascii", errors="replace").rstrip(),
        "endian": endian,
        "ND": nd,
        "NI": ni,
        "nd": nd,
        "ni": ni,
        "forward": forward,
        "backward": backward,
        "free": free,
    }


def parse_summary_record(data: bytes, endian: str) -> dict[str, object]:
    """Parse one SPK summary record for the standard ``ND=2, NI=6`` layout."""

    if not isinstance(data, (bytes, bytearray, memoryview)) or len(data) < _RECORD_BYTES:
        raise ValueError("summary record must contain at least 1024 bytes")
    fmt = _fmt(endian)
    raw = bytes(data[:_RECORD_BYTES])
    next_record, previous_record, count_value = struct.unpack_from(fmt + "ddd", raw, 0)
    next_record_i = _integral(next_record, "summary next", nonnegative=True)
    previous_record_i = _integral(previous_record, "summary previous", nonnegative=True)
    count = _integral(count_value, "summary count", nonnegative=True)
    summary_size = 2 * 8 + 6 * 4
    capacity = (_RECORD_BYTES - 24) // summary_size
    if count > capacity:
        raise ValueError("summary count exceeds record capacity")
    segments = []
    for index in range(count):
        offset = 24 + index * summary_size
        start_et, end_et = struct.unpack_from(fmt + "dd", raw, offset)
        target, center, frame, segment_type, first_address, last_address = struct.unpack_from(fmt + "iiiiii", raw, offset + 16)
        if not all(math.isfinite(value) for value in (start_et, end_et)) or end_et < start_et:
            raise ValueError("summary segment has invalid time bounds")
        if first_address <= 0 or last_address < first_address:
            raise ValueError("summary segment has invalid DAF addresses")
        segments.append({
            "start_et": start_et,
            "end_et": end_et,
            "target": target,
            "center": center,
            "frame": frame,
            "type": segment_type,
            "first_address": first_address,
            "last_address": last_address,
        })
    return {"next": next_record_i, "prev": previous_record_i, "previous": previous_record_i, "segments": segments}


def parse_type2_directory(data: bytes, endian: str) -> tuple[float, float, int, int]:
    """Parse the four-double Type 2 directory: INIT, INTLEN, RSIZE, N."""

    if not isinstance(data, (bytes, bytearray, memoryview)) or len(data) < 32:
        raise ValueError("Type 2 directory must contain 32 bytes")
    init_et, interval_length, record_size_value, count_value = struct.unpack_from(_fmt(endian) + "dddd", bytes(data[:32]))
    init_et = _finite(init_et, "INIT")
    interval_length = _finite(interval_length, "INTLEN")
    if interval_length <= 0.0:
        raise ValueError("INTLEN must be positive")
    record_size = _integral(record_size_value, "RSIZE", positive=True)
    count = _integral(count_value, "N", positive=True)
    if record_size < 5 or (record_size - 2) % 3 != 0:
        raise ValueError("RSIZE is incompatible with three Chebyshev coefficient groups")
    return init_et, interval_length, record_size, count


@dataclass(frozen=True)
class _Type2Record:
    midpoint: float
    radius: float
    coefficients: tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]


@dataclass(frozen=True)
class Type2Segment:
    """Immutable Type 2 records with split-epoch state and acceleration access."""

    metadata: Mapping[str, object]
    records: tuple[_Type2Record, ...]
    directory: tuple[float, float, int, int]
    first_record_index: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        if self.metadata.get("type") != 2:
            raise ValueError("only SPK Type 2 segments are supported")
        if self.metadata.get("frame") != _SUPPORTED_FRAME:
            raise ValueError("only SPK frame 1 (J2000/ICRF) is supported")
        start = _finite(self.metadata.get("start_et"), "segment start_et")
        end = _finite(self.metadata.get("end_et"), "segment end_et")
        if end < start:
            raise ValueError("segment end_et must be at or after start_et")
        init_et, interval_length, record_size, count = self.directory
        _finite(init_et, "INIT")
        _finite(interval_length, "INTLEN")
        if interval_length <= 0.0 or record_size < 5 or count <= 0 or not self.records:
            raise ValueError("invalid Type 2 directory or record count")
        if self.first_record_index < 0:
            raise ValueError("first_record_index must be non-negative")
        if self.first_record_index + len(self.records) > count:
            raise ValueError("coefficient records exceed Type 2 directory count")

    @classmethod
    def from_records(
        cls,
        metadata: Mapping[str, object],
        raw_record_bytes: bytes,
        endian: str,
        first_record_index: int,
        directory: tuple[float, float, int, int] | Mapping[str, object],
    ) -> "Type2Segment":
        """Decode coefficient records from a byte range.

        ``first_record_index`` is the zero-based index of the first record in
        ``raw_record_bytes`` relative to the segment's coefficient records.
        The directory is supplied separately, so the byte range contains only
        complete ``RSIZE``-double coefficient records.
        """

        if not isinstance(metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        if not isinstance(raw_record_bytes, (bytes, bytearray, memoryview)):
            raise TypeError("raw_record_bytes must be bytes-like")
        if isinstance(first_record_index, bool) or not isinstance(first_record_index, int) or first_record_index < 0:
            raise ValueError("first_record_index must be a non-negative integer")
        if isinstance(directory, Mapping):
            directory_tuple = (
                _finite(directory.get("init_et", directory.get("INIT")), "INIT"),
                _finite(directory.get("interval_length", directory.get("INTLEN")), "INTLEN"),
                _integral(directory.get("record_size", directory.get("RSIZE")), "RSIZE", positive=True),
                _integral(directory.get("count", directory.get("N")), "N", positive=True),
            )
        else:
            if len(directory) != 4:
                raise ValueError("Type 2 directory needs four values")
            directory_tuple = (
                _finite(directory[0], "INIT"), _finite(directory[1], "INTLEN"),
                _integral(directory[2], "RSIZE", positive=True), _integral(directory[3], "N", positive=True),
            )
        init_et, interval_length, record_size, count = directory_tuple
        if interval_length <= 0.0 or record_size < 5 or (record_size - 2) % 3 != 0:
            raise ValueError("invalid Type 2 directory")
        raw = bytes(raw_record_bytes)
        record_bytes = record_size * 8
        if len(raw) == 0 or len(raw) % record_bytes:
            raise ValueError("raw Type 2 bytes must contain complete coefficient records")
        available_count = len(raw) // record_bytes
        if first_record_index + available_count > count:
            raise ValueError("raw Type 2 records exceed directory count")
        fmt = _fmt(endian)
        coefficient_count = (record_size - 2) // 3
        records = []
        for index in range(available_count):
            offset = index * record_bytes
            values = struct.unpack_from(fmt + f"{record_size}d", raw, offset)
            midpoint = _finite(values[0], f"record[{index}] midpoint")
            radius = _finite(values[1], f"record[{index}] radius")
            if radius <= 0.0:
                raise ValueError("Type 2 record radius must be positive")
            coefficients = (
                tuple(_finite(value, f"record[{index}] x coefficient") for value in values[2:2 + coefficient_count]),
                tuple(_finite(value, f"record[{index}] y coefficient") for value in values[2 + coefficient_count:2 + 2 * coefficient_count]),
                tuple(_finite(value, f"record[{index}] z coefficient") for value in values[2 + 2 * coefficient_count:]),
            )
            records.append(_Type2Record(midpoint, radius, coefficients))
        if any(right.midpoint <= left.midpoint for left, right in zip(records, records[1:])):
            raise ValueError("Type 2 record midpoints must be strictly increasing")
        for index, record in enumerate(records):
            global_index = first_record_index + index
            expected_midpoint = init_et + (global_index + 0.5) * interval_length
            expected_radius = 0.5 * interval_length
            if not math.isclose(record.midpoint, expected_midpoint, rel_tol=1e-13, abs_tol=1e-6):
                raise ValueError("Type 2 record midpoint disagrees with directory")
            if not math.isclose(record.radius, expected_radius, rel_tol=1e-13, abs_tol=1e-6):
                raise ValueError("Type 2 record radius disagrees with directory")
        return cls(metadata, tuple(records), directory_tuple, first_record_index)

    def _record_for(self, origin_et_seconds: float, delta_seconds: float) -> tuple[_Type2Record, float]:
        origin = _finite(origin_et_seconds, "origin_et_seconds")
        delta = _finite(delta_seconds, "delta_seconds")
        start = _finite(self.metadata["start_et"], "segment start_et")
        end = _finite(self.metadata["end_et"], "segment end_et")
        relative = (origin - start) + delta
        if relative < 0.0 or relative > end - start:
            raise ValueError("requested ET lies outside Type 2 segment; extrapolation is disabled")
        # Binary search with signed differences avoids forming the large
        # absolute requested ET (and also avoids subtracting a distant segment
        # start from a midpoint when normalizing the Chebyshev argument).
        low, high = 0, len(self.records)
        while low < high:
            middle = (low + high) // 2
            signed = (origin - self.records[middle].midpoint) + delta
            if signed > 0.0:
                low = middle + 1
            else:
                high = middle
        candidates = range(max(0, low - 2), min(len(self.records), low + 3))
        matches = []
        for index in candidates:
            record = self.records[index]
            signed = (origin - record.midpoint) + delta
            argument = signed / record.radius
            if -1.0 <= argument <= 1.0:
                matches.append((index, record, argument))
        if matches:
            # At a shared boundary SPK's interval floor is the right record;
            # selecting the greatest matching index also selects the last
            # record at the segment's final endpoint.
            _, record, argument = matches[-1]
            return record, max(-1.0, min(1.0, argument))
        raise ValueError("requested ET does not lie in a Type 2 record")

    @staticmethod
    def _evaluate(record: _Type2Record, argument: float) -> tuple[State, tuple[float, float, float]]:
        position = []
        first = []
        second = []
        for coefficients in record.coefficients:
            t_prev, t_curr = 1.0, argument
            d_prev, d_curr = 0.0, 1.0
            dd_prev, dd_curr = 0.0, 0.0
            value = coefficients[0] * t_prev + (coefficients[1] * t_curr if len(coefficients) > 1 else 0.0)
            derivative = coefficients[1] if len(coefficients) > 1 else 0.0
            second_derivative = 0.0
            for degree in range(2, len(coefficients)):
                t_next = 2.0 * argument * t_curr - t_prev
                d_next = 2.0 * t_curr + 2.0 * argument * d_curr - d_prev
                dd_next = 4.0 * d_curr + 2.0 * argument * dd_curr - dd_prev
                value += coefficients[degree] * t_next
                derivative += coefficients[degree] * d_next
                second_derivative += coefficients[degree] * dd_next
                t_prev, t_curr = t_curr, t_next
                d_prev, d_curr = d_curr, d_next
                dd_prev, dd_curr = dd_curr, dd_next
            position.append(value)
            first.append(derivative / record.radius)
            second.append(second_derivative / record.radius**2)
        return State(tuple(position), tuple(first)), tuple(second)  # type: ignore[arg-type,return-value]

    def state_at_split(self, origin_et_seconds: float, delta_seconds: float) -> State:
        """Evaluate position (km) and velocity (km/s) at ``origin + delta``."""

        record, argument = self._record_for(origin_et_seconds, delta_seconds)
        state, _ = self._evaluate(record, argument)
        return state

    def acceleration_at_split(self, origin_et_seconds: float, delta_seconds: float) -> tuple[float, float, float]:
        """Evaluate analytic Type 2 acceleration (km/s²) at a split epoch."""

        record, argument = self._record_for(origin_et_seconds, delta_seconds)
        _, acceleration = self._evaluate(record, argument)
        return acceleration


__all__ = ["Type2Segment", "parse_file_record", "parse_summary_record", "parse_type2_directory"]

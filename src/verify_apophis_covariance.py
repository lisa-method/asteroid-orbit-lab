"""Independent offline verifier for the Apophis joint covariance experiment.

The verifier reads a completed matrix and its checkpoints, but never calls the
runner or a propagator.  It reconstructs the SBDB covariance ordering,
Cholesky probe vectors, heliocentric conversion, teacher diagnostics, and the
15-date covariance summaries.  ``main`` writes one immutable verification
record only after every check succeeds; it is deliberately not run during
source review.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any

from covariance_ephemerides import CovarianceDE441
from analyze_apophis_covariance import analyze_records
from orbit_baselines import State
from relative_time_dynamics import RelativeEphemerisInterpolator
from run_eda import parse_horizons
from run_physics_baselines import state_from_row
from run_apophis_spk_audit import context as previous_context
from sbdb_covariance import SBDB_LABELS, load_sbdb_covariance, state_from_covariance


CONFIG = "configs/apophis_covariance.json"
RAW = "data/raw/apophis_covariance/sbdb_cov_vec.json"
SUMMARY_FIELDS = (
    "max_grid_position_error_km",
    "final_position_error_km",
    "max_grid_velocity_error_m_s",
    "final_velocity_error_m_s",
)
REFERENCE_FIELDS = ("new_nominal_teacher_diagnostic",)
NATIVE_BASIS = "uniform_inertial_q=rb-rSun0-t*vSun0,w=vb-vSun0"
OUTPUT_BASIS = "heliocentric_ICRF_geometric_AU_AU-per-day"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _path(root: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else root / candidate


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


def _state(value: object, name: str = "state") -> State:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or len(value) != 6:
        raise ValueError(f"{name} must have six components")
    values = tuple(_finite(item, f"{name}[{i}]") for i, item in enumerate(value))
    return State(values[:3], values[3:])  # type: ignore[arg-type]


def _flat(state: State) -> list[float]:
    return [*state.position, *state.velocity]


def _norm(values: Sequence[float]) -> float:
    return math.sqrt(math.fsum(value * value for value in values))


def _check_step_bound(left: float, right: float, limit: float) -> None:
    # The frozen solver advances to a representable endpoint then uses its
    # actual difference. Bound representation error using the two endpoint
    # ULPs, rather than a scale-independent epsilon smaller than one time ULP.
    width = right-left
    if width <= 0. or width > limit + math.ulp(left) + math.ulp(right):
        raise ValueError('Accepted interval exceeds step limit plus endpoint representation bound')


def _errors(predicted: State, truth: State, au_km: float, day_s: float) -> tuple[float, float]:
    position = tuple(a - b for a, b in zip(predicted.position, truth.position, strict=True))
    velocity = tuple(a - b for a, b in zip(predicted.velocity, truth.velocity, strict=True))
    return _norm(position) * au_km, _norm(velocity) * au_km * 1000.0 / day_s


def _shift(left: Sequence[State], right: Sequence[State], au_km: float, day_s: float) -> dict[str, float]:
    if len(left) != len(right) or not left:
        raise ValueError("paired states have incompatible lengths")
    positions = [_errors(a, b, au_km, day_s)[0] for a, b in zip(left, right, strict=True)]
    velocities = [_errors(a, b, au_km, day_s)[1] for a, b in zip(left, right, strict=True)]
    return {
        "max_position_shift_km": max(positions),
        "final_position_shift_km": positions[-1],
        "max_velocity_shift_m_s": max(velocities),
        "final_velocity_shift_m_s": velocities[-1],
    }


def _summary(predicted: Sequence[State], truth: Sequence[State], au_km: float, day_s: float) -> dict[str, float]:
    if len(predicted) != len(truth) or not predicted:
        raise ValueError("summary states have incompatible lengths")
    values = [_errors(a, b, au_km, day_s) for a, b in zip(predicted, truth, strict=True)]
    positions = [value[0] for value in values]
    velocities = [value[1] for value in values]
    return {
        "max_grid_position_error_km": max(positions),
        "final_position_error_km": positions[-1],
        "max_grid_velocity_error_m_s": max(velocities),
        "final_velocity_error_m_s": velocities[-1],
    }


def _assert_close(actual: object, expected: object, name: str, *, atol: float = 1e-12, rtol: float = 1e-11) -> None:
    left, right = _finite(actual, name), _finite(expected, f"expected {name}")
    if abs(left - right) > atol + rtol * abs(right):
        raise ValueError(f"{name} mismatch: {left!r} != {right!r}")


def _assert_matrix_close(actual: object, expected: Sequence[Sequence[float]], name: str) -> None:
    if not isinstance(actual, Sequence) or len(actual) != len(expected):
        raise ValueError(f"{name} shape mismatch")
    for i, (actual_row, expected_row) in enumerate(zip(actual, expected, strict=True)):
        if not isinstance(actual_row, Sequence) or len(actual_row) != len(expected_row):
            raise ValueError(f"{name}[{i}] shape mismatch")
        for j, (left, right) in enumerate(zip(actual_row, expected_row, strict=True)):
            # Do not use a universal AU-scale absolute tolerance: the NG
            # covariance block is around 1e-25.  A relative comparison keeps
            # that block visible while still tolerating serialization ulps.
            _assert_close(left, right, f"{name}[{i}][{j}]", atol=0.0, rtol=2e-10)


def _write_immutable(path: Path, value: Any) -> None:
    if path.exists():
        if _load(path) != value:
            raise RuntimeError(f"immutable verification differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _manifest_inventory(root: Path) -> dict[str, Any]:
    """Verify every raw entry in every recursively discovered manifest."""
    manifest_paths = sorted((root / "data/checksums").rglob("*manifest.json"))
    records: list[tuple[str, str, int, str]] = []
    for manifest_path in manifest_paths:
        document = _load(manifest_path)
        entries = document.get("files")
        if entries is None:
            entries = document.get("downloads")
        if not isinstance(entries, list):
            raise ValueError(f"manifest has no files/downloads list: {manifest_path}")
        for item in entries:
            if not isinstance(item, Mapping) or not all(key in item for key in ("path", "sha256", "bytes")):
                raise ValueError(f"incomplete manifest entry: {manifest_path}")
            name = str(item["path"])
            digest = str(item["sha256"]).lower()
            size = int(item["bytes"])
            raw = _path(root, name)
            relative = raw.relative_to(root).as_posix() if raw.is_relative_to(root) else name
            if not raw.is_file() or _sha(raw).lower() != digest or raw.stat().st_size != size:
                raise ValueError(f"manifest raw mismatch: {name}")
            tracked = subprocess.run(
                ["git", "ls-files", "--error-unmatch", "--", relative], cwd=root,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            ).returncode == 0
            ignored = subprocess.run(
                ["git", "check-ignore", "--no-index", "--quiet", "--", relative], cwd=root,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            ).returncode == 0
            if tracked or not ignored:
                raise ValueError(f"raw Git exclusion failed: {relative}")
            records.append((relative, digest, size, manifest_path.relative_to(root).as_posix()))
    unique_paths = {record[0] for record in records}
    if len(manifest_paths) != 19 or len(records) != 221 or len(unique_paths) != 202:
        raise ValueError(
            f"raw manifest inventory mismatch: manifests={len(manifest_paths)}, "
            f"records={len(records)}, unique_paths={len(unique_paths)}"
        )
    return {
        "manifest_count": len(manifest_paths),
        "manifest_records": len(records),
        "unique_raw_paths": len(unique_paths),
        "raw_sha256_size_records": len({(digest, size) for _, digest, size, _ in records}),
        "manifests": {name: sum(source == name for _, _, _, source in records)
                       for name in sorted({source for _, _, _, source in records})},
    }


def _check_freeze(root: Path, matrix: Mapping[str, Any], freeze_path: Path) -> dict[str, Any]:
    freeze = _load(freeze_path)
    if not isinstance(freeze, Mapping) or matrix.get("fingerprint") != freeze.get("fingerprint"):
        raise ValueError("matrix/freeze fingerprint mismatch")
    if matrix.get("freeze_sha256") != _sha(freeze_path):
        raise ValueError("matrix freeze SHA mismatch")
    hashes, frozen_runtime = freeze.get("hashes"), freeze.get("runtime")
    if not isinstance(hashes, Mapping) or not isinstance(frozen_runtime, Mapping):
        raise ValueError("freeze lacks hashes/runtime")
    for name, digest in hashes.items():
        path = _path(root, str(name))
        if not path.is_file() or _sha(path) != str(digest):
            raise ValueError(f"frozen source hash mismatch: {name}")
    runtime = {"executable": sys.executable, "version": sys.version, "platform": platform.platform()}
    if dict(frozen_runtime) != runtime:
        raise ValueError("current runtime differs from frozen runtime")
    payload = {"hashes": dict(hashes), "runtime": dict(frozen_runtime)}
    if _digest(payload) != freeze.get("fingerprint"):
        raise ValueError("freeze fingerprint recomputation mismatch")
    return dict(freeze)


def _manual_covariance(root: Path) -> tuple[dict[str, Any], tuple[tuple[float, ...], ...], dict[str, str]]:
    """Decode the raw column-major covariance independently of the adapter."""
    document = _load(root / RAW)
    if document.get("signature", {}).get("version") != "1.3":
        raise ValueError("raw SBDB schema version mismatch")
    orbit = document.get("orbit")
    if not isinstance(orbit, Mapping) or orbit.get("equinox") != "J2000":
        raise ValueError("raw SBDB equinox mismatch")
    covariance = orbit.get("covariance")
    if not isinstance(covariance, Mapping) or tuple(covariance.get("labels", ())) != SBDB_LABELS:
        raise ValueError("raw SBDB covariance labels mismatch")
    data = covariance.get("data")
    if not isinstance(data, list) or len(data) != 36:
        raise ValueError("raw SBDB covariance length mismatch")
    matrix = [[0.0] * 8 for _ in range(8)]
    index = 0
    for column in range(8):
        for row in range(column + 1):
            value = _finite(data[index], f"raw covariance.data[{index}]")
            matrix[row][column] = matrix[column][row] = value
            index += 1
    elements = covariance.get("elements")
    if not isinstance(elements, list):
        raise ValueError("raw covariance elements missing")
    aliases = {"e": "e", "q": "q", "tp": "tp", "om": "node", "w": "peri", "i": "i"}
    text: dict[str, str] = {}
    for item in elements:
        if not isinstance(item, Mapping) or item.get("name") not in aliases:
            raise ValueError("raw covariance orbital element unsupported")
        canonical = aliases[item["name"]]
        if canonical in text:
            raise ValueError("raw covariance orbital element duplicated")
        value = item.get("value")
        _finite(value, f"raw element {canonical}")
        text[canonical] = value if isinstance(value, str) else repr(value)
    if set(text) != {"e", "q", "tp", "node", "peri", "i"}:
        raise ValueError("raw covariance orbital element set mismatch")
    return document, tuple(tuple(row) for row in matrix), text


def _scaled_cholesky(matrix: Sequence[Sequence[float]]) -> tuple[tuple[float, ...], ...]:
    if len(matrix) != 8 or any(len(row) != 8 for row in matrix):
        raise ValueError("covariance must be 8x8")
    scales = [math.sqrt(matrix[i][i]) for i in range(8)]
    if any(not math.isfinite(value) or value <= 0.0 for value in scales):
        raise ValueError("covariance diagonal must be strictly positive")
    correlation = [[matrix[i][j] / (scales[i] * scales[j]) for j in range(8)] for i in range(8)]
    lower = [[0.0] * 8 for _ in range(8)]
    for i in range(8):
        for j in range(i + 1):
            remainder = math.fsum(lower[i][k] * lower[j][k] for k in range(j))
            if i == j:
                pivot = correlation[i][i] - remainder
                if not math.isfinite(pivot) or pivot <= 0.0:
                    raise ValueError("covariance is not positive definite")
                lower[i][j] = math.sqrt(pivot)
            else:
                lower[i][j] = (correlation[i][j] - remainder) / lower[j][j]
    return tuple(tuple(scales[i] * lower[i][j] if j <= i else 0.0 for j in range(8)) for i in range(8))


def _expected_specs(config: Mapping[str, Any], factor: Sequence[Sequence[float]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    labels = config.get("nominal_settings")
    if labels != ["extreme", "ultra"]:
        raise ValueError("nominal settings differ from contract")
    for label in labels:
        result.append({"id": "nominal_" + label, "kind": "nominal", "solver_label": label,
                       "delta_elements": [0.0] * 8})
    for scale in config.get("sigma_scales", ()):
        for column in range(8):
            for sign in (-1, 1):
                identifier = f"probe_s{scale:g}_j{column}_{'plus' if sign > 0 else 'minus'}".replace(".", "p")
                result.append({
                    "id": identifier, "kind": "probe", "solver_label": config["probe_setting"],
                    "scale": scale, "column": column, "sign": sign,
                    "delta_elements": [sign * math.sqrt(8.0) * scale * factor[i][column] for i in range(8)],
                })
    if len(result) != 34 or len({row["id"] for row in result}) != 34:
        raise ValueError("prescribed covariance matrix is not 34 runs")
    return result


def _check_inputs(root: Path, config: Mapping[str, Any], matrix: Mapping[str, Any], parsed: Any,
                  raw_document: Mapping[str, Any], raw_matrix: Sequence[Sequence[float]], raw_text: Mapping[str, str],
                  factor: Sequence[Sequence[float]], ctx: Mapping[str, Any]) -> tuple[dict[str, Any], list[float]]:
    output = root / config["output_directory"]
    inputs = _load(output / "inputs.json")
    input_checks = _load(output / "input_checks.json")
    if inputs.get("fingerprint") != matrix.get("fingerprint") or input_checks.get("fingerprint") != matrix.get("fingerprint"):
        raise ValueError("input artifacts have a different experiment fingerprint")
    if matrix.get("input_checks_sha256") != _sha(output / "input_checks.json"):
        raise ValueError("matrix input_checks SHA mismatch")
    if _sha(root / RAW) != parsed.source_sha256:
        raise ValueError("SBDB parser source SHA mismatch")
    if tuple(inputs.get("parameter_labels", ())) != SBDB_LABELS or tuple(parsed.labels) != SBDB_LABELS:
        raise ValueError("input covariance labels mismatch")
    if inputs.get("covariance_units") != list(parsed.covariance_units):
        raise ValueError("input covariance units mismatch")
    if inputs.get("parameter_covariance") != [list(row) for row in raw_matrix]:
        raise ValueError("inputs covariance differs from independent raw decode")
    if inputs.get("mean_elements_text") != dict(raw_text):
        raise ValueError("inputs mean-element provenance differs from raw")
    if inputs.get("ng_nominal") != list(parsed.ng_nominal):
        raise ValueError("inputs NG nominal differs from SBDB")
    if inputs.get("native_basis") != NATIVE_BASIS or inputs.get("output_basis") != OUTPUT_BASIS:
        raise ValueError("state basis metadata mismatch")
    _assert_matrix_close(inputs.get("cholesky_factor"), factor, "inputs.cholesky_factor")
    if inputs.get("covariance_epoch_jd_tdb") != config["covariance_epoch_jd_tdb"] or inputs.get("forecast_epoch_jd_tdb") != config["forecast_epoch_jd_tdb"]:
        raise ValueError("input epochs differ from config")
    if raw_document.get("object", {}).get("des") != "99942" or raw_document["orbit"].get("orbit_id") != "220":
        raise ValueError("SBDB object/solution identity mismatch")
    if raw_document["orbit"].get("pe_used") != "DE441" or raw_document["orbit"].get("sb_used") != "SB441-N16":
        raise ValueError("SBDB ephemeris source identity mismatch")
    if parsed.covariance_epoch_jd_tdb != config["covariance_epoch_jd_tdb"]:
        raise ValueError("parsed covariance epoch mismatch")
    if not (raw_document["orbit"].get("last_obs") < config["forecast_epoch_calendar_tdb"] and
            raw_document["orbit"].get("soln_date") < config["forecast_epoch_calendar_tdb"]):
        raise ValueError("orbit fit/last observation is not before forecast origin")
    if input_checks.get("sbdb_ng_nominal") != list(parsed.ng_nominal):
        raise ValueError("input gate SBDB NG mismatch")
    if input_checks.get("availability", {}).get("forecast_issued_in_2021") is not False:
        raise ValueError("availability provenance incorrectly claims a 2021 forecast")
    expected_times = [float(value) for value in inputs.get("times", ())]
    if not expected_times or expected_times[0] != 0.0 or expected_times[-1] != 3287.0:
        raise ValueError("input time grid bounds mismatch")
    if any(not math.isfinite(left) or not math.isfinite(right) or right <= left
           for left, right in zip(expected_times, expected_times[1:])):
        raise ValueError("input time grid is not finite and strictly increasing")
    if len(ctx.get("times", ())) != 797:
        raise ValueError("frozen forecast grid is not 797 points")
    shift = config["forecast_epoch_jd_tdb"] - config["covariance_epoch_jd_tdb"]
    expected_from_context = sorted(set(
        [float(value) for value in range(0, int(shift), config["early_output_step_days"])] +
        [float(shift + value) for value in ctx["times"]]
    ))
    if expected_times != expected_from_context:
        raise ValueError("input time grid differs from frozen context")
    return inputs, expected_times


def _source_and_epoch_gates(root: Path, config: Mapping[str, Any], parsed: Any, ctx: Mapping[str, Any],
                            backend: CovarianceDE441) -> dict[str, Any]:
    """Recompute the pre-propagation epoch and direct-source gates.

    This intentionally mirrors only the data comparison in the runner.  It
    never evaluates a target trajectory or invokes a force callback.
    """
    epoch_config = _load(root / "configs/covariance_epoch_check.json")
    epoch_header, epoch_rows = parse_horizons(
        root / epoch_config["raw"], "99942", "Apophis"
    )
    if len(epoch_rows) != 2 or epoch_rows[0]["epoch_jd_tdb"] != 2459215.5:
        raise ValueError("covariance epoch control rows are malformed")
    if not (epoch_header.get("center", "").startswith("Sun (10)") and
            epoch_header.get("reference_frame") == "ICRF" and
            epoch_header.get("units") == "AU-D" and epoch_header.get("geometric") and epoch_header.get("tdb")):
        raise ValueError("covariance epoch control frame metadata mismatch")
    initial = state_from_covariance(parsed, float(ctx["mu"]))
    epoch_position_km, epoch_velocity_m_s = _errors(initial, state_from_row(epoch_rows[0]), float(ctx["au"]), float(ctx["day"]))
    if epoch_position_km > float(epoch_config["position_gate_km"]) or epoch_velocity_m_s > float(epoch_config["velocity_gate_m_s"]):
        raise ValueError("covariance epoch conversion input gate failed")

    sources: list[tuple[str, Any, int]] = [
        (p.body_id, p.ephemeris, 10)
        for p in tuple(ctx["planets"]) + (ctx["pluto"],)
        if not p.body_id.startswith("sb:")
    ]
    sources.append(("10", ctx["sun_barycentric"], 0))
    # The covariance runner has a separate daily Venus source gate because
    # the preceding 2029 context may use a refined Venus backbone.
    _, venus_rows = parse_horizons(root / "data/raw/horizons/body_299.json", "299", "Venus")
    venus = RelativeEphemerisInterpolator(
        tuple(float(row["epoch_jd_tdb"]) - float(ctx["origin"]) for row in venus_rows),
        tuple(state_from_row(row) for row in venus_rows),
    )
    sources.append(("299", venus, 10))
    shift = float(config["forecast_epoch_jd_tdb"] - config["covariance_epoch_jd_tdb"])
    checks: list[dict[str, Any]] = []
    for target, old, center in sources:
        direct = backend.ephemeris(int(target), center)
        count, max_position, max_velocity = 0, 0.0, 0.0
        for time_value, old_state in zip(old.epochs_days, old.states, strict=True):
            absolute = float(time_value) + shift
            if -1.0 <= absolute <= 3288.0:
                position, velocity = _errors(direct.state_at(absolute), old_state, float(ctx["au"]), float(ctx["day"]))
                count += 1
                max_position = max(max_position, position)
                max_velocity = max(max_velocity, velocity)
        checks.append({
            "target": target, "center": center, "count": count,
            "max_position_km": max_position, "max_velocity_m_s": max_velocity,
            "passed": count > 0 and max_position <= float(config["source_knot_position_budget_km"]) and
            max_velocity <= float(config["source_knot_velocity_budget_m_s"]),
        })
    if not all(item["passed"] for item in checks):
        raise ValueError("direct ephemeris source input gate failed")
    # source_knots are compared with input_checks by the caller.
    return {"epoch_position_difference_km": epoch_position_km,
            "epoch_velocity_difference_m_s": epoch_velocity_m_s,
            "source_knots": checks,
            "source_knot_count": sum(item["count"] for item in checks)}


def _sun_convert(sun: Any, time: float, native: State, sun0: State) -> State:
    sun_t = sun.state_at(time)
    ds = tuple((sun_t.position[i] - sun0.position[i]) - time * sun0.velocity[i] for i in range(3))
    dvs = tuple(sun_t.velocity[i] - sun0.velocity[i] for i in range(3))
    return State(tuple(native.position[i] - ds[i] for i in range(3)),
                 tuple(native.velocity[i] - dvs[i] for i in range(3)))


def _check_record(row: Mapping[str, Any], spec: Mapping[str, Any], checkpoint: Mapping[str, Any],
                  times: Sequence[float], parsed: Any, ctx: Mapping[str, Any], sun: Any,
                  initial: State, mu: float, au: float, day_s: float) -> int:
    if any(row.get(key) != value for key, value in spec.items()):
        raise ValueError(f"record specification mismatch: {row.get('id')}")
    if checkpoint.get("payload_sha256") != _digest({key: value for key, value in checkpoint.items() if key != "payload_sha256"}):
        raise ValueError(f"checkpoint payload digest mismatch: {row.get('id')}")
    compact = {key: value for key, value in checkpoint.items() if key not in ("native_endpoints", "endpoints")}
    if dict(row) != compact:
        raise ValueError(f"matrix/checkpoint compact record mismatch: {row.get('id')}")
    if row.get("fingerprint") is None or row.get("native_basis") != NATIVE_BASIS or row.get("output_basis") != OUTPUT_BASIS:
        raise ValueError(f"record provenance mismatch: {row.get('id')}")
    if row.get("origin_jd_tdb") != parsed.covariance_epoch_jd_tdb:
        raise ValueError(f"record origin mismatch: {row.get('id')}")
    for field in ("samples", "native_samples", "native_endpoints", "endpoints"):
        if not isinstance(checkpoint.get(field), list) or not checkpoint[field]:
            raise ValueError(f"record {row.get('id')} missing {field}")
    samples, native_samples = checkpoint["samples"], checkpoint["native_samples"]
    if len(samples) != len(times) or len(native_samples) != len(times):
        raise ValueError(f"record sample count mismatch: {row.get('id')}")
    sample_times: list[float] = []
    native_states: list[State] = []
    reported_states: list[State] = []
    for index, (sample, native_sample) in enumerate(zip(samples, native_samples, strict=True)):
        if not isinstance(sample, Mapping) or not isinstance(native_sample, Mapping):
            raise ValueError("malformed output sample")
        sample_time = _finite(sample.get("day"), f"sample[{index}].day")
        native_time = _finite(native_sample.get("day"), f"native_sample[{index}].day")
        if sample_time != times[index] or native_time != times[index]:
            raise ValueError(f"sample time grid mismatch: {row.get('id')}")
        reported_states.append(_state(sample.get("state"), f"sample[{index}].state"))
        native_states.append(_state(native_sample.get("state"), f"native_sample[{index}].state"))
        sample_times.append(sample_time)
    if sample_times != list(times):
        raise ValueError("sample times are not the frozen grid")
    if reported_states[0] != initial or native_states[0] != initial:
        raise ValueError(f"initial sample differs: {row.get('id')}")
    accepted = checkpoint["native_endpoints"]
    converted = checkpoint["endpoints"]
    if len(accepted) < 2 or len(converted) != len(accepted):
        raise ValueError(f"accepted trace length mismatch: {row.get('id')}")
    accepted_times: list[float] = []
    accepted_states: list[State] = []
    endpoint_states: list[State] = []
    for index, (native_endpoint, endpoint) in enumerate(zip(accepted, converted, strict=True)):
        if not isinstance(native_endpoint, Sequence) or len(native_endpoint) != 2 or not isinstance(endpoint, Sequence) or len(endpoint) != 2:
            raise ValueError("malformed accepted endpoint")
        left_time = _finite(native_endpoint[0], f"accepted[{index}].time")
        right_time = _finite(endpoint[0], f"converted_endpoint[{index}].time")
        if left_time != right_time or (index and left_time <= accepted_times[-1]):
            raise ValueError(f"accepted endpoint ordering mismatch: {row.get('id')}")
        accepted_times.append(left_time)
        accepted_states.append(_state(native_endpoint[1], f"accepted[{index}].state"))
        endpoint_states.append(_state(endpoint[1], f"endpoint[{index}].state"))
    if accepted_times[0] != 0.0 or accepted_times[-1] != times[-1] or accepted_states[0] != initial:
        raise ValueError(f"accepted endpoint bounds mismatch: {row.get('id')}")
    sun0 = sun.state_at(0.0)
    by_time = {time: state for time, state in zip(accepted_times, accepted_states, strict=True)}
    by_endpoint_time = {time: state for time, state in zip(accepted_times, endpoint_states, strict=True)}
    for time, native_sample, reported_sample in zip(times, native_states, reported_states, strict=True):
        if time not in by_time or by_time[time] != native_sample:
            raise ValueError(f"requested output is not an accepted endpoint: {row.get('id')} {time!r}")
        expected_reported = _sun_convert(sun, time, native_sample, sun0)
        if expected_reported != reported_sample or expected_reported != by_endpoint_time[time]:
            raise ValueError(f"independent heliocentric conversion mismatch: {row.get('id')} {time!r}")
    for time, native_state, reported_state in zip(accepted_times, accepted_states, endpoint_states, strict=True):
        if _sun_convert(sun, time, native_state, sun0) != reported_state:
            raise ValueError(f"accepted endpoint conversion mismatch: {row.get('id')} {time!r}")
    stats = row.get("solver_stats")
    if not isinstance(stats, Mapping) or stats.get("status") != "finished":
        raise ValueError(f"solver status mismatch: {row.get('id')}")
    if int(stats.get("accepted_steps", -1)) != len(accepted_states) - 1:
        raise ValueError(f"accepted step count mismatch: {row.get('id')}")
    widths = [right-left for left, right in zip(accepted_times, accepted_times[1:])]
    for left, right in zip(accepted_times, accepted_times[1:]):
        _check_step_bound(left, right, .25)
    if max(widths) != stats.get('max_accepted_step') or min(widths) != stats.get('min_accepted_step'):
        raise ValueError(f"Recorded accepted step range differs from trace: {row.get('id')}")
    delta = tuple(_finite(value, f"delta[{i}]") for i, value in enumerate(spec["delta_elements"]))
    expected_initial = state_from_covariance(parsed, mu, delta=delta)
    if list(_flat(expected_initial)) != row.get("initial_state") or list(_flat(expected_initial)) != row.get("native_samples", [])[0]["state"]:
        raise ValueError(f"covariance initial state mismatch: {row.get('id')}")
    return len(accepted_states)


def _ng_expected(parsed: Any, row: Mapping[str, Any], delta: Sequence[float], prior_ng: Any) -> dict[str, float]:
    if not isinstance(prior_ng, Mapping):
        raise ValueError("prior NG context is malformed")
    expected = dict(prior_ng)
    expected["a1_au_d2"] = parsed.ng_nominal[0] + delta[6]
    expected["a2_au_d2"] = parsed.ng_nominal[1] + delta[7]
    saved = row.get("ng")
    if not isinstance(saved, Mapping) or set(saved) != set(expected):
        raise ValueError(f"NG metadata mismatch: {row.get('id')}")
    for key, value in expected.items():
        _assert_close(saved[key], value, f"{row.get('id')}.ng.{key}", atol=0.0, rtol=0.0)
    return {key: float(value) for key, value in saved.items()}


def _covariance_analysis(records: Sequence[Mapping[str, Any]], config: Mapping[str, Any], times: Sequence[float],
                         shift: float, au: float, day_s: float) -> tuple[dict[str, Any], int]:
    by_id = {str(row["id"]): row for row in records}
    nominal = by_id["nominal_ultra"]
    nominal_samples = nominal["samples"]
    diagnostics: list[dict[str, Any]] = []
    sqrt8 = math.sqrt(8.0)

    def offsets(scale: float, index: int) -> tuple[list[list[float]], list[list[float]]]:
        state_offsets: list[list[float]] = []
        augmented_offsets: list[list[float]] = []
        for column in range(8):
            prefix = f"probe_s{scale:g}_j{column}_".replace(".", "p")
            for sign in ("plus", "minus"):
                row = by_id[prefix + sign]
                sample = _state(row["samples"][index]["state"])
                nominal_state = _state(nominal_samples[index]["state"])
                sample_flat, nominal_flat = _flat(sample), _flat(nominal_state)
                state_offset = [sample_flat[i] - nominal_flat[i] for i in range(6)]
                ng = row["ng"]
                ng0 = nominal["ng"]
                augmented = state_offset + [float(ng["a1_au_d2"]) - float(ng0["a1_au_d2"]),
                                            float(ng["a2_au_d2"]) - float(ng0["a2_au_d2"])]
                state_offsets.append(state_offset)
                augmented_offsets.append(augmented)
        return state_offsets, augmented_offsets

    def mean_cov(values: Sequence[Sequence[float]]) -> tuple[list[float], list[list[float]]]:
        if len(values) != 16:
            raise ValueError("cubature requires 16 offsets")
        dimension = len(values[0])
        mean = [math.fsum(row[i] for row in values) / 16.0 for i in range(dimension)]
        covariance = [[0.0] * dimension for _ in range(dimension)]
        for i in range(dimension):
            for j in range(i + 1):
                value = math.fsum((row[i] - mean[i]) * (row[j] - mean[j]) for row in values) / 16.0
                covariance[i][j] = covariance[j][i] = value
        return mean, covariance

    def basis(values: Sequence[Sequence[float]], scale: float) -> list[list[float]]:
        return [[math.fsum((values[2 * col][row], -values[2 * col + 1][row])) / (2.0 * sqrt8 * scale)
                 for col in range(8)] for row in range(len(values[0]))]

    def bbt(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
        return [[math.fsum(matrix[i][k] * matrix[j][k] for k in range(8)) for j in range(len(matrix))]
                for i in range(len(matrix))]

    diagnostic_days = config.get("forecast_diagnostic_days")
    if not isinstance(diagnostic_days, list) or len(diagnostic_days) != 15:
        raise ValueError("covariance diagnostic day set must contain 15 dates")
    for raw_day in diagnostic_days:
        forecast_day = _finite(raw_day, "forecast diagnostic day")
        target = shift + forecast_day
        try:
            index = next(index for index, time in enumerate(times) if time == target)
        except StopIteration as exc:
            raise ValueError(f"diagnostic time absent from output grid: {target}") from exc
        small, small_aug = offsets(0.25, index)
        full, full_aug = offsets(1.0, index)
        small_mean, small_cov_aug = mean_cov(small_aug)
        full_mean, full_cov_aug = mean_cov(full_aug)
        small_state_cov = [row[:6] for row in small_cov_aug[:6]]
        full_state_cov = [row[:6] for row in full_cov_aug[:6]]
        normalized_small = [[value / (0.25 * 0.25) for value in row] for row in small_state_cov]
        small_basis = basis(small, 0.25)
        full_basis = basis(full, 1.0)
        linear = bbt(small_basis)
        linear_aug = bbt(basis(small_aug, 0.25))
        diagnostics.append({
            "forecast_day": forecast_day,
            "tau_since_covariance_epoch": target,
            "state_basis_small": small_basis,
            "state_basis_full": full_basis,
            "linear_state_covariance": linear,
            "linear_augmented_covariance": linear_aug,
            "full_state_covariance": full_state_cov,
            "full_augmented_covariance": full_cov_aug,
            "normalized_small_state_covariance": normalized_small,
            "normalized_small_augmented_covariance": [[value / (0.25 * 0.25) for value in row] for row in small_cov_aug],
            "weighted_mean_state": full_mean[:6],
            "weighted_mean_augmented": full_mean,
            "weighted_small_mean_augmented": small_mean,
        })
    recomputed = {
        "schema_version": 1,
        "record_count": len(records),
        "forecast_epoch_jd_tdb": float(config["forecast_epoch_jd_tdb"]),
        "covariance_epoch_jd_tdb": float(config["covariance_epoch_jd_tdb"]),
        "forecast_origin_offset_days": shift,
        "diagnostic_days": diagnostics,
        "units": {"position": "AU", "velocity": "AU/day", "ng": "AU/day^2"},
        "scope": "independent BB^T and weighted cubature recomputation",
    }
    return recomputed, len(diagnostics)


def _compare_analysis(saved: Mapping[str, Any], recomputed: Mapping[str, Any]) -> int:
    rows = saved.get("diagnostic_days")
    own_rows = recomputed.get("diagnostic_days")
    expected_units = {"position": "AU", "velocity": "AU/day", "ng": "AU/day^2",
                      "position_si": "m", "velocity_si": "m/s", "ng_si": "AU/day^2"}
    if saved.get("units") != expected_units:
        raise ValueError("saved covariance analysis units are not Cartesian state units")
    if not isinstance(rows, list) or len(rows) != len(own_rows):
        raise ValueError("saved covariance analysis diagnostic count mismatch")
    for index, (saved_row, own) in enumerate(zip(rows, own_rows, strict=True)):
        for field in ("forecast_day", "tau_since_covariance_epoch"):
            _assert_close(saved_row.get(field), own[field], f"analysis[{index}].{field}")
        if saved_row.get("basis_units") != ["AU", "AU", "AU", "AU/day", "AU/day", "AU/day"]:
            raise ValueError(f"analysis[{index}] state basis units mismatch")
        if saved_row.get("augmented_basis_units") != ["AU", "AU", "AU", "AU/day", "AU/day", "AU/day", "AU/day^2", "AU/day^2"]:
            raise ValueError(f"analysis[{index}] augmented basis units mismatch")
        # The runner's names are descriptive; these are the independent
        # six-state/augmented matrices they contain.
        linear = saved_row.get("linear_covariance", {}).get("native")
        full = saved_row.get("full_scale_cubature_covariance", {}).get("native")
        small = saved_row.get("normalized_small_scale_cubature_covariance", {}).get("native")
        _assert_matrix_close(linear, own["linear_state_covariance"], f"analysis[{index}].linear")
        _assert_matrix_close(full, own["full_state_covariance"], f"analysis[{index}].full")
        _assert_matrix_close(small, own["normalized_small_state_covariance"], f"analysis[{index}].small")
        saved_aug = saved_row.get("full_augmented_cubature_covariance", {}).get("native")
        saved_linear_aug = saved_row.get("linear_augmented_covariance", {}).get("native")
        saved_small_aug = saved_row.get("normalized_small_augmented_cubature_covariance", {}).get("native")
        _assert_matrix_close(saved_aug, own["full_augmented_covariance"], f"analysis[{index}].full_augmented")
        _assert_matrix_close(saved_linear_aug, own["linear_augmented_covariance"], f"analysis[{index}].linear_augmented")
        _assert_matrix_close(saved_small_aug, own["normalized_small_augmented_covariance"], f"analysis[{index}].small_augmented")
        _assert_matrix_close(saved_row.get("state_basis_small"), own["state_basis_small"], f"analysis[{index}].B_small")
        _assert_matrix_close(saved_row.get("state_basis_full"), own["state_basis_full"], f"analysis[{index}].B_full")
        saved_mean = saved_row.get("weighted_mean_offset", {}).get("state_native")
        if not isinstance(saved_mean, Sequence) or len(saved_mean) != 6:
            raise ValueError(f"analysis[{index}] weighted mean missing")
        for component, (left, right) in enumerate(zip(saved_mean, own["weighted_mean_state"], strict=True)):
            _assert_close(left, right, f"analysis[{index}].weighted_mean[{component}]", atol=0.0, rtol=2e-10)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    root = args.root.resolve()
    config = _load(root / CONFIG)
    output = root / config["output_directory"]
    matrix_path, freeze_path = output / "matrix.json", output / "freeze.json"
    matrix = _load(matrix_path)
    freeze = _check_freeze(root, matrix, freeze_path)
    manifest = _manifest_inventory(root)
    raw_document, raw_matrix, raw_text = _manual_covariance(root)
    parsed = load_sbdb_covariance(root / RAW)
    if tuple(tuple(row) for row in parsed.covariance) != raw_matrix or dict(parsed.mean_element_text) != raw_text:
        raise ValueError("SBDB adapter differs from independent raw parser")
    factor = _scaled_cholesky(raw_matrix)
    ctx, _, previous_config, _, _, _, _ = previous_context(root)
    inputs, times = _check_inputs(root, config, matrix, parsed, raw_document, raw_matrix, raw_text, factor, ctx)
    expected_specs = _expected_specs(config, factor)
    records = matrix.get("records")
    if not isinstance(records, list) or len(records) != 34:
        raise ValueError("matrix must contain exactly 34 records")
    by_id = {row.get("id"): row for row in records if isinstance(row, Mapping)}
    if len(by_id) != 34 or set(by_id) != {spec["id"] for spec in expected_specs}:
        raise ValueError("matrix run IDs differ from the prescribed covariance matrix")
    if any(row.get("fingerprint") != freeze["fingerprint"] for row in records):
        raise ValueError("record fingerprint differs from freeze")
    if matrix.get("fingerprint") != freeze["fingerprint"]:
        raise ValueError("matrix fingerprint mismatch")
    if matrix.get("analysis", {}).get("record_count") != 34:
        raise ValueError("saved analysis record count mismatch")
    annual = ctx.get("annual")
    refs = ctx.get("refs")
    if not isinstance(annual, list) or len(annual) != 797 or not isinstance(refs, Mapping):
        raise ValueError("frozen teacher context is not 797 rows")
    shift = float(config["forecast_epoch_jd_tdb"] - config["covariance_epoch_jd_tdb"])
    suffix = tuple(float(value) for value in times if value >= shift)
    expected_suffix = tuple(shift + float(value) for value in ctx.get("times", ()))
    if suffix != expected_suffix:
        raise ValueError("forecast time suffix differs from frozen teacher context")
    au, day_s, mu = float(ctx["au"]), float(ctx["day"]), float(ctx["mu"])
    backend = CovarianceDE441(root, au, day_s)
    sun = backend.ephemeris(10, 0)
    gates = _source_and_epoch_gates(root, config, parsed, ctx, backend)
    saved_gates = _load(output / "input_checks.json")
    _assert_close(saved_gates.get("epoch_position_difference_km"), gates["epoch_position_difference_km"], "input gate epoch position", atol=1e-12, rtol=1e-10)
    _assert_close(saved_gates.get("epoch_velocity_difference_m_s"), gates["epoch_velocity_difference_m_s"], "input gate epoch velocity", atol=1e-12, rtol=1e-10)
    if saved_gates.get("source_knot_count") != gates["source_knot_count"] or saved_gates.get("passed") is not True:
        raise ValueError("saved input gate does not match independent gate")
    saved_source = saved_gates.get("source_knots")
    if not isinstance(saved_source, list) or len(saved_source) != len(gates["source_knots"]):
        raise ValueError("saved source gate rows differ from independent gate")
    for saved, expected in zip(saved_source, gates["source_knots"], strict=True):
        if saved.get("target") != expected["target"] or saved.get("center") != expected["center"] or saved.get("count") != expected["count"] or saved.get("passed") != expected["passed"]:
            raise ValueError("saved source gate identity differs from independent gate")
        _assert_close(saved.get("max_position_km"), expected["max_position_km"], "source gate position", atol=1e-12, rtol=1e-9)
        _assert_close(saved.get("max_velocity_m_s"), expected["max_velocity_m_s"], "source gate velocity", atol=1e-12, rtol=1e-9)
    settings = {item["label"]: item for item in previous_config.get("solver_settings", []) if item.get("solver") == "dopri54"}
    if set(settings) != {"extreme", "ultra"}:
        raise ValueError("frozen DP settings are incomplete")
    initial_nominal = state_from_covariance(parsed, mu)
    checkpoint_dir = output / "checkpoints"
    checkpoint_paths = sorted(checkpoint_dir.glob("*.json"))
    if len(checkpoint_paths) != 34:
        raise ValueError(f"checkpoint count mismatch: {len(checkpoint_paths)}")
    accepted_count = 0
    ng_count = 0
    checkpoint_hashes: dict[str, str] = {}
    for spec in expected_specs:
        row = by_id[spec["id"]]
        path = checkpoint_dir / f"{spec['id']}.json"
        if not path.is_file():
            raise ValueError(f"missing checkpoint: {path.name}")
        checkpoint = _load(path)
        if checkpoint.get("id") != spec["id"]:
            raise ValueError(f"checkpoint identity mismatch: {path.name}")
        delta = tuple(_finite(value, f"{spec['id']}.delta[{i}]") for i, value in enumerate(spec["delta_elements"]))
        if row.get("setting") != settings[spec["solver_label"]]:
            raise ValueError(f"solver setting differs from frozen predecessor: {spec['id']}")
        stats = row.get("solver_stats")
        if not isinstance(stats, Mapping) or stats.get("epoch") != 0.0 or stats.get("final_time") != times[-1] or stats.get("compensated") is not True:
            raise ValueError(f"solver statistics metadata mismatch: {spec['id']}")
        for field in ("max_step_mismatch_days", "min_accepted_step", "max_accepted_step", "final_step", "max_error_norm"):
            if field in stats:
                _finite(stats[field], f"{spec['id']}.solver_stats.{field}")
        if "max_accepted_step" in stats and float(stats["max_accepted_step"]) > float(config["max_dp_step_days"]) + 2*math.ulp(times[-1]):
            raise ValueError(f"accepted step exceeds configured maximum: {spec['id']}")
        if int(stats.get("attempted_steps", -1)) < int(stats.get("accepted_steps", -1)):
            raise ValueError(f"solver attempted/accepted count mismatch: {spec['id']}")
        _finite(row.get("runtime_seconds"), f"{spec['id']}.runtime_seconds")
        expected_initial = initial_nominal if not any(delta) else state_from_covariance(parsed, mu, delta=delta)
        accepted_count += _check_record(row, spec, checkpoint, times, parsed, ctx, sun, expected_initial, mu, au, day_s)
        _ng_expected(parsed, row, delta, ctx["ng"] if hasattr(ctx["ng"], "items") else vars(ctx["ng"]))
        ng_count += 1
        checkpoint_hashes[path.relative_to(root).as_posix()] = _sha(path)
    if matrix.get("checkpoint_hashes") != checkpoint_hashes:
        raise ValueError("matrix checkpoint SHA map differs from disk")
    # Matrix stores the compact rows, while checkpoints retain native traces.
    if set(path.name for path in checkpoint_paths) != {f"{spec['id']}.json" for spec in expected_specs}:
        raise ValueError("checkpoint filename set differs from matrix IDs")
    nominal = by_id["nominal_ultra"]
    shift = float(config["forecast_epoch_jd_tdb"] - config["covariance_epoch_jd_tdb"])
    teacher_diagnostic = []
    sample_map = {float(item["day"]): _state(item["state"]) for item in nominal["samples"]}
    for target_row, relative_time in zip(annual, ctx["times"], strict=True):
        day = float(relative_time)
        sample_time = shift + day
        sample = sample_map.get(sample_time)
        if sample is None:
            raise ValueError(f"nominal sample missing teacher time {sample_time}")
        # Match the runner's provenance expression exactly; this preserves
        # the final bit for fractional relative knots.
        teacher_diagnostic.append({"forecast_day": sample_time - shift,
                                   "position_km": _errors(sample, state_from_row(target_row), au, day_s)[0]})
    saved_teacher = matrix.get("new_nominal_teacher_diagnostic")
    if not isinstance(saved_teacher, list) or len(saved_teacher) != len(teacher_diagnostic):
        raise ValueError("nominal teacher diagnostics differ from independent recomputation")
    for index, (saved, expected) in enumerate(zip(saved_teacher, teacher_diagnostic, strict=True)):
        _assert_close(saved.get("forecast_day"), expected["forecast_day"],
                     f"nominal teacher day[{index}]", atol=1e-12, rtol=0.0)
        _assert_close(saved.get("position_km"), expected["position_km"],
                     f"nominal teacher position[{index}]", atol=1e-12, rtol=1e-10)
    own_analysis, diagnostic_count = _covariance_analysis(records, config, times, shift, au, day_s)
    saved_analysis = matrix.get("analysis")
    if not isinstance(saved_analysis, Mapping):
        raise ValueError("matrix analysis missing")
    compared_analysis_count = _compare_analysis(saved_analysis, own_analysis)
    agent_analysis = analyze_records(records, config, au, day_s)
    if _digest(agent_analysis) != _digest(saved_analysis):
        raise ValueError("saved analysis differs from the frozen analysis function")
    verification = {
        "schema_version": 1,
        "matrix": str(matrix_path.relative_to(root)),
        "matrix_sha256": _sha(matrix_path),
        "verifier_sha256": _sha(root / "src/verify_apophis_covariance.py"),
        "freeze_sha256": _sha(freeze_path),
        "fingerprint": freeze["fingerprint"],
        "inputs_sha256": _sha(output / "inputs.json"),
        "input_checks_sha256": _sha(output / "input_checks.json"),
        "raw_covariance_sha256": _sha(root / RAW),
        "run_count": len(records),
        "accepted_endpoint_count": accepted_count,
        "max_step_overshoot_days": max(float(r['solver_stats']['max_accepted_step'])-.25 for r in records),
        "step_representation_policy": "each dt <= .25 day + ulp(left) + ulp(right); min/max recomputed from accepted traces",
        "checkpoint_count": len(checkpoint_paths),
        "ng_identity_count": ng_count,
        "teacher_diagnostic_count": len(teacher_diagnostic),
        "teacher_diagnostic_fields": list(REFERENCE_FIELDS),
        "analysis_diagnostic_count": diagnostic_count,
        "analysis_recomputed_count": compared_analysis_count,
        "saved_analysis_digest": _digest(saved_analysis),
        "recomputed_analysis_digest": _digest(own_analysis),
        "analysis_function_digest": _digest(agent_analysis),
        "checkpoint_sha256": checkpoint_hashes,
        "manifest": manifest,
        "scope": "offline integrity and joint covariance recomputation; no propagation or forecast validation",
    }
    _write_immutable(output / "verification.json", verification)
    print(json.dumps(verification, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

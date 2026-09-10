"""Deterministic initial-state sensitivity probes for the Apophis SPK audit.

This module deliberately keeps the sensitivity calculation separate from the
parent trajectory audit.  The 24 rollouts are signed, prescribed finite
differences around the already frozen annual-hourly initial state; they are
diagnostic probes and are not a covariance or a validation experiment.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import platform
import sys
import time
from typing import Any, Iterable, Mapping, Sequence

from barycentric_eih import build_barycentric_eih
from precise_dopri import integrate_precise_dopri54
from prepare_fresh_holdout import check_hashes, immutable_json, sha
from run_apophis_spk_audit import _flat, _state, context as spk_context, ephemerides as spk_ephemerides, gates as spk_gates
from run_apophis_reference_time import source_closure
from run_physics_baselines import state_from_row


CONFIG = "configs/apophis_initial_sensitivity.json"
NATIVE_BASIS = "uniform_inertial_q=rb-rSun0-t*vSun0,w=vb-vSun0"
OUTPUT_BASIS = "heliocentric_ICRF_geometric_AU_AU-per-day"
_SI_KM = 1000.0


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


def _positive(value: object, name: str) -> float:
    result = _finite(value, name)
    if result <= 0.0:
        raise ValueError(f"{name} must be positive")
    return result


def _vector(value: Iterable[object], name: str) -> tuple[float, ...]:
    try:
        result = tuple(_finite(item, f"{name}[{i}]") for i, item in enumerate(value))
    except TypeError as exc:
        raise ValueError(f"{name} must be a finite sequence") from exc
    if len(result) != 6:
        raise ValueError(f"{name} must contain six components")
    return result


def _norm(values: Iterable[float]) -> float:
    return math.sqrt(math.fsum(value * value for value in values))


def _json_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(payload).hexdigest()


def _trace_hashes(row: Mapping[str, Any]) -> dict[str, str]:
    """Hash each persisted trace independently for immutable resume checks."""
    fields = (
        "requested_times_relative_days", "requested_states", "native_solver_requested_states",
        "accepted_endpoints", "native_solver_endpoints",
    )
    missing = [field for field in fields if field not in row]
    if missing:
        raise ValueError(f"checkpoint lacks trace fields: {', '.join(missing)}")
    return {field: _json_digest(row[field]) for field in fields}


def _verify_checkpoint(row: Mapping[str, Any], fingerprint: str) -> None:
    if row.get("fingerprint") != fingerprint:
        raise ValueError("Sensitivity checkpoint fingerprint mismatch")
    saved = row.get("trace_hashes")
    actual = _trace_hashes(row)
    if saved != actual:
        raise ValueError("Sensitivity checkpoint trace hash mismatch; recovery is required")


def _runtime() -> dict[str, str]:
    return {"executable": sys.executable, "version": sys.version, "platform": platform.platform()}


def _read_config(root: Path) -> dict[str, Any]:
    config = json.loads((root / CONFIG).read_text(encoding="utf-8"))
    if config.get("expected_new_propagations") != 24:
        raise ValueError("The sensitivity contract requires exactly 24 propagations")
    for field in ("contract", "output_directory", "parent_matrix", "parent_freeze", "parent_verification",
                  "baseline_key", "ephemeris_arm", "solver_setting"):
        if not isinstance(config.get(field), str) or not config[field]:
            raise ValueError(f"Missing sensitivity config field: {field}")
    axes = config.get("axes")
    signs = config.get("signs")
    if axes != [0, 1, 2] or signs != [-1, 1]:
        raise ValueError("Sensitivity axes/signs are part of the frozen contract")
    for field in ("position_amplitudes_m", "velocity_amplitudes_m_s"):
        values = config.get(field)
        if not isinstance(values, list) or len(values) != 2 or any(_positive(v, field) <= 0 for v in values):
            raise ValueError(f"{field} must contain two positive amplitudes")
        if values != sorted(values):
            raise ValueError(f"{field} must be ascending")
    if not isinstance(config.get("diagnostic_days"), list) or not config["diagnostic_days"]:
        raise ValueError("diagnostic_days must be a non-empty list")
    return config


def _parent_gate(root: Path, config: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    matrix_path = root / config["parent_matrix"]
    freeze_path = root / config["parent_freeze"]
    verification_path = root / config["parent_verification"]
    for path in (matrix_path, freeze_path, verification_path):
        if not path.is_file():
            raise FileNotFoundError(f"Completed parent artifact is required: {path}")
    parent_freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    if not isinstance(parent_freeze.get("hashes"), dict) or not parent_freeze["hashes"]:
        raise ValueError("Parent freeze has no source/input hashes")
    check_hashes(root, parent_freeze["hashes"])
    parent_matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    if not isinstance(verification, dict):
        raise ValueError("Parent verification must be a JSON object")
    matrix_sha = sha(matrix_path)
    expected_freeze_sha = sha(freeze_path)
    if verification.get("matrix_sha256") != matrix_sha:
        raise ValueError("Parent verification does not identify the frozen parent matrix")
    if verification.get("freeze_sha256") != expected_freeze_sha:
        raise ValueError("Parent verification does not identify the frozen parent freeze")
    if verification.get("fingerprint") != parent_freeze.get("fingerprint"):
        raise ValueError("Parent verification fingerprint differs from parent freeze")
    if parent_freeze.get("fingerprint") != parent_matrix.get("fingerprint"):
        raise ValueError("Parent matrix and freeze fingerprints differ")
    if parent_matrix.get("freeze_sha256") != expected_freeze_sha:
        raise ValueError("Parent matrix does not identify the frozen parent freeze")
    if verification.get("checkpoint_count") != 18 or verification.get("run_count") != 18:
        raise ValueError("Parent verification does not report all 18 completed records")
    records = parent_matrix.get("records")
    if not isinstance(records, list):
        raise ValueError("Parent matrix has no records")
    baseline = next((row for row in records if row.get("key") == config["baseline_key"]), None)
    if baseline is None:
        raise ValueError(f"Parent matrix lacks {config['baseline_key']}")
    if baseline.get("fingerprint") != parent_matrix.get("fingerprint"):
        raise ValueError("Baseline checkpoint is not from the frozen parent matrix")
    checkpoint_path = matrix_path.parent / "checkpoints" / f"{config['baseline_key']}.json"
    if not checkpoint_path.is_file():
        raise FileNotFoundError(f"Frozen baseline checkpoint is required: {checkpoint_path}")
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if checkpoint.get("key") != config["baseline_key"] or checkpoint.get("fingerprint") != baseline.get("fingerprint"):
        raise ValueError("Frozen baseline checkpoint identity differs from parent matrix")
    for field in ("requested_times_relative_days", "requested_states", "native_solver_requested_states",
                  "accepted_endpoints", "native_solver_endpoints"):
        if _json_digest(checkpoint.get(field)) != _json_digest(baseline.get(field)):
            raise ValueError(f"Frozen baseline checkpoint trace differs: {field}")
    return parent_matrix, parent_freeze, verification, matrix_sha


def _fingerprint(root: Path, config: Mapping[str, Any], parent_freeze: Mapping[str, Any], matrix_sha: str,
                 verification_sha: str) -> tuple[str, dict[str, str]]:
    paths = set(parent_freeze["hashes"])
    paths.update((CONFIG, config["contract"], config["parent_matrix"], config["parent_freeze"],
                  config["parent_verification"], "src/run_apophis_initial_sensitivity.py"))
    paths.update(source_closure(root, ["run_apophis_initial_sensitivity"]))
    test_path = root / "tests/test_run_apophis_initial_sensitivity.py"
    if test_path.exists():
        paths.add(test_path.relative_to(root).as_posix())
    baseline_checkpoint = root / config["parent_matrix"]
    baseline_checkpoint = baseline_checkpoint.parent / "checkpoints" / f"{config['baseline_key']}.json"
    if baseline_checkpoint.exists():
        paths.add(baseline_checkpoint.relative_to(root).as_posix())
    hashes = {path: sha(root / path) for path in sorted(paths)}
    runtime = parent_freeze.get("runtime")
    if not isinstance(runtime, dict):
        raise ValueError("Parent freeze lacks runtime")
    payload = {"hashes": hashes, "runtime": runtime, "parent_matrix_sha256": matrix_sha,
               "parent_verification_sha256": verification_sha}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest(), hashes


def prepare(root: Path) -> dict[str, Any]:
    """Read-only source/parent gate used by ``--prepare-only`` and ``run``."""
    config = _read_config(root)
    parent_matrix, parent_freeze, verification, matrix_sha = _parent_gate(root, config)
    fingerprint, hashes = _fingerprint(root, config, parent_freeze, matrix_sha,
                                        sha(root / config["parent_verification"]))
    current = _runtime()
    if current != parent_freeze.get("runtime"):
        raise ValueError("Sensitivity run requires the frozen parent runtime")
    return {"config": config, "parent_matrix": parent_matrix, "parent_freeze": parent_freeze,
            "parent_verification": verification, "fingerprint": fingerprint, "hashes": hashes,
            "parent_matrix_sha256": matrix_sha}


def _probe_specs(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for component, amplitudes, units in (("position", config["position_amplitudes_m"], "m"),
                                         ("velocity", config["velocity_amplitudes_m_s"], "m_s")):
        for axis in config["axes"]:
            for amplitude in amplitudes:
                for sign in config["signs"]:
                    label = f"{component}_axis{axis}_{amplitude:g}{units}_{'plus' if sign > 0 else 'minus'}"
                    result.append({"key": label, "component": component, "axis": axis,
                                   "amplitude": float(amplitude), "amplitude_units": units, "sign": sign})
    if len(result) != 24 or len({item["key"] for item in result}) != 24:
        raise ValueError("Probe specification is not the fixed 24-case matrix")
    return result


def _output_si(flat: Sequence[float], au_km: float, day_s: float) -> tuple[float, ...]:
    values = _vector(flat, "state")
    factor_position = au_km * _SI_KM
    factor_velocity = factor_position / day_s
    return tuple(value * (factor_position if i < 3 else factor_velocity) for i, value in enumerate(values))


def _probe_initial(base: Sequence[float], spec: Mapping[str, Any], au_km: float, day_s: float) -> tuple[tuple[float, ...], dict[str, float]]:
    base_values = _vector(base, "baseline initial state")
    values = list(base_values)
    component = spec["component"]
    amplitude = _positive(spec["amplitude"], "probe amplitude")
    axis = spec["axis"]
    sign = spec["sign"]
    if axis not in (0, 1, 2) or sign not in (-1, 1):
        raise ValueError("invalid probe axis/sign")
    if component == "position":
        internal = amplitude / _SI_KM / au_km
        values[axis] += sign * internal
        # Derive this from the represented state after addition.  The
        # requested metre value is only a target; binary AU arithmetic can
        # make the realised signed delta slightly different.
        actual = (values[axis] - base_values[axis]) * au_km * _SI_KM
        delta = {"position_m": actual, "velocity_m_s": 0.0}
    elif component == "velocity":
        internal = amplitude / _SI_KM * day_s / au_km
        values[axis + 3] += sign * internal
        actual = (values[axis + 3] - base_values[axis + 3]) * au_km * _SI_KM / day_s
        delta = {"position_m": 0.0, "velocity_m_s": actual}
    else:
        raise ValueError("invalid probe component")
    return tuple(values), delta


def _times_and_index(times: Sequence[float], config: Mapping[str, Any]) -> dict[str, int]:
    if len(times) != 797 or times[0] != 0.0 or times[-1] != 365.0:
        raise ValueError("Parent SPK audit does not provide the fixed 797-time grid")
    indices: dict[str, int] = {}
    for day in config["diagnostic_days"]:
        value = _finite(day, "diagnostic day")
        if value < 0.0 or value > 365.0:
            raise ValueError("diagnostic day is outside the annual horizon")
        matches = [i for i, t in enumerate(times) if t == value]
        if len(matches) != 1:
            raise ValueError(f"diagnostic day {value:g} is not an exact requested time")
        indices[str(value)] = matches[0]
    return indices


def _analysis_pair(baseline: Mapping[str, Any], plus: Mapping[str, Any], minus: Mapping[str, Any],
                   au_km: float, day_s: float, diagnostic_indices: Mapping[str, int]) -> dict[str, Any]:
    base_trace = baseline["requested_states"]
    plus_trace = plus["requested_states"]
    minus_trace = minus["requested_states"]
    if not (len(base_trace) == len(plus_trace) == len(minus_trace)):
        raise ValueError("Sensitivity traces have different requested-time lengths")
    plus_delta = plus["actual_delta_si"]
    minus_delta = minus["actual_delta_si"]
    input_kind = plus["probe"]["component"]
    input_field = "position_m" if input_kind == "position" else "velocity_m_s"
    span = math.fsum((plus_delta[input_field], -minus_delta[input_field]))
    if span == 0.0 or not math.isfinite(span):
        raise ValueError("Probe pair has a zero/non-finite actual input span")
    rows: dict[str, Any] = {}
    for day_label, index in diagnostic_indices.items():
        # Form differences while values are still in AU/AU-day.  Converting
        # absolute heliocentric states to metres first would discard the
        # small probe signal against a ~AU offset.
        base_internal = _vector(base_trace[index], "baseline state")
        plus_internal = _vector(plus_trace[index], "plus state")
        minus_internal = _vector(minus_trace[index], "minus state")
        position_factor = au_km * _SI_KM
        velocity_factor = position_factor / day_s
        factors = (position_factor,) * 3 + (velocity_factor,) * 3
        derivative = tuple(math.fsum((plus_internal[i], -minus_internal[i])) * factors[i] / span for i in range(6))
        raw_midpoint = tuple(math.fsum((plus_internal[i] - base_internal[i], minus_internal[i] - base_internal[i])) * factors[i] / 2.0 for i in range(6))
        midpoint_input = math.fsum((plus_delta[input_field], minus_delta[input_field])) / 2.0
        corrected_midpoint = tuple(raw_midpoint[i] - derivative[i] * midpoint_input for i in range(6))
        elapsed = float(day_label) * day_s
        position_response = derivative[:3]
        velocity_response = derivative[3:]
        if input_kind == "position":
            derivative_units = {"position": "m_per_m", "velocity": "(m_per_s)_per_m"}
            free_flight_gain = None
        else:
            derivative_units = {"position": "m_per_(m_per_s)=s", "velocity": "dimensionless"}
            # A velocity perturbation has a free-flight position scale.  A
            # velocity-response norm divided by time would have the wrong
            # units for this diagnostic.
            free_flight_gain = (_norm(position_response) / elapsed if elapsed > 0.0 else None)
        rows[day_label] = {
            "day": float(day_label), "elapsed_seconds": elapsed,
            "derivative_position": list(position_response),
            "derivative_velocity": list(velocity_response),
            "derivative_units": derivative_units,
            "position_response_norm": _norm(position_response),
            "velocity_response_norm": _norm(velocity_response),
            "position_response_relative_to_free_flight": free_flight_gain,
            "raw_midpoint_displacement": list(raw_midpoint),
            "corrected_midpoint_displacement": list(corrected_midpoint),
            "raw_input_midpoint_si": midpoint_input,
            "actual_input_span_si": span,
        }
    return {"probe_pair": {"plus_key": plus["key"], "minus_key": minus["key"],
                            "axis": plus["probe"]["axis"], "component": input_kind,
                            "amplitude": plus["probe"]["amplitude"],
                            "amplitude_units": plus["probe"]["amplitude_units"],
                            "actual_plus_delta_si": plus_delta, "actual_minus_delta_si": minus_delta,
                            "actual_span_si": span}, "diagnostics": rows,
            "annual": rows["365.0"]}


def _consistency(left: Sequence[float], right: Sequence[float]) -> float:
    left_norm = _norm(left)
    right_norm = _norm(right)
    difference = _norm(a - b for a, b in zip(left, right, strict=True))
    denominator = max(left_norm, right_norm)
    if denominator == 0.0:
        return 0.0 if difference == 0.0 else math.inf
    return difference / denominator


def compare_amplitudes(analyses: Sequence[Mapping[str, Any]], budget: float) -> dict[str, Any]:
    """Compare same-axis derivative vectors at two probe amplitudes.

    Position and velocity response vectors are compared separately because
    their units differ.  The denominator is the larger of the two norms.
    """
    if len(analyses) != 2:
        raise ValueError("exactly two amplitudes are required for consistency")
    first, second = analyses
    days = first["diagnostics"]
    if set(days) != set(second["diagnostics"]):
        raise ValueError("consistency analyses use different diagnostic days")
    position = {day: _consistency(first["diagnostics"][day]["derivative_position"],
                                  second["diagnostics"][day]["derivative_position"]) for day in days}
    velocity = {day: _consistency(first["diagnostics"][day]["derivative_velocity"],
                                  second["diagnostics"][day]["derivative_velocity"]) for day in days}
    max_position = max(position.values())
    max_velocity = max(velocity.values())
    return {"relative_difference_position": position, "relative_difference_velocity": velocity,
            "max_relative_difference_position": max_position,
            "max_relative_difference_velocity": max_velocity,
            "budget": budget, "position_passes": max_position <= budget,
            "velocity_passes": max_velocity <= budget,
            "passes": max_position <= budget and max_velocity <= budget}


def analyze_matrix(records: Sequence[Mapping[str, Any]], baseline: Mapping[str, Any], au_km: float,
                   day_s: float, config: Mapping[str, Any]) -> dict[str, Any]:
    by_key = {row.get("key"): row for row in records}
    if len(by_key) != 24:
        raise ValueError("Sensitivity matrix must contain 24 unique records")
    times = baseline.get("requested_times_relative_days")
    if not isinstance(times, list):
        raise ValueError("Baseline lacks requested times")
    diagnostic_indices = _times_and_index(times, config)
    families: list[dict[str, Any]] = []
    for component, amplitudes, units in (("position", config["position_amplitudes_m"], "m"),
                                         ("velocity", config["velocity_amplitudes_m_s"], "m_s")):
        for axis in config["axes"]:
            amplitude_analyses = []
            for amplitude in amplitudes:
                selected = [row for row in records if row["probe"]["component"] == component
                            and row["probe"]["axis"] == axis and row["probe"]["amplitude"] == float(amplitude)]
                if sorted(row["probe"]["sign"] for row in selected) != [-1, 1]:
                    raise ValueError("Sensitivity matrix is missing a signed pair")
                plus = next(row for row in selected if row["probe"]["sign"] == 1)
                minus = next(row for row in selected if row["probe"]["sign"] == -1)
                amplitude_analyses.append(_analysis_pair(baseline, plus, minus, au_km, day_s, diagnostic_indices))
            comparison = compare_amplitudes(amplitude_analyses, float(config["derivative_relative_consistency_budget"]))
            families.append({"component": component, "axis": axis, "amplitude_units": units,
                             "amplitudes": amplitude_analyses, "consistency": comparison})
    return {"diagnostic_days": config["diagnostic_days"], "families": families,
            "scope": "signed initial-state finite-difference probes; not covariance or validation"}


def _new_row(root: Path, prepared: Mapping[str, Any], ctx: Mapping[str, Any], baseline: Mapping[str, Any],
             spec: Mapping[str, Any], setting: Mapping[str, Any]) -> dict[str, Any]:
    config = prepared["config"]
    initial = _vector(baseline["native_initial_state"], "baseline native initial state")
    perturbed, delta_si = _probe_initial(initial, spec, ctx["au"], ctx["day"])
    planets, sun = spk_ephemerides(ctx, prepared["backend"], config["ephemeris_arm"])
    force, convert = build_barycentric_eih(
        ctx["origin"], planets, ctx["mu"], ctx["c"], ctx["ng"],
        ctx["base"]["earth_j2"]["reference_radius_km"] / ctx["au"],
        ctx["base"]["earth_j2"]["j2"], sun, gr="eih_sun", solar_j2=None)
    perturbed_state = _state(perturbed)
    if convert(0.0, perturbed_state) != perturbed_state:
        raise ValueError("Initial-state conversion changed a sensitivity probe")
    started = time.perf_counter()
    result = integrate_precise_dopri54(
        force, 0.0, perturbed, ctx["times"],
        **{key: value for key, value in setting.items() if key not in ("solver", "label")},
        max_step=prepared["previous_config"]["max_dp_step_days"],
    )
    propagation_seconds = time.perf_counter() - started
    native_states = [_state(state) for _, state in result.samples]
    native_endpoints = [[float(t), list(_vector(state, "native endpoint"))] for t, state in result.accepted_endpoints]
    states = [convert(t, state) for t, state in zip(ctx["times"], native_states, strict=True)]
    endpoints = [[t, list(_flat(convert(t, _state(state))))] for t, state in native_endpoints]
    row: dict[str, Any] = {
        "key": spec["key"], "probe": dict(spec), "fingerprint": prepared["fingerprint"],
        "parent_matrix_sha256": prepared["parent_matrix_sha256"], "initial_source": "annual_hourly",
        "origin_jd_tdb": ctx["origin"], "origin_calendar_tdb": ctx["calendar"],
        "requested_times_relative_days": list(ctx["times"]), "initial_state": list(_flat(perturbed_state)),
        "native_initial_state": list(perturbed), "actual_delta_si": delta_si,
        "native_state_basis": NATIVE_BASIS, "reported_state_basis": OUTPUT_BASIS,
        "native_solver_requested_states": [list(_flat(state)) for state in native_states],
        "requested_states": [list(_flat(state)) for state in states],
        "native_solver_endpoints": native_endpoints, "accepted_endpoints": endpoints,
        "accepted_time_basis": "relative_days_since_start", "solver": setting["solver"],
        "setting": dict(setting), "numerical_mode": "precise_dp", "force_specification": {
            "arm": config["ephemeris_arm"], "gr": "eih_sun", "solar_j2": False,
            "earth_pole": "fixed_j2000", "newton_sources": "Sun + 10 major + SB16",
            "ng": "nominal", "teacher_states_in_force": False,
        }, "solver_stats": dict(result.stats), "propagation_runtime_seconds": propagation_seconds,
    }
    row["runtime_seconds"] = time.perf_counter() - started
    row["trace_hashes"] = _trace_hashes(row)
    return row


def run(root: Path, *, prepare_only: bool = False, resume: bool = True) -> None:
    prepared = prepare(root)
    config = prepared["config"]
    if prepare_only:
        print(json.dumps({"prepared": True, "fingerprint": prepared["fingerprint"],
                          "expected_new_propagations": config["expected_new_propagations"],
                          "parent_matrix_sha256": prepared["parent_matrix_sha256"]}, sort_keys=True), flush=True)
        return
    # The SPK context is loaded only after parent verification/gates have passed.
    ctx, spk_config, previous_config, backend, parent_output, _, parent_fingerprint = spk_context(root)
    prepared = dict(prepared, previous_config=previous_config, backend=backend)
    previous = prepared["parent_matrix"]
    spk_gates(ctx, spk_config, backend, parent_output, previous, parent_fingerprint)
    baseline = next(row for row in previous["records"] if row["key"] == config["baseline_key"])
    if baseline.get("fingerprint") != parent_fingerprint:
        raise ValueError("Baseline parent record fingerprint changed")
    times = baseline.get("requested_times_relative_days")
    if times != list(ctx["times"]):
        raise ValueError("Parent baseline time grid differs from current SPK context")
    initial_from_rows = state_from_row(ctx["refs"]["annual_hourly"][0]) if "refs" in ctx else None
    if initial_from_rows is not None and tuple(baseline["native_initial_state"]) != _flat(initial_from_rows):
        raise ValueError("Parent baseline initial state differs from annual-hourly input")
    setting = next((item for item in previous_config["solver_settings"]
                    if item.get("label") == config["solver_setting"] and item.get("solver") == "dopri54"), None)
    if setting is None:
        raise ValueError("Parent ultra DP solver setting is unavailable")
    output = root / config["output_directory"]
    freeze_payload = {"hashes": prepared["hashes"], "runtime": prepared["parent_freeze"]["runtime"],
                      "parent_matrix_sha256": prepared["parent_matrix_sha256"],
                      "parent_verification_sha256": sha(root / config["parent_verification"])}
    freeze = output / "freeze.json"
    if freeze.exists():
        old = json.loads(freeze.read_text(encoding="utf-8"))
        if old.get("fingerprint") != prepared["fingerprint"] or any(old.get(k) != v for k, v in freeze_payload.items()):
            raise ValueError("Sensitivity freeze changed")
    else:
        immutable_json(freeze, {**freeze_payload, "fingerprint": prepared["fingerprint"],
                               "created_utc": dt.datetime.now(dt.timezone.utc).isoformat()})
    immutable_json(output / "inputs.json", {
        "fingerprint": prepared["fingerprint"], "parent_matrix_sha256": prepared["parent_matrix_sha256"],
        "parent_verification_sha256": freeze_payload["parent_verification_sha256"],
        "times_sha256": _json_digest(ctx["times"]), "times_count": len(ctx["times"]),
        "native_state_basis": NATIVE_BASIS, "reported_state_basis": OUTPUT_BASIS,
        "probes": _probe_specs(config), "scope": "diagnostic initial-state sensitivity; no covariance",
    })
    checkpoint_dir = output / "checkpoints"
    records: list[dict[str, Any]] = []
    for spec in _probe_specs(config):
        path = checkpoint_dir / f"{spec['key']}.json"
        if path.exists():
            if not resume:
                raise RuntimeError(f"resume disabled but checkpoint exists: {path.name}")
            row = json.loads(path.read_text(encoding="utf-8"))
            _verify_checkpoint(row, prepared["fingerprint"])
            if row.get("probe") != spec:
                raise ValueError(f"Checkpoint probe identity changed: {path.name}")
        else:
            row = _new_row(root, prepared, ctx, baseline, spec, setting)
            immutable_json(path, row)
            print(json.dumps({"completed": spec["key"], "propagation_seconds": row["propagation_runtime_seconds"]}), flush=True)
        records.append(row)
    if len(records) != 24:
        raise ValueError("Sensitivity matrix is incomplete")
    analysis = analyze_matrix(records, baseline, ctx["au"], ctx["day"], config)
    matrix = {"fingerprint": prepared["fingerprint"], "freeze_sha256": sha(freeze),
              "parent_matrix_sha256": prepared["parent_matrix_sha256"], "records": records,
              "analysis": analysis, "scope": "diagnostic initial-state finite differences; not covariance or validation"}
    matrix_path = output / "matrix.json"
    if matrix_path.exists():
        old = json.loads(matrix_path.read_text(encoding="utf-8"))
        if _json_digest(old) != _json_digest(matrix):
            raise ValueError("Completed sensitivity matrix is immutable and differs")
    else:
        immutable_json(matrix_path, matrix)
    print(json.dumps({"complete": 24, "new_propagations": 24, "fingerprint": prepared["fingerprint"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    run(args.root.resolve(), prepare_only=args.prepare_only, resume=args.resume)


__all__ = ["analyze_matrix", "compare_amplitudes", "prepare", "run"]

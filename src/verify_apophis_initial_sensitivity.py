"""Offline verifier for deterministic Apophis initial-state sensitivity probes."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import sys
from typing import Any, Mapping, Sequence

from de441_subset import DE441Subset
from run_eda import load_json
from run_physics_baselines import state_from_row
import verify_apophis_reference_time as rv
import verify_apophis_spk_audit as spk


CONFIG_PATH = "configs/apophis_initial_sensitivity.json"
NATIVE_BASIS = "uniform_inertial_q=rb-rSun0-t*vSun0,w=vb-vSun0"
OUTPUT_BASIS = "heliocentric_ICRF_geometric_AU_AU-per-day"
SI_KM = 1000.0


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _write_immutable(path: Path, value: Any) -> None:
    if path.exists():
        if load_json(path) != value:
            raise RuntimeError(f"immutable artifact differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp-{os.getpid()}")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _runtime() -> dict[str, str]:
    return {"executable": sys.executable, "version": sys.version, "platform": platform.platform()}


def _json_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _trace_hashes(row: Mapping[str, Any]) -> dict[str, str]:
    fields = ("requested_times_relative_days", "requested_states", "native_solver_requested_states",
              "accepted_endpoints", "native_solver_endpoints")
    if any(field not in row for field in fields):
        raise ValueError("sensitivity trace fields are incomplete")
    return {field: _json_digest(row[field]) for field in fields}


def _check_freeze(root: Path, matrix: Mapping[str, Any], freeze_path: Path) -> dict[str, Any]:
    freeze = load_json(freeze_path)
    if matrix.get("fingerprint") != freeze.get("fingerprint") or matrix.get("freeze_sha256") != _sha(freeze_path):
        raise ValueError("sensitivity matrix/freeze provenance mismatch")
    hashes, runtime = freeze.get("hashes"), freeze.get("runtime")
    if not isinstance(hashes, dict) or not isinstance(runtime, dict):
        raise ValueError("sensitivity freeze lacks hashes/runtime")
    for name, digest in hashes.items():
        path = _path(root, str(name))
        if not path.is_file() or _sha(path) != str(digest):
            raise ValueError(f"sensitivity frozen hash mismatch: {name}")
    if _runtime() != runtime:
        raise ValueError("sensitivity runtime differs from frozen runtime")
    payload = {"hashes": hashes, "runtime": runtime,
               "parent_matrix_sha256": freeze.get("parent_matrix_sha256"),
               "parent_verification_sha256": freeze.get("parent_verification_sha256")}
    if any(freeze.get(k) is None for k in ("parent_matrix_sha256", "parent_verification_sha256")):
        raise ValueError("sensitivity freeze lacks parent provenance")
    if hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest() != freeze.get("fingerprint"):
        raise ValueError("sensitivity freeze fingerprint recomputation mismatch")
    return freeze


def _parent_gate(root: Path, config: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    matrix_path, freeze_path, verification_path = (_path(root, config[k]) for k in ("parent_matrix", "parent_freeze", "parent_verification"))
    parent_matrix, parent_freeze, verification = load_json(matrix_path), load_json(freeze_path), load_json(verification_path)
    if parent_matrix.get("freeze_sha256") != _sha(freeze_path) or parent_matrix.get("fingerprint") != parent_freeze.get("fingerprint"):
        raise ValueError("parent matrix/freeze mismatch")
    for name, digest in parent_freeze.get("hashes", {}).items():
        path = _path(root, str(name))
        if not path.is_file() or _sha(path) != str(digest):
            raise ValueError(f"parent frozen hash mismatch: {name}")
    if parent_freeze.get("runtime") != _runtime():
        raise ValueError("parent runtime differs from current runtime")
    if verification.get("matrix_sha256") != _sha(matrix_path) or verification.get("freeze_sha256") != _sha(freeze_path) or verification.get("fingerprint") != parent_freeze.get("fingerprint") or verification.get("run_count") != 18 or verification.get("checkpoint_count") != 18:
        raise ValueError("parent verification does not certify the 18-record SPK matrix")
    baseline = next((row for row in parent_matrix.get("records", []) if row.get("key") == config["baseline_key"]), None)
    if baseline is None or baseline.get("fingerprint") != parent_matrix.get("fingerprint"):
        raise ValueError("parent baseline record is missing or not frozen")
    checkpoint = matrix_path.parent / "checkpoints" / (config["baseline_key"] + ".json")
    if load_json(checkpoint) != baseline or verification.get("checkpoint_sha256", {}).get(checkpoint.name) != _sha(checkpoint):
        raise ValueError("parent baseline checkpoint is not the verified matrix record")
    return parent_matrix, parent_freeze, verification, _sha(matrix_path)


def _expected_probes(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = []
    for component, amplitudes, units in (("position", config["position_amplitudes_m"], "m"), ("velocity", config["velocity_amplitudes_m_s"], "m_s")):
        for axis in config["axes"]:
            for amplitude in amplitudes:
                for sign in config["signs"]:
                    result.append({"key": f"{component}_axis{axis}_{float(amplitude):g}{units}_{'plus' if sign > 0 else 'minus'}",
                                   "component": component, "axis": axis, "amplitude": float(amplitude), "amplitude_units": units, "sign": sign})
    if len(result) != 24 or len({x["key"] for x in result}) != 24:
        raise ValueError("sensitivity probe specification is not the fixed 24-case set")
    return result


def _representable_delta(base: Sequence[float], probe: Mapping[str, Any], au_km: float, day_s: float) -> tuple[list[float], dict[str, float]]:
    values = [float(x) for x in base]
    axis, sign, amplitude = int(probe["axis"]), int(probe["sign"]), float(probe["amplitude"])
    if probe["component"] == "position":
        internal = amplitude / SI_KM / au_km
        values[axis] += sign * internal
        actual = (values[axis] - float(base[axis])) * au_km * SI_KM
        return values, {"position_m": actual, "velocity_m_s": 0.0}
    if probe["component"] == "velocity":
        internal = amplitude / SI_KM * day_s / au_km
        values[axis + 3] += sign * internal
        actual = (values[axis + 3] - float(base[axis + 3])) * au_km * SI_KM / day_s
        return values, {"position_m": 0.0, "velocity_m_s": actual}
    raise ValueError(f"unknown probe component: {probe['component']}")


def _norm(values: Sequence[float]) -> float:
    return math.sqrt(math.fsum(float(x) * float(x) for x in values))


def _difference(a: Sequence[float], b: Sequence[float]) -> list[float]:
    return [float(x) - float(y) for x, y in zip(a, b, strict=True)]


def _pair_analysis(baseline: Mapping[str, Any], plus: Mapping[str, Any], minus: Mapping[str, Any], au: float, day_s: float, indices: Mapping[str, int]) -> dict[str, Any]:
    input_kind = plus["probe"]["component"]
    field = "position_m" if input_kind == "position" else "velocity_m_s"
    span = math.fsum((float(plus["actual_delta_si"][field]), -float(minus["actual_delta_si"][field])))
    if not math.isfinite(span) or span == 0.0:
        raise ValueError(f"invalid actual input span: {plus['key']}")
    base, pos, neg = baseline["requested_states"], plus["requested_states"], minus["requested_states"]
    if not (len(base) == len(pos) == len(neg) == 797):
        raise ValueError("sensitivity requested traces must each have 797 states")
    result = {}
    for label, index in indices.items():
        b, p, m = (trace[index] for trace in (base, pos, neg))
        factors = (au * SI_KM,) * 3 + (au * SI_KM / day_s,) * 3
        derivative = [math.fsum((float(p[i]), -float(m[i]))) * factors[i] / span for i in range(6)]
        raw_mid = [math.fsum((float(p[i]) - float(b[i]), float(m[i]) - float(b[i]))) * factors[i] / 2.0 for i in range(6)]
        midpoint_input = math.fsum((float(plus["actual_delta_si"][field]), float(minus["actual_delta_si"][field]))) / 2.0
        corrected = [raw_mid[i] - derivative[i] * midpoint_input for i in range(6)]
        elapsed = float(label) * day_s
        result[label] = {"day": float(label), "elapsed_seconds": elapsed,
                         "derivative_position": derivative[:3], "derivative_velocity": derivative[3:],
                         "derivative_units": {"position": "m_per_m", "velocity": "(m_per_s)_per_m"} if plus["probe"]["component"] == "position" else {"position": "m_per_(m_per_s)=s", "velocity": "dimensionless"},
                         "position_response_norm": _norm(derivative[:3]), "velocity_response_norm": _norm(derivative[3:]),
                         "position_response_relative_to_free_flight": _norm(derivative[:3]) / elapsed if plus["probe"]["component"] == "velocity" and elapsed > 0 else None,
                         "raw_midpoint_displacement": raw_mid, "corrected_midpoint_displacement": corrected,
                         "raw_input_midpoint_si": midpoint_input, "actual_input_span_si": span}
    return {"probe_pair": {"plus_key": plus["key"], "minus_key": minus["key"],
                            "axis": plus["probe"]["axis"], "component": input_kind,
                            "amplitude": plus["probe"]["amplitude"], "amplitude_units": plus["probe"]["amplitude_units"],
                            "actual_plus_delta_si": plus["actual_delta_si"], "actual_minus_delta_si": minus["actual_delta_si"], "actual_span_si": span},
            "diagnostics": result, "annual": result["365.0"]}


def _consistency(first: Mapping[str, Any], second: Mapping[str, Any], budget: float) -> dict[str, Any]:
    def relative(a: Sequence[float], b: Sequence[float]) -> float:
        difference, denominator = _norm(_difference(a, b)), max(_norm(a), _norm(b))
        return 0.0 if denominator == 0.0 and difference == 0.0 else difference / denominator if denominator else math.inf
    position = {day: relative(first["diagnostics"][day]["derivative_position"], second["diagnostics"][day]["derivative_position"]) for day in first["diagnostics"]}
    velocity = {day: relative(first["diagnostics"][day]["derivative_velocity"], second["diagnostics"][day]["derivative_velocity"]) for day in first["diagnostics"]}
    mp, mv = max(position.values()), max(velocity.values())
    return {"relative_difference_position": position, "relative_difference_velocity": velocity,
            "max_relative_difference_position": mp, "max_relative_difference_velocity": mv,
            "budget": budget, "position_passes": mp <= budget, "velocity_passes": mv <= budget, "passes": mp <= budget and mv <= budget}


def _analysis(records: Sequence[Mapping[str, Any]], baseline: Mapping[str, Any], au: float, day_s: float, config: Mapping[str, Any]) -> dict[str, Any]:
    by_key = {str(row.get("key")): row for row in records}; times = baseline.get("requested_times_relative_days")
    if not isinstance(times, list) or len(times) != 797 or times[0] != 0.0 or times[-1] != 365.0:
        raise ValueError("sensitivity baseline has wrong requested grid")
    indices = {}
    for day in config["diagnostic_days"]:
        found = [i for i, t in enumerate(times) if float(t) == float(day)]
        if len(found) != 1: raise ValueError(f"diagnostic day is not an exact knot: {day}")
        indices[str(float(day))] = found[0]
    families = []
    for component, amplitudes, units in (("position", config["position_amplitudes_m"], "m"), ("velocity", config["velocity_amplitudes_m_s"], "m_s")):
        for axis in config["axes"]:
            analyses = []
            for amplitude in amplitudes:
                selected = [r for r in records if r["probe"]["component"] == component and r["probe"]["axis"] == axis and r["probe"]["amplitude"] == float(amplitude)]
                if sorted(r["probe"]["sign"] for r in selected) != [-1, 1]: raise ValueError("missing signed sensitivity pair")
                analyses.append(_pair_analysis(baseline, next(r for r in selected if r["probe"]["sign"] == 1), next(r for r in selected if r["probe"]["sign"] == -1), au, day_s, indices))
            families.append({"component": component, "axis": axis, "amplitude_units": units, "amplitudes": analyses,
                             "consistency": _consistency(analyses[0], analyses[1], float(config["derivative_relative_consistency_budget"]))})
    return {"diagnostic_days": config["diagnostic_days"], "families": families, "scope": "signed initial-state finite-difference probes; not covariance or validation"}


def verify(root: Path) -> dict[str, Any]:
    root = root.resolve(); config = load_json(root / CONFIG_PATH); output = root / config["output_directory"]
    matrix_path, freeze_path = output / "matrix.json", output / "freeze.json"; matrix = load_json(matrix_path); freeze = _check_freeze(root, matrix, freeze_path)
    parent_matrix, parent_freeze, parent_verification, parent_matrix_sha = _parent_gate(root, config)
    parent_baseline = next(r for r in parent_matrix["records"] if r["key"] == config["baseline_key"])
    if matrix.get("parent_matrix_sha256") != parent_matrix_sha or freeze.get("parent_matrix_sha256") != parent_matrix_sha or freeze.get("parent_verification_sha256") != _sha(root / config["parent_verification"]) or matrix.get("fingerprint") != freeze["fingerprint"]:
        raise ValueError("sensitivity parent/fingerprint provenance mismatch")
    inputs = load_json(output / "inputs.json")
    if inputs.get("fingerprint") != freeze["fingerprint"] or inputs.get("parent_matrix_sha256") != parent_matrix_sha or inputs.get("parent_verification_sha256") != _sha(root / config["parent_verification"]):
        raise ValueError("sensitivity inputs provenance mismatch")
    records = matrix.get("records"); expected = _expected_probes(config)
    if not isinstance(records, list) or len(records) != 24 or {r.get("key") for r in records} != {p["key"] for p in expected}:
        raise ValueError("sensitivity matrix does not contain exactly 24 probes")
    if inputs.get("probes") != expected or inputs.get("times_count") != 797:
        raise ValueError("sensitivity input probe/grid provenance mismatch")
    if inputs.get("native_state_basis") != NATIVE_BASIS or inputs.get("reported_state_basis") != OUTPUT_BASIS:
        raise ValueError("sensitivity input basis provenance mismatch")
    ctx, _, _, backend, _, _, _ = __import__("run_apophis_spk_audit", fromlist=["context"]).context(root)
    au, day_s = float(ctx["au"]), float(ctx["day"])
    setting = next(s for s in load_json(root / "configs/apophis_eih.json")["solver_settings"] if s["label"] == config["solver_setting"])
    sun = backend.ephemeris(10, 0)
    annual_initial = state_from_row(ctx["refs"]["annual_hourly"][0])
    if parent_baseline.get("native_initial_state") != [*annual_initial.position, *annual_initial.velocity]:
        raise ValueError("parent baseline initial does not equal annual-hourly input")
    expected_force = {"arm": config["ephemeris_arm"], "gr": "eih_sun", "solar_j2": False,
                      "earth_pole": "fixed_j2000", "newton_sources": "Sun + 10 major + SB16",
                      "ng": "nominal", "teacher_states_in_force": False}
    for row, probe in zip(sorted(records, key=lambda x: x["key"]), sorted(expected, key=lambda x: x["key"]), strict=True):
        if row.get("probe") != probe or row.get("initial_source") != "annual_hourly" or row.get("solver") != "dopri54" or row.get("numerical_mode") != "precise_dp" or row.get("setting") != setting:
            raise ValueError(f"sensitivity metadata mismatch: {row.get('key')}")
        if row.get("fingerprint") != freeze["fingerprint"] or row.get("parent_matrix_sha256") != parent_matrix_sha or row.get("origin_jd_tdb") != parent_baseline["origin_jd_tdb"] or row.get("origin_calendar_tdb") != parent_baseline["origin_calendar_tdb"]:
            raise ValueError(f"sensitivity provenance mismatch: {row['key']}")
        if row.get("requested_times_relative_days") != parent_baseline["requested_times_relative_days"]:
            raise ValueError(f"sensitivity time grid mismatch: {row['key']}")
        expected_native, expected_delta = _representable_delta(parent_baseline["native_initial_state"], probe, au, day_s)
        if row.get("native_initial_state") != expected_native or row.get("actual_delta_si") != expected_delta:
            raise ValueError(f"representable perturbation mismatch: {row['key']}")
        if row.get("initial_state") != row.get("native_initial_state") or row.get("accepted_time_basis") != "relative_days_since_start":
            raise ValueError(f"initial state basis mismatch: {row['key']}")
        if row.get("force_specification") != expected_force or row.get("native_state_basis") != NATIVE_BASIS or row.get("reported_state_basis") != OUTPUT_BASIS:
            raise ValueError(f"sensitivity force/basis metadata mismatch: {row['key']}")
        if row.get("trace_hashes") != _trace_hashes(row):
            raise ValueError(f"sensitivity trace hash mismatch: {row['key']}")
        if inputs.get("times_sha256") != _json_digest(row["requested_times_relative_days"]):
            raise ValueError(f"sensitivity times provenance mismatch: {row['key']}")
        stats = row.get("solver_stats")
        if not isinstance(stats, dict) or stats.get("compensated") is not True:
            raise ValueError(f"solver compensation metadata missing: {row['key']}")
        times = row["requested_times_relative_days"]
        spk._verify_native(row, sun, rv._state(row["native_initial_state"]), times)
        if stats.get("accepted_steps") != len(row["native_solver_endpoints"]) - 1:
            raise ValueError(f"sensitivity accepted step count mismatch: {row['key']}")
        if stats.get("status") != "finished" or stats.get("final_time") != 365.0 or stats.get("epoch") != 0.0 or stats.get("endpoint_consistent") is not True:
            raise ValueError(f"sensitivity solver completion metadata mismatch: {row['key']}")
        if not 0 < stats["min_accepted_step"] <= stats["max_accepted_step"] <= 0.25 or stats["attempted_steps"] != stats["accepted_steps"] + stats["rejected_steps"]:
            raise ValueError(f"sensitivity step controls mismatch: {row['key']}")
    checkpoints = sorted((output / "checkpoints").glob("*.json"))
    if {p.stem for p in checkpoints} != {p["key"] for p in expected}: raise ValueError("sensitivity checkpoint set mismatch")
    by_key = {r["key"]: r for r in records}; checkpoint_hashes = {}
    for path in checkpoints:
        if load_json(path) != by_key[path.stem]: raise ValueError(f"sensitivity checkpoint differs: {path.name}")
        checkpoint_hashes[path.name] = _sha(path)
    analysis = _analysis(records, parent_baseline, au, day_s, config)
    if matrix.get("analysis") != analysis: raise ValueError("saved sensitivity analysis differs from independent recomputation")
    provenance = {"matrix_sha256": _sha(matrix_path), "freeze_sha256": _sha(freeze_path), "fingerprint": freeze["fingerprint"],
                  "verifier_sha256": _sha(root / "src/verify_apophis_initial_sensitivity.py"), "parent_matrix_sha256": parent_matrix_sha,
                  "parent_verification_sha256": _sha(root / config["parent_verification"]), "parent_spk_verifier_sha256": _sha(root / "src/verify_apophis_spk_audit.py"),
                  "reference_verifier_sha256": _sha(root / "src/verify_apophis_reference_time.py")}
    analysis["provenance"] = provenance; _write_immutable(output / "analysis.json", analysis)
    verification = {"schema_version": 1, **provenance, "config_sha256": _sha(root / CONFIG_PATH), "inputs_sha256": _sha(output / "inputs.json"),
                    "run_count": len(records), "checkpoint_count": len(checkpoints), "checkpoint_sha256": checkpoint_hashes,
                    "primary_rows": 797, "native_requested_state_count": sum(len(r["native_solver_requested_states"]) for r in records),
                    "native_accepted_endpoint_count": sum(len(r["native_solver_endpoints"]) for r in records),
                    "derivative_family_count": len(analysis["families"]), "cross_solver_parent_limit_recorded": parent_verification.get("cross_solver_within_budget")}
    _write_immutable(output / "verification.json", verification); return verification


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--root", type=Path, default=Path(".")); args = parser.parse_args()
    print(json.dumps(verify(args.root), indent=2, ensure_ascii=False))

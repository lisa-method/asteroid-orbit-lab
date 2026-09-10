"""Verify the saved Apophis closure and long-convergence traces.

This module only reads existing checkpoints.  It never calls an integrator.  The
reported comparisons are numerical reproducibility checks between saved
trajectories; they are not a new validation set or a physical explanation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

from prepare_fresh_holdout import check_hashes, immutable_json, sha
from run_apophis_encounter_closure import build_context, digest, scenario
from run_apophis_solver_audit import _flat, _state
from run_physics_baselines import errors, state_from_row


LONG_FREEZE = "outputs/apophis_long_convergence/freeze.json"
CLOSURE_FREEZE = "outputs/apophis_encounter_closure/freeze.json"
CLOSURE_ANALYSIS = "outputs/apophis_encounter_closure/analysis.json"
LONG_SETTINGS = {
    "extrap": {"rtol": 1e-16, "atol_position": 1e-19, "atol_velocity": 1e-20, "max_step": 0.125},
    "dp": {"rtol": 1e-16, "atol_position": 1e-18, "atol_velocity": 1e-19, "max_step": 0.03125},
}
DAYS = (0.0, 30.0, 60.0, 90.0, 99.0, 102.0, 103.0, 106.0, 180.0, 365.0)


def _finite(value: Any) -> bool:
    if isinstance(value, (list, tuple)):
        return all(_finite(x) for x in value)
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _samples(row: dict[str, Any]) -> list[tuple[float, list[float]]]:
    """Normalize both long-run pairs and the older day/state sample schema."""
    result = []
    for item in row["samples"]:
        if isinstance(item, dict):
            result.append((float(item["day"]), [float(x) for x in item["state"]]))
        else:
            result.append((float(item[0]), [float(x) for x in item[1]]))
    return result


def _native_samples(row: dict[str, Any]) -> list[tuple[float, list[float]]]:
    return [(float(item[0]), [float(x) for x in item[1]]) for item in row["native_samples"]]


def _endpoints(row: dict[str, Any]) -> list[tuple[float, list[float]]]:
    return [(float(item[0]), [float(x) for x in item[1]]) for item in row["native_endpoints"]]


def _trace_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _check_trace(row: dict[str, Any], *, name: str, convert: Any, expected_times: list[float], initial: Iterable[float]) -> dict[str, Any]:
    """Check payload, requested grid, native-to-heliocentric conversion and endpoints."""
    _assert(row.get("solver") in ("extrap", "dp") or name.startswith("closure:"), f"Unexpected solver in {name}")
    _assert(row.get("solver_stats", {}).get("status") == "finished", f"unfinished {name}")
    _assert(row.get("payload_sha256") == digest({k: v for k, v in row.items() if k != "payload_sha256"}), f"payload changed: {name}")
    samples = _samples(row)
    native = _native_samples(row)
    _assert([t for t, _ in samples] == expected_times, f"requested grid changed: {name}")
    _assert([t for t, _ in native] == expected_times, f"native requested grid changed: {name}")
    _assert(samples[0][1] == list(initial), f"converted initial changed: {name}")
    _assert(native[0][1] == list(row["initial_native_state"]), f"native initial changed: {name}")
    _assert(all(_finite(s) for _, s in samples + native), f"nonfinite requested state: {name}")
    conversion_error = 0.0
    for (time_a, state_a), (time_b, state_b) in zip(native, samples, strict=True):
        _assert(time_a == time_b, f"native/output time mismatch: {name}")
        converted = _flat(convert(time_a, _state(state_a)))
        conversion_error = max(conversion_error, max(abs(a - b) for a, b in zip(converted, state_b, strict=True)))
        _assert(list(converted) == state_b, f"native-to-heliocentric conversion changed: {name} day={time_a}")
    endpoints = _endpoints(row)
    _assert(endpoints and endpoints[0][0] == 0.0, f"missing initial endpoint: {name}")
    _assert(endpoints[-1][0] == expected_times[-1], f"endpoint coverage changed: {name}")
    _assert(all(_finite(s) for _, s in endpoints), f"nonfinite endpoint: {name}")
    return {
        "name": name,
        "requested_count": len(samples),
        "native_endpoint_count": len(endpoints),
        "requested_first_day": samples[0][0],
        "requested_last_day": samples[-1][0],
        "native_endpoint_first_day": endpoints[0][0],
        "native_endpoint_last_day": endpoints[-1][0],
        "native_to_heliocentric_exact": True,
        "maximum_conversion_absolute_difference": conversion_error,
        "sample_trace_sha256": _trace_hash(samples),
        "native_sample_trace_sha256": _trace_hash(native),
        "native_endpoint_trace_sha256": _trace_hash(endpoints),
    }


def _metrics(left: dict[float, list[float]], right: dict[float, list[float]], au_km: float, day_s: float, expected_count: int) -> dict[str, Any]:
    common = sorted(set(left).intersection(right))
    _assert(len(common) == expected_count, f"expected {expected_count} common nodes, got {len(common)}")
    diagnostics = []
    for day in common:
        a = left[day]
        b = right[day]
        dp = [a[i] - b[i] for i in range(3)]
        dv = [a[i + 3] - b[i + 3] for i in range(3)]
        p = math.sqrt(math.fsum(x * x for x in dp)) * au_km * 1000.0
        v = math.sqrt(math.fsum(x * x for x in dv)) * au_km * 1000.0 / day_s
        diagnostics.append({"day": day, "position_m": p, "velocity_m_s": v,
                            "delta_position_m": [x * au_km * 1000.0 for x in dp],
                            "delta_velocity_m_s": [x * au_km * 1000.0 / day_s for x in dv]})
    worst = max(diagnostics, key=lambda item: item["position_m"])
    return {
        "count": len(diagnostics),
        "max_position_m": worst["position_m"],
        "max_velocity_m_s": max(x["velocity_m_s"] for x in diagnostics),
        "worst_day": worst["day"],
        "final": diagnostics[-1],
        "diagnostics": [x for x in diagnostics if x["day"] in DAYS],
        "numerical_1m_gate": worst["position_m"] <= 1.0,
        "pairing": "exact common nodes; refined nodes are omitted when comparing with the daily teacher",
    }


def _map(row: dict[str, Any]) -> dict[float, list[float]]:
    return {t: s for t, s in _samples(row)}


def _validate_long(root: Path, cctx: dict[str, Any], ctx: dict[str, Any], planets: Any, sun: Any, fingerprint: str) -> tuple[dict[str, Any], dict[str, list[float]]]:
    result: dict[str, Any] = {}
    converted: dict[str, dict[float, list[float]]] = {}
    force, convert, initial, times, _, _ = scenario(cctx, ctx, planets, sun, "long")
    del force
    expected_initial = _flat(initial)
    expected_times = [float(t) for t in times]
    for solver, setting in LONG_SETTINGS.items():
        path = root / "outputs/apophis_long_convergence" / f"{solver}.json"
        row = json.loads(path.read_text())
        _assert(row.get("fingerprint") == fingerprint, f"long fingerprint changed: {solver}")
        _assert(row.get("setting") == setting, f"long setting changed: {solver}")
        check = _check_trace(row, name=f"long:{solver}", convert=convert, expected_times=expected_times,
                             initial=_flat(convert(0.0, initial)))
        check.update({"solver": solver, "runtime_seconds": row["runtime_seconds"], "solver_stats": row["solver_stats"]})
        _assert(_finite(row["runtime_seconds"]) and row["runtime_seconds"] >= 0.0, f"invalid runtime: {solver}")
        result[solver] = check
        converted[solver] = _map(row)
    result["grid"] = {"count": len(expected_times), "first_day": expected_times[0], "last_day": expected_times[-1], "exact": True}
    return result, converted


def _validate_closure(root: Path, cctx: dict[str, Any], ctx: dict[str, Any], planets: Any, sun: Any) -> tuple[dict[str, Any], dict[str, dict[float, list[float]]]]:
    freeze = json.loads((root / CLOSURE_FREEZE).read_text())
    check_hashes(root, freeze["hashes"])
    output: dict[str, Any] = {}
    maps: dict[str, dict[float, list[float]]] = {}
    for name in ("annual", "long", "local"):
        force, convert, initial, times, _, _ = scenario(cctx, ctx, planets, sun, name)
        del force
        expected_times = [float(t) for t in times]
        for setting in ("coarse", "fine"):
            key = f"{name}__{setting}"
            path = root / "outputs/apophis_encounter_closure" / "checkpoints" / f"{key}.json"
            row = json.loads(path.read_text())
            _assert(row.get("fingerprint") == freeze["fingerprint"], f"closure fingerprint changed: {key}")
            output[key] = _check_trace(row, name=f"closure:{key}", convert=convert,
                                       expected_times=expected_times,
                                       initial=_flat(convert(0.0, initial)))
            maps[key] = _map(row)
    return output, maps


def analyze(root: Path) -> dict[str, Any]:
    root = root.resolve()
    long_freeze = json.loads((root / LONG_FREEZE).read_text())
    check_hashes(root, long_freeze["hashes"])
    cctx, ctx, planets, sun, runtime, _ = build_context(root)
    _assert(runtime == long_freeze["runtime"], "runtime differs from long-run freeze")
    closure_checks, closure_maps = _validate_closure(root, cctx, ctx, planets, sun)
    long_checks, long_maps = _validate_long(root, cctx, ctx, planets, sun, long_freeze["fingerprint"])

    comparisons = {
        "new_extrap_vs_new_dp": _metrics(long_maps["extrap"], long_maps["dp"], ctx["au"], ctx["day"], 895),
        "new_extrap_vs_prior_long_fine": _metrics(long_maps["extrap"], closure_maps["long__fine"], ctx["au"], ctx["day"], 895),
        "new_dp_vs_prior_long_fine": _metrics(long_maps["dp"], closure_maps["long__fine"], ctx["au"], ctx["day"], 895),
    }

    teacher = {float(row["epoch_jd_tdb"] - ctx["origin"]): list(_flat(state_from_row(row))) for row in ctx["refs"]["annual_daily"]}
    annual_fine = closure_maps["annual__fine"]
    teacher_comparison = _metrics(annual_fine, teacher, ctx["au"], ctx["day"], 366)
    recomputed_nodes = {"teacher_daily_count": len(teacher), "annual_fine_vs_teacher": teacher_comparison,
                        "selected_days": [x for x in teacher_comparison["diagnostics"] if x["day"] in DAYS]}

    existing = json.loads((root / CLOSURE_ANALYSIS).read_text())
    summary = {
        "schema": "apophis-encounter-closure-long-convergence-analysis-v1",
        "scope": "read-only recomputation from frozen closure and long-run traces; no propagation",
        "long_freeze_fingerprint": long_freeze["fingerprint"],
        "long_freeze_sha256": sha(root / LONG_FREEZE),
        "closure_freeze_sha256": sha(root / CLOSURE_FREEZE),
        "runtime": runtime,
        "trace_checks": {"closure": closure_checks, "long": long_checks},
        "comparisons": comparisons,
        "teacher_daily": {"count": len(teacher), "grid_first_day": min(teacher), "grid_last_day": max(teacher),
                           "state_trace_sha256": _trace_hash(sorted(teacher.items()))},
        "recomputed_nodes": recomputed_nodes,
        "encounters_from_frozen_analysis": existing["encounters"],
        "closure_analysis_sha256": sha(root / CLOSURE_ANALYSIS),
        "covariance_ensemble_convergence_checked": False,
        "interpretation_limits": [
            "The nine-year nominal covariance run is not the annual forecast.",
            "A one-metre numerical convergence claim remains unresolved.",
            "Covariance ensemble convergence and uncertainty calibration were not checked.",
            "The teacher minimum uses a 300-second Hermite evaluator and is not sub-millisecond ground truth.",
        ],
    }
    immutable_json(root / "outputs/apophis_long_convergence/summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    result = analyze(args.root)
    print(json.dumps({"summary": "outputs/apophis_long_convergence/summary.json",
                      "comparisons": {k: v["max_position_m"] for k, v in result["comparisons"].items()},
                      "teacher_max_m": result["recomputed_nodes"]["annual_fine_vs_teacher"]["max_position_m"]}, indent=2))

"""Canonical read-only verification of the saved Apophis closure artifacts.

No model context is built here and no propagation is performed.  The verifier
checks the two experiment freezes, all eight saved traces, requested-node
coverage, and the published comparison values.  It intentionally keeps
teacher accuracy statuses separate from numerical-convergence statuses.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from prepare_fresh_holdout import check_hashes, immutable_json, sha


CLOSURE = "outputs/apophis_encounter_closure"
LONG = "outputs/apophis_long_convergence"
ANALYSIS = f"{CLOSURE}/analysis.json"
SUMMARY = f"{LONG}/summary.json"


def _require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def _digest(value: Any) -> str:
    # The frozen closure/long runners use the standard json separators.
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _finite_state(state: Any) -> bool:
    return isinstance(state, list) and len(state) == 6 and all(isinstance(x, (int, float)) and math.isfinite(float(x)) for x in state)


def _pairs(value: Any, label: str) -> list[tuple[float, list[float]]]:
    _require(isinstance(value, list), f"{label} is not a list")
    result = []
    for index, item in enumerate(value):
        _require(isinstance(item, list) and len(item) == 2, f"{label}[{index}] is not a pair")
        time_value, state = float(item[0]), item[1]
        _require(math.isfinite(time_value) and _finite_state(state), f"invalid {label}[{index}]")
        result.append((time_value, [float(x) for x in state]))
    return result


def _verify_trace(root: Path, path: Path, expected_fingerprint: str, label: str) -> dict[str, Any]:
    row = json.loads(path.read_text(encoding="utf-8"))
    _require(row.get("fingerprint") == expected_fingerprint, f"{label}: fingerprint mismatch")
    _require(row.get("payload_sha256") == _digest({k: v for k, v in row.items() if k != "payload_sha256"}), f"{label}: payload mismatch")
    stats = row.get("solver_stats")
    _require(isinstance(stats, dict) and stats.get("status") == "finished", f"{label}: unfinished solver")
    accepted_steps = stats.get("accepted_steps")
    _require(isinstance(accepted_steps, int) and not isinstance(accepted_steps, bool) and accepted_steps >= 0, f"{label}: invalid accepted_steps")
    endpoints = _pairs(row.get("native_endpoints"), f"{label}.native_endpoints")
    requested = _pairs(row.get("native_samples"), f"{label}.native_samples")
    _require(len(endpoints) == accepted_steps + 1, f"{label}: endpoint count differs from accepted_steps")
    _require(endpoints and all(b[0] > a[0] for a, b in zip(endpoints, endpoints[1:])), f"{label}: endpoints are not strictly increasing")
    endpoint_by_time = {time_value: state for time_value, state in endpoints}
    for time_value, state in requested:
        _require(time_value in endpoint_by_time, f"{label}: requested time absent from endpoints: {time_value}")
        _require(state == endpoint_by_time[time_value], f"{label}: requested state differs from endpoint: {time_value}")
    output = _pairs(row.get("samples"), f"{label}.samples")
    _require([time_value for time_value, _ in output] == [time_value for time_value, _ in requested], f"{label}: output grid differs from native grid")
    _require(row.get("runtime_seconds") is not None and math.isfinite(float(row["runtime_seconds"])) and float(row["runtime_seconds"]) >= 0.0, f"{label}: invalid runtime")
    return {
        "path": path.relative_to(root).as_posix(),
        "payload_sha256": row["payload_sha256"],
        "requested_count": len(requested),
        "accepted_steps": accepted_steps,
        "native_endpoint_count": len(endpoints),
        "requested_times_present_exactly": True,
        "requested_states_match_endpoints_exactly": True,
        "strictly_increasing_native_endpoints": True,
        "runtime_seconds": row["runtime_seconds"],
    }


def _max_expected(actual: float, expected: float, label: str, tolerance: float = 1e-9) -> dict[str, Any]:
    _require(math.isfinite(actual), f"{label}: nonfinite metric")
    _require(abs(actual - expected) <= tolerance, f"{label}: expected {expected!r}, got {actual!r}")
    return {"value": actual, "expected": expected, "exact_within": tolerance}


def verify(root: Path) -> dict[str, Any]:
    root = root.resolve()
    closure_freeze = json.loads((root / f"{CLOSURE}/freeze.json").read_text())
    long_freeze = json.loads((root / f"{LONG}/freeze.json").read_text())
    check_hashes(root, closure_freeze["hashes"])
    check_hashes(root, long_freeze["hashes"])

    traces: dict[str, dict[str, Any]] = {}
    for name in ("annual__coarse", "annual__fine", "long__coarse", "long__fine", "local__coarse", "local__fine"):
        traces[name] = _verify_trace(root, root / f"{CLOSURE}/checkpoints/{name}.json", closure_freeze["fingerprint"], f"closure:{name}")
    for name in ("extrap", "dp"):
        traces[f"long_convergence:{name}"] = _verify_trace(root, root / f"{LONG}/{name}.json", long_freeze["fingerprint"], f"long:{name}")

    analysis = json.loads((root / ANALYSIS).read_text())
    summary = json.loads((root / SUMMARY).read_text())
    prior = analysis["comparisons"]
    expected_prior = {
        "annual_coarse_fine": 0.26992469203592034,
        "annual_fine_vs_rk4_fine": 0.34199377386205665,
        "annual_fine_vs_ultra": 0.8848621758982271,
        "annual_fine_vs_extreme": 1.0275994258405425,
        "local_coarse_fine": 0.0059238893326966505,
    }
    prior_metrics = {key: _max_expected(float(prior[key]["max_position_m"]), value, f"prior:{key}") for key, value in expected_prior.items()}
    summary_expected = {
        "new_extrap_vs_new_dp": 2.565972747977741,
        "new_extrap_vs_prior_long_fine": 18.915604739560155,
        "new_dp_vs_prior_long_fine": 16.34971249026025,
    }
    summary_metrics = {key: _max_expected(float(summary["comparisons"][key]["max_position_m"]), value, f"summary:{key}") for key, value in summary_expected.items()}
    teacher_m = float(summary["recomputed_nodes"]["annual_fine_vs_teacher"]["max_position_m"])
    teacher_prior_m = float(analysis["teacher"]["annual__fine"]["max_position_m"])
    _require(abs(teacher_m - teacher_prior_m) <= 1e-9, "annual teacher metric differs between summary and prior analysis")
    _require(abs(teacher_m - 2552.13743168636) <= 1e-9, "annual teacher metric differs from expected")

    source_paths = [
        "src/analyze_apophis_encounter_closure.py",
        "src/analyze_apophis_long_convergence.py",
        "src/verify_apophis_closure_final.py",
        SUMMARY,
    ]
    source_hashes = {path: sha(root / path) for path in source_paths}
    result = {
        "schema_version": 1,
        "verification": "passed",
        "scope": "canonical read-only verification of frozen Apophis closure and long-convergence artifacts",
        "source_hashes": source_hashes,
        "freeze_hashes": {"closure": sha(root / f"{CLOSURE}/freeze.json"), "long": sha(root / f"{LONG}/freeze.json")},
        "trace_count": len(traces),
        "traces": traces,
        "prior_analysis_sha256": sha(root / ANALYSIS),
        "summary_sha256": sha(root / SUMMARY),
        "prior_analysis_pair_metrics": prior_metrics,
        "summary_pair_metrics": summary_metrics,
        "teacher_status": {
            "annual_teacher_max_position_m": teacher_m,
            "annual_teacher_pass_1km": False,
            "annual_teacher_pass_10km": True,
            "note": "Teacher accuracy status; not a numerical convergence gate.",
        },
        "numerical_status": {
            "long_numerical_1m": False,
            "new_long_max_pair_difference_m": summary_expected["new_extrap_vs_new_dp"],
            "strict_1m_convergence_resolved": False,
        },
        "semantic_correction": "The annual teacher comparison's archived numerical_1m_gate field is not used as a teacher accuracy status.",
        "propagation_performed_by_verifier": False,
    }
    immutable_json(root / f"{LONG}/final_verification.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    result = verify(args.root)
    print(json.dumps({"verification": result["verification"], "trace_count": result["trace_count"],
                      "annual_teacher_pass_1km": result["teacher_status"]["annual_teacher_pass_1km"],
                      "annual_teacher_pass_10km": result["teacher_status"]["annual_teacher_pass_10km"],
                      "long_numerical_1m": result["numerical_status"]["long_numerical_1m"]}, indent=2))

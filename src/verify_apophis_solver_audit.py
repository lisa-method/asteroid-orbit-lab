"""Offline verifier for the completed Apophis independent-solver audit.

The verifier consumes only the frozen audit JSON, checkpoints, source hashes,
and raw reference rows.  It reconstructs summaries and pairwise separations
from the stored requested states; it never calls either propagator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

from orbit_baselines import State
from run_apophis_solver_audit import _shift, _summary, _target_rows, _state, state_from_row
from run_force_models_v2 import _runtime_environment
from run_eda import load_json
from ng_inputs_v2 import load_ng_input


DEFAULT_OUTPUT = Path("outputs/apophis_solver_audit/stage3")
DEFAULT_AUDIT = "apophis_solver_audit.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _finite(value: Any, label: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be finite")
    number = float(value)
    if not math.isfinite(number) or (nonnegative and number < 0.0):
        raise ValueError(f"{label} must be finite and non-negative" if nonnegative else f"{label} must be finite")
    return number


def _states(values: Any, label: str) -> list[State]:
    if not isinstance(values, list) or not values:
        raise ValueError(f"{label} must be a non-empty state list")
    states = []
    for index, value in enumerate(values):
        if not isinstance(value, list) or len(value) != 6:
            raise ValueError(f"{label}[{index}] must be a six-component list")
        flat = tuple(_finite(component, f"{label}[{index}][{j}]") for j, component in enumerate(value))
        states.append(_state(flat))
    return states


def _close(actual: Mapping[str, Any], expected: Mapping[str, Any], label: str, *, atol: float = 1e-12) -> None:
    for key, value in expected.items():
        if key not in actual or not math.isclose(float(actual[key]), float(value), rel_tol=0.0, abs_tol=atol):
            raise ValueError(f"{label} mismatch at {key}: {actual.get(key)!r} != {value!r}")


def _source_paths(root: Path, config_path: Path, config: Mapping[str, Any], data: Mapping[str, Any], force_config: Mapping[str, Any], event: Mapping[str, Any]) -> list[Path]:
    paths = [config_path, root / config["data_config"], root / config["force_config"], root / config["event_config"],
             root / "data/raw/horizons/asteroid_99942.json", root / "src/independent_rk.py",
             root / "src/run_apophis_solver_audit.py"]
    paths += [root / "data/raw/horizons" / f"body_{body['id']}.json" for body in data["perturbers"]]
    paths += [root / force_config["small_body_ephemeris_directory"] / f"asteroid_{body['id']}.json"
              for body in force_config["small_body_perturbers"]]
    paths += sorted((root / "data/raw/horizons_refined").glob("apophis_earth_2029_*.json"))
    paths += sorted((root / "data/raw/development30/planets").glob("body_*.json"))
    return paths


def check_source_freeze(root: Path, audit: Mapping[str, Any]) -> dict[str, Any]:
    config_path = root / audit["config"]
    config = load_json(config_path)
    data = load_json(root / config["data_config"])
    force_config = load_json(root / config["force_config"])
    event = load_json(root / config["event_config"])
    paths = _source_paths(root, config_path, config, data, force_config, event)
    actual = {path.relative_to(root).as_posix(): sha(path) for path in paths}
    if actual != audit.get("source_sha256"):
        raise ValueError("stage3 source hash map does not match current files")
    fingerprint = hashlib.sha256(json.dumps({"config": config, "sources": actual}, sort_keys=True).encode()).hexdigest()
    if fingerprint != audit.get("fingerprint"):
        raise ValueError("stage3 fingerprint mismatch")

    rules_path = root / "outputs/force_models_v2/rules.json"
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    shared = rules["provenance"]["force_source_hashes"]
    shared_checked = {}
    for relative, expected in shared.items():
        path = root / relative
        current = sha(path)
        if current != expected:
            raise ValueError(f"shared force source changed: {relative}")
        shared_checked[relative] = current
    ng = load_ng_input(root / "data/raw/horizons/asteroid_99942.json")
    annual_start = _target_rows(root, data, event, config["annual_horizon_days"])[0][0]["epoch_jd_tdb"]
    gate = audit["ng_gate"]
    if gate.get("source_sha256") != ng.source_sha256 or gate.get("status") != ng.status(annual_start):
        raise ValueError("NG gate no longer matches the frozen raw header")
    return {"audit_source_files": len(actual), "audit_fingerprint": fingerprint,
            "shared_force_source_files": len(shared_checked),
            "runtime_environment_stored_in_audit": False,
            "verification_runtime_environment": _runtime_environment(),
            "ng_status": gate["status"], "ng_source_sha256": ng.source_sha256}


def _check_accepted(record: Mapping[str, Any], start: float, end: float, label: str) -> None:
    accepted = record.get("accepted_endpoints")
    if not isinstance(accepted, list) or not accepted:
        raise ValueError(f"{label} has no accepted endpoints")
    times = []
    for index, item in enumerate(accepted):
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError(f"{label}.accepted_endpoints[{index}] has invalid schema")
        times.append(_finite(item[0], f"{label}.accepted_endpoints[{index}].time"))
        _states([item[1]], f"{label}.accepted_endpoints[{index}].state")
    if any(right <= left for left, right in zip(times, times[1:])) or times[-1] != end:
        raise ValueError(f"{label} accepted endpoints are not monotone or do not reach the end")
    if record["solver"] == "rk4":
        if times[0] <= start:
            raise ValueError(f"{label} RK4 endpoints must begin after the initial epoch")
        steps = record.get("rk4_steps")
        if isinstance(steps, bool) or not isinstance(steps, int) or steps != len(accepted):
            raise ValueError(f"{label} RK4 accepted-step count mismatch")
    else:
        if times[0] != start:
            raise ValueError(f"{label} DP endpoints must include the initial epoch")
        steps = record.get("accepted_steps")
        if isinstance(steps, bool) or not isinstance(steps, int) or steps + 1 != len(accepted):
            raise ValueError(f"{label} DP accepted-step count mismatch")
        for key in ("accepted_steps", "rejected_steps", "attempted_steps", "function_evaluations"):
            _finite(record.get(key), f"{label}.{key}", nonnegative=True)
        if record["attempted_steps"] != record["accepted_steps"] + record["rejected_steps"]:
            raise ValueError(f"{label} DP attempted-step accounting mismatch")
        if record.get("status") != "finished":
            raise ValueError(f"{label} DP status is not finished")


def _check_solver_record(record: Mapping[str, Any], truth: list[State], au: float, day_s: float, start: float, end: float, tight: list[State] | None, label: str) -> list[State]:
    if record.get("solver") not in ("rk4", "dopri54") or record.get("id", "").split(":")[0] != record["solver"]:
        raise ValueError(f"{label} solver identity mismatch")
    predicted = _states(record.get("requested_states"), f"{label}.requested_states")
    if len(predicted) != len(truth) or predicted[0] != truth[0]:
        raise ValueError(f"{label} requested grid/initial state mismatch")
    endpoint_start, endpoint_end = (0.0, end - start) if record["solver"] == "rk4" else (start, end)
    _check_accepted(record, endpoint_start, endpoint_end, label)
    if record["solver"] == "dopri54" and _states([record["accepted_endpoints"][0][1]], label)[0] != truth[0]:
        raise ValueError(f"{label} accepted initial state mismatch")
    expected = _summary(predicted, truth, au, day_s)
    _close(record, expected, label)
    if tight is not None:
        expected_shift = _shift(predicted, tight, au, day_s)
        _close(record["cross_solver_to_tight"], expected_shift, f"{label}.cross_solver_to_tight")
    return predicted


def check_case(root: Path, case: Mapping[str, Any], config: Mapping[str, Any], data: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    case_id = case["case_id"]
    annual, short = _target_rows(root, data, event, config["annual_horizon_days"])
    rows = short if case_id.endswith("refined36h") else annual
    truth = [state_from_row(row) for row in rows]
    au, day_s = data["constants"]["au_km"], data["constants"]["day_s"]
    start, end = rows[0]["epoch_jd_tdb"], rows[-1]["epoch_jd_tdb"]
    if case.get("samples") != len(rows) or case.get("start_tdb") != rows[0]["epoch_tdb"] or case.get("stop_tdb") != rows[-1]["epoch_tdb"]:
        raise ValueError(f"{case_id} grid metadata mismatch")
    records = case.get("solver_comparison")
    expected_ids = [f"rk4:{scale}" for scale in config["rk4_step_scales"]] + [f"dopri54:{item['label']}" for item in config["dopri54_tolerances"]]
    if not isinstance(records, list) or [record.get("id") for record in records] != expected_ids:
        raise ValueError(f"{case_id} solver record coverage/order mismatch")
    by_id = {record["id"]: record for record in records}
    tight = _states(by_id["dopri54:tight"]["requested_states"], f"{case_id}.tight")
    for record in records:
        _check_solver_record(record, truth, au, day_s, start, end, tight, f"{case_id}.{record['id']}")
    old = _states(case["ephemeris_comparison"]["old_daily_refined"]["requested_states"], f"{case_id}.old")
    hourly = _states(case["ephemeris_comparison"]["development30_hourly"]["requested_states"], f"{case_id}.hourly")
    e = case["ephemeris_comparison"]
    _close(e["old_daily_refined"], _summary(old, truth, au, day_s), f"{case_id}.old_daily_refined")
    _close(e["development30_hourly"], _summary(hourly, truth, au, day_s), f"{case_id}.development30_hourly")
    old_fine = _states(by_id["rk4:0.25"]["requested_states"], f"{case_id}.old_fine")
    hourly_records = e.get("hourly_solver_comparison")
    expected_hourly_ids = ["rk4:0.5", "rk4:0.25", "dopri54:tight", "dopri54:tighter"]
    if [row.get("id") for row in hourly_records] != expected_hourly_ids:
        raise ValueError(f"{case_id} hourly solver coverage mismatch")
    hourly_by_id = {row["id"]: row for row in hourly_records}
    hourly_tight = _states(hourly_by_id["dopri54:tight"]["requested_states"], f"{case_id}.hourly_tight")
    for row in hourly_records:
        _check_solver_record(row, truth, au, day_s, start, end, hourly_tight, f"{case_id}.hourly.{row['id']}")
    if old != _states(by_id["rk4:0.5"]["requested_states"], f"{case_id}.old_rk4") or hourly != _states(hourly_by_id["rk4:0.5"]["requested_states"], f"{case_id}.hourly_rk4"):
        raise ValueError(f"{case_id} ephemeris comparison is not the matching RK4 baseline")
    _close(e["hourly_minus_old"], _shift(hourly, old, au, day_s), f"{case_id}.hourly_minus_old")
    _close(e["old_rk4_step_sensitivity"], _shift(old, old_fine, au, day_s), f"{case_id}.old_rk4_step_sensitivity")
    hourly_fine = _states(hourly_by_id["rk4:0.25"]["requested_states"], f"{case_id}.hourly_fine")
    _close(e["hourly_rk4_step_sensitivity"], _shift(hourly, hourly_fine, au, day_s), f"{case_id}.hourly_rk4_step_sensitivity")
    _close(e["hourly_tight_vs_tighter"], _shift(hourly_tight, _states(hourly_by_id["dopri54:tighter"]["requested_states"], f"{case_id}.hourly_tighter"), au, day_s), f"{case_id}.hourly_tight_vs_tighter")
    if case_id == "apophis_2029_long365d":
        if not math.isclose(e["old_daily_refined"]["max_grid_position_error_km"], 13.14807051432607, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("historical old RK4 baseline is not reproduced")
        if not math.isclose(e["old_rk4_step_sensitivity"]["max_position_shift_km"], 1.9977699372970446, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("historical RK4 step sensitivity is not reproduced")
    return {"case_id": case_id, "samples": len(rows), "solver_records": len(records),
            "requested_state_samples": len(rows) * len(records), "recomputed": True}


def verify(root: Path, output_directory: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    root = Path(root).resolve()
    output_directory = output_directory if output_directory.is_absolute() else root / output_directory
    audit = json.loads((output_directory / DEFAULT_AUDIT).read_text(encoding="utf-8"))
    source_check = check_source_freeze(root, audit)
    config = load_json(root / audit["config"])
    data = load_json(root / config["data_config"])
    event = load_json(root / config["event_config"])
    if audit.get("schema_version") != 1 or len(audit.get("cases", [])) != 2:
        raise ValueError("stage3 audit schema/case coverage mismatch")
    expected_cases = {"apophis_2029_long365d", "apophis_earth_2029_refined36h"}
    if {case.get("case_id") for case in audit["cases"]} != expected_cases:
        raise ValueError("stage3 must contain exactly the annual and short cases")
    for case in audit["cases"]:
        checkpoint = json.loads((output_directory / "checkpoints" / f"{case['case_id']}.json").read_text(encoding="utf-8"))
        if checkpoint.get("fingerprint") != audit["fingerprint"] or checkpoint.get("result") != case:
            raise ValueError(f"checkpoint/result mismatch: {case['case_id']}")
    case_checks = [check_case(root, case, config, data, event) for case in audit["cases"]]
    annual = next(case for case in audit["cases"] if case["case_id"] == "apophis_2029_long365d")
    annual_by_id = {record["id"]: record for record in annual["solver_comparison"]}
    result = {"schema_version": 1, "audit_sha256": sha(output_directory / DEFAULT_AUDIT),
              "verifier_source_sha256": sha(Path(__file__)), "checkpoints_checked": len(expected_cases),
              "source_check": source_check, "cases": case_checks,
              "interpretation": {
                  "dp_tight_tighter_extreme_km": [annual_by_id[f"dopri54:{label}"]["max_grid_position_error_km"] for label in ("tight", "tighter", "extreme")],
                  "numerical_conclusion": "These DP values are not convergence to metres and do not close the numerical task or establish a numerical bound.",
                  "force_conclusion": "Old/hourly differences are ephemeris/interpolation sensitivity; force terms are the same branch, not evidence for a missing force.",
              },
              "passed": True}
    path = output_directory / "verification.json"
    output_directory.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{__import__('os').getpid()}")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(verify(args.root, args.output_directory), indent=2, sort_keys=True))

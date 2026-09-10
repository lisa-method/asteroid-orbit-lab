"""Offline integrity verifier for the direct barycentric EIH follow-up."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any

from orbit_baselines import State
from relative_time_dynamics import RelativeEphemerisInterpolator, relative_rows
from run_eda import load_json, parse_horizons
from run_physics_baselines import state_from_row
import verify_apophis_reference_time as rv
import verify_precise_propagation as pv


ARMS = ("pn_sun", "pn_all", "pn_sun_solar_j2", "pn_all_solar_j2")
LABELS = ("extreme", "ultra", "rk4_fine")
RAW_MANIFEST_COUNT = 15
RAW_PATH_COUNT = 140
SUMMARY_FIELDS = rv.SUMMARY_FIELDS


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _path(root: Path, value: str | Path) -> Path:
    p = Path(value)
    return p if p.is_absolute() else root / p


def _state(value: Any) -> State:
    return rv._state(value)


def _flat(state: State) -> list[float]:
    return [*state.position, *state.velocity]


def _write_immutable(path: Path, value: Any) -> None:
    if path.exists():
        if load_json(path) != value:
            raise RuntimeError(f"immutable artifact differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp-{os.getpid()}")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _finite_state(value: Any, label: str) -> State:
    try:
        result = _state(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}: invalid state") from exc
    if any(not math.isfinite(float(x)) for x in _flat(result)):
        raise ValueError(f"{label}: nonfinite state")
    return result


def _check_freeze(root: Path, matrix: dict[str, Any], freeze_path: Path) -> dict[str, Any]:
    freeze = load_json(freeze_path)
    if matrix.get("fingerprint") != freeze.get("fingerprint") or matrix.get("freeze_sha256") != _sha(freeze_path):
        raise ValueError("matrix/freeze fingerprint mismatch")
    hashes, frozen_runtime = freeze.get("hashes"), freeze.get("runtime")
    if not isinstance(hashes, dict) or not isinstance(frozen_runtime, dict):
        raise ValueError("freeze lacks hashes/runtime")
    for name, digest in hashes.items():
        p = _path(root, str(name))
        if not p.is_file() or _sha(p) != str(digest):
            raise ValueError(f"frozen hash mismatch: {name}")
    runtime = {"executable": sys.executable, "version": sys.version, "platform": platform.platform()}
    if runtime != frozen_runtime:
        raise ValueError("current runtime differs from frozen runtime")
    payload = {"hashes": hashes, "runtime": frozen_runtime}
    if hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest() != freeze.get("fingerprint"):
        raise ValueError("freeze fingerprint recomputation mismatch")
    return freeze


def _check_inputs(root: Path, config: dict[str, Any], freeze: dict[str, Any], previous_config: dict[str, Any], previous_matrix: Path) -> dict[str, Any]:
    path = root / config["output_directory"] / "inputs.json"
    inputs = load_json(path)
    if inputs.get("fingerprint") != freeze["fingerprint"]:
        raise ValueError("barycentric inputs fingerprint differs from freeze")
    if inputs.get("previous_matrix_sha256") != _sha(previous_matrix):
        raise ValueError("barycentric previous matrix provenance differs")
    previous_inputs = root / previous_config["output_directory"] / "inputs.json"
    if inputs.get("previous_inputs_sha256") != _sha(previous_inputs):
        raise ValueError("barycentric previous inputs provenance differs")
    expected = {
        "native_state_basis": "uniform_inertial_q=rb-rSun0-t*vSun0,w=vb-vSun0",
        "reported_state_basis": "heliocentric_ICRF_geometric_AU_AU-per-day",
        "initial_source": "annual_hourly", "target_data_unchanged": True,
        "sun_origin_acceleration_subtraction": False,
        "rk4_step_selection_state_basis": "heliocentric_ICRF_geometric_AU_AU-per-day",
        "pn_velocity_basis": "true_barycentric_v=w+vSun0",
    }
    for key, value in expected.items():
        if inputs.get(key) != value:
            raise ValueError(f"barycentric input metadata mismatch: {key}")
    if inputs.get("ephemeris_inputs") != "unchanged hourly Sun-SSB and Pluto-Sun from preceding PPN audit":
        raise ValueError("barycentric ephemeris provenance mismatch")
    return {"inputs_sha256": _sha(path), "previous_matrix_sha256": _sha(previous_matrix), "previous_inputs_sha256": _sha(previous_inputs)}


def _manifest_inventory(root: Path) -> dict[str, Any]:
    rows: list[tuple[str, str, int, str]] = []
    manifests = sorted((root / "data/checksums").glob("*manifest.json"))
    for manifest_path in manifests:
        doc = load_json(manifest_path)
        entries = doc.get("files") if doc.get("files") is not None else doc.get("downloads")
        if not isinstance(entries, list):
            raise ValueError(f"manifest has no files/downloads list: {manifest_path.name}")
        for item in entries:
            if not isinstance(item, dict) or not all(k in item for k in ("path", "sha256", "bytes")):
                raise ValueError(f"incomplete manifest record: {manifest_path.name}")
            name, digest, size = str(item["path"]), str(item["sha256"]).lower(), int(item["bytes"])
            p = _path(root, name)
            relative = p.relative_to(root).as_posix() if p.is_relative_to(root) else name
            if not p.is_file() or _sha(p) != digest or p.stat().st_size != size:
                raise ValueError(f"manifest raw mismatch: {name}")
            tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "--", relative], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            ignored = subprocess.run(["git", "check-ignore", "--no-index", "--quiet", "--", relative], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            if tracked.returncode == 0 or ignored.returncode != 0:
                raise ValueError(f"raw Git exclusion failed: {relative}")
            rows.append((relative, digest, size, manifest_path.name))
    unique = {r[0] for r in rows}
    if len(manifests) != RAW_MANIFEST_COUNT or len(unique) != RAW_PATH_COUNT:
        raise ValueError(f"raw inventory expected {RAW_MANIFEST_COUNT} manifests/{RAW_PATH_COUNT} paths, got {len(manifests)}/{len(unique)}")
    return {"manifest_count": len(manifests), "manifest_records": len(rows), "unique_raw_paths": len(unique),
            "raw_sha256_size_records": len({(r[1], r[2]) for r in rows})}


def _teachers(root: Path) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, Any]]:
    previous = load_json(root / "outputs/apophis_reference_time/matrix.json")
    annual, refs, data = pv._load_teachers(root, previous)
    if len(annual) != 797 or len(refs) != 8:
        raise ValueError("teacher set is not 797 rows/eight references")
    return annual, refs, data


def _native_sun(root: Path, origin: float, calendar: str) -> RelativeEphemerisInterpolator:
    header, rows = parse_horizons(root / "data/raw/apophis_eih/sun_ssb_hourly.json", "10", "Sun")
    if not header.get("tdb") or header.get("units") != "AU-D" or header.get("reference_frame") != "ICRF":
        raise ValueError("Sun raw input header is not frozen AU-D/TDB/ICRF")
    if not str(header.get("center", "")).lstrip().startswith("Solar System Barycenter (0)"):
        raise ValueError("Sun raw input is not SSB centered")
    if len(rows) != 8809:
        raise ValueError(f"Sun raw row count changed: {len(rows)}")
    rel = relative_rows(rows, origin, calendar, "calendar")
    times = tuple(float(r["epoch_relative_days"]) for r in rel)
    states = tuple(state_from_row(r) for r in rel)
    if times[0] >= 0 or times[-1] <= 365 or any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("Sun ephemeris coverage/order invalid")
    return RelativeEphemerisInterpolator(times, states)


def _convert(sun: RelativeEphemerisInterpolator, t: float, native: State, sun0: State) -> State:
    st = sun.state_at(float(t))
    ds = tuple((st.position[i] - sun0.position[i]) - float(t) * sun0.velocity[i] for i in range(3))
    dvs = tuple(st.velocity[i] - sun0.velocity[i] for i in range(3))
    return State(tuple(native.position[i] - ds[i] for i in range(3)), tuple(native.velocity[i] - dvs[i] for i in range(3)))


def _check_native(row: dict[str, Any], sun: RelativeEphemerisInterpolator, initial: State, times: list[float]) -> dict[str, int]:
    native_initial = _finite_state(row.get("native_initial_state"), f"{row['key']} native initial")
    if native_initial != initial or _convert(sun, 0.0, native_initial, sun.state_at(0.0)) != initial:
        raise ValueError(f"native initial state changed: {row['key']}")
    native_req = row.get("native_solver_requested_states")
    if not isinstance(native_req, list) or len(native_req) != len(times) or len(native_req) != 797:
        raise ValueError(f"native requested length mismatch: {row['key']}")
    converted_req = []
    for t, val in zip(times, native_req, strict=True):
        ns = _finite_state(val, f"{row['key']} native requested")
        converted_req.append(_convert(sun, t, ns, sun.state_at(0.0)))
    if [_flat(s) for s in converted_req] != row.get("requested_states"):
        raise ValueError(f"native requested conversion mismatch: {row['key']}")
    native_end = row.get("native_solver_endpoints")
    output_end = row.get("accepted_endpoints")
    if not isinstance(native_end, list) or not isinstance(output_end, list) or len(native_end) != len(output_end) or not native_end:
        raise ValueError(f"native/output endpoint lengths mismatch: {row['key']}")
    ntimes = []
    for idx, (nitem, oitem) in enumerate(zip(native_end, output_end, strict=True)):
        if not isinstance(nitem, list) or len(nitem) != 2 or not isinstance(oitem, list) or len(oitem) != 2:
            raise ValueError(f"malformed endpoint: {row['key']}")
        nt, ot = float(nitem[0]), float(oitem[0])
        if not math.isfinite(nt) or nt != ot:
            raise ValueError(f"endpoint time alignment mismatch: {row['key']} index {idx}")
        if idx and nt <= ntimes[-1]:
            raise ValueError(f"native endpoints not strictly increasing: {row['key']}")
        ntimes.append(nt)
        converted = _convert(sun, nt, _finite_state(nitem[1], f"{row['key']} native endpoint"), sun.state_at(0.0))
        if _finite_state(oitem[1], f"{row['key']} output endpoint") != converted:
            raise ValueError(f"native endpoint conversion mismatch: {row['key']} index {idx}")
    if ntimes[0] != 0.0 or ntimes[-1] != times[-1]:
        raise ValueError(f"native endpoint bounds mismatch: {row['key']}")
    native_states = [_finite_state(item[1], f"{row['key']} endpoint") for item in native_end]
    endpoint_by_time = dict(zip(ntimes, native_states, strict=True))
    for t, value in zip(times, row["requested_states"], strict=True):
        native_value = _finite_state(row["native_solver_requested_states"][times.index(t)], f"{row['key']} native requested")
        endpoint = endpoint_by_time.get(t)
        if endpoint is None or endpoint != native_value or _convert(sun, t, endpoint, sun.state_at(0.0)) != _state(value):
            raise ValueError(f"requested time is not exact native endpoint: {row['key']} {t}")
    return {"native_requested": len(native_req), "native_accepted": len(native_end)}


def _helper_row(row: dict[str, Any]) -> dict[str, Any]:
    if row.get("solver") != "rk4" or "rk4_steps" in row.get("solver_stats", {}):
        return row
    clone = dict(row)
    clone["solver_stats"] = {**row["solver_stats"], "rk4_steps": row["solver_stats"].get("steps")}
    return clone


def _shift(left: list[State], right: list[State], au: float, day_s: float) -> dict[str, float]:
    return rv._shift(left, right, au, day_s)


def _analysis(records: list[dict[str, Any]], previous: list[dict[str, Any]], au: float, day_s: float, config: dict[str, Any], annual: list[dict[str, Any]], refs: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    by_key = {r["key"]: r for r in records}
    old = {r["key"]: r for r in previous}
    dp, cross, frame, diagnostics = [], [], [], []
    budget = float(config["cross_solver_budget_m"])
    for arm in ARMS:
        e, u, k = (by_key[f"{arm}__{x}"] for x in LABELS)
        es, us, ks = ([ _state(s) for s in r["requested_states"] ] for r in (e, u, k))
        dp_shift = _shift(es, us, au, day_s)
        dp.append({"arm": arm, **dp_shift, "within_budget": dp_shift["max_position_shift_km"] * 1000.0 <= budget,
                   "criterion": "DP extreme vs DP ultra"})
        sh = _shift(us, ks, au, day_s)
        cross.append({"arm": arm, "dp_key": u["key"], "rk4_key": k["key"], **sh,
                      "within_budget": sh["max_position_shift_km"] * 1000.0 <= budget,
                      "criterion": "DP_ultra_vs_compensated_RK4"})
        for label in LABELS:
            current = [_state(s) for s in by_key[f"{arm}__{label}"]["requested_states"]]
            prior = [_state(s) for s in old[f"{arm}__{label}"]["requested_states"]]
            frame.append({"arm": arm, "setting": label, **_shift(current, prior, au, day_s)})
    for row in records:
        errors = row["reference_comparisons"].get("annual_daily")
        pred = {item["epoch_tdb"]: _state(s) for item, s in zip(annual, row["requested_states"], strict=True)}
        day_values = {}
        for day in (0, 30, 60, 90, 98, 100, 101, 102, 103, 104, 105, 110, 120, 180, 365):
            matches = [x for x in refs["annual_daily"] if abs(float(x["epoch_jd_tdb"]) - float(annual[0]["epoch_jd_tdb"]) - day) <= 1e-8]
            if len(matches) != 1 or matches[0]["epoch_tdb"] not in pred:
                raise ValueError(f"missing diagnostic day {day}: {row['key']}")
            p, v = __import__("run_physics_baselines", fromlist=["errors"]).errors(pred[matches[0]["epoch_tdb"]], state_from_row(matches[0]), au, day_s)
            day_values[str(day)] = {"position_error_km": p, "velocity_error_m_s": v}
        diagnostics.append({"key": row["key"], "annual_daily": errors, "errors": day_values})
    return {"schema_version": 1, "dp_setting_differences": dp, "cross_solver_differences": cross,
            "frame_differences_vs_heliocentric": frame, "matched_annual_daily": diagnostics,
            "cross_solver_budget_m": budget, "cross_solver_criterion": "DP ultra vs compensated RK4 fine", "empirical_only": True}


def verify(root: Path) -> dict[str, Any]:
    root = root.resolve()
    config = load_json(root / "configs/apophis_eih_barycentric.json")
    output = root / config["output_directory"]
    matrix_path, freeze_path = output / "matrix.json", output / "freeze.json"
    matrix = load_json(matrix_path)
    freeze = _check_freeze(root, matrix, freeze_path)
    inventory = _manifest_inventory(root)
    annual, refs, data = _teachers(root)
    previous_config = load_json(root / "configs/apophis_eih.json")
    previous_matrix_path = root / config["previous_matrix"]
    previous = load_json(previous_matrix_path)
    previous_records = previous.get("records", [])
    if len(previous_records) != 18:
        raise ValueError("previous EIH matrix is not the frozen 18-record matrix")
    _check_freeze(root, previous, root / previous_config["output_directory"] / "freeze.json")
    input_provenance = _check_inputs(root, config, freeze, previous_config, previous_matrix_path)
    initial = {"annual_hourly": state_from_row(refs["annual_hourly"][0])}
    if not isinstance(matrix.get("records"), list) or len(matrix["records"]) != 12:
        raise ValueError("barycentric matrix must contain 12 records")
    expected = {f"{arm}__{label}" for arm in ARMS for label in LABELS}
    records = matrix["records"]
    if {r.get("key") for r in records} != expected:
        raise ValueError("prescribed barycentric keys differ")
    if any(r.get("fingerprint") != freeze["fingerprint"] for r in records):
        raise ValueError("record fingerprint differs from freeze")
    sun = _native_sun(root, float(annual[0]["epoch_jd_tdb"]), annual[0]["epoch_tdb"])
    for row in records:
        if row.get("key") != f"{row.get('arm')}__{row.get('key', '').rsplit('__', 1)[-1]}":
            raise ValueError(f"noncanonical key: {row.get('key')}")
        if row.get("arm") not in ARMS or row.get("solver") not in {"dopri54", "rk4"} or row.get("initial_source") != "annual_hourly":
            raise ValueError(f"metadata mismatch: {row.get('key')}")
        label = row["key"].rsplit("__", 1)[-1]
        if label not in LABELS or row.get("mode") != "relative_calendar_knots" or row.get("accepted_time_basis") != "relative_days_since_start":
            raise ValueError(f"frame/setting metadata mismatch: {row['key']}")
        arm_spec = next((a for a in previous_config["arms"] if a["id"] == row["arm"]), None)
        setting_spec = next((s for s in previous_config["solver_settings"] if s["label"] == label), None)
        if arm_spec is None or row.get("force_specification") != arm_spec or setting_spec is None or row.get("setting") != setting_spec:
            raise ValueError(f"force/solver setting provenance mismatch: {row['key']}")
        if row.get("native_state_basis") != "uniform_inertial_q=rb-rSun0-t*vSun0,w=vb-vSun0" or row.get("reported_state_basis") != "heliocentric_ICRF_geometric_AU_AU-per-day":
            raise ValueError(f"state basis metadata mismatch: {row['key']}")
        if row.get("sun_origin_acceleration_subtraction") is not False or row.get("rk4_step_selection_state_basis") != "heliocentric_ICRF_geometric_AU_AU-per-day":
            raise ValueError(f"frame/selector metadata mismatch: {row['key']}")
        if label in {"extreme", "ultra"} and row["solver"] != "dopri54":
            raise ValueError(f"DP label/solver mismatch: {row['key']}")
        if label == "rk4_fine" and row["solver"] != "rk4":
            raise ValueError(f"RK4 label/solver mismatch: {row['key']}")
        if not isinstance(row.get("solver_stats"), dict) or row["solver_stats"].get("compensated") is not True:
            raise ValueError(f"compensated solver provenance missing: {row['key']}")
        times = rv._expected_times(row, annual)
        if row.get("requested_times_relative_days") != times:
            raise ValueError(f"calendar time axis mismatch: {row['key']}")
        rv._verify_record(_helper_row(row), annual, refs, initial, float(data["constants"]["au_km"]), float(data["constants"]["day_s"]))
        _check_native(row, sun, initial["annual_hourly"], times)
    checkpoints = sorted((output / "checkpoints").glob("*.json"))
    if {p.stem for p in checkpoints} != expected:
        raise ValueError("checkpoint set differs from prescribed matrix")
    checkpoint_hashes = {}
    by_key = {r["key"]: r for r in records}
    for path in checkpoints:
        if load_json(path) != by_key[path.stem]:
            raise ValueError(f"checkpoint differs from matrix: {path.name}")
        checkpoint_hashes[path.name] = _sha(path)
    au, day_s = float(data["constants"]["au_km"]), float(data["constants"]["day_s"])
    analysis = _analysis(records, previous_records, au, day_s, {**load_json(root / "configs/apophis_eih.json"), **config}, annual, refs)
    provenance = {"matrix_sha256": _sha(matrix_path), "freeze_sha256": _sha(freeze_path), "fingerprint": freeze["fingerprint"],
                  "verifier_sha256": _sha(root / "src/verify_apophis_eih_barycentric.py"),
                  "reference_verifier_sha256": _sha(root / "src/verify_apophis_reference_time.py"),
                  "previous_eih_verifier_sha256": _sha(root / "src/verify_apophis_eih.py"),
                  "propagation_verifier_sha256": _sha(root / "src/verify_precise_propagation.py")}
    provenance["input_provenance"] = input_provenance
    analysis["provenance"] = provenance
    _write_immutable(output / "analysis.json", analysis)
    scalar_count = sum(2 * (len(annual) + sum(int(row["reference_comparisons"][name]["samples"]) for name in refs)) for row in records)
    paired_count = len(analysis["cross_solver_differences"]) * len(annual) * 2
    verification = {"schema_version": 1, "matrix": str(matrix_path.relative_to(root)), **provenance,
                    "config_sha256": _sha(root / "configs/apophis_eih_barycentric.json"),
                    "inputs_sha256": _sha(output / "inputs.json"), "run_count": len(records), "checkpoint_count": len(checkpoints),
                    "checkpoint_sha256": checkpoint_hashes, "manifest": inventory, "input_provenance": input_provenance, "primary_rows": len(annual),
                    "reference_tables": len(refs), "accepted_endpoint_count": sum(len(r["accepted_endpoints"]) for r in records),
                    "native_accepted_endpoint_count": sum(len(r["native_solver_endpoints"]) for r in records),
                    "scalar_error_count": scalar_count, "paired_scalar_count": paired_count,
                    "cross_solver_budget_m": config["cross_solver_budget_m"],
                    "cross_solver_within_budget": all(x["within_budget"] for x in analysis["cross_solver_differences"])}
    _write_immutable(output / "verification.json", verification)
    return verification


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(verify(args.root), indent=2, ensure_ascii=False))

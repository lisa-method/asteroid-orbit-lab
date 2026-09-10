"""Bounded relative-time force regression on the frozen development30 sample."""

from __future__ import annotations

import argparse
import ast
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

from ng_inputs_v2 import load_ng_input
from orbit_baselines import State, norm, subtract
from planetary_dynamics import Perturber
from prepare_fresh_holdout import check_hashes
from relative_force_model import build_relative_force_model
from relative_time_dynamics import relative_perturbers
from run_eda import load_json, parse_horizons
from run_physics_baselines import errors, state_from_row
from run_development_benchmark import load_development_context, load_object_rows, _find_initial
from run_apophis_shape_weak_force import context as shape_context, make_force as shape_make_force


CONFIG = "configs/relative_force_regression.json"
EXPECTED_IDS = ("35396", "170086", "675603", "875508", "1480", "120")
EXPECTED_SAMPLE_IDS = (
    "35396", "292220", "137108", "893859", "153814", "170086", "317643", "308635", "666241", "613569",
    "675603", "206887", "498441", "286080", "406221", "875508", "848993", "880024", "771309", "145485",
    "1480", "2784", "2941", "2402", "2112", "120", "3346", "1851", "983", "165",
)
REFERENCE_NAMES = {"annual_daily", "annual_hourly", "short_daily", "short_hourly", "old_daily", "old_refined", "recent_refined", "long_repeat"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _state(value: Any) -> State:
    if not isinstance(value, list) or len(value) != 6 or any(not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(float(x)) for x in value):
        raise ValueError("invalid finite six-state")
    return State(tuple(float(x) for x in value[:3]), tuple(float(x) for x in value[3:]))  # type: ignore[arg-type]


def _flat(state: State) -> list[float]:
    return [*state.position, *state.velocity]


def _summary(predictions: list[State], truth: list[State], au: float, day_s: float) -> dict[str, float]:
    if len(predictions) != len(truth) or not predictions:
        raise ValueError("summary traces have incompatible lengths")
    p = [errors(a, b, au, day_s)[0] for a, b in zip(predictions, truth)]
    v = [errors(a, b, au, day_s)[1] for a, b in zip(predictions, truth)]
    return {"max_position_error_km": max(p), "final_position_error_km": p[-1], "max_velocity_error_m_s": max(v), "final_velocity_error_m_s": v[-1], "samples": len(predictions)}


def _shift(left: list[State], right: list[State], au: float, day_s: float) -> dict[str, float]:
    if len(left) != len(right) or not left:
        raise ValueError("paired traces have incompatible lengths")
    p = [norm(subtract(a.position, b.position)) * au for a, b in zip(left, right)]
    v = [norm(subtract(a.velocity, b.velocity)) * au * 1000.0 / day_s for a, b in zip(left, right)]
    return {"max_position_shift_km": max(p), "final_position_shift_km": p[-1], "max_velocity_shift_m_s": max(v), "final_velocity_shift_m_s": v[-1]}


def _atomic_json(path: Path, value: Any) -> None:
    if path.exists():
        if load_json(path) != value:
            raise RuntimeError(f"immutable file differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _source_closure(root: Path, names: list[str]) -> set[str]:
    queue, seen = list(names), set()
    while queue:
        name = queue.pop()
        path = root / "src" / f"{name}.py"
        if not path.is_file() or path.as_posix() in seen:
            continue
        seen.add(path.as_posix())
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                queue.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                queue.append(node.module.split(".")[0])
    return seen


def _manifest_records(root: Path) -> list[dict[str, Any]]:
    output = []
    manifest_paths = sorted((root / "data/checksums").glob("*manifest.json"))
    if len(manifest_paths) != 13:
        raise ValueError(f"expected 13 checksum manifests, found {len(manifest_paths)}")
    for manifest_path in manifest_paths:
        document = load_json(manifest_path)
        entries = document.get("files")
        if entries is None:
            entries = document.get("downloads")
        if not isinstance(entries, list):
            raise ValueError(f"manifest lacks files/downloads: {manifest_path.name}")
        for record in entries:
            if not isinstance(record, dict) or not all(key in record for key in ("path", "sha256", "bytes")):
                raise ValueError(f"incomplete manifest record: {manifest_path.name}")
            raw = Path(str(record["path"]))
            if not raw.is_absolute():
                raw = root / raw
            if not raw.is_file() or _sha(raw) != str(record["sha256"]).lower() or raw.stat().st_size != int(record["bytes"]):
                raise ValueError(f"manifest raw mismatch: {record['path']}")
            relative = raw.relative_to(root).as_posix() if raw.is_relative_to(root) else str(record["path"])
            if subprocess.run(["git", "ls-files", "--error-unmatch", "--", relative], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0:
                raise ValueError(f"manifest raw is tracked: {relative}")
            if subprocess.run(["git", "check-ignore", "--no-index", "--quiet", "--", relative], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode != 0:
                raise ValueError(f"manifest raw is not Git-ignored: {relative}")
            output.append({"path": relative, "sha256": str(record["sha256"]).lower(), "bytes": int(record["bytes"])})
    if len({item["path"] for item in output}) != 137:
        raise ValueError("expected 137 unique raw manifest paths")
    return output


def _trim_rows(rows: list[dict[str, Any]], start_jd: float, end_jd: float) -> list[dict[str, Any]]:
    epochs = [float(row["epoch_jd_tdb"]) for row in rows]
    if len(rows) < 2 or epochs[0] > start_jd or epochs[-1] < end_jd:
        raise ValueError("ephemeris does not cover forecast window")
    import bisect
    left = max(0, bisect.bisect_right(epochs, start_jd) - 1)
    right = min(len(rows), bisect.bisect_left(epochs, end_jd) + 2)
    result = rows[left:right]
    if len(result) < 2 or result[0]["epoch_jd_tdb"] > start_jd or result[-1]["epoch_jd_tdb"] < end_jd:
        raise ValueError("trimmed ephemeris lacks surrounding knots")
    return result


def _relative_entries(root: Path, context: dict[str, Any], start_jd: float, start_calendar: str, end_jd: float) -> tuple[Perturber, ...]:
    key = (start_jd, start_calendar, end_jd)
    if context.get("_relative_window_key") == key:
        return context["_relative_window"]
    raw = root / "data/raw/development30"
    entries = []
    for body in (*context["planets"], *context["small"]):
        if str(body.body_id).startswith("sb:"):
            identifier = str(body.body_id)[3:]
            path = root / context["force_config"]["small_body_ephemeris_directory"] / f"asteroid_{identifier}.json"
            rows = parse_horizons(path, identifier, body.name)[1]
        else:
            path = raw / "planets" / f"body_{body.body_id}.json"
            rows = parse_horizons(path, body.body_id, body.name)[1]
        entries.append((body, _trim_rows(rows, start_jd, end_jd)))
    result = relative_perturbers(entries, start_jd, start_calendar, "calendar")
    context["_relative_window_key"], context["_relative_window"] = key, result
    return result


def _force_check(root: Path) -> dict[str, float]:
    ctx = shape_context(root, freeze=False)
    zonal = {**ctx["config"]["earth_zonals"], "au_km": ctx["au"], "day_s": ctx["day"]}
    earth_j2 = {**ctx["base"]["earth_j2"], "reference_radius_au": ctx["base"]["earth_j2"]["reference_radius_km"] / ctx["au"]}
    ng = load_ng_input(root / "data/raw/horizons/asteroid_99942.json")
    if ng is None:
        raise ValueError("Apophis NG gate input missing")
    results = {}
    force_config = load_json(root / ctx["base"]["force_config"])
    speed = force_config["speed_of_light_km_s"] * ctx["day"] / ctx["au"]
    prior_shape = load_json(root / "outputs/apophis_shape_weak_force/matrix.json")
    for arm, flags in (("baseline", (False, False)), ("j3j4", (True, True))):
        generic, _ = build_relative_force_model(ctx["origin"], ctx["planets"], ctx["mu"], speed, ng, earth_j2, zonal, enabled_j3=flags[0], enabled_j4=flags[1], ng_policy="available")
        frozen = shape_make_force(ctx, arm)
        previous = next(row for row in prior_shape["records"] if row["key"] == f"{arm}__old__extreme")
        maximum = 0.0
        for time, values in zip(ctx["times"], previous["requested_states"]):
            position = tuple(values[:3])
            velocity = tuple(values[3:])
            a, b = generic(time, position, velocity), frozen(time, position, velocity)
            if tuple(a) != tuple(b):
                raise ValueError(f"generic force arithmetic differs from frozen Apophis force ({arm}, t={time})")
            maximum = max(maximum, norm(subtract(a, b)))
        results[arm] = maximum
    if any(value > 1.0e-25 for value in results.values()):
        raise ValueError(f"generic force differs from frozen Apophis force: {results}")
    return results


def _freeze(root: Path, config: dict[str, Any], base: dict[str, Any], zonal: dict[str, Any], sample: dict[str, Any], raw_records: list[dict[str, Any]], force_check: dict[str, float]) -> tuple[dict[str, Any], Path]:
    names = ["run_relative_force_regression", "relative_force_model", "relative_time_dynamics", "extended_body_forces", "precise_rk4", "forecast_precise", "run_development_benchmark", "run_b3plus_ablation", "run_eda", "planetary_dynamics", "orbit_baselines", "ng_inputs_v2", "earth_oblateness", "prepare_fresh_holdout"]
    source_paths = _source_closure(root, names)
    forecaster_path = root / "src/forecast_precise.py"
    if not forecaster_path.is_file():
        raise RuntimeError("forecast_precise.py is required before freeze")
    required = [CONFIG, config["contract"], config["base_config"], config["solver_config"], config["zonal_config"], base["data_config"], base["force_config"], config["sample_path"], "outputs/apophis_shape_weak_force/freeze.json", "outputs/apophis_shape_weak_force/matrix.json"]
    required += [f"data/checksums/{path.name}" for path in sorted((root / "data/checksums").glob("*manifest.json"))]
    required += [item["path"] for item in raw_records]
    required += [str(path.relative_to(root)) for path in (root / "src").glob("*.py") if path.as_posix() in source_paths]
    prior = load_json(root / "outputs/apophis_shape_weak_force/freeze.json")
    check_hashes(root, prior["hashes"])
    hashes = {**prior["hashes"], **{relative: _sha(root / relative) for relative in sorted(set(required))}}
    runtime = {"executable": sys.executable, "version": sys.version, "platform": platform.platform()}
    payload = {"hashes": hashes, "runtime": runtime}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    freeze_path = root / config["output_directory"] / "freeze.json"
    freeze = {**payload, "fingerprint": fingerprint, "force_check": force_check}
    if freeze_path.exists():
        old = load_json(freeze_path)
        if old.get("fingerprint") != fingerprint or old.get("hashes") != hashes or old.get("runtime") != runtime:
            raise ValueError("relative regression freeze changed")
    else:
        _atomic_json(freeze_path, {**freeze, "created_utc": dt.datetime.now(dt.timezone.utc).isoformat()})
    return freeze, freeze_path


def _call_forecaster(initial: State, origin_jd: float, planets: tuple[Perturber, ...], constants: dict[str, float], numerical_config: dict[str, Any], ng: Any, times: list[float], arm: str, solver: str) -> tuple[list[State], dict[str, Any]]:
    # This is the sole integration boundary.  The generic API receives only
    # forecast-time inputs; reference rows and event metadata stay outside it.
    from forecast_precise import forecast_precise
    result = forecast_precise(
        initial=initial,
        origin_jd_tdb=origin_jd,
        horizon_days=times[-1],
        planets=planets,
        constants=constants,
        config=numerical_config,
        ng=ng,
        solver=solver,
        enabled_j3=arm == "j3j4",
        enabled_j4=arm == "j3j4",
    )
    if not isinstance(result, tuple) or len(result) != 2:
        raise ValueError("forecast_precise must return (predictions, metadata)")
    predictions, metadata = result
    if not isinstance(predictions, list) or not isinstance(metadata, dict):
        raise ValueError("forecast_precise returned invalid result")
    normalized = [_state(_flat(state) if isinstance(state, State) else state) for state in predictions]
    if list(metadata.get("daily_times_days", ())) != times:
        raise ValueError("forecast API returned a different daily grid")
    return normalized, metadata


def _run_one(root: Path, ctx: dict[str, Any], obj: dict[str, Any], arm: str, solver: str, setting: Any, source_freeze: dict[str, Any], resume: bool) -> dict[str, Any]:
    identifier, name, start_date = str(obj["id"]), str(obj["name"]), str(obj["start_date"])
    key = f"{identifier}__{arm}__{solver}__{setting['label'] if isinstance(setting, dict) else setting}"
    checkpoint_path = ctx["output"] / "checkpoints" / f"{key}.json"
    if checkpoint_path.exists():
        if not resume:
            raise RuntimeError(f"completed checkpoint exists; use --resume: {checkpoint_path}")
        saved = load_json(checkpoint_path)
        if saved.get("fingerprint") != source_freeze["fingerprint"]:
            raise RuntimeError(f"checkpoint freeze mismatch: {key}")
        return saved
    daily = parse_horizons(root / ctx["base"]["raw_directory"] / "asteroids" /
        f"asteroid_{identifier}_daily.json", identifier, name)[1]
    start_jd, initial = _find_initial(daily, start_date)
    start_calendar = next(row["epoch_tdb"] for row in daily if row["epoch_jd_tdb"] == start_jd)
    end_jd = start_jd + float(ctx["config"]["horizon_days"])
    reference_rows = [row for row in daily if start_jd <= float(row["epoch_jd_tdb"]) <= end_jd]
    if len(reference_rows) != 366 or reference_rows[0]["epoch_tdb"] != start_calendar:
        raise ValueError(f"daily reference coverage is not 366 rows: {identifier}")
    times = [float(i) for i in range(366)]
    if any(abs(float(row["epoch_jd_tdb"]) - start_jd - t) > 1.0e-8 for row, t in zip(reference_rows, times)):
        raise ValueError(f"daily reference is not a uniform forecast grid: {identifier}")
    planets = _relative_entries(root, ctx["context"], start_jd, start_calendar, end_jd)
    raw_ng = root / ctx["base"]["raw_directory"] / "asteroids" / f"asteroid_{identifier}_daily.json"
    ng = load_ng_input(raw_ng)
    if ng is None:
        raise ValueError(f"NG adapter returned None: {identifier}")
    solver_name = "compensated_rk4" if solver == "rk4" else "dopri54"
    setting_config = dict(setting) if isinstance(setting, dict) else {}
    if solver == "dopri54":
        setting_config["max_step"] = float(ctx["solver_config"]["dopri54_max_step_days"])
    if solver == "rk4":
        setting_config = {"default_step_days": float(ctx["base"]["default_step_days"]),
            "step_scale": float(setting), "max_steps": 1_000_000}
    numerical_config = {
        "earth_j2": ctx["earth_j2"],
        "earth_zonals": ctx["zonal"],
        solver_name: setting_config,
        "ng_policy": "available",
    }
    start_runtime = __import__("time").perf_counter()
    predictions, metadata = _call_forecaster(
        initial, start_jd, planets,
        {"mu_sun_au3_d2": ctx["context"]["mu"], "speed_of_light_au_d": ctx["context"]["c_au_d"]},
        numerical_config, ng, times, arm, solver_name,
    )
    elapsed = __import__("time").perf_counter() - start_runtime
    metadata["runtime_seconds"] = elapsed
    trace = metadata.get("native_trace", metadata.get("accepted_endpoints"))
    if not isinstance(trace, list):
        raise ValueError(f"forecaster omitted native accepted trace: {key}")
    if len(predictions) != 366 or predictions[0] != initial or any(not math.isfinite(x) for state in predictions for x in _flat(state)):
        raise ValueError(f"forecast state validation failed: {key}")
    if len(trace) < 2 or any(not isinstance(item, list) or len(item) != 2 or not isinstance(item[1], list) or len(item[1]) != 6 or any(not math.isfinite(float(x)) for x in item[1]) for item in trace) or any(trace[i + 1][0] <= trace[i][0] for i in range(len(trace) - 1)) or trace[0][0] != 0.0 or trace[-1][0] != 365.0:
        raise ValueError(f"native trace validation failed: {key}")
    truth = [state_from_row(row) for row in reference_rows]
    reference = _summary(predictions, truth, ctx["context"]["au_km"], ctx["context"]["day_s"])
    record = {"schema_version": 1, "key": key, "object_id": identifier, "object_name": name, "split": obj.get("split"), "stratum": obj.get("stratum"), "start_date": start_date, "horizon_days": 365, "arm": arm, "solver": solver_name, "setting": setting_config, "initial_state": _flat(initial), "requested_times_relative_days": times, "requested_states": [_flat(state) for state in predictions], "accepted_time_basis": "relative_days_since_start", "accepted_endpoints": trace, "primary_daily": reference, "reference_comparisons": {"daily": reference}, "runtime_seconds": elapsed, "solver_stats": metadata.get("solver_stats", {}), "numerical_settings": metadata.get("numerical_settings", setting_config), "frame_contract": metadata.get("frame_contract", {}), "force_metadata": metadata.get("force_metadata", {}), "ng_status": ng.status(start_jd), "ng_source": ng.source, "ng_source_sha256": ng.source_sha256, "phase_info": {"forecast_origin": start_calendar, "target_inputs": "initial_state + relative ephemerides + exogenous constants + NG input", "future_event_inputs": False, "reference_role": "daily teacher evaluation only"}, "fingerprint": source_freeze["fingerprint"]}
    _atomic_json(checkpoint_path, record)
    print(json.dumps({"completed": key, "matched_max_position_error_km": reference["max_position_error_km"]}, sort_keys=True), flush=True)
    return record


def run(root: Path, *, resume: bool = False, freeze_only: bool = False) -> dict[str, Any]:
    root = root.resolve()
    config = load_json(root / CONFIG)
    base = load_json(root / config["base_config"])
    solver_config = load_json(root / config["solver_config"])
    zonal = load_json(root / config["zonal_config"])["earth_zonals"]
    sample = load_json(root / config["sample_path"])
    objects = sample.get("objects")
    if not isinstance(objects, list) or [str(obj["id"]) for obj in objects] != list(EXPECTED_SAMPLE_IDS):
        raise ValueError("sample order/IDs differ from frozen development30 sample")
    if [str(x) for x in config["rk4_object_ids"]] != list(EXPECTED_IDS):
        raise ValueError("RK4 control IDs differ from the frozen contract")
    raw_records = _manifest_records(root)
    force_check = _force_check(root)
    development = load_development_context(root, base)
    source_freeze, freeze_path = _freeze(root, config, base, zonal, sample, raw_records, force_check)
    if freeze_only:
        return {"fingerprint": source_freeze["fingerprint"], "freeze": str(freeze_path.relative_to(root))}
    output = root / config["output_directory"]
    au, day = development["au_km"], development["day_s"]
    earth_j2 = {**base["earth_j2"], "reference_radius_au": float(base["earth_j2"]["reference_radius_km"]) / au}
    zonal = {**zonal, "au_km": au, "day_s": day}
    context = {"config": config, "base": base, "solver_config": solver_config, "context": development, "output": output, "earth_j2": earth_j2, "zonal": zonal}
    tolerances = {item["label"]: item for item in solver_config["dopri54_tolerances"]}
    records = []
    for obj in objects:
        for arm in config["arms"]:
            for label in config["dp_labels"]:
                records.append(_run_one(root, context, obj, arm, "dopri54", tolerances[label], source_freeze, resume))
    for obj in objects:
        if str(obj["id"]) not in config["rk4_object_ids"]:
            continue
        for arm in config["arms"]:
            records.append(_run_one(root, context, obj, arm, "rk4", float(config["rk4_step_scale"]), source_freeze, resume))
    compact = []
    for record in records:
        checkpoint = output / "checkpoints" / f"{record['key']}.json"
        compact.append({key: record[key] for key in ("key", "object_id", "object_name", "split", "stratum", "start_date", "horizon_days", "arm", "solver", "setting", "primary_daily", "reference_comparisons", "runtime_seconds", "solver_stats", "force_metadata", "ng_status", "ng_source", "ng_source_sha256", "phase_info", "fingerprint")} | {"checkpoint": str(checkpoint.relative_to(root)), "checkpoint_sha256": _sha(checkpoint), "requested_count": len(record["requested_states"]), "accepted_count": len(record["accepted_endpoints"])})
    paired = []
    for obj in objects:
        for solver in ("dopri54", "rk4"):
            for arm in ("baseline", "j3j4"):
                solver_key = "compensated_rk4" if solver == "rk4" else solver
                left = [r for r in records if r["object_id"] == str(obj["id"]) and r["solver"] == solver_key and r["arm"] == arm]
                if solver == "rk4" and not left:
                    continue
                if solver == "dopri54":
                    for label in config["dp_labels"]:
                        a = next(r for r in left if r["setting"]["label"] == label)
                        b = next(r for r in records if r["key"] == f"{obj['id']}__j3j4__dopri54__{label}") if arm == "baseline" else None
                        if b is not None:
                            paired.append({"left": a["key"], "right": b["key"], **_shift([_state(s) for s in a["requested_states"]], [_state(s) for s in b["requested_states"]], development["au_km"], development["day_s"])})
                else:
                    if arm == "baseline":
                        a = next(r for r in left)
                        b = next(r for r in records if r["object_id"] == str(obj["id"]) and r["solver"] == solver_key and r["arm"] == "j3j4")
                        paired.append({"left": a["key"], "right": b["key"], **_shift([_state(s) for s in a["requested_states"]], [_state(s) for s in b["requested_states"]], development["au_km"], development["day_s"])})
    result = {"schema_version": 1, "fingerprint": source_freeze["fingerprint"], "freeze_sha256": _sha(freeze_path), "records": compact, "paired_shifts": paired, "scope": "post-hoc relative-force regression; no selector retraining or new validation"}
    _atomic_json(output / "matrix.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--freeze-only", action="store_true")
    args = parser.parse_args()
    result = run(args.root, resume=args.resume, freeze_only=args.freeze_only)
    print(json.dumps({"fingerprint": result["fingerprint"],
        "records": len(result.get("records", [])), "freeze_only": args.freeze_only}, sort_keys=True))

"""Moon/Venus v1.1: native solver trace times; unchanged force/integration design."""

from __future__ import annotations

import argparse
import datetime as dt
from bisect import bisect_right
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import platform
import sys
import time
from typing import Any, Iterable

from independent_rk import integrate_dopri54
from orbit_baselines import State, norm, propagate_variable_step, subtract
from planetary_dynamics import EphemerisInterpolator, Perturber
from run_apophis_solver_audit import (
    _force, _merge_rows, _old_inputs, _old_planets, _run_dp, _run_rk4,
    _shift, _summary, _target_rows, _flat, _state,
)
from run_b3plus_ablation import load_small_body_perturbers
from run_eda import load_json, parse_horizons
from run_nbody_baseline import load_perturbers
from run_physics_baselines import errors, load_asteroid_series, state_from_row
from ng_inputs_v2 import load_ng_input


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != value:
            raise RuntimeError(f"Immutable artifact differs; refusing overwrite: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _body_rows(root: Path, directory: str, body: dict) -> list[dict]:
    filename = f"asteroid_{body['id']}.json" if body["id"] == "99942" else f"body_{body['id']}.json"
    return parse_horizons(root / directory / filename, body["id"], body["name"])[1]


def _new_rows(root: Path, config: dict) -> dict[str, list[dict]]:
    return {item["id"]: _body_rows(root, config["raw_directory"], item) for item in config["download_windows"]}


def _replace(planets: tuple[Perturber, ...], body_id: str, rows: list[dict]) -> tuple[Perturber, ...]:
    return tuple(Perturber(p.body_id, p.name, p.mu_au3_d2, EphemerisInterpolator.from_rows(rows) if p.body_id == body_id else p.ephemeris) for p in planets)


def _decimate(rows: list[dict], stride: int) -> list[dict]:
    selected = rows[::stride]
    if not selected or selected[-1]["epoch_jd_tdb"] != rows[-1]["epoch_jd_tdb"]:
        raise ValueError("15-minute Venus decimation does not include its endpoint")
    return selected


def _state6(values: Iterable[float]) -> list[float]:
    values = list(values)
    if len(values) != 6 or not all(math.isfinite(float(v)) for v in values):
        raise ValueError("non-finite six-state trace")
    return [float(v) for v in values]


def _hermite(accepted: list[list[Any]], targets: list[float]) -> list[State]:
    if not accepted:
        raise ValueError("accepted trace is empty")
    times = [float(row[0]) for row in accepted]
    if not all(math.isfinite(value) for value in times):
        raise ValueError("accepted trace contains non-finite time")
    states = [tuple(float(v) for v in row[1]) for row in accepted]
    if any(len(s) != 6 or not all(math.isfinite(v) for v in s) for s in states):
        raise ValueError("invalid accepted trace state")
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("accepted trace times must increase")
    result: list[State] = []
    for target in targets:
        if not math.isfinite(float(target)):
            raise ValueError("dense target contains non-finite time")
        if target < times[0] - 1e-12 or target > times[-1] + 1e-12:
            raise ValueError("dense target lies outside accepted trace")
        index = bisect_right(times, target) - 1
        if index < 0:
            index = 0
        if index >= len(times) - 1:
            result.append(State(states[-1][:3], states[-1][3:]))
            continue
        left, right = times[index], times[index + 1]
        if target == left:
            result.append(State(states[index][:3], states[index][3:]))
            continue
        h = right - left
        u = (target - left) / h
        u2, u3 = u * u, u * u * u
        h00, h10 = 2*u3 - 3*u2 + 1, u3 - 2*u2 + u
        h01, h11 = -2*u3 + 3*u2, u3 - u2
        y0, y1 = states[index], states[index + 1]
        value = tuple(h00*y0[k] + h10*h*y0[k+3] + h01*y1[k] + h11*h*y1[k+3] for k in range(3))
        velocity = tuple((6*u2 - 6*u)*y0[k] / h + (3*u2 - 4*u + 1)*y0[k+3] + (-6*u2 + 6*u)*y1[k] / h + (3*u2 - 2*u)*y1[k+3] for k in range(3))
        result.append(State(value, velocity))
    return result


def _normalize_trace(meta: dict, start: float, initial: State, solver: str) -> list[list[Any]]:
    trace = [[float(row[0]), _state6(row[1])] for row in meta.get("accepted_endpoints", [])]
    origin = 0.0 if solver == "rk4" else start
    initial_flat = _state6(_flat(initial))
    if not trace or trace[0][0] != origin:
        trace.insert(0, [origin, initial_flat])
    elif trace[0][1] != initial_flat:
        raise ValueError("accepted trace initial state differs from requested initial state")
    if any(b[0] <= a[0] for a, b in zip(trace, trace[1:])):
        raise ValueError("normalized accepted trace has duplicate/non-increasing times")
    return trace


def _dense_reference(rows: dict[str, list[dict]], constants: dict) -> tuple[list[dict], list[State], list[State], list[State]]:
    target = rows["99942"]
    if len(target) != 2305:
        raise ValueError("new Apophis dense grid must contain 2305 rows")
    return target, [state_from_row(r) for r in target], [state_from_row(r) for r in rows["399"]], [state_from_row(r) for r in rows["301"]]


def _validate_overlap(old_refined: dict[str, list[dict]], new: dict[str, list[dict]], pos_tol_km: float, vel_tol_ms: float, au_km: float, day_s: float) -> None:
    for body_id in ("399", "301", "99942"):
        old_by = {row["epoch_jd_tdb"]: row for row in old_refined[body_id]}
        common = 0
        for row in new[body_id]:
            old = old_by.get(row["epoch_jd_tdb"])
            if old is None:
                continue
            common += 1
            position = norm(subtract(row["r"], old["r"])) * au_km
            velocity = norm(subtract(row["v"], old["v"])) * au_km * 1000.0 / day_s
            if position > pos_tol_km or velocity > vel_tol_ms:
                raise ValueError(f"old/new overlap mismatch for {body_id}: {position} km, {velocity} m/s")
        if common == 0:
            raise ValueError(f"no old/new overlap for {body_id}")


def _validate_manifest(root: Path, config: dict) -> list[Path]:
    manifest_path = root / config["manifest"]
    manifest = load_json(manifest_path)
    if manifest.get("complete") is not True:
        raise ValueError("Moon/Venus manifest is not complete")
    records = manifest.get("files")
    if not isinstance(records, list):
        raise ValueError("manifest files must be a list")
    expected = {f"{config['raw_directory']}/{'asteroid' if item['id'] == '99942' else 'body'}_{item['id']}.json": item for item in config["download_windows"]}
    found: list[Path] = []
    for relative, window in expected.items():
        match = next((r for r in records if isinstance(r, dict) and r.get("path") == relative), None)
        path = root / relative
        if match is None or not path.is_file():
            raise ValueError(f"manifest/raw file missing: {relative}")
        if str(match.get("sha256", "")).lower() != _sha(path) or match.get("bytes") != path.stat().st_size:
            raise ValueError(f"manifest hash/size mismatch: {relative}")
        for field in ("source_url", "retrieved_at_utc", "signature", "rows", "coordinates", "time_scale"):
            if field not in match:
                raise ValueError(f"manifest field missing: {relative}:{field}")
        if int(match["rows"]) != int(window["rows"]):
            raise ValueError(f"manifest row count mismatch: {relative}")
        found.append(path)
    return found


def _module_paths(root: Path) -> list[Path]:
    # Traverse imports of the experiment entrypoints, not the global sys.modules
    # inventory: importing this runner in a verifier must not alter its freeze.
    import ast
    queue = ["run_apophis_moon_venus_v1_1", "run_apophis_moon_venus", "prepare_apophis_moon_venus", "finalize_apophis_moon_venus_inputs"]
    visited = set()
    while queue:
        name = queue.pop()
        path = root / "src" / (name + ".py")
        if path in visited or not path.exists():
            continue
        visited.add(path)
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                queue.append(node.module.split(".")[0])
            elif isinstance(node, ast.Import):
                queue.extend(alias.name.split(".")[0] for alias in node.names)
    return sorted(visited)


def _provenance(root: Path, config_path: Path, config: dict, data: dict, force: dict, manifest_paths: list[Path]) -> tuple[dict[str, str], str]:
    paths = [config_path, root / config["baseline_config"], root / config["contract"], root / config["manifest"], root / "docs/APOPHIS_MOON_VENUS_INPUT_AMENDMENT.md", root / "src/finalize_apophis_moon_venus_inputs.py", root / "data/processed/apophis_moon_venus/download_design_freeze.json", root / "configs/eda_pilot_6.json", root / "configs/b3plus_pilot_6.json", root / "configs/eda_apophis_2029_refinement.json", root / "data/checksums/jpl_pilot_6_manifest.json", root / "data/checksums/b3plus_small_perturbers_manifest.json", root / "data/processed/apophis_moon_venus/input_validation.json", root / "outputs/apophis_solver_audit/stage3/apophis_solver_audit.json"]
    paths += [root / "docs/APOPHIS_MOON_VENUS_TRACE_AMENDMENT.md", root / "outputs/apophis_moon_venus/freeze.json"]
    paths += manifest_paths
    paths += [root / "data/raw/horizons/asteroid_99942.json"]
    paths += [root / "data/raw/horizons" / f"asteroid_{body['id']}.json" for body in data["asteroids"]]
    paths += [root / "data/raw/horizons" / f"body_{body['id']}.json" for body in data["perturbers"]]
    paths += [root / force["small_body_ephemeris_directory"] / f"asteroid_{body['id']}.json" for body in force["small_body_perturbers"]]
    paths += [root / "data/raw/horizons_refined" / f"apophis_earth_2029_{body_id}.json" for body_id in ("399", "301", "99942")]
    paths += [root / "data/raw/development30/planets/body_299.json"]
    paths += [root / "data/checksums/apophis_earth_2029_manifest.json", root / "data/checksums/development30_data_manifest.json"]
    # The NG adapter reads checksum manifests to establish timestamp provenance.
    paths += sorted((root / "data/checksums").glob("*manifest.json"))
    paths += _module_paths(root)
    hashes = {path.relative_to(root).as_posix(): _sha(path) for path in sorted(set(paths))}
    runtime = {"python_executable": sys.executable, "python_version": sys.version, "platform": platform.platform()}
    fingerprint = hashlib.sha256(json.dumps({"hashes": hashes, "runtime": runtime}, sort_keys=True).encode()).hexdigest()
    return {**hashes, "__runtime__": hashlib.sha256(json.dumps(runtime, sort_keys=True).encode()).hexdigest()}, fingerprint


def _arm_plan(config: dict) -> list[dict]:
    return list(config["arms"])


def _make_arm_planets(arm: dict, old: tuple[Perturber, ...], new: dict[str, list[dict]], daily: dict[str, list[dict]], constants: dict) -> tuple[Perturber, ...]:
    selected = old
    if arm["earth"] == "5m":
        selected = _replace(selected, "399", _merge_rows(daily["399"], new["399"]))
    if arm["moon"] == "5m":
        selected = _replace(selected, "301", _merge_rows(daily["301"], new["301"]))
    if arm["venus"] == "15m":
        selected = _replace(selected, "299", new["299"])
    elif arm["venus"] == "hourly":
        selected = _replace(selected, "299", _decimate(new["299"], 4))
    remove = arm.get("remove_force")
    if remove:
        selected = tuple(p for p in selected if p.body_id != remove)
    return selected


def _run_variant(case: str, arm: dict, solver: str, scale_or_tol: Any, target_rows: list[dict], dense_rows: list[dict], planets: tuple[Perturber, ...], ng: Any, config: dict, constants: dict, out: Path, fingerprint: str, baseline: dict[str, float] | None) -> dict:
    if out.exists():
        saved = json.loads(out.read_text(encoding="utf-8"))
        if saved.get("fingerprint") != fingerprint:
            raise RuntimeError(f"Completed checkpoint fingerprint mismatch; refusing overwrite: {out}")
        return saved["result"]
    au_km, day_s = constants["au_km"], constants["day_s"]
    start = target_rows[0]["epoch_jd_tdb"]
    initial = state_from_row(target_rows[0])
    truth = [state_from_row(row) for row in target_rows]
    dense_truth = [state_from_row(row) for row in dense_rows]
    dense_times = [row["epoch_jd_tdb"] for row in dense_rows]
    mu = constants["mu_sun_km3_s2"] * day_s**2 / au_km**3
    c = config["speed_of_light_km_s"] * day_s / au_km
    force_cfg = config["earth_j2"]
    earth = next(p for p in planets if p.body_id == "399")
    force = _force(start, planets, earth, mu, c, ng, force_cfg["reference_radius_km"] / au_km, force_cfg["j2"])
    relative_times = [row["epoch_jd_tdb"] - start for row in target_rows]
    started = time.perf_counter()
    if solver == "rk4":
        predictions, meta = _run_rk4(initial, start, relative_times, force, planets, float(scale_or_tol), config["default_step_days"])
    else:
        predictions, meta = _run_dp(initial, start, [row["epoch_jd_tdb"] for row in target_rows], force, scale_or_tol, config["dopri54_max_step_days"])
    meta = {**meta, "runtime_seconds": time.perf_counter() - started}
    accepted = _normalize_trace(meta, start, initial, solver)
    if accepted[-1][0] < (relative_times[-1] if solver == "rk4" else target_rows[-1]["epoch_jd_tdb"]) - 1e-12:
        raise ValueError("accepted trace does not cover the final requested stop")
    dense_predictions = _hermite(accepted, [t-start for t in dense_times] if solver == "rk4" else dense_times)
    if len(predictions) != len(target_rows) or len(dense_predictions) != len(dense_rows):
        raise ValueError("requested/dense output length mismatch")
    if _flat(predictions[0]) != _flat(initial):
        raise ValueError("requested output does not preserve exact initial state")
    requested_hermite = _hermite(accepted, relative_times if solver == "rk4" else [row["epoch_jd_tdb"] for row in target_rows])
    consistency = _shift(requested_hermite, predictions, au_km, day_s)
    if consistency["max_position_shift_km"] > 1e-7:
        raise ValueError("Hermite interpolation is inconsistent with requested solver knots")
    result = {"case": case, "arm": arm["id"], "solver": solver, "setting": scale_or_tol, "requested_samples": len(predictions), "dense_samples": len(dense_predictions), "summary_old_grid": _summary(predictions, truth, au_km, day_s), "summary_dense": _summary(dense_predictions, dense_truth, au_km, day_s), "runtime_seconds": meta.get("runtime_seconds"), "counts": {key: value for key, value in meta.items() if key.endswith("steps") or key.endswith("evaluations") or key in {"accepted_steps", "rejected_steps", "attempted_steps"}}, "requested_hermite_consistency": consistency, "requested_states": [list(_flat(state)) for state in predictions], "dense_states": [list(_flat(state)) for state in dense_predictions], "accepted_time_basis": "relative_days_since_start" if solver == "rk4" else "absolute_jd_tdb", "start_jd_tdb": start, "accepted_endpoints": accepted}
    if baseline is not None:
        differences = {key: abs(result["summary_old_grid"][key] - value) for key, value in baseline.items()}
        result["baseline_reproduction_abs_differences"] = differences
        if any(value > config["baseline_reproduction_absolute_km"] for value in differences.values()):
            raise ValueError("old solver baseline reproduction exceeds frozen tolerance")
    _atomic_json(out, {"fingerprint": fingerprint, "result": result})
    print(json.dumps({"completed": f"{arm['id']}:{solver}:{scale_or_tol if solver == 'rk4' else scale_or_tol['label']}"}), flush=True)
    return result


def _state_list(rows: list[list[float]]) -> list[State]:
    return [_state(row) for row in rows]


def _setting_key(setting: Any) -> str:
    return setting["label"] if isinstance(setting, dict) else str(setting)


def _matrix(result_rows: list[dict], au_km: float, day_s: float, config: dict) -> dict[str, list[dict]]:
    lookup = {(row["arm"], row["solver"], _setting_key(row["setting"])): row for row in result_rows}
    paired_to_old = []
    for row in result_rows:
        reference = lookup.get(("old", row["solver"], _setting_key(row["setting"])))
        if reference is not None:
            paired_to_old.append({"arm": row["arm"], "solver": row["solver"], "setting": row["setting"], "shift": _shift(_state_list(row["requested_states"]), _state_list(reference["requested_states"]), au_km, day_s)})
    within_arm = []
    for arm in config["arms"]:
        tight = lookup.get((arm["id"], "dopri54", "tight"))
        tighter = lookup.get((arm["id"], "dopri54", "tighter"))
        if tight and tighter:
            within_arm.append({"kind": "dp_tight_to_tighter", "arm": arm["id"], "shift": _shift(_state_list(tight["requested_states"]), _state_list(tighter["requested_states"]), au_km, day_s)})
        coarse = lookup.get((arm["id"], "rk4", "0.5")); fine = lookup.get((arm["id"], "rk4", "0.25"))
        if coarse and fine:
            within_arm.append({"kind": "rk4_0.5_to_0.25", "arm": arm["id"], "shift": _shift(_state_list(coarse["requested_states"]), _state_list(fine["requested_states"]), au_km, day_s)})
    arm_pairs = [("moon5m", "earthmoon5m"), ("earthmoon_venus", "earthmoon_venus15m"), ("earthmoon_venus15m", "no_venus"), ("earthmoon_venus15m", "no_moon")]
    paired_arms = []
    for left, right in arm_pairs:
        for solver in ("dopri54", "rk4"):
            for setting in ("tight", "tighter") if solver == "dopri54" else ("0.5", "0.25"):
                a, b = lookup.get((left, solver, setting)), lookup.get((right, solver, setting))
                if a and b:
                    paired_arms.append({"left": left, "right": right, "solver": solver, "setting": setting, "shift": _shift(_state_list(a["requested_states"]), _state_list(b["requested_states"]), au_km, day_s)})
    return {"to_old_same_solver": paired_to_old, "within_arm": within_arm, "selected_arm_pairs": paired_arms}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--config", type=Path, default=Path("configs/apophis_moon_venus_v1_1.json"))
    parser.add_argument("--freeze-only", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    config_path = (root / args.config).resolve() if not args.config.is_absolute() else args.config.resolve()
    config = load_json(config_path)
    baseline_config = load_json(root / config["baseline_config"])
    data = load_json(root / "configs/eda_pilot_6.json")
    force_config = load_json(root / "configs/b3plus_pilot_6.json")
    manifest_paths = _validate_manifest(root, config)
    validation = load_json(root / "data/processed/apophis_moon_venus/input_validation.json")
    if validation.get("complete") is not True:
        raise ValueError("input validation is not complete")
    from finalize_apophis_moon_venus_inputs import finalize
    if finalize(root) != validation:
        raise ValueError("Input validation no longer matches its source")
    source_hashes, fingerprint = _provenance(root, config_path, config, data, force_config, manifest_paths)
    output_dir = root / config["output_directory"]
    freeze = {"schema_version": 1, "config": config_path.relative_to(root).as_posix(), "source_sha256": source_hashes, "fingerprint": fingerprint, "runtime": {"executable": sys.executable, "version": sys.version, "platform": platform.platform()}}
    _atomic_json(output_dir / "freeze.json", freeze)
    if args.freeze_only:
        print(json.dumps({"freeze": str(output_dir / 'freeze.json'), "fingerprint": fingerprint}, indent=2))
        return 0
    daily, refined = _old_inputs(root, data, load_json(root / "configs/eda_apophis_2029_refinement.json"))
    new = _new_rows(root, config)
    _validate_overlap(refined, new, config["raw_overlap_position_tolerance_km"], config["raw_overlap_velocity_tolerance_m_s"], data["constants"]["au_km"], data["constants"]["day_s"])
    old_planets = _old_planets(root, data, daily, refined)
    small = load_small_body_perturbers(root, force_config, data["constants"]["mu_sun_km3_s2"] * data["constants"]["day_s"]**2 / data["constants"]["au_km"]**3)
    old_planets += small
    hourly_venus = _decimate(new["299"], 4)
    development_venus = _body_rows(root, "data/raw/development30/planets", {"id": "299", "name": "Venus"})
    if [r["epoch_jd_tdb"] for r in hourly_venus] != [r["epoch_jd_tdb"] for r in development_venus if hourly_venus[0]["epoch_jd_tdb"] <= r["epoch_jd_tdb"] <= hourly_venus[-1]["epoch_jd_tdb"]]:
        raise ValueError("new 15-minute Venus decimation does not match development30 hourly epochs")
    ng = load_ng_input(root / "data/raw/horizons/asteroid_99942.json")
    if ng is None or ng.parameters is None:
        raise ValueError("nominal Apophis NG parameters absent")
    annual, _ = _target_rows(root, data, load_json(root / "configs/eda_apophis_2029_refinement.json"), 365)
    dense_rows = new["99942"]
    constants = data["constants"]
    runtime_config = dict(baseline_config)
    runtime_config.update(force_config)
    runtime_config.update(config)
    runtime_config["earth_j2"] = baseline_config["earth_j2"]
    stage3 = load_json(root / "outputs/apophis_solver_audit/stage3/apophis_solver_audit.json")
    baseline_expected: dict[tuple[str, str], dict[str, float]] = {}
    stage3_case = next(c for c in stage3["cases"] if c["case_id"] == "apophis_2029_long365d")
    for row in stage3_case["solver_comparison"]:
        if row["id"].startswith("rk4:"):
            key = ("rk4", row["id"].split(":", 1)[1])
        elif row["id"].startswith("dopri54:") and row["id"].split(":", 1)[1] in config["dp_tolerance_labels"]:
            key = ("dopri54", row["id"].split(":", 1)[1])
        else:
            continue
        baseline_expected[key] = {key: row[key] for key in ("max_grid_position_error_km", "final_position_error_km", "max_grid_velocity_error_m_s", "final_velocity_error_m_s")}
    output_dir.joinpath("checkpoints").mkdir(parents=True, exist_ok=True)
    result_rows: list[dict] = []
    for arm in _arm_plan(config):
        planets = _make_arm_planets(arm, old_planets, new, daily, constants)
        for label in config["dp_tolerance_labels"]:
            tolerance = next(t for t in baseline_config["dopri54_tolerances"] if t["label"] == label)
            result_rows.append(_run_variant("annual365", arm, "dopri54", tolerance, annual, dense_rows, planets, ng.parameters, runtime_config, constants, output_dir / "checkpoints" / f"{arm['id']}_dopri54_{label}.json", fingerprint, baseline_expected.get(("dopri54", label)) if arm["id"] == "old" else None))
        if arm["id"] in config["rk4_arms"]:
            for scale in config["rk4_scales"]:
                result_rows.append(_run_variant("annual365", arm, "rk4", scale, annual, dense_rows, planets, ng.parameters, runtime_config, constants, output_dir / "checkpoints" / f"{arm['id']}_rk4_{scale}.json", fingerprint, baseline_expected.get(("rk4", str(scale))) if arm["id"] == "old" else None))
    matrix = {"schema_version": 1, "fingerprint": fingerprint, "source_sha256": source_hashes, "teacher_roles": {"primary": "old immutable 797-row Apophis target used for every integration and primary score", "secondary": "new 2305-row Apophis target used only for dense evaluator geometry; daily/refined discrepancy is explicitly retained in input_validation"}, "input_validation": validation, "ng_gate": {"status": ng.status(annual[0]["epoch_jd_tdb"]), "source": ng.source, "source_sha256": ng.source_sha256, "availability_basis": ng.availability_basis, "solution_date": ng.solution_date}, "runs": result_rows, "paired_shifts": _matrix(result_rows, constants["au_km"], constants["day_s"], config), "dense_grid": {"samples": len(dense_rows), "evaluation": "Hermite interpolation of accepted endpoints; integration stops remain old 797 grid", "teacher": "secondary new2305 only"}}
    _atomic_json(output_dir / "apophis_moon_venus_results.json", matrix)
    print(json.dumps({"output": str(output_dir / "apophis_moon_venus_results.json"), "runs": len(result_rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

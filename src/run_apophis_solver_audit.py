"""Run the bounded independent-solver audit for the frozen Apophis teacher case."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

from earth_oblateness import earth_j2_acceleration
from independent_rk import integrate_dopri54
from orbit_baselines import State, norm, propagate_variable_step, subtract
from planetary_dynamics import (
    EphemerisInterpolator,
    NonGravitationalParameters,
    Perturber,
    combine_accelerations,
    encounter_aware_step_selector,
    non_gravitational_acceleration,
    restricted_n_body_acceleration,
    solar_schwarzschild_acceleration,
)
from run_b3plus_ablation import load_small_body_perturbers
from run_eda import load_json, parse_horizons
from run_nbody_baseline import load_perturbers
from run_physics_baselines import errors, load_asteroid_series, state_from_row
from ng_inputs_v2 import load_ng_input


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _merge_rows(daily: list[dict], refined: list[dict]) -> list[dict]:
    by_epoch = {row["epoch_jd_tdb"]: row for row in daily}
    by_epoch.update({row["epoch_jd_tdb"]: row for row in refined})
    return [by_epoch[key] for key in sorted(by_epoch)]


def _rows(path: Path, body: dict) -> list[dict]:
    return parse_horizons(path, body["id"], body["name"])[1]


def _old_inputs(root: Path, data: dict, event: dict) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    refined: dict[str, list[dict]] = {}
    event_id = event["event_id"].replace("-", "_")
    for target in [event["asteroid"], *event["bodies"]]:
        refined[target["id"]] = _rows(root / "data/raw/horizons_refined" / f"{event_id}_{target['id']}.json", target)
    daily: dict[str, list[dict]] = {}
    for body in data["perturbers"]:
        daily[body["id"]] = _rows(root / "data/raw/horizons" / f"body_{body['id']}.json", body)
    return daily, refined


def _development30_planets(root: Path, data: dict) -> tuple[Perturber, ...]:
    conversion = data["constants"]["day_s"] ** 2 / data["constants"]["au_km"] ** 3
    output = []
    for body in data["perturbers"]:
        rows = _rows(root / "data/raw/development30/planets" / f"body_{body['id']}.json", body)
        output.append(Perturber(body["id"], body["name"], body["mu_km3_s2"] * conversion, EphemerisInterpolator.from_rows(rows)))
    return tuple(output)


def _old_planets(root: Path, data: dict, daily: dict[str, list[dict]], refined: dict[str, list[dict]]) -> tuple[Perturber, ...]:
    overrides = {key: _merge_rows(daily[key], refined[key]) for key in ("399", "301")}
    return load_perturbers(root, data, overrides)


def _target_rows(root: Path, data: dict, event: dict, horizon: int) -> tuple[list[dict], list[dict]]:
    series = load_asteroid_series(root, data)[event["asteroid"]["id"]]
    short = _rows(root / "data/raw/horizons_refined" / f"{event['event_id'].replace('-', '_')}_{event['asteroid']['id']}.json", event["asteroid"])
    merged = _merge_rows(series, short)
    start = next(row for row in merged if row["epoch_tdb"].startswith("2029-01-01"))
    stop = start["epoch_jd_tdb"] + horizon
    selected = [row for row in merged if start["epoch_jd_tdb"] <= row["epoch_jd_tdb"] <= stop]
    if not selected or selected[-1]["epoch_jd_tdb"] != stop:
        raise ValueError("old Apophis grid lacks the declared endpoint")
    return selected, short


def _force(
    start_jd: float,
    perturbers: tuple[Perturber, ...],
    earth: Perturber,
    mu_sun: float,
    c_au_d: float,
    ng: NonGravitationalParameters,
    reference_radius_au: float,
    j2: float,
) -> Any:
    return combine_accelerations(
        restricted_n_body_acceleration(mu_sun, perturbers, start_jd),
        solar_schwarzschild_acceleration(mu_sun, c_au_d),
        non_gravitational_acceleration(ng),
        earth_j2_acceleration(earth, start_jd, reference_radius_au, j2, pole_model="iau"),
    )


def _flat(state: State) -> tuple[float, ...]:
    return (*state.position, *state.velocity)


def _state(flat: Iterable[float]) -> State:
    values = tuple(float(value) for value in flat)
    return State(values[:3], values[3:6])  # type: ignore[arg-type]


def _summary(predictions: list[State], truth: list[State], au_km: float, day_s: float) -> dict[str, float]:
    pos = [errors(a, b, au_km, day_s)[0] for a, b in zip(predictions, truth)]
    vel = [errors(a, b, au_km, day_s)[1] for a, b in zip(predictions, truth)]
    return {
        "max_grid_position_error_km": max(pos),
        "final_position_error_km": pos[-1],
        "max_grid_velocity_error_m_s": max(vel),
        "final_velocity_error_m_s": vel[-1],
    }


def _shift(left: list[State], right: list[State], au_km: float, day_s: float) -> dict[str, float]:
    pos = [norm(subtract(a.position, b.position)) * au_km for a, b in zip(left, right)]
    vel = [norm(subtract(a.velocity, b.velocity)) * au_km * 1000.0 / day_s for a, b in zip(left, right)]
    return {"max_position_shift_km": max(pos), "final_position_shift_km": pos[-1], "max_velocity_shift_m_s": max(vel), "final_velocity_shift_m_s": vel[-1]}


def _run_rk4(initial: State, start: float, times: list[float], force: Any, perturbers: tuple[Perturber, ...], scale: float, default_step: float) -> tuple[list[State], dict]:
    selector = encounter_aware_step_selector(perturbers, start, default_step_days=default_step, scale_factor=scale)
    accepted: list[list[Any]] = []
    predictions, steps = propagate_variable_step(
        initial, times, force, selector,
        on_step=lambda _left_t, _left_s, right_t, right_s: accepted.append([right_t, list(_flat(right_s))]),
    )
    return predictions, {"solver": "rk4", "step_scale": scale, "rk4_steps": steps, "force_evaluations": steps * 4, "requested_states": [list(_flat(state)) for state in predictions], "accepted_endpoints": accepted}


def _run_dp(initial: State, start: float, absolute_times: list[float], force: Any, tolerance: dict, max_step: float) -> tuple[list[State], dict]:
    result = integrate_dopri54(
        lambda epoch, position, velocity: force(epoch - start, position, velocity),
        start,
        _flat(initial),
        absolute_times,
        atol_position=tolerance["atol_position"],
        atol_velocity=tolerance["atol_velocity"],
        rtol=tolerance["rtol"],
        max_step=max_step,
    )
    return [_state(sample[1]) for sample in result.samples], {"solver": "dopri54", "tolerance": tolerance, **dict(result.stats), "requested_states": [list(sample[1]) for sample in result.samples], "accepted_endpoints": [[epoch, list(state)] for epoch, state in result.accepted_endpoints]}


def _run_case(
    case_id: str,
    target_rows: list[dict],
    old_planets: tuple[Perturber, ...],
    hourly_planets: tuple[Perturber, ...],
    ng: NonGravitationalParameters,
    config: dict,
    constants: dict,
    checkpoint: Path,
    fingerprint: str,
) -> dict:
    if checkpoint.exists():
        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        if saved.get("fingerprint") == fingerprint:
            return saved["result"]
        raise RuntimeError(f"Completed checkpoint fingerprint mismatch; refusing overwrite: {checkpoint}")
    au_km = constants["au_km"]
    day_s = constants["day_s"]
    start = target_rows[0]["epoch_jd_tdb"]
    times = [row["epoch_jd_tdb"] - start for row in target_rows]
    absolute_times = [row["epoch_jd_tdb"] for row in target_rows]
    initial = state_from_row(target_rows[0])
    truth = [state_from_row(row) for row in target_rows]
    earth_old = next(p for p in old_planets if p.body_id == "399")
    earth_hourly = next(p for p in hourly_planets if p.body_id == "399")
    ref_earth = [earth_old.ephemeris.state_at(row["epoch_jd_tdb"]) for row in target_rows]
    mu = constants["mu_sun_km3_s2"] * day_s**2 / au_km**3
    c = config["speed_of_light_km_s"] * day_s / au_km
    radius = config["earth_j2"]["reference_radius_km"] / au_km
    j2 = config["earth_j2"]["j2"]
    records: list[dict] = []
    predictions: dict[str, list[State]] = {}
    for scale in config["rk4_step_scales"]:
        force = _force(start, old_planets, earth_old, mu, c, ng, radius, j2)
        pred, meta = _run_rk4(initial, start, times, force, old_planets, scale, config["default_step_days"])
        key = f"rk4:{scale}"
        predictions[key] = pred
        records.append({"id": key, **meta, **_summary(pred, truth, au_km, day_s)})
    for tolerance in config["dopri54_tolerances"]:
        force = _force(start, old_planets, earth_old, mu, c, ng, radius, j2)
        pred, meta = _run_dp(initial, start, absolute_times, force, tolerance, config["dopri54_max_step_days"])
        key = f"dopri54:{tolerance['label']}"
        predictions[key] = pred
        records.append({"id": key, **meta, **_summary(pred, truth, au_km, day_s)})
    tight_key = "dopri54:tight"
    for record in records:
        record["cross_solver_to_tight"] = _shift(predictions[record["id"]], predictions[tight_key], au_km, day_s)
    old_force = _force(start, old_planets, earth_old, mu, c, ng, radius, j2)
    hourly_force = _force(start, hourly_planets, earth_hourly, mu, c, ng, radius, j2)
    old_pred, old_meta = _run_rk4(initial, start, times, old_force, old_planets, 0.5, config["default_step_days"])
    old_fine_pred, old_fine_meta = _run_rk4(initial, start, times, old_force, old_planets, 0.25, config["default_step_days"])
    hourly_pred, hourly_meta = _run_rk4(initial, start, times, hourly_force, hourly_planets, 0.5, config["default_step_days"])
    hourly_fine_pred, hourly_fine_meta = _run_rk4(initial, start, times, hourly_force, hourly_planets, 0.25, config["default_step_days"])
    hourly_solver = []
    hourly_predictions: dict[str, list[State]] = {}
    for pred, meta, label in ((hourly_pred, hourly_meta, "rk4:0.5"), (hourly_fine_pred, hourly_fine_meta, "rk4:0.25")):
        hourly_predictions[label] = pred
        hourly_solver.append({"id": label, **meta, **_summary(pred, truth, au_km, day_s)})
    for tolerance in config["dopri54_tolerances"]:
        if tolerance["label"] not in {"tight", "tighter"}:
            continue
        pred, meta = _run_dp(initial, start, absolute_times, hourly_force, tolerance, config["dopri54_max_step_days"])
        label = f"dopri54:{tolerance['label']}"
        hourly_predictions[label] = pred
        hourly_solver.append({"id": label, **meta, **_summary(pred, truth, au_km, day_s)})
    hourly_tight = hourly_predictions["dopri54:tight"]
    hourly_tighter = hourly_predictions["dopri54:tighter"]
    for record in hourly_solver:
        record["cross_solver_to_tight"] = _shift(hourly_predictions[record["id"]], hourly_tight, au_km, day_s)
    hourly_tight_vs_tighter = _shift(hourly_tight, hourly_tighter, au_km, day_s)
    result = {
        "case_id": case_id,
        "samples": len(target_rows),
        "start_tdb": target_rows[0]["epoch_tdb"],
        "stop_tdb": target_rows[-1]["epoch_tdb"],
        "solver_comparison": records,
        "ephemeris_comparison": {
            "old_daily_refined": {**old_meta, **_summary(old_pred, truth, au_km, day_s)},
            "development30_hourly": {**hourly_meta, **_summary(hourly_pred, truth, au_km, day_s)},
            "hourly_minus_old": _shift(hourly_pred, old_pred, au_km, day_s),
            "old_rk4_step_sensitivity": _shift(old_pred, old_fine_pred, au_km, day_s),
            "hourly_rk4_step_sensitivity": _shift(hourly_pred, hourly_fine_pred, au_km, day_s),
            "hourly_solver_comparison": hourly_solver,
            "hourly_tight_vs_tighter": hourly_tight_vs_tighter,
        },
        "reference_closest_distance_km": min(norm(subtract(a.position, b.position)) * au_km for a, b in zip(truth, ref_earth)),
    }
    _atomic_json(checkpoint, {"fingerprint": fingerprint, "result": result})
    return result


def _report(result: dict) -> str:
    lines = ["# Apophis independent-solver audit report", "", f"Generated {result['generated_at_utc']}.", "", "This is a bounded numerical/teacher audit, not an operational validation or fresh holdout.", "", f"NG status at the declared start: `{result['ng_gate']['status']}`; source: `{result['ng_gate']['source']}`; availability basis: `{result['ng_gate']['availability_basis']}`.", "", "## Results", ""]
    for case in result["cases"]:
        lines += [f"### {case['case_id']}", "", f"Grid: {case['samples']} samples, {case['start_tdb']} through {case['stop_tdb']} TDB; reference grid minimum {case['reference_closest_distance_km']:.6g} km.", "", "| Run | max position error km | final position error km | max velocity error m/s |", "|---|---:|---:|---:|"]
        for row in case["solver_comparison"]:
            lines.append(f"| {row['id']} | {row['max_grid_position_error_km']:.6g} | {row['final_position_error_km']:.6g} | {row['max_grid_velocity_error_m_s']:.6g} |")
        e = case["ephemeris_comparison"]
        lines += ["", "| Planet ephemeris | max position error km | final position error km |", "|---|---:|---:|"]
        for name in ("old_daily_refined", "development30_hourly"):
            lines.append(f"| {name} | {e[name]['max_grid_position_error_km']:.6g} | {e[name]['final_position_error_km']:.6g} |")
        lines += ["", f"Hourly minus old merged input: max position shift `{e['hourly_minus_old']['max_position_shift_km']:.6g}` km, final `{e['hourly_minus_old']['final_position_shift_km']:.6g}` km.", f"Old RK4 .5→.25 step sensitivity: max `{e['old_rk4_step_sensitivity']['max_position_shift_km']:.6g}` km, final `{e['old_rk4_step_sensitivity']['final_position_shift_km']:.6g}` km.", "", "Hourly input solver checks:", "", "| Run | max position error km | final position error km |", "|---|---:|---:|"]
        for row in e["hourly_solver_comparison"]:
            lines.append(f"| {row['id']} | {row['max_grid_position_error_km']:.6g} | {row['final_position_error_km']:.6g} |")
        lines.append("")
    lines += ["## Interpretation and limits", "", "RK4/DP agreement is an empirical numerical sensitivity bound at the declared tolerances. The old-versus-hourly shift is an ephemeris/interpolation sensitivity measurement. Neither result identifies a missing physical force. The old nominal NG values are retained only to reproduce the historical teacher branch; their future retrieval timestamp cannot establish availability for an earlier historical start, and osculating epoch/solution date are descriptive."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--config", type=Path, default=Path("configs/apophis_solver_audit.json"))
    parser.add_argument("--output-directory", type=Path, default=Path("outputs/apophis_solver_audit"))
    args = parser.parse_args(argv)
    root = args.root.resolve()
    config_path = (root / args.config).resolve() if not args.config.is_absolute() else args.config.resolve()
    config = load_json(config_path)
    data = load_json(root / config["data_config"])
    force_config = load_json(root / config["force_config"])
    event = load_json(root / config["event_config"])
    daily, refined = _old_inputs(root, data, event)
    old_planets = _old_planets(root, data, daily, refined)
    hourly_planets = _development30_planets(root, data)
    small = load_small_body_perturbers(root, force_config, data["constants"]["mu_sun_km3_s2"] * data["constants"]["day_s"] ** 2 / data["constants"]["au_km"] ** 3)
    old_planets = old_planets + small
    hourly_planets = hourly_planets + small
    ng_path = root / "data/raw/horizons/asteroid_99942.json"
    ng_input = load_ng_input(ng_path)
    if ng_input.parameters is None:
        raise ValueError("Frozen Apophis nominal NG header is missing")
    target_annual, short = _target_rows(root, data, event, config["annual_horizon_days"])
    targets = [("apophis_2029_long365d", target_annual), ("apophis_earth_2029_refined36h", short)]
    output_dir = args.output_directory if args.output_directory.is_absolute() else root / args.output_directory
    checkpoints = output_dir / "checkpoints"
    source_paths = [config_path, root / config["data_config"], root / config["force_config"], root / config["event_config"], ng_path, root / "src/independent_rk.py", root / "src/run_apophis_solver_audit.py"]
    source_paths += [root / "data/raw/horizons" / f"body_{body['id']}.json" for body in data["perturbers"]]
    source_paths += [root / force_config["small_body_ephemeris_directory"] / f"asteroid_{body['id']}.json" for body in force_config["small_body_perturbers"]]
    source_paths += sorted((root / "data/raw/horizons_refined").glob("apophis_earth_2029_*.json"))
    source_paths += sorted((root / "data/raw/development30/planets").glob("body_*.json"))
    source_hashes = {path.relative_to(root).as_posix(): _sha(path) for path in source_paths}
    fingerprint = hashlib.sha256(json.dumps({"config": config, "sources": source_hashes}, sort_keys=True).encode()).hexdigest()
    runtime_config = dict(config)
    runtime_config.update(force_config)
    cases = [_run_case(case_id, rows, old_planets, hourly_planets, ng_input.parameters, runtime_config, data["constants"], checkpoints / f"{case_id}.json", fingerprint) for case_id, rows in targets]
    result = {"schema_version": 1, "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "config": config_path.relative_to(root).as_posix(), "source_sha256": source_hashes, "fingerprint": fingerprint, "ng_gate": {"status": ng_input.status(target_annual[0]["epoch_jd_tdb"]), "source": ng_input.source, "source_sha256": ng_input.source_sha256, "available_from_jd_tdb": ng_input.available_from_jd_tdb, "availability_basis": ng_input.availability_basis, "solution_date": ng_input.solution_date}, "cases": cases}
    _atomic_json(output_dir / "apophis_solver_audit.json", result)
    (output_dir / "apophis_solver_audit_report.md").write_text(_report(result), encoding="utf-8")
    (root / "docs/APOPHIS_SOLVER_AUDIT_REPORT.md").write_text(_report(result), encoding="utf-8")
    print(json.dumps({"output": str(output_dir / "apophis_solver_audit.json"), "cases": [case["case_id"] for case in cases]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

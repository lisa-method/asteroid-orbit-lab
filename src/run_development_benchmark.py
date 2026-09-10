"""Run the bounded object-disjoint development30 benchmark.

The runner is intentionally a standard-library orchestration layer.  It loads
the immutable Horizons responses prepared by the sampling/download steps,
propagates each candidate from a single forecast-time state, and evaluates
against a separately merged reference grid.  In particular, reference rows
are never passed to :func:`forecast_candidate`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any, Iterable, Mapping

from development_features import build_features
from encounter_screening import scan_body, uniform_times
from orbit_baselines import State, add, norm, propagate_variable_step, subtract
from planetary_dynamics import (
    EphemerisInterpolator,
    Perturber,
    combine_accelerations,
    encounter_aware_step_selector,
    restricted_n_body_acceleration,
    solar_schwarzschild_acceleration,
)
from run_eda import load_json, parse_horizons
from run_b3plus_ablation import load_small_body_perturbers
from trajectory_metrics import trajectory_error_rows, summarize_error_rows


MODEL_IDS = ("B2", "B3", "B3+GR", "B3+GR+SB16")
_SOURCE_FILES = (
    "src/run_development_benchmark.py",
    "src/development_features.py",
    "src/development_rules.py",
    "src/orbit_baselines.py",
    "src/planetary_dynamics.py",
    "src/encounter_geometry.py",
    "src/encounter_screening.py",
    "src/run_eda.py",
    "src/run_b3plus_ablation.py",
    "src/trajectory_metrics.py",
)


def merge_rows(daily: list[dict], refined: list[dict]) -> list[dict]:
    """Merge reference rows, replacing coincident daily nodes with refinement."""

    by_epoch = {row["epoch_jd_tdb"]: row for row in daily}
    by_epoch.update({row["epoch_jd_tdb"]: row for row in refined})
    return [by_epoch[epoch] for epoch in sorted(by_epoch)]


def state_from_row(row: Mapping[str, Any]) -> State:
    return State(tuple(float(v) for v in row["r"]), tuple(float(v) for v in row["v"]))  # type: ignore[arg-type]


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rel(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def source_hashes(root: Path, config_path: Path, sample_path: Path, raw_directory: Path) -> dict[str, str]:
    """Freeze only runner/features/rules, config/sample and immutable raw inputs."""

    paths: list[Path] = [root / relative for relative in _SOURCE_FILES]
    paths.append(config_path)
    for relative in ("configs/eda_pilot_6.json", "configs/b3plus_pilot_6.json"):
        paths.append(root / relative)
    paths.append(sample_path)
    for manifest in (
        root / "data" / "checksums" / "development30_catalogue_manifest.json",
        root / "data" / "checksums" / "development30_data_manifest.json",
        root / "data" / "checksums" / "b3plus_small_perturbers_manifest.json",
    ):
        if manifest.exists():
            paths.append(manifest)
    # Include raw development inputs in the input hash, while keeping this
    # independent of mutable reports and generated output directories.
    if raw_directory.exists():
        paths.extend(sorted(p for p in raw_directory.rglob("*.json") if p.is_file()))
    # SB441-N16 ephemerides are outside raw_directory but are an input to the
    # B3+GR+SB16 candidate and therefore part of a resumable run's freeze.
    small_directory = root / "data" / "raw" / "horizons_small_perturbers"
    if small_directory.exists():
        paths.extend(sorted(p for p in small_directory.glob("*.json") if p.is_file()))
    result = {}
    for path in paths:
        if path.exists() and path.is_file():
            result[_rel(root, path)] = _sha256(path)
    return dict(sorted(result.items()))


def validate_raw_manifest(root: Path) -> None:
    """Check the downloaded development manifest before any propagation."""

    manifest_path = root / "data" / "checksums" / "development30_data_manifest.json"
    if not manifest_path.exists():
        return
    document = load_json(manifest_path)
    for entry in document.get("files", []):
        relative = entry.get("path")
        expected = entry.get("sha256")
        if not relative or not expected:
            continue
        path = root / relative
        if not path.exists():
            raise FileNotFoundError(f"manifest input is missing: {relative}")
        actual = _sha256(path)
        if actual != expected:
            raise RuntimeError(f"raw manifest hash mismatch for {relative}: {actual} != {expected}")


def _config_hash(config_path: Path) -> str:
    return _sha256(config_path)


def _runtime_environment() -> dict[str, str]:
    return {
        "python_version": sys.version,
        "python_implementation": sys.implementation.name,
        "platform": sys.platform,
    }


def _validate_model(model: Mapping[str, Any]) -> None:
    if model.get("model_id") not in MODEL_IDS:
        raise ValueError(f"unsupported model {model.get('model_id')!r}")


def _model_force(initial: State, start_jd: float, model: Mapping[str, Any], context: Mapping[str, Any], config: Mapping[str, Any]):
    _ = initial
    model_id = str(model["model_id"])
    bodies: tuple[Perturber, ...] = tuple(context["planets"]) if model_id != "B2" else ()
    if model_id == "B3+GR+SB16":
        bodies += tuple(context["small"])
    terms = [restricted_n_body_acceleration(float(context["mu"]), bodies, start_jd)]
    if model_id in ("B3+GR", "B3+GR+SB16"):
        terms.append(solar_schwarzschild_acceleration(float(context["mu"]), float(context["c_au_d"])))
    return combine_accelerations(*terms), bodies


def forecast_candidate(
    initial: State,
    start_jd: float,
    horizon_days: float,
    model: Mapping[str, Any],
    context: Mapping[str, Any],
    config: Mapping[str, Any],
    step_scale: float,
) -> tuple[list[State], dict[str, Any]]:
    """Propagate a candidate using forecast-time inputs only.

    Returns daily-output states and metadata.  ``metadata['accepted_states']``
    contains every accepted RK4 endpoint (including t=0) and
    ``metadata['snapshots']`` records causal runtime/step prefixes at each
    daily output.  Reference trajectories are deliberately absent from this
    API.  The caller supplies the frozen production or sensitivity step scale
    explicitly.
    """

    started = time.perf_counter()
    if not isinstance(initial, State):
        raise TypeError("initial must be an orbit_baselines.State")
    _validate_model(model)
    horizon = float(horizon_days)
    start = float(start_jd)
    scale_factor = float(step_scale)
    if not math.isfinite(horizon) or horizon <= 0.0 or not math.isfinite(start) or not math.isfinite(scale_factor) or scale_factor <= 0.0:
        raise ValueError("start, horizon and step_scale must be finite; horizon and step_scale positive")
    default_step = float(config.get("default_step_days", 0.0625))
    acceleration, bodies = _model_force(initial, start, model, context, config)
    selector = encounter_aware_step_selector(
        bodies,
        start,
        default_step_days=default_step,
        scale_factor=scale_factor,
    )
    targets = uniform_times(horizon, 1.0)
    accepted: dict[float, State] = {0.0: initial}
    snapshots: dict[str, dict[str, Any]] = {"0": {"runtime_seconds": 0.0, "rk4_steps": 0}}

    def on_step(left_time: float, left_state: State, right_time: float, right_state: State) -> None:
        accepted[float(left_time)] = left_state
        accepted[float(right_time)] = right_state

    def on_output(epoch: float, _state: State, steps: int) -> None:
        # Callback runs before propagate_variable_step starts the next target.
        snapshots[str(float(epoch))] = {
            "runtime_seconds": time.perf_counter() - started,
            "rk4_steps": int(steps),
        }

    predictions, steps = propagate_variable_step(
        initial, targets, acceleration, selector, on_output=on_output, on_step=on_step
    )
    # Deduplicate coincident left/right endpoints and retain exact state objects.
    endpoints = [{"time_days": t, "state": accepted[t]} for t in sorted(accepted)]
    return predictions, {
        "horizon_days": horizon,
        "daily_times_days": targets,
        "snapshots": snapshots,
        "accepted_states": endpoints,
        "rk4_steps": steps,
        "runtime_seconds": time.perf_counter() - started,
        "model_id": model["model_id"],
        "step_scale": scale_factor,
    }


def _interpolate_endpoints(endpoints: list[dict[str, Any]]):
    times = tuple(float(item["time_days"]) for item in endpoints)
    states = tuple(item["state"] for item in endpoints)
    ephemeris = EphemerisInterpolator(times, states)
    return ephemeris.state_at


def _state_json(state: State) -> dict[str, list[float]]:
    return {"r": list(state.position), "v": list(state.velocity)}


def _trace_json(trace: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{"time_days": float(item["time_days"]), "state": _state_json(item["state"])} for item in trace]


def _reference_grid(rows: list[dict], start_jd: float, horizon: float) -> tuple[list[float], list[State]]:
    selected = [row for row in rows if -1e-10 <= float(row["epoch_jd_tdb"]) - start_jd <= horizon + 1e-10]
    selected.sort(key=lambda row: float(row["epoch_jd_tdb"]))
    if len(selected) < 2:
        raise ValueError("reference grid must have at least two rows")
    return [float(row["epoch_jd_tdb"]) - start_jd for row in selected], [state_from_row(row) for row in selected]


def _common_grid(times: Iterable[float], *more: Iterable[float]) -> list[float]:
    values = {float(value) for value in times}
    for sequence in more:
        values.update(float(value) for value in sequence)
    return sorted(values)


def _geometry(body: Perturber, times: list[float], asteroid_states: list[State], context: Mapping[str, Any]) -> dict:
    planet_states = [body.ephemeris.state_at(float(context["start_jd"]) + t) for t in times]
    return scan_body(times, asteroid_states, planet_states, body, float(context["mu"]), float(context["au_km"]), float(context["day_s"]), refine=True)


def _feature_values(result: Mapping[str, Any]) -> dict[str, float]:
    return {
        key: float(result.get(key, 0.0))
        for key in ("planet_proxy_km", "gr_proxy_km", "small_body_proxy_km")
    }


def _validate_sample(sample: Mapping[str, Any]) -> list[dict[str, Any]]:
    objects = sample.get("objects")
    if not isinstance(objects, list) or not objects:
        raise ValueError("sample must contain a non-empty objects list")
    for index, obj in enumerate(objects):
        if not isinstance(obj, Mapping):
            raise ValueError(f"sample.objects[{index}] must be an object")
        for field in ("id", "name", "stratum", "split", "start_date"):
            if field not in obj:
                raise ValueError(f"sample.objects[{index}] is missing {field}")
        if obj["split"] not in ("train", "validation"):
            raise ValueError("sample split must be train or validation")
    return [dict(obj) for obj in objects]


def load_development_context(root: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    data_config = load_json(root / str(config["data_config"]))
    force_config = load_json(root / str(config["force_config"]))
    constants = data_config["constants"]
    au_km = float(constants["au_km"])
    day_s = float(constants["day_s"])
    mu = float(constants["mu_sun_km3_s2"]) * day_s**2 / au_km**3
    raw_directory = root / str(config["raw_directory"])
    planets: list[Perturber] = []
    conversion = day_s**2 / au_km**3
    for body in data_config["perturbers"]:
        path = raw_directory / "planets" / f"body_{body['id']}.json"
        _, rows = parse_horizons(path, str(body["id"]), str(body["name"]))
        planets.append(Perturber(str(body["id"]), str(body["name"]), float(body["mu_km3_s2"]) * conversion, EphemerisInterpolator.from_rows(rows)))
    small = load_small_body_perturbers(root, force_config, mu)
    return {
        "mu": mu,
        "au_km": au_km,
        "day_s": day_s,
        "c_au_d": float(force_config["speed_of_light_km_s"]) * day_s / au_km,
        "planets": tuple(planets),
        "small": tuple(small),
        "data_config": data_config,
        "force_config": force_config,
        "load_seconds": time.perf_counter() - started,
    }


def _raw_path(root: Path, raw_directory: Path, kind: str, body_id: str, refined: bool = False) -> Path:
    if kind == "asteroid":
        suffix = "_refined" if refined else "_daily"
        return raw_directory / "asteroids" / f"asteroid_{body_id}{suffix}.json"
    return raw_directory / "planets" / f"body_{body_id}.json"


def load_object_rows(root: Path, raw_directory: Path, obj: Mapping[str, Any]) -> tuple[list[dict], list[dict]]:
    object_id, name = str(obj["id"]), str(obj["name"])
    daily_path = _raw_path(root, raw_directory, "asteroid", object_id)
    _, daily = parse_horizons(daily_path, object_id, name)
    refined_path = _raw_path(root, raw_directory, "asteroid", object_id, True)
    refined = parse_horizons(refined_path, object_id, name)[1] if refined_path.exists() else []
    return daily, merge_rows(daily, refined)


def _find_initial(rows: list[dict], date: str) -> tuple[float, State]:
    exact = [row for row in rows if str(row.get("epoch_tdb", "")).startswith(date + "T00:00:00")]
    if not exact:
        raise ValueError(f"missing midnight TDB initial state for {date}")
    row = min(exact, key=lambda item: float(item["epoch_jd_tdb"]))
    return float(row["epoch_jd_tdb"]), state_from_row(row)


def _reference_catalogue(rows: list[dict], start_jd: float, max_horizon: float, context: Mapping[str, Any]) -> dict[str, dict]:
    times, states = _reference_grid(rows, start_jd, max_horizon)
    output: dict[str, dict] = {}
    for body in context["planets"]:
        output[body.body_id] = _geometry(body, times, states, {**context, "start_jd": start_jd})
    return output


def _evaluate_model(
    initial: State,
    start_jd: float,
    horizon: float,
    model: Mapping[str, Any],
    context: Mapping[str, Any],
    config: Mapping[str, Any],
    reference_times: list[float],
    reference_states: list[State],
    production_runs: list[tuple[list[State], dict[str, Any]]],
    fine_runs: list[tuple[list[State], dict[str, Any]]],
) -> dict[str, Any]:
    production = production_runs[0]
    fine = fine_runs[0]
    p_at = _interpolate_endpoints(production[1]["accepted_states"])
    f_at = _interpolate_endpoints(fine[1]["accepted_states"])
    pred_ref = [p_at(t) for t in reference_times]
    error_rows = trajectory_error_rows(reference_times, pred_ref, reference_states, context["au_km"], context["day_s"])
    summary = summarize_error_rows(error_rows)
    # Sensitivity is measured on the same causal reference grid used for the
    # error metric.  Accepted integration endpoints are retained for geometry
    # and trace inspection but do not add a hidden future target grid here.
    compare_times = list(reference_times)
    production_common = [p_at(t) for t in compare_times]
    fine_common = [f_at(t) for t in compare_times]
    numerical_difference_km = max(norm(subtract(a.position, b.position)) * float(context["au_km"]) for a, b in zip(production_common, fine_common))
    bodies = []
    geometry_compare = {}
    body_list = tuple(context["planets"])
    for body in body_list:
        pgeo = _geometry(body, compare_times, production_common, {**context, "start_jd": start_jd})
        rgeo = _geometry(body, reference_times, reference_states, {**context, "start_jd": start_jd})
        geometry_compare[body.body_id] = {
            "predicted": pgeo,
            "reference": rgeo,
            "distance_difference_km": pgeo["distance_km"] - rgeo["distance_km"],
            "time_difference_days": pgeo["time_days"] - rgeo["time_days"],
        }
        bodies.append(pgeo)
    runtimes = [float(run[1]["snapshots"][str(float(horizon))]["runtime_seconds"]) for run in production_runs]
    return {
        "model_id": model["model_id"],
        "max_position_error_km": summary["max_position_error_km"],
        "max_position_error_time_days": summary["max_position_error_time_days"],
        "endpoint_velocity_error_m_s": summary["endpoint_velocity_error_m_s"],
        "max_velocity_error_m_s": summary["max_velocity_error_m_s"],
        "rtn_endpoint_position_error_km": summary["rtn_endpoint_position_error_km"],
        "rtn_max_abs_position_error_km": summary["rtn_max_abs_position_error_km"],
        "summarize_error_rows": summary,
        "numerical_difference_km": numerical_difference_km,
        "runtime_median_seconds": statistics.median(runtimes),
        "runtime_trials_seconds": runtimes,
        "rk4_steps": production[1]["snapshots"][str(float(horizon))]["rk4_steps"],
        "geometry": bodies,
        "geometry_compare": geometry_compare,
        "error_rows": error_rows,
    }


def _determinism_difference(runs: Iterable[tuple[list[State], dict[str, Any]]], au_km: float) -> float:
    materialized = list(runs)
    if len(materialized) < 2:
        return 0.0
    baseline = materialized[0][0]
    return max(
        (norm(subtract(left.position, right.position)) * au_km
         for run in materialized[1:]
         for left, right in zip(baseline, run[0])),
        default=0.0,
    )


def _determinism_velocity_difference(runs: Iterable[tuple[list[State], dict[str, Any]]], au_km: float, day_s: float) -> float:
    materialized = list(runs)
    if len(materialized) < 2:
        return 0.0
    baseline = materialized[0][0]
    return max(
        (norm(subtract(left.velocity, right.velocity)) * au_km * 1000.0 / day_s
         for run in materialized[1:]
         for left, right in zip(baseline, run[0])),
        default=0.0,
    )


def _feature_determinism_difference(results: Iterable[Mapping[str, Any]]) -> float:
    """Return the largest repeat discrepancy, excluding timing fields."""

    materialized = list(results)
    if len(materialized) < 2:
        return 0.0
    baseline = list(_feature_values(materialized[0]).values()) + [float(materialized[0].get("min_solar_distance_au", 0.0))]
    return max(
        (abs(left - right) for result in materialized[1:] for left, right in zip(
            baseline,
            list(_feature_values(result).values()) + [float(result.get("min_solar_distance_au", 0.0))],
        )),
        default=0.0,
    )


def _input_hashes(root: Path, raw_directory: Path, obj: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, str]:
    paths = [_raw_path(root, raw_directory, "asteroid", str(obj["id"])), _raw_path(root, raw_directory, "asteroid", str(obj["id"]), True)]
    paths.extend(_raw_path(root, raw_directory, "planet", body.body_id) for body in context["planets"])
    result = {}
    for path in paths:
        if path.exists():
            result[_rel(root, path)] = _sha256(path)
    return dict(sorted(result.items()))


def _object_checkpoint(root: Path, out: Path, split: str, obj: Mapping[str, Any], hashes: dict[str, str], result: dict) -> None:
    payload = {"schema_version": 1, "object_id": str(obj["id"]), "split": split, "source_hashes": hashes, "result": result}
    _atomic_json(out / split / "checkpoints" / f"object_{obj['id']}.json", payload)


def load_object_checkpoint(path: Path, expected_hashes: Mapping[str, str]) -> dict[str, Any] | None:
    """Load a completed object only when its complete input freeze matches."""

    if not path.exists():
        return None
    checkpoint = load_json(path)
    if checkpoint.get("source_hashes") != dict(expected_hashes):
        raise RuntimeError(f"resume hash mismatch for checkpoint {path}")
    result = checkpoint.get("result")
    if not isinstance(result, dict):
        raise ValueError(f"checkpoint result is not a dictionary: {path}")
    return result


def run(root: Path, config_path: Path, split: str, *, smoke: bool = False) -> dict[str, Any]:
    root = root.resolve()
    config_path = config_path.resolve()
    config = load_json(config_path)
    if split not in ("train", "validation"):
        raise ValueError("split must be train or validation")
    sample_path = root / str(config["sample_path"])
    raw_directory = root / str(config["raw_directory"])
    validate_raw_manifest(root)
    sample = {**load_json(sample_path)}
    objects = [obj for obj in _validate_sample(sample) if obj["split"] == split]
    if smoke:
        objects = objects[:1]
        config = {**config, "horizons_days": [7], "timing_repeats": 1}
    if split == "validation":
        rules_path = root / str(config["output_directory"]) / "rules.json"
        if not rules_path.exists():
            raise FileNotFoundError(f"validation requires frozen rules artifact: {rules_path}")
        rules_hash = _sha256(rules_path)
    else:
        rules_hash = None
    context = load_development_context(root, config)
    frozen_hashes = source_hashes(root, config_path, sample_path, raw_directory)
    references: dict[str, dict] = {}
    records: list[dict] = []
    feature_rows: list[dict] = []
    out = root / str(config["output_directory"])
    if smoke:
        out = out / "smoke"
    run_started_path = out / split / "run_started.json"
    run_started = {
        "schema_version": 1,
        "split": split,
        "smoke": smoke,
        "config_sha256": _config_hash(config_path),
        "source_sha256_at_start": frozen_hashes,
        "rules_sha256_at_start": rules_hash,
        "runtime_environment": _runtime_environment(),
        "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    if run_started_path.exists():
        prior = load_json(run_started_path)
        for key in ("split", "config_sha256", "source_sha256_at_start", "rules_sha256_at_start", "runtime_environment"):
            if prior.get(key) != run_started[key]:
                raise RuntimeError(f"run-start hash mismatch for {split}; existing checkpoints are not compatible")
    else:
        _atomic_json(run_started_path, run_started)
    for obj in objects:
        daily, reference_rows = load_object_rows(root, raw_directory, obj)
        start_jd, initial = _find_initial(daily, str(obj["start_date"]))
        context_case = {**context, "start_jd": start_jd}
        max_horizon = float(max(config["horizons_days"]))
        input_hash = _input_hashes(root, raw_directory, obj, context)
        expected_hashes = {**frozen_hashes, **{f"input:{key}": value for key, value in input_hash.items()}}
        checkpoint_path = out / split / "checkpoints" / f"object_{obj['id']}.json"
        saved = load_object_checkpoint(checkpoint_path, expected_hashes)
        if saved is not None:
            records.extend(saved.get("records", []))
            feature_rows.extend(saved.get("feature_rows", []))
            references[str(obj["id"])] = saved.get("references", {})
            continue
        references[str(obj["id"])] = _reference_catalogue(reference_rows, start_jd, max_horizon, context)
        obj_records: list[dict] = []
        obj_features: list[dict] = []
        annual_horizon = max_horizon
        # Three serial annual production runs per model.  Rotation makes the
        # measured prefix cost insensitive to a fixed model ordering.  The
        # fine run is a single offline sensitivity rollout per model.
        production_runs_by_model: dict[str, list[tuple[list[State], dict[str, Any]]]] = {model_id: [] for model_id in MODEL_IDS}
        for repeat in range(int(config.get("timing_repeats", 3))):
            for offset in range(len(MODEL_IDS)):
                model_id = MODEL_IDS[(repeat + offset) % len(MODEL_IDS)]
                model = next(m for m in config["models"] if m["model_id"] == model_id)
                production_runs_by_model[model_id].append(
                    forecast_candidate(initial, start_jd, annual_horizon, model, context_case, config, float(config.get("step_scale", 0.5)))
                )
                print(json.dumps({"object_id": str(obj["id"]), "split": split, "repeat": repeat + 1, "model_id": model_id, "horizon_days": annual_horizon}, sort_keys=True), flush=True)
        fine_runs_by_model: dict[str, list[tuple[list[State], dict[str, Any]]]] = {}
        fine_scale = float(config.get("step_scale", 0.5)) * float(config.get("sensitivity_scale_factor", 0.5))
        for model in config["models"]:
            fine_runs_by_model[model["model_id"]] = [
                forecast_candidate(initial, start_jd, annual_horizon, model, context_case, config, fine_scale)
            ]
        for horizon_value in config["horizons_days"]:
            horizon = float(horizon_value)
            reference_times, reference_states = _reference_grid(reference_rows, start_jd, horizon)
            for model in config["models"]:
                model_id = model["model_id"]
                production_runs = production_runs_by_model[model_id]
                fine_runs = fine_runs_by_model[model_id]
                evaluated = _evaluate_model(initial, start_jd, horizon, model, context_case, config, reference_times, reference_states, production_runs, fine_runs)
                evaluated["determinism_max_position_difference_km"] = _determinism_difference(production_runs, float(context["au_km"]))
                evaluated["determinism_max_velocity_difference_m_s"] = _determinism_velocity_difference(production_runs, float(context["au_km"]), float(context["day_s"]))
                record = {
                    "case_id": f"{obj['id']}:{obj['start_date']}:{horizon:g}",
                    "object_id": str(obj["id"]), "object_name": obj["name"], "stratum": obj["stratum"], "split": split,
                    "start_date": obj["start_date"], "horizon_days": horizon, **evaluated,
                }
                obj_records.append(record)
            feature_trials = [build_features(initial, start_jd, horizon, context_case, config) for _ in range(int(config.get("timing_repeats", 3)))]
            obj_features.append({
                "case_id": f"{obj['id']}:{obj['start_date']}:{horizon:g}", "object_id": str(obj["id"]), "horizon_days": horizon, "split": split,
                "features": _feature_values(feature_trials[0]),
                "per_body": [{"body_id": body_id, **value} for body_id, value in feature_trials[0]["per_body"].items()],
                "min_solar_distance_au": float(feature_trials[0]["min_solar_distance_au"]),
                "planet_max_eta": float(feature_trials[0]["planet_max_eta"]),
                "max_eta_by_body": feature_trials[0]["max_eta_by_body"],
                "runtime_median_seconds": statistics.median(float(row["runtime_seconds"]) for row in feature_trials),
                "runtime_trials_seconds": [float(row["runtime_seconds"]) for row in feature_trials],
                "determinism_max_scalar_difference": _feature_determinism_difference(feature_trials),
            })
        _atomic_json(
            out / split / "traces" / f"object_{obj['id']}.json",
            {
                "object_id": str(obj["id"]),
                "horizon_days": annual_horizon,
                "production": {model_id: _trace_json(runs[0][1]["accepted_states"]) for model_id, runs in production_runs_by_model.items()},
                "fine": {model_id: _trace_json(runs[0][1]["accepted_states"]) for model_id, runs in fine_runs_by_model.items()},
            },
        )
        records.extend(obj_records)
        feature_rows.extend(obj_features)
        _object_checkpoint(root, out, split, obj, expected_hashes, {"records": obj_records, "feature_rows": obj_features, "references": references[str(obj["id"])]})
    provenance = {"source_sha256_at_start": frozen_hashes, "rules_sha256_at_start": rules_hash, "runtime_environment": _runtime_environment(), "shared_load_seconds": float(context.get("load_seconds", 0.0)), "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "split": split, "smoke": smoke}
    result = {"schema_version": 1, "config": config, "records": records, "feature_rows": feature_rows, "references": references, "provenance": provenance}
    _atomic_json(out / split / "results.json", result)
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--split", choices=("train", "validation"), required=True)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args(argv)
    result = run(args.root, args.config, args.split, smoke=args.smoke)
    print(json.dumps({"split": args.split, "records": len(result["records"]), "feature_rows": len(result["feature_rows"])}, sort_keys=True))


if __name__ == "__main__":
    main()


__all__ = ["MODEL_IDS", "merge_rows", "forecast_candidate", "load_development_context", "load_object_checkpoint", "run", "source_hashes"]

"""Evaluate interpretable force additions on top of the planets-only B3 model."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
import statistics
import time

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
from run_eda import load_json, parse_horizons, percentile
from run_nbody_baseline import load_perturbers
from run_physics_baselines import (
    aggregate,
    errors,
    indexed_series,
    load_asteroid_series,
    state_from_row,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    return parser.parse_args()


def load_small_body_perturbers(
    root: Path,
    config: dict,
    mu_sun_au3_d2: float,
) -> tuple[Perturber, ...]:
    directory = root / config["small_body_ephemeris_directory"]
    output = []
    for body in config["small_body_perturbers"]:
        _, rows = parse_horizons(
            directory / f"asteroid_{body['id']}.json",
            body["id"],
            body["name"],
        )
        output.append(
            Perturber(
                f"sb:{body['id']}",
                body["name"],
                body["gm_over_gm_sun"] * mu_sun_au3_d2,
                EphemerisInterpolator.from_rows(rows),
            )
        )
    return tuple(output)


def _header_number(result: str, name: str) -> float:
    match = re.search(rf"\b{re.escape(name)}=\s*([-+.0-9Ee]+)", result)
    if not match:
        raise ValueError(f"Missing {name} in Horizons non-gravitational header")
    return float(match.group(1))


def load_non_gravitational_parameters(
    root: Path,
    data_config: dict,
) -> tuple[dict[str, NonGravitationalParameters], list[dict]]:
    parameters = {}
    audit = []
    for asteroid in data_config["asteroids"]:
        object_id = asteroid["id"]
        horizons_document = load_json(
            root / "data" / "raw" / "horizons" / f"asteroid_{object_id}.json"
        )
        result = horizons_document["result"]
        if "Asteroid non-gravitational force model" not in result:
            continue
        a1 = _header_number(result, "A1")
        a2 = _header_number(result, "A2")
        a3 = _header_number(result, "A3")
        if a1 == 0.0 and a2 == 0.0 and a3 == 0.0:
            continue
        model = NonGravitationalParameters(
            a1_au_d2=a1,
            a2_au_d2=a2,
            a3_au_d2=a3,
            alpha=_header_number(result, "ALN"),
            exponent_k=_header_number(result, "NK"),
            exponent_m=_header_number(result, "NM"),
            exponent_n=_header_number(result, "NN"),
            r0_au=_header_number(result, "R0"),
        )
        parameters[object_id] = model

        sbdb = load_json(root / "data" / "raw" / "sbdb" / f"object_{object_id}.json")
        uncertainties = {
            row["name"]: float(row["sigma"])
            for row in (sbdb["orbit"].get("model_pars") or [])
            if row.get("kind") == "EST" and row.get("sigma")
        }
        audit.append(
            {
                "object_id": object_id,
                "object_name": asteroid["name"],
                "a1_au_d2": a1,
                "a1_sigma_au_d2": uncertainties.get("A1"),
                "a2_au_d2": a2,
                "a2_sigma_au_d2": uncertainties.get("A2"),
                "a3_au_d2": a3,
                "a3_sigma_au_d2": uncertainties.get("A3"),
                "alpha": model.alpha,
                "exponent_m": model.exponent_m,
                "exponent_n": model.exponent_n,
                "exponent_k": model.exponent_k,
                "r0_au": model.r0_au,
                "last_observation": sbdb["orbit"].get("last_obs"),
            }
        )
    return parameters, audit


def active_perturbers(
    model: dict,
    planets: tuple[Perturber, ...],
    small_bodies: tuple[Perturber, ...],
) -> tuple[Perturber, ...]:
    return planets + small_bodies if model["small_bodies"] else planets


def propagate_model_case(
    initial: State,
    start_jd: float,
    target_times: list[float],
    model: dict,
    object_id: str,
    mu_sun: float,
    speed_of_light_au_d: float,
    planets: tuple[Perturber, ...],
    small_bodies: tuple[Perturber, ...],
    non_grav: dict[str, NonGravitationalParameters],
    default_step_days: float,
    scale_factor: float = 1.0,
) -> tuple[list[State], int]:
    perturbers = active_perturbers(model, planets, small_bodies)
    terms = [restricted_n_body_acceleration(mu_sun, perturbers, start_jd)]
    if model["solar_gr"]:
        terms.append(solar_schwarzschild_acceleration(mu_sun, speed_of_light_au_d))
    if model["non_gravitational"] and object_id in non_grav:
        terms.append(non_gravitational_acceleration(non_grav[object_id]))
    acceleration = combine_accelerations(*terms)
    selector = encounter_aware_step_selector(
        perturbers,
        start_jd,
        default_step_days=default_step_days,
        scale_factor=scale_factor,
    )
    return propagate_variable_step(initial, target_times, acceleration, selector)


def evaluate_ablations(
    all_series: dict[str, list[dict]],
    objects: list[dict],
    start_dates: list[str],
    horizons_days: list[int],
    models: list[dict],
    mu_sun: float,
    speed_of_light_au_d: float,
    planets: tuple[Perturber, ...],
    small_bodies: tuple[Perturber, ...],
    non_grav: dict[str, NonGravitationalParameters],
    default_step_days: float,
    au_km: float,
    day_s: float,
) -> tuple[list[dict], dict, dict]:
    records = []
    runtimes = {}
    predictions = {}
    for model in models:
        model_name = model["model"]
        started = time.perf_counter()
        model_steps = 0
        for asteroid in objects:
            object_id = asteroid["id"]
            by_date = indexed_series(all_series[object_id])
            by_jd = {row["epoch_jd_tdb"]: row for row in all_series[object_id]}
            for start_date in start_dates:
                start = by_date[start_date]
                target_times = [float(value) for value in horizons_days]
                predicted_states, steps = propagate_model_case(
                    state_from_row(start),
                    start["epoch_jd_tdb"],
                    target_times,
                    model,
                    object_id,
                    mu_sun,
                    speed_of_light_au_d,
                    planets,
                    small_bodies,
                    non_grav,
                    default_step_days,
                )
                model_steps += steps
                for horizon, prediction in zip(horizons_days, predicted_states):
                    key = (model_name, object_id, start_date, horizon)
                    predictions[key] = prediction
                    target = state_from_row(by_jd[start["epoch_jd_tdb"] + horizon])
                    position_km, velocity_m_s = errors(
                        prediction, target, au_km, day_s
                    )
                    records.append(
                        {
                            "model": model_name,
                            "object_id": object_id,
                            "object_name": asteroid["name"],
                            "regime": asteroid["regime"],
                            "start_date": start_date,
                            "horizon_days": horizon,
                            "position_error_km": position_km,
                            "velocity_error_m_s": velocity_m_s,
                        }
                    )
        runtimes[model_name] = {
            "runtime_seconds": time.perf_counter() - started,
            "rk4_steps": model_steps,
            "force_evaluations": model_steps * 4,
            "point_mass_perturbers": len(
                active_perturbers(model, planets, small_bodies)
            ),
        }

    incremental_shifts = []
    for previous, current in zip(models, models[1:]):
        previous_name = previous["model"]
        current_name = current["model"]
        for asteroid in objects:
            for start_date in start_dates:
                for horizon in horizons_days:
                    position_km, velocity_m_s = errors(
                        predictions[(current_name, asteroid["id"], start_date, horizon)],
                        predictions[(previous_name, asteroid["id"], start_date, horizon)],
                        au_km,
                        day_s,
                    )
                    incremental_shifts.append(
                        {
                            "stage": current_name,
                            "previous_stage": previous_name,
                            "object_id": asteroid["id"],
                            "object_name": asteroid["name"],
                            "start_date": start_date,
                            "horizon_days": horizon,
                            "position_shift_km": position_km,
                            "velocity_shift_m_s": velocity_m_s,
                        }
                    )
    return records, runtimes, {"rows": incremental_shifts, "predictions": predictions}


def aggregate_shifts(rows: list[dict]) -> list[dict]:
    groups = {}
    for row in rows:
        key = (row["stage"], row["previous_stage"], row["horizon_days"])
        groups.setdefault(key, []).append(row)
    output = []
    for (stage, previous, horizon), group in sorted(groups.items()):
        position = [row["position_shift_km"] for row in group]
        velocity = [row["velocity_shift_m_s"] for row in group]
        output.append(
            {
                "stage": stage,
                "previous_stage": previous,
                "horizon_days": horizon,
                "position_shift_median_km": statistics.median(position),
                "position_shift_p95_km": percentile(position, 0.95),
                "velocity_shift_median_m_s": statistics.median(velocity),
            }
        )
    return output


def summarize_event_model(
    model_name: str,
    predictions: list[State],
    truth_states: list[State],
    earth_states: list[State],
    asteroid_rows: list[dict],
    au_km: float,
    day_s: float,
) -> dict:
    position_errors = [
        errors(prediction, truth, au_km, day_s)[0]
        for prediction, truth in zip(predictions, truth_states)
    ]
    velocity_errors = [
        errors(prediction, truth, au_km, day_s)[1]
        for prediction, truth in zip(predictions, truth_states)
    ]
    separations = [
        norm(subtract(prediction.position, earth.position)) * au_km
        for prediction, earth in zip(predictions, earth_states)
    ]
    minimum_index = min(range(len(separations)), key=separations.__getitem__)
    return {
        "model": model_name,
        "closest_approach_epoch_tdb": asteroid_rows[minimum_index]["epoch_tdb"],
        "closest_approach_jd_tdb": asteroid_rows[minimum_index]["epoch_jd_tdb"],
        "closest_approach_distance_km": separations[minimum_index],
        "final_position_error_km": position_errors[-1],
        "max_position_error_km": max(position_errors),
        "final_velocity_error_m_s": velocity_errors[-1],
        "max_velocity_error_m_s": max(velocity_errors),
    }


def evaluate_refined_event(
    root: Path,
    data_config: dict,
    event_config: dict,
    models: list[dict],
    mu_sun: float,
    speed_of_light_au_d: float,
    small_bodies: tuple[Perturber, ...],
    non_grav: dict[str, NonGravitationalParameters],
    default_step_days: float,
    fine_scale: float,
    au_km: float,
    day_s: float,
) -> dict:
    event_id = event_config["event_id"].replace("-", "_")
    asteroid = event_config["asteroid"]
    _, asteroid_rows = parse_horizons(
        root / "data" / "raw" / "horizons_refined" / f"{event_id}_{asteroid['id']}.json",
        asteroid["id"],
        asteroid["name"],
    )
    overrides = {}
    for body in event_config["bodies"]:
        _, rows = parse_horizons(
            root / "data" / "raw" / "horizons_refined" / f"{event_id}_{body['id']}.json",
            body["id"],
            body["name"],
        )
        overrides[body["id"]] = rows
    planets = load_perturbers(root, data_config, overrides)
    start_jd = asteroid_rows[0]["epoch_jd_tdb"]
    target_times = [row["epoch_jd_tdb"] - start_jd for row in asteroid_rows]
    initial = state_from_row(asteroid_rows[0])
    truth_states = [state_from_row(row) for row in asteroid_rows]
    earth_states = [state_from_row(row) for row in overrides["399"]]
    truth = summarize_event_model(
        "Horizons",
        truth_states,
        truth_states,
        earth_states,
        asteroid_rows,
        au_km,
        day_s,
    )
    summaries = []
    predictions_by_model = {}
    step_counts = {}
    for model in models:
        predictions, steps = propagate_model_case(
            initial,
            start_jd,
            target_times,
            model,
            asteroid["id"],
            mu_sun,
            speed_of_light_au_d,
            planets,
            small_bodies,
            non_grav,
            default_step_days,
        )
        predictions_by_model[model["model"]] = predictions
        step_counts[model["model"]] = steps
        summary = summarize_event_model(
            model["model"],
            predictions,
            truth_states,
            earth_states,
            asteroid_rows,
            au_km,
            day_s,
        )
        summary["closest_approach_distance_error_km"] = (
            summary["closest_approach_distance_km"]
            - truth["closest_approach_distance_km"]
        )
        summary["closest_approach_time_error_minutes"] = (
            summary["closest_approach_jd_tdb"] - truth["closest_approach_jd_tdb"]
        ) * 1440.0
        summaries.append(summary)

    full_model = models[-1]
    fine_predictions, fine_steps = propagate_model_case(
        initial,
        start_jd,
        target_times,
        full_model,
        asteroid["id"],
        mu_sun,
        speed_of_light_au_d,
        planets,
        small_bodies,
        non_grav,
        default_step_days,
        fine_scale,
    )
    sensitivity = [
        errors(coarse, fine, au_km, day_s)
        for coarse, fine in zip(
            predictions_by_model[full_model["model"]], fine_predictions
        )
    ]
    return {
        "event_id": event_config["event_id"],
        "start_tdb": asteroid_rows[0]["epoch_tdb"],
        "stop_tdb": asteroid_rows[-1]["epoch_tdb"],
        "cadence_minutes": 5,
        "samples": len(asteroid_rows),
        "truth": truth,
        "models": summaries,
        "step_counts": step_counts,
        "full_model_fine_steps": fine_steps,
        "full_model_step_sensitivity_max_position_km": max(row[0] for row in sensitivity),
        "full_model_step_sensitivity_max_velocity_m_s": max(row[1] for row in sensitivity),
    }


def sensitivity_check(
    cases: list[dict],
    model: dict,
    all_series: dict[str, list[dict]],
    mu_sun: float,
    speed_of_light_au_d: float,
    planets: tuple[Perturber, ...],
    small_bodies: tuple[Perturber, ...],
    non_grav: dict[str, NonGravitationalParameters],
    default_step_days: float,
    fine_scale: float,
    au_km: float,
    day_s: float,
) -> list[dict]:
    output = []
    for case in cases:
        start = indexed_series(all_series[case["object_id"]])[case["start_date"]]
        horizon = float(case["horizon_days"])
        common = (
            state_from_row(start),
            start["epoch_jd_tdb"],
            [horizon],
            model,
            case["object_id"],
            mu_sun,
            speed_of_light_au_d,
            planets,
            small_bodies,
            non_grav,
            default_step_days,
        )
        coarse, coarse_steps = propagate_model_case(*common)
        fine, fine_steps = propagate_model_case(*common, scale_factor=fine_scale)
        position_km, velocity_m_s = errors(coarse[0], fine[0], au_km, day_s)
        output.append(
            {
                **case,
                "model": model["model"],
                "position_difference_km": position_km,
                "velocity_difference_m_s": velocity_m_s,
                "coarse_steps": coarse_steps,
                "fine_steps": fine_steps,
            }
        )
    return output


def base_regression_check(records: list[dict], previous_nbody: dict) -> dict:
    keys = ("model", "object_id", "start_date", "horizon_days")
    previous = {
        tuple(row[key] for key in keys): row for row in previous_nbody["b3_records"]
    }
    current = {
        tuple(row[key] for key in keys): row
        for row in records
        if row["model"] == "B3"
    }
    if set(previous) != set(current):
        raise ValueError("B3 regression records do not have matching keys")
    return {
        "records": len(current),
        "max_position_error_difference_km": max(
            abs(current[key]["position_error_km"] - previous[key]["position_error_km"])
            for key in current
        ),
        "max_velocity_error_difference_m_s": max(
            abs(current[key]["velocity_error_m_s"] - previous[key]["velocity_error_m_s"])
            for key in current
        ),
    }


def build_report(result: dict) -> str:
    lines = [
        "# B3+ small-force ablation — six-object engineering pilot",
        "",
        f"> Generated {result['generated_at_utc']}. This is a teacher-matching ablation, not a frozen historical forecast test.",
        "",
        "## Protocol",
        "",
        "- `B3`: Sun + nine planetary perturbers.",
        "- `B3+GR`: adds the leading solar Schwarzschild 1PN term.",
        "- `B3+GR+SB16`: adds the 16 massive asteroid perturbers associated with SB441-N16.",
        "- `B3+GR+SB16+NG`: adds target-specific nominal Horizons A1/A2/A3 terms where present.",
        "- Every stage is a fully recursive rollout from the same initial Horizons state.",
        "- The GR term is a solar two-body correction, not the full EIH Solar-system formulation.",
        "",
        "## Nominal non-gravitational parameters",
        "",
        "| Object | A1, AU/d² | sigma A1 | A2, AU/d² | sigma A2 | Last observation |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in result["non_gravitational_parameter_audit"]:
        a1_sigma = "—" if row["a1_sigma_au_d2"] is None else f"{row['a1_sigma_au_d2']:.3g}"
        a2_sigma = "—" if row["a2_sigma_au_d2"] is None else f"{row['a2_sigma_au_d2']:.3g}"
        lines.append(
            f"| {row['object_name']} | {row['a1_au_d2']:.6g} | {a1_sigma} | "
            f"{row['a2_au_d2']:.6g} | {a2_sigma} | {row['last_observation']} |"
        )
    lines.extend(
        [
            "",
            "These parameters reproduce the nominal Horizons force header. They are not treated as universally known forecast-time features; notably Apophis A1 has uncertainty comparable to its nominal value.",
            "",
            "## Error by horizon",
            "",
            "| Model | Horizon, d | Position median, km | Position p95, km | Velocity median, m/s |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    model_order = {name: index for index, name in enumerate(result["model_order"])}
    rows = sorted(
        result["aggregate_by_horizon"],
        key=lambda row: (model_order[row["model"]], row["horizon_days"]),
    )
    for row in rows:
        lines.append(
            f"| {row['model']} | {row['horizon_days']} | {row['position_median_km']:,.4g} | "
            f"{row['position_p95_km']:,.4g} | {row['velocity_median_m_s']:,.4g} |"
        )
    lines.extend(
        [
            "",
            "## Incremental trajectory shift caused by each added force",
            "",
            "| Stage | Horizon, d | Shift median, km | Shift p95, km | Velocity shift median, m/s |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in result["aggregate_incremental_shifts"]:
        lines.append(
            f"| {row['previous_stage']} → {row['stage']} | {row['horizon_days']} | "
            f"{row['position_shift_median_km']:,.4g} | {row['position_shift_p95_km']:,.4g} | "
            f"{row['velocity_shift_median_m_s']:,.4g} |"
        )
    lines.extend(
        [
            "",
            "## 365-day position error by object",
            "",
            "| Model | Object | Regime | Median, km | p95, km |",
            "| --- | --- | --- | ---: | ---: |",
        ]
    )
    object_rows = [
        row for row in result["aggregate_by_object_horizon"] if row["horizon_days"] == 365
    ]
    object_rows.sort(key=lambda row: (model_order[row["model"]], row["object_name"]))
    for row in object_rows:
        lines.append(
            f"| {row['model']} | {row['object_name']} | {row['regime']} | "
            f"{row['position_median_km']:,.4g} | {row['position_p95_km']:,.4g} |"
        )
    event = result["refined_event"]
    lines.extend(
        [
            "",
            "## Refined Apophis–Earth 2029 event",
            "",
            f"Horizons five-minute grid minimum: **{event['truth']['closest_approach_distance_km']:,.3f} km** at **{event['truth']['closest_approach_epoch_tdb']} TDB**.",
            "",
            "| Model | Minimum, km | Distance error, km | Time error, min | Final 36 h error, km |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in event["models"]:
        lines.append(
            f"| {row['model']} | {row['closest_approach_distance_km']:,.3f} | "
            f"{row['closest_approach_distance_error_km']:+,.3f} | "
            f"{row['closest_approach_time_error_minutes']:+.1f} | "
            f"{row['final_position_error_km']:,.3f} |"
        )
    lines.extend(
        [
            "",
            "## Numerical sensitivity and cost",
            "",
            "| Model | Runtime, s | RK4 steps | Point-mass perturbers |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for model in result["model_order"]:
        runtime = result["runtime_by_model"][model]
        lines.append(
            f"| {model} | {runtime['runtime_seconds']:.3f} | {runtime['rk4_steps']:,} | "
            f"{runtime['point_mass_perturbers']} |"
        )
    lines.extend(
        [
            "",
            f"The rerun of B3 differs from the stored B3 result by at most {result['base_regression']['max_position_error_difference_km']:.3g} km in reported position error.",
            f"The full-model refined-event step sensitivity is {event['full_model_step_sensitivity_max_position_km']:.3g} km in position.",
            "",
            "## Main findings",
            "",
            "- Solar GR is the dominant missing smooth force: it reduces the 365-day median from 24.95 km to 0.465 km.",
            "- SB16 matters most for main-belt accuracy and reduces the overall 365-day median further to 0.0157 km.",
            "- Nominal A1/A2 reduces the overall 365-day median to 0.0133 km and p95 to 0.0486 km, but the maximum is 1,871.5 km for the post-encounter Apophis case. With only 24 windows, p95 excludes this single extreme value.",
            "- For the Apophis rollout starting 2029-01-01, A1/A2 improves the pre-encounter 90-day error from 0.311 km to 0.00194 km but increases the 365-day post-encounter error from 377.9 km to 1,871.5 km. The flyby amplifies the remaining mismatch, so fitted terms cannot be judged only by aggregate medians.",
            "- The isolated 36-hour event starts immediately before the flyby; GR, SB16 and A1/A2 have almost no time to accumulate there. Its remaining 3.65 km final error points instead to close-encounter model details such as Earth J2 and full EIH relativity.",
            "- SB16 increases runtime from about 46 s to 129 s for the 24 rollout windows, a factor of roughly 2.8 in this standard-library implementation.",
            "",
            "## Guardrails",
            "",
            "- The six objects and all evaluated windows have already been inspected; this remains an engineering regression set.",
            "- Matching Horizons with its nominal fitted A1/A2 values measures model reproduction, not honest historical forecasting.",
            "- Apollo and Phaethon parameters in this snapshot use observations through March/April 2026, so their 2026-01-01 windows are explicitly not forecast-valid.",
            "- Earth J2 is present in the Apophis Horizons header but is not included here because a correct implementation requires an epoch-dependent Earth-pole frame; it must be a separate verified ablation.",
            "- Remaining error may include full EIH relativity, Earth oblateness, additional perturbers, interpolation mismatch and force-model details.",
            "- Closest approach is still a minimum on a five-minute grid, not a continuous optimization or hazard product.",
            "",
            "## Reproduction",
            "",
            "```bash",
            "env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache uv run --no-project --python-preference only-system python src/download_small_perturbers.py --config configs/b3plus_pilot_6.json --root .",
            "env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache uv run --no-project --python-preference only-system python src/run_b3plus_ablation.py --config configs/b3plus_pilot_6.json --root .",
            "```",
            "",
            f"Mass source: [{result['small_body_mass_source']}]({result['small_body_mass_source_url']}). Reference trajectories and force headers: [JPL Horizons](https://ssd.jpl.nasa.gov/horizons/).",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    config = load_json(args.config.resolve())
    data_config = load_json(root / config["data_config"])
    baseline = load_json(root / config["baseline_results"])
    previous_nbody = load_json(root / config["nbody_results"])
    constants = data_config["constants"]
    au_km = constants["au_km"]
    day_s = constants["day_s"]
    mu_sun = constants["mu_sun_km3_s2"] * day_s**2 / au_km**3
    speed_of_light_au_d = config["speed_of_light_km_s"] * day_s / au_km
    all_series = load_asteroid_series(root, data_config)
    planets = load_perturbers(root, data_config)
    small_bodies = load_small_body_perturbers(root, config, mu_sun)
    non_grav, non_grav_audit = load_non_gravitational_parameters(root, data_config)
    records, runtimes, auxiliary = evaluate_ablations(
        all_series,
        data_config["asteroids"],
        baseline["evaluation_start_dates"],
        baseline["horizons_days"],
        config["ablations"],
        mu_sun,
        speed_of_light_au_d,
        planets,
        small_bodies,
        non_grav,
        config["default_step_days"],
        au_km,
        day_s,
    )
    refined_event = evaluate_refined_event(
        root,
        data_config,
        load_json(root / config["event_config"]),
        config["ablations"],
        mu_sun,
        speed_of_light_au_d,
        small_bodies,
        non_grav,
        config["default_step_days"],
        config["sensitivity_scale_factor"],
        au_km,
        day_s,
    )
    sensitivity = sensitivity_check(
        config["sensitivity_cases"],
        config["ablations"][-1],
        all_series,
        mu_sun,
        speed_of_light_au_d,
        planets,
        small_bodies,
        non_grav,
        config["default_step_days"],
        config["sensitivity_scale_factor"],
        au_km,
        day_s,
    )
    result = {
        "schema_version": 1,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "config": args.config.resolve().relative_to(root).as_posix(),
        "model_order": [model["model"] for model in config["ablations"]],
        "small_body_mass_source": config["small_body_mass_source"],
        "small_body_mass_source_url": config["small_body_mass_source_url"],
        "small_body_perturber_count": len(small_bodies),
        "non_gravitational_parameter_audit": non_grav_audit,
        "base_regression": base_regression_check(records, previous_nbody),
        "runtime_by_model": runtimes,
        "step_sensitivity": sensitivity,
        "refined_event": refined_event,
        "evaluation_records": records,
        "incremental_shift_records": auxiliary["rows"],
        "aggregate_by_horizon": aggregate(records, ("model", "horizon_days")),
        "aggregate_by_object_horizon": aggregate(
            records, ("model", "object_name", "regime", "horizon_days")
        ),
        "aggregate_incremental_shifts": aggregate_shifts(auxiliary["rows"]),
    }
    output_path = root / config["artifacts"]["output_json"]
    report_path = root / config["artifacts"]["report"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(build_report(result), encoding="utf-8")
    print(
        json.dumps(
            {
                "base_regression": result["base_regression"],
                "runtime_by_model": runtimes,
                "step_sensitivity": sensitivity,
                "refined_event": refined_event,
                "report": report_path.as_posix(),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Evaluate the explicit heliocentric restricted N-body baseline B3."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import statistics
import time

from orbit_baselines import (
    State,
    norm,
    propagate,
    propagate_variable_step,
    subtract,
    two_body_acceleration,
)
from planetary_dynamics import (
    EphemerisInterpolator,
    Perturber,
    encounter_aware_step_selector,
    restricted_n_body_acceleration,
)
from run_eda import load_json, parse_horizons, percentile
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


def load_perturbers(
    root: Path,
    data_config: dict,
    overrides: dict[str, list[dict]] | None = None,
) -> tuple[Perturber, ...]:
    constants = data_config["constants"]
    conversion = constants["day_s"] ** 2 / constants["au_km"] ** 3
    perturbers = []
    overrides = overrides or {}
    for body in data_config["perturbers"]:
        if body["id"] in overrides:
            rows = overrides[body["id"]]
        else:
            _, rows = parse_horizons(
                root / "data" / "raw" / "horizons" / f"body_{body['id']}.json",
                body["id"],
                body["name"],
            )
        perturbers.append(
            Perturber(
                body["id"],
                body["name"],
                body["mu_km3_s2"] * conversion,
                EphemerisInterpolator.from_rows(rows),
            )
        )
    return tuple(perturbers)


def propagate_case(
    initial: State,
    start_jd: float,
    target_times: list[float],
    mu_sun: float,
    perturbers: tuple[Perturber, ...],
    default_step_days: float,
    scale_factor: float = 1.0,
) -> tuple[list[State], int]:
    acceleration = restricted_n_body_acceleration(mu_sun, perturbers, start_jd)
    selector = encounter_aware_step_selector(
        perturbers,
        start_jd,
        default_step_days=default_step_days,
        scale_factor=scale_factor,
    )
    return propagate_variable_step(initial, target_times, acceleration, selector)


def evaluate_b3(
    all_series: dict[str, list[dict]],
    objects: list[dict],
    start_dates: list[str],
    horizons_days: list[int],
    mu_sun: float,
    perturbers: tuple[Perturber, ...],
    default_step_days: float,
    au_km: float,
    day_s: float,
) -> tuple[list[dict], dict]:
    records = []
    total_steps = 0
    started = time.perf_counter()
    for asteroid in objects:
        object_id = asteroid["id"]
        by_date = indexed_series(all_series[object_id])
        by_jd = {row["epoch_jd_tdb"]: row for row in all_series[object_id]}
        for start_date in start_dates:
            start = by_date[start_date]
            target_times = [float(value) for value in horizons_days]
            predictions, steps = propagate_case(
                state_from_row(start),
                start["epoch_jd_tdb"],
                target_times,
                mu_sun,
                perturbers,
                default_step_days,
            )
            total_steps += steps
            for horizon, prediction in zip(horizons_days, predictions):
                target = state_from_row(by_jd[start["epoch_jd_tdb"] + horizon])
                position_km, velocity_m_s = errors(prediction, target, au_km, day_s)
                records.append(
                    {
                        "model": "B3",
                        "object_id": object_id,
                        "object_name": asteroid["name"],
                        "regime": asteroid["regime"],
                        "start_date": start_date,
                        "horizon_days": horizon,
                        "position_error_km": position_km,
                        "velocity_error_m_s": velocity_m_s,
                    }
                )
    runtime = time.perf_counter() - started
    return records, {
        "runtime_seconds": runtime,
        "rk4_steps": total_steps,
        "force_evaluations": total_steps * 4,
        "perturbers_per_force_evaluation": len(perturbers),
    }


def sensitivity_check(
    cases: list[dict],
    all_series: dict[str, list[dict]],
    mu_sun: float,
    perturbers: tuple[Perturber, ...],
    default_step_days: float,
    fine_scale: float,
    au_km: float,
    day_s: float,
) -> list[dict]:
    output = []
    for case in cases:
        object_id = case["object_id"]
        start = indexed_series(all_series[object_id])[case["start_date"]]
        horizon = float(case["horizon_days"])
        coarse, coarse_steps = propagate_case(
            state_from_row(start),
            start["epoch_jd_tdb"],
            [horizon],
            mu_sun,
            perturbers,
            default_step_days,
        )
        fine, fine_steps = propagate_case(
            state_from_row(start),
            start["epoch_jd_tdb"],
            [horizon],
            mu_sun,
            perturbers,
            default_step_days,
            fine_scale,
        )
        position_km, velocity_m_s = errors(coarse[0], fine[0], au_km, day_s)
        output.append(
            {
                **case,
                "position_difference_km": position_km,
                "velocity_difference_m_s": velocity_m_s,
                "coarse_steps": coarse_steps,
                "fine_steps": fine_steps,
            }
        )
    return output


def evaluate_refined_event(
    root: Path,
    data_config: dict,
    event_config: dict,
    mu_sun: float,
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
    body_rows = {}
    for body in event_config["bodies"]:
        _, rows = parse_horizons(
            root / "data" / "raw" / "horizons_refined" / f"{event_id}_{body['id']}.json",
            body["id"],
            body["name"],
        )
        body_rows[body["id"]] = rows
    earth_rows = body_rows["399"]
    start_jd = asteroid_rows[0]["epoch_jd_tdb"]
    target_times = [row["epoch_jd_tdb"] - start_jd for row in asteroid_rows]
    initial = state_from_row(asteroid_rows[0])
    event_perturbers = load_perturbers(root, data_config, body_rows)
    b3, b3_steps = propagate_case(
        initial,
        start_jd,
        target_times,
        mu_sun,
        event_perturbers,
        default_step_days,
    )
    b3_fine, b3_fine_steps = propagate_case(
        initial,
        start_jd,
        target_times,
        mu_sun,
        event_perturbers,
        default_step_days,
        fine_scale,
    )
    b2 = propagate(
        initial,
        target_times,
        two_body_acceleration(mu_sun),
        max_step_days=5.0 / 1440.0,
    )

    truth_states = [state_from_row(row) for row in asteroid_rows]
    earth_states = [state_from_row(row) for row in earth_rows]

    def summarize(model: str, predictions: list[State]) -> dict:
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
            "model": model,
            "closest_approach_epoch_tdb": asteroid_rows[minimum_index]["epoch_tdb"],
            "closest_approach_jd_tdb": asteroid_rows[minimum_index]["epoch_jd_tdb"],
            "closest_approach_distance_km": separations[minimum_index],
            "final_position_error_km": position_errors[-1],
            "max_position_error_km": max(position_errors),
            "final_velocity_error_m_s": velocity_errors[-1],
            "max_velocity_error_m_s": max(velocity_errors),
        }

    truth_summary = summarize("Horizons", truth_states)
    b2_summary = summarize("B2", b2)
    b3_summary = summarize("B3", b3)
    for summary in (b2_summary, b3_summary):
        summary["closest_approach_distance_error_km"] = (
            summary["closest_approach_distance_km"]
            - truth_summary["closest_approach_distance_km"]
        )
        summary["closest_approach_time_error_minutes"] = (
            summary["closest_approach_jd_tdb"]
            - truth_summary["closest_approach_jd_tdb"]
        ) * 1440.0
    b3_fine_difference = [
        errors(coarse, fine, au_km, day_s)
        for coarse, fine in zip(b3, b3_fine)
    ]
    return {
        "event_id": event_config["event_id"],
        "start_tdb": asteroid_rows[0]["epoch_tdb"],
        "stop_tdb": asteroid_rows[-1]["epoch_tdb"],
        "cadence_minutes": 5,
        "samples": len(asteroid_rows),
        "truth": truth_summary,
        "models": [b2_summary, b3_summary],
        "b3_steps": b3_steps,
        "b3_fine_steps": b3_fine_steps,
        "b3_step_sensitivity_max_position_km": max(row[0] for row in b3_fine_difference),
        "b3_step_sensitivity_max_velocity_m_s": max(row[1] for row in b3_fine_difference),
    }


def build_report(result: dict) -> str:
    lines = [
        "# B3 restricted N-body engineering baseline — six-object pilot",
        "",
        f"> Generated {result['generated_at_utc']}. This is an engineering comparison, not a frozen final test.",
        "",
        "## Force model",
        "",
        "- Sun plus nine configured perturbers; asteroids are massless test particles.",
        "- Earth and Moon are separate; the Earth–Moon barycenter is not added.",
        "- Planetary forces include direct and indirect heliocentric terms.",
        "- Cubic-Hermite interpolation uses the daily JPL body positions and velocities.",
        "- RK4 steps shrink near the Sun and close planetary approaches.",
        "",
        "## B2 versus B3 by horizon",
        "",
        "| Model | Horizon, d | Position median, km | Position p95, km | Velocity median, m/s |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in result["aggregate_by_horizon"]:
        if row["model"] in {"B2", "B3"}:
            lines.append(
                f"| {row['model']} | {row['horizon_days']} | {row['position_median_km']:,.3g} | "
                f"{row['position_p95_km']:,.3g} | {row['velocity_median_m_s']:,.3g} |"
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
    for row in result["aggregate_by_object_horizon"]:
        if row["model"] in {"B2", "B3"} and row["horizon_days"] == 365:
            lines.append(
                f"| {row['model']} | {row['object_name']} | {row['regime']} | "
                f"{row['position_median_km']:,.3g} | {row['position_p95_km']:,.3g} |"
            )
    lines.extend(
        [
            "",
            "## Step-sensitivity cases",
            "",
            "| Object | Start | Horizon, d | Position difference, km | Velocity difference, m/s | Steps coarse/fine |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    object_names = result["object_names"]
    for row in result["step_sensitivity"]:
        lines.append(
            f"| {object_names[row['object_id']]} | {row['start_date']} | {row['horizon_days']} | "
            f"{row['position_difference_km']:.3g} | {row['velocity_difference_m_s']:.3g} | "
            f"{row['coarse_steps']:,}/{row['fine_steps']:,} |"
        )
    runtime = result["runtime"]
    refined = result["refined_event"]
    lines.extend(
        [
            "",
            "## Refined Apophis–Earth 2029 event",
            "",
            f"Horizons grid minimum: **{refined['truth']['closest_approach_distance_km']:,.3f} km** at **{refined['truth']['closest_approach_epoch_tdb']} TDB**.",
            "",
            "| Model | Minimum, km | Distance error, km | Time error, min | Final position error, km |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in refined["models"]:
        lines.append(
            f"| {row['model']} | {row['closest_approach_distance_km']:,.3f} | "
            f"{row['closest_approach_distance_error_km']:+,.3f} | "
            f"{row['closest_approach_time_error_minutes']:+.1f} | "
            f"{row['final_position_error_km']:,.3f} |"
        )
    lines.extend(
        [
            "",
            f"B3 event step-sensitivity maximum is {refined['b3_step_sensitivity_max_position_km']:.3g} km in position and {refined['b3_step_sensitivity_max_velocity_m_s']:.3g} m/s in velocity.",
            "",
            "## Cost",
            "",
            f"B3 evaluation used {runtime['rk4_steps']:,} RK4 steps and {runtime['force_evaluations']:,} force evaluations in {runtime['runtime_seconds']:.3f} seconds.",
            "",
            "## Guardrails",
            "",
            "- Horizons remains richer than this planets-only B3 because it includes additional small perturbers and, for some asteroids, fitted non-gravitational terms.",
            "- The six-object set has already been inspected and is not a final test.",
            "- Closest-approach values remain five-minute grid minima, not continuous optimizations or operational hazard products.",
            "",
            "## Reproduction",
            "",
            "```bash",
            "env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache uv run --no-project --python-preference only-system python src/run_nbody_baseline.py --config configs/nbody_pilot_6.json --root .",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    config = load_json(args.config.resolve())
    data_config = load_json(root / config["data_config"])
    baseline = load_json(root / config["baseline_results"])
    constants = data_config["constants"]
    au_km = constants["au_km"]
    day_s = constants["day_s"]
    mu_sun = constants["mu_sun_km3_s2"] * day_s**2 / au_km**3
    all_series = load_asteroid_series(root, data_config)
    perturbers = load_perturbers(root, data_config)
    b3_records, runtime = evaluate_b3(
        all_series,
        data_config["asteroids"],
        baseline["evaluation_start_dates"],
        baseline["horizons_days"],
        mu_sun,
        perturbers,
        config["default_step_days"],
        au_km,
        day_s,
    )
    sensitivity = sensitivity_check(
        config["sensitivity_cases"],
        all_series,
        mu_sun,
        perturbers,
        config["default_step_days"],
        config["sensitivity_scale_factor"],
        au_km,
        day_s,
    )
    refined_event = evaluate_refined_event(
        root,
        data_config,
        load_json(root / config["event_config"]),
        mu_sun,
        config["default_step_days"],
        config["sensitivity_scale_factor"],
        au_km,
        day_s,
    )
    all_records = baseline["evaluation_records"] + b3_records
    result = {
        "schema_version": 1,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "config": args.config.resolve().relative_to(root).as_posix(),
        "data_config": config["data_config"],
        "baseline_results": config["baseline_results"],
        "object_names": {row["id"]: row["name"] for row in data_config["asteroids"]},
        "runtime": runtime,
        "step_sensitivity": sensitivity,
        "refined_event": refined_event,
        "b3_records": b3_records,
        "aggregate_by_horizon": aggregate(all_records, ("model", "horizon_days")),
        "aggregate_by_object_horizon": aggregate(
            all_records, ("model", "object_name", "regime", "horizon_days")
        ),
    }
    output_path = root / config["artifacts"]["output_json"]
    report_path = root / config["artifacts"]["report"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.write_text(build_report(result), encoding="utf-8")
    print(
        json.dumps(
            {
                "runtime": runtime,
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

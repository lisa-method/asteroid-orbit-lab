"""Run dependency-free B0/B1/B2 engineering baselines on the six-object pilot."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path
import statistics
import time

from orbit_baselines import (
    State,
    TrajectoryWindow,
    constant_velocity,
    fit_central_law,
    norm,
    propagate,
    subtract,
    central_acceleration,
    trajectory_loss,
    two_body_acceleration,
)
from run_eda import extract_sbdb, load_json, parse_horizons, percentile


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    return parser.parse_args()


def state_from_row(row: dict) -> State:
    return State(tuple(row["r"]), tuple(row["v"]))  # type: ignore[arg-type]


def load_asteroid_series(root: Path, data_config: dict) -> dict[str, list[dict]]:
    series = {}
    for asteroid in data_config["asteroids"]:
        _, rows = parse_horizons(
            root / "data" / "raw" / "horizons" / f"asteroid_{asteroid['id']}.json",
            asteroid["id"],
            asteroid["name"],
        )
        series[asteroid["id"]] = rows
    return series


def indexed_series(series: list[dict]) -> dict[str, dict]:
    return {row["epoch_tdb"][:10]: row for row in series}


def build_windows(
    all_series: dict[str, list[dict]],
    object_ids: list[str],
    start_dates: list[str],
    horizons_days: list[int],
) -> list[TrajectoryWindow]:
    windows = []
    for object_id in object_ids:
        by_date = indexed_series(all_series[object_id])
        for start_date in start_dates:
            if start_date not in by_date:
                raise ValueError(f"Missing start date {start_date} for object {object_id}")
            start = by_date[start_date]
            targets = []
            for horizon in horizons_days:
                target_jd = start["epoch_jd_tdb"] + horizon
                target = next(
                    (row for row in all_series[object_id] if row["epoch_jd_tdb"] == target_jd),
                    None,
                )
                if target is None:
                    raise ValueError(f"Missing +{horizon} d target from {start_date} for {object_id}")
                targets.append(state_from_row(target))
            windows.append(
                TrajectoryWindow(
                    object_id=object_id,
                    initial_state=state_from_row(start),
                    target_times_days=tuple(float(value) for value in horizons_days),
                    target_states=tuple(targets),
                )
            )
    return windows


def perihelion_state(semi_major_axis_au: float, eccentricity: float, inclination_deg: float, mu: float) -> State:
    radius = semi_major_axis_au * (1.0 - eccentricity)
    speed = math.sqrt(mu * (1.0 + eccentricity) / radius)
    inclination = math.radians(inclination_deg)
    return State(
        (radius, 0.0, 0.0),
        (0.0, speed * math.cos(inclination), speed * math.sin(inclination)),
    )


def synthetic_windows(
    root: Path,
    data_config: dict,
    object_ids: list[str],
    horizons_days: list[int],
    mu_sun: float,
    truth_step: float,
) -> list[TrajectoryWindow]:
    definitions = {row["id"]: row for row in data_config["asteroids"]}
    windows = []
    for object_id in object_ids:
        metadata = extract_sbdb(
            root / "data" / "raw" / "sbdb" / f"object_{object_id}.json",
            definitions[object_id]["name"],
        )
        initial = perihelion_state(
            metadata["a_au"], metadata["e"], metadata["i_ecliptic_deg"], mu_sun
        )
        targets = propagate(
            initial,
            horizons_days,
            two_body_acceleration(mu_sun),
            max_step_days=truth_step,
        )
        windows.append(
            TrajectoryWindow(
                object_id=object_id,
                initial_state=initial,
                target_times_days=tuple(float(value) for value in horizons_days),
                target_states=tuple(targets),
            )
        )
    return windows


def errors(predicted: State, target: State, au_km: float, day_s: float) -> tuple[float, float]:
    position_km = norm(subtract(predicted.position, target.position)) * au_km
    velocity_m_s = norm(subtract(predicted.velocity, target.velocity)) * au_km * 1000.0 / day_s
    return position_km, velocity_m_s


def evaluate_models(
    all_series: dict[str, list[dict]],
    objects: list[dict],
    start_dates: list[str],
    horizons_days: list[int],
    mu_sun: float,
    fitted_a_ref: float,
    fitted_exponent: float,
    max_step_days: float,
    au_km: float,
    day_s: float,
) -> tuple[list[dict], dict[str, float]]:
    records = []
    runtimes = {"B0": 0.0, "B1": 0.0, "B2": 0.0}
    for asteroid in objects:
        object_id = asteroid["id"]
        by_date = indexed_series(all_series[object_id])
        by_jd = {row["epoch_jd_tdb"]: row for row in all_series[object_id]}
        for start_date in start_dates:
            start_row = by_date[start_date]
            initial = state_from_row(start_row)
            target_times = [float(value) for value in horizons_days]
            target_states = [
                state_from_row(by_jd[start_row["epoch_jd_tdb"] + horizon])
                for horizon in horizons_days
            ]
            started = time.perf_counter()
            predictions = constant_velocity(initial, target_times)
            runtimes["B0"] += time.perf_counter() - started
            model_predictions = {"B0": predictions}

            started = time.perf_counter()
            model_predictions["B1"] = propagate(
                initial,
                target_times,
                central_acceleration(fitted_a_ref, fitted_exponent),
                max_step_days=max_step_days,
            )
            runtimes["B1"] += time.perf_counter() - started

            started = time.perf_counter()
            model_predictions["B2"] = propagate(
                initial,
                target_times,
                two_body_acceleration(mu_sun),
                max_step_days=max_step_days,
            )
            runtimes["B2"] += time.perf_counter() - started

            for model, predictions in model_predictions.items():
                for horizon, predicted, target in zip(horizons_days, predictions, target_states):
                    position_km, velocity_m_s = errors(predicted, target, au_km, day_s)
                    records.append(
                        {
                            "model": model,
                            "object_id": object_id,
                            "object_name": asteroid["name"],
                            "regime": asteroid["regime"],
                            "start_date": start_date,
                            "horizon_days": horizon,
                            "position_error_km": position_km,
                            "velocity_error_m_s": velocity_m_s,
                        }
                    )
    return records, runtimes


def aggregate(records: list[dict], keys: tuple[str, ...]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for row in records:
        groups.setdefault(tuple(row[key] for key in keys), []).append(row)
    output = []
    for key_values, rows in sorted(groups.items()):
        position = [row["position_error_km"] for row in rows]
        velocity = [row["velocity_error_m_s"] for row in rows]
        result = {key: value for key, value in zip(keys, key_values)}
        result.update(
            {
                "samples": len(rows),
                "position_median_km": statistics.median(position),
                "position_p95_km": percentile(position, 0.95),
                "velocity_median_m_s": statistics.median(velocity),
                "velocity_p95_m_s": percentile(velocity, 0.95),
            }
        )
        output.append(result)
    return output


def integration_step_sensitivity(
    all_series: dict[str, list[dict]],
    objects: list[dict],
    start_dates: list[str],
    horizons_days: list[int],
    mu_sun: float,
    coarse_step_days: float,
    au_km: float,
) -> dict:
    records = []
    acceleration = two_body_acceleration(mu_sun)
    for asteroid in objects:
        object_id = asteroid["id"]
        by_date = indexed_series(all_series[object_id])
        for start_date in start_dates:
            initial = state_from_row(by_date[start_date])
            coarse = propagate(
                initial, horizons_days, acceleration, max_step_days=coarse_step_days
            )
            fine = propagate(
                initial, horizons_days, acceleration, max_step_days=coarse_step_days / 2.0
            )
            for horizon, left, right in zip(horizons_days, coarse, fine):
                records.append(
                    {
                        "object_id": object_id,
                        "object_name": asteroid["name"],
                        "start_date": start_date,
                        "horizon_days": horizon,
                        "position_difference_km": norm(
                            subtract(left.position, right.position)
                        )
                        * au_km,
                    }
                )
    differences_km = [row["position_difference_km"] for row in records]
    worst = max(records, key=lambda row: row["position_difference_km"])
    return {
        "coarse_step_days": coarse_step_days,
        "fine_step_days": coarse_step_days / 2.0,
        "samples": len(differences_km),
        "position_difference_median_km": statistics.median(differences_km),
        "position_difference_p95_km": percentile(differences_km, 0.95),
        "position_difference_max_km": max(differences_km),
        "worst_case": worst,
    }


def build_report(result: dict) -> str:
    synthetic = result["synthetic_recovery"]
    real = result["real_recovery"]
    lines = [
        "# B0/B1/B2 engineering baseline — six-object pilot",
        "",
        f"> Generated {result['generated_at_utc']}. This is an engineering comparison, not a frozen final test.",
        "",
        "## Protocol",
        "",
        f"- Fit objects: {', '.join(real['fit_object_names'])}.",
        f"- Fit starts: {', '.join(real['fit_start_dates'])}.",
        f"- Evaluation starts: {', '.join(result['evaluation_start_dates'])}.",
        f"- Horizons: {', '.join(str(value) for value in result['horizons_days'])} days.",
        f"- Integrator: fixed-step RK4; fit step up to {result['fit_max_step_days']} day and evaluation step up to {result['evaluation_max_step_days']} day.",
        "- B1 fits whole recursive trajectories; no numerical differentiation of positions is used.",
        "",
        "## Synthetic recovery gate",
        "",
        "| Parameter | Truth | Recovered | Relative/absolute error |",
        "| --- | ---: | ---: | ---: |",
        f"| Reference acceleration at 1 AU, AU/day² | {synthetic['truth_a_ref_au_d2']:.12g} | {synthetic['fitted_a_ref_au_d2']:.12g} | {synthetic['a_ref_relative_error']:.3g} |",
        f"| Exponent n | 2 | {synthetic['fitted_exponent']:.8f} | {synthetic['exponent_absolute_error']:.3g} |",
        "",
        f"Gate status: **{'PASS' if synthetic['passed'] else 'FAIL'}**. The synthetic target uses a finer integration step than the fitted model.",
        "",
        "## Recovered law on JPL trajectories",
        "",
        f"- Reference acceleration at 1 AU: **{real['fitted_a_ref_au_d2']:.12g} AU/day²**.",
        f"- Exponent: **n = {real['fitted_exponent']:.8f}**.",
        f"- Difference from inverse-square exponent: **{real['exponent_minus_two']:+.6g}**.",
        f"- Reference-amplitude difference from the fixed solar value: **{real['a_ref_relative_difference']:+.3%}**.",
        "",
        "This point estimate absorbs planetary, small-body and any fitted non-gravitational effects present in Horizons. It is a system-identification sanity check, not a new measurement of the solar gravitational parameter.",
        "",
        "## Object-level stability",
        "",
        "| Held-out fit object | Recovered n from other two | Reference-amplitude difference | Held-out trajectory loss |",
        "| --- | ---: | ---: | ---: |",
    ]
    for row in real["leave_one_object_out"]:
        lines.append(
            f"| {row['held_out_object_name']} | {row['fitted_exponent']:.8f} | "
            f"{row['a_ref_relative_difference']:+.3%} | {row['held_out_loss']:.3g} |"
        )
    sensitivity = result["integration_step_sensitivity"]
    lines.extend(
        [
            "",
            "## Integrator step sensitivity",
            "",
            f"Comparing B2 at {sensitivity['coarse_step_days']} and {sensitivity['fine_step_days']} day steps gives a median position difference of **{sensitivity['position_difference_median_km']:.3g} km**, p95 **{sensitivity['position_difference_p95_km']:.3g} km**, and maximum **{sensitivity['position_difference_max_km']:.3g} km** ({sensitivity['worst_case']['object_name']}, start {sensitivity['worst_case']['start_date']}, horizon {sensitivity['worst_case']['horizon_days']} days).",
            "",
        "## Evaluation by horizon",
        "",
        "| Model | Horizon, d | Position median, km | Position p95, km | Velocity median, m/s |",
        "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in result["aggregate_by_horizon"]:
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
        if row["horizon_days"] == 365:
            lines.append(
                f"| {row['model']} | {row['object_name']} | {row['regime']} | "
                f"{row['position_median_km']:,.3g} | {row['position_p95_km']:,.3g} |"
            )
    lines.extend(
        [
            "",
            "## Interpretation guardrails",
            "",
            "- The six objects are an inspected engineering set; these numbers are not final generalization estimates.",
            "- A lower B1/B2 error does not yet establish an adaptive advantage; B3 restricted N-body is the next required reference.",
            "- Apophis rollouts that cross the 2029 encounter are a stress diagnostic, not an operational hazard calculation.",
            "",
            "## Reproduction",
            "",
            "```bash",
            "env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache uv run --no-project --python-preference only-system python src/run_physics_baselines.py --config configs/baseline_pilot_6.json --root .",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    config = load_json(args.config.resolve())
    data_config_path = root / config["data_config"]
    data_config = load_json(data_config_path)
    horizons_days = config["horizons_days"]
    fit_ids = config["law_fit_object_ids"]
    fit_dates = config["law_fit_start_dates"]
    evaluation_dates = config["evaluation_start_dates"]
    fit_step = config["integrator"]["fit_max_step_days"]
    evaluation_step = config["integrator"]["evaluation_max_step_days"]
    truth_step = config["integrator"]["synthetic_truth_max_step_days"]
    fit_config = config["central_law_fit"]
    constants = data_config["constants"]
    au_km = constants["au_km"]
    day_s = constants["day_s"]
    mu_sun = constants["mu_sun_km3_s2"] * day_s**2 / au_km**3
    object_lookup = {row["id"]: row for row in data_config["asteroids"]}
    if any(not object_lookup[object_id].get("law_fit") for object_id in fit_ids):
        raise ValueError("All fit objects must be predeclared with law_fit=true")

    all_series = load_asteroid_series(root, data_config)
    synthetic = synthetic_windows(root, data_config, fit_ids, horizons_days, mu_sun, truth_step)
    started = time.perf_counter()
    synthetic_fit = fit_central_law(
        synthetic,
        reference_acceleration_bounds=tuple(fit_config["reference_acceleration_bounds_au_d2"]),
        exponent_bounds=tuple(fit_config["exponent_bounds"]),
        max_step_days=fit_step,
        iterations=fit_config["iterations"],
    )
    synthetic_runtime = time.perf_counter() - started
    synthetic_a_error = abs(synthetic_fit.reference_acceleration_au_d2 / mu_sun - 1.0)
    synthetic_n_error = abs(synthetic_fit.exponent - 2.0)
    synthetic_passed = synthetic_a_error < 1.0e-3 and synthetic_n_error < 1.0e-2
    if not synthetic_passed:
        raise RuntimeError(
            f"Synthetic recovery gate failed: a_ref error={synthetic_a_error}, n error={synthetic_n_error}"
        )

    real_windows = build_windows(all_series, fit_ids, fit_dates, horizons_days)
    started = time.perf_counter()
    real_fit = fit_central_law(
        real_windows,
        reference_acceleration_bounds=tuple(fit_config["reference_acceleration_bounds_au_d2"]),
        exponent_bounds=tuple(fit_config["exponent_bounds"]),
        max_step_days=fit_step,
        iterations=fit_config["iterations"],
    )
    real_runtime = time.perf_counter() - started

    leave_one_out = []
    for held_out_id in fit_ids:
        training_ids = [object_id for object_id in fit_ids if object_id != held_out_id]
        training_windows = build_windows(all_series, training_ids, fit_dates, horizons_days)
        held_out_windows = build_windows(all_series, [held_out_id], fit_dates, horizons_days)
        started = time.perf_counter()
        fit = fit_central_law(
            training_windows,
            reference_acceleration_bounds=tuple(fit_config["reference_acceleration_bounds_au_d2"]),
            exponent_bounds=tuple(fit_config["exponent_bounds"]),
            max_step_days=fit_step,
            iterations=fit_config["stability_iterations"],
        )
        runtime = time.perf_counter() - started
        held_out_loss = trajectory_loss(
            held_out_windows,
            fit.reference_acceleration_au_d2,
            fit.exponent,
            max_step_days=fit_step,
        )
        leave_one_out.append(
            {
                "held_out_object_id": held_out_id,
                "held_out_object_name": object_lookup[held_out_id]["name"],
                "training_object_ids": training_ids,
                "fitted_a_ref_au_d2": fit.reference_acceleration_au_d2,
                "fitted_exponent": fit.exponent,
                "a_ref_relative_difference": fit.reference_acceleration_au_d2 / mu_sun - 1.0,
                "training_loss": fit.loss,
                "held_out_loss": held_out_loss,
                "evaluations": fit.evaluations,
                "runtime_seconds": runtime,
            }
        )

    records, runtimes = evaluate_models(
        all_series,
        data_config["asteroids"],
        evaluation_dates,
        horizons_days,
        mu_sun,
        real_fit.reference_acceleration_au_d2,
        real_fit.exponent,
        evaluation_step,
        au_km,
        day_s,
    )
    sensitivity = integration_step_sensitivity(
        all_series,
        data_config["asteroids"],
        evaluation_dates,
        horizons_days,
        mu_sun,
        evaluation_step,
        au_km,
    )
    result = {
        "schema_version": 1,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "config": args.config.resolve().relative_to(root).as_posix(),
        "data_config": data_config_path.relative_to(root).as_posix(),
        "horizons_days": horizons_days,
        "evaluation_start_dates": evaluation_dates,
        "fit_max_step_days": fit_step,
        "evaluation_max_step_days": evaluation_step,
        "synthetic_recovery": {
            "truth_a_ref_au_d2": mu_sun,
            "fitted_a_ref_au_d2": synthetic_fit.reference_acceleration_au_d2,
            "fitted_exponent": synthetic_fit.exponent,
            "a_ref_relative_error": synthetic_a_error,
            "exponent_absolute_error": synthetic_n_error,
            "loss": synthetic_fit.loss,
            "evaluations": synthetic_fit.evaluations,
            "runtime_seconds": synthetic_runtime,
            "passed": synthetic_passed,
        },
        "real_recovery": {
            "fit_object_ids": fit_ids,
            "fit_object_names": [object_lookup[object_id]["name"] for object_id in fit_ids],
            "fit_start_dates": fit_dates,
            "fitted_a_ref_au_d2": real_fit.reference_acceleration_au_d2,
            "fitted_exponent": real_fit.exponent,
            "exponent_minus_two": real_fit.exponent - 2.0,
            "a_ref_relative_difference": real_fit.reference_acceleration_au_d2 / mu_sun - 1.0,
            "loss": real_fit.loss,
            "evaluations": real_fit.evaluations,
            "runtime_seconds": real_runtime,
            "leave_one_object_out": leave_one_out,
        },
        "integration_step_sensitivity": sensitivity,
        "evaluation_runtimes_seconds": runtimes,
        "evaluation_records": records,
        "aggregate_by_horizon": aggregate(records, ("model", "horizon_days")),
        "aggregate_by_object_horizon": aggregate(
            records, ("model", "object_name", "regime", "horizon_days")
        ),
    }
    output_path = root / config["artifacts"]["output_json"]
    report_path = root / config["artifacts"]["report"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(build_report(result), encoding="utf-8")
    print(
        json.dumps(
            {
                "synthetic_recovery": result["synthetic_recovery"],
                "real_recovery": result["real_recovery"],
                "report": report_path.as_posix(),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

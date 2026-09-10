"""Audit fixed-step B2 RK4 against a solar-distance adaptive step policy.

The audit intentionally keeps the force model fixed to the B2 two-body law.  It
therefore measures integration error and cost, rather than mixing numerical and
force-model differences.  All comparisons are made at the same daily output
times, while each propagator is free to use its own internal steps.
"""

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
    norm,
    propagate,
    propagate_variable_step,
    subtract,
    two_body_acceleration,
)
from planetary_dynamics import encounter_aware_step_selector
from run_eda import load_json, percentile
from run_physics_baselines import indexed_series, load_asteroid_series, state_from_row


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    return parser.parse_args()


def distance_errors(
    left: State, right: State, au_km: float, day_s: float
) -> tuple[float, float]:
    """Return position difference in km and velocity difference in m/s."""

    return (
        norm(subtract(left.position, right.position)) * au_km,
        norm(subtract(left.velocity, right.velocity)) * au_km * 1000.0 / day_s,
    )


def daily_targets(max_horizon_days: int) -> list[float]:
    if max_horizon_days < 1:
        raise ValueError("max_horizon_days must be positive")
    return [float(day) for day in range(max_horizon_days + 1)]


def recommend_variable_policy(cases: list[dict], scales: list[float], budget_km: float) -> dict:
    """Require a distinct finer run; the reference cannot validate itself."""
    eligible = []
    finest = min(scales)
    for scale in scales:
        if scale == finest:
            continue
        rows = [case["variable"][f"scale_{scale:g}"] for case in cases]
        difference = max(row["vs_finest_variable"]["max_position_difference_km"] for row in rows)
        if difference <= budget_km:
            eligible.append((statistics.median(row["rk4_steps"] for row in rows), scale, difference,
                             statistics.median(row["runtime_seconds"] for row in rows)))
    if not eligible:
        return {"scale_factor": None, "basis": "no independently compared tested policy met budget; refine further"}
    steps, scale, difference, runtime = min(eligible)
    return {"scale_factor": scale, "max_position_difference_km": difference,
            "median_rk4_steps": steps, "median_runtime_seconds": runtime,
            "basis": "fewest steps among policies meeting the empirical budget against a distinct finer run"}


def fixed_policy(
    initial: State,
    target_times: list[float],
    acceleration,
    step_days: float,
) -> tuple[list[State], int]:
    """Run the historical fixed-step RK4 policy and return an estimated count."""

    predictions = propagate(
        initial, target_times, acceleration, max_step_days=step_days
    )
    # ``propagate`` does not expose its count.  Since daily targets are sorted,
    # this is exactly the number of RK4 steps used by its implementation.
    steps = sum(max(0, math.ceil(duration / step_days)) for duration in _durations(target_times))
    return predictions, steps


def _durations(target_times: list[float]) -> list[float]:
    previous = 0.0
    durations = []
    for target in target_times:
        durations.append(target - previous)
        previous = target
    return durations


def variable_policy(
    initial: State,
    start_jd: float,
    target_times: list[float],
    acceleration,
    default_step_days: float,
    scale_factor: float,
) -> tuple[list[State], int]:
    """Run the existing solar-distance selector with no planetary perturbers."""

    selector = encounter_aware_step_selector(
        (),
        start_jd,
        default_step_days=default_step_days,
        scale_factor=scale_factor,
    )
    return propagate_variable_step(initial, target_times, acceleration, selector)


def grid_difference(
    left: list[State], right: list[State], au_km: float, day_s: float
) -> dict:
    if len(left) != len(right):
        raise ValueError("Trajectories must have equal grid lengths")
    rows = [distance_errors(a, b, au_km, day_s) for a, b in zip(left, right)]
    positions = [row[0] for row in rows]
    velocities = [row[1] for row in rows]
    worst_position = max(range(len(rows)), key=lambda index: positions[index])
    worst_velocity = max(range(len(rows)), key=lambda index: velocities[index])
    return {
        "max_position_difference_km": positions[worst_position],
        "max_position_difference_day": worst_position,
        "max_velocity_difference_m_s": velocities[worst_velocity],
        "max_velocity_difference_day": worst_velocity,
        "final_position_difference_km": positions[-1],
        "final_velocity_difference_m_s": velocities[-1],
    }


def _aggregate(rows: list[dict], key: str) -> dict:
    values = [row[key] for row in rows]
    return {
        "count": len(values),
        "median": statistics.median(values),
        "p95": percentile(values, 0.95),
        "maximum": max(values),
    }


def empirical_order(
    coarse: float, fine: float, *, scale_ratio: float = 2.0
) -> float | None:
    """Estimate an order from successive differences; this is diagnostic only."""

    if coarse <= 0.0 or fine <= 0.0 or scale_ratio <= 1.0:
        return None
    return math.log(coarse / fine, scale_ratio)


def audit_case(
    *,
    object_id: str,
    object_name: str,
    start_date: str,
    start_row: dict,
    target_times: list[float],
    acceleration,
    fixed_step_days: float,
    fine_fixed_step_days: float,
    variable_scale_factors: list[float],
    au_km: float,
    day_s: float,
) -> dict:
    initial = state_from_row(start_row)
    started = time.perf_counter()
    fixed, fixed_steps = fixed_policy(
        initial, target_times, acceleration, fixed_step_days
    )
    fixed_runtime = time.perf_counter() - started
    started = time.perf_counter()
    fine_fixed, fine_steps = fixed_policy(
        initial, target_times, acceleration, fine_fixed_step_days
    )
    fine_runtime = time.perf_counter() - started
    result = {
        "object_id": object_id,
        "object_name": object_name,
        "start_date": start_date,
        "grid_days": len(target_times) - 1,
        "fixed": {"step_days": fixed_step_days, "rk4_steps": fixed_steps, "runtime_seconds": fixed_runtime},
        "fine_fixed": {
            "step_days": fine_fixed_step_days,
            "rk4_steps": fine_steps,
            "runtime_seconds": fine_runtime,
        },
        "fixed_vs_fine_fixed": grid_difference(fixed, fine_fixed, au_km, day_s),
        "variable": {},
        "variable_pairwise": {},
    }
    variable_states = {}
    for scale in variable_scale_factors:
        started = time.perf_counter()
        predictions, steps = variable_policy(
            initial,
            start_row["epoch_jd_tdb"],
            target_times,
            acceleration,
            fixed_step_days,
            scale,
        )
        runtime = time.perf_counter() - started
        label = f"scale_{scale:g}"
        variable_states[label] = predictions
        result["variable"][label] = {
            "scale_factor": scale,
            "rk4_steps": steps,
            "runtime_seconds": runtime,
            "vs_fine_fixed": grid_difference(predictions, fine_fixed, au_km, day_s),
        }
    for left_scale, right_scale in zip(variable_scale_factors, variable_scale_factors[1:]):
        left_label = f"scale_{left_scale:g}"
        right_label = f"scale_{right_scale:g}"
        result["variable_pairwise"][f"{left_label}_vs_{right_label}"] = grid_difference(
            variable_states[left_label], variable_states[right_label], au_km, day_s
        )
    finest_label = f"scale_{variable_scale_factors[-1]:g}"
    for scale in variable_scale_factors:
        label = f"scale_{scale:g}"
        result["variable"][label]["vs_finest_variable"] = grid_difference(
            variable_states[label], variable_states[finest_label], au_km, day_s
        )
    return result


def build_report(result: dict) -> str:
    lines = [
        "# B2 RK4 numerical audit — six-object pilot",
        "",
        f"> Generated {result['generated_at_utc']}. This is an empirical step-sensitivity audit, not a rigorous truncation-error bound.",
        "",
        "## Protocol",
        "",
        f"- Force model: fixed solar two-body acceleration (`mu_sun`) for every policy.",
        f"- Output grid: daily samples from day 0 through day {result['max_horizon_days']} for {len(result['cases'])} object/start windows.",
        f"- Historical B2 policy: fixed RK4 step up to {result['fixed_step_days']} day.",
        f"- Comparison reference: fixed RK4 step up to {result['fine_fixed_step_days']} day.",
        "- Variable policies use the existing solar-distance `encounter_aware_step_selector` with an empty perturber tuple and the listed scale factors.",
        "- Differences are measured at the common daily grid; step halving is treated as empirical convergence evidence only.",
        "",
        "## Aggregate differences against the fine fixed reference",
        "",
        "| Policy | Grid position median, km | Grid position p95, km | Grid position max, km | Grid velocity max, m/s | Median RK4 steps | Median runtime, s |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    fixed_rows = [case["fixed_vs_fine_fixed"] for case in result["cases"]]
    lines.append(
        f"| fixed {result['fixed_step_days']:g} d | {statistics.median([r['max_position_difference_km'] for r in fixed_rows]):.6g} | "
        f"{percentile([r['max_position_difference_km'] for r in fixed_rows], 0.95):.6g} | "
        f"{max(r['max_position_difference_km'] for r in fixed_rows):.6g} | "
        f"{max(r['max_velocity_difference_m_s'] for r in fixed_rows):.6g} | "
        f"{statistics.median([case['fixed']['rk4_steps'] for case in result['cases']]):.0f} | "
        f"{statistics.median([case['fixed']['runtime_seconds'] for case in result['cases']]):.6g} |"
    )
    for label in result["variable_scale_factors"]:
        rows = [case["variable"][f"scale_{label:g}"]["vs_fine_fixed"] for case in result["cases"]]
        steps = [case["variable"][f"scale_{label:g}"]["rk4_steps"] for case in result["cases"]]
        lines.append(
            f"| variable scale {label:g} | {statistics.median([r['max_position_difference_km'] for r in rows]):.6g} | "
            f"{percentile([r['max_position_difference_km'] for r in rows], 0.95):.6g} | "
            f"{max(r['max_position_difference_km'] for r in rows):.6g} | "
            f"{max(r['max_velocity_difference_m_s'] for r in rows):.6g} | {statistics.median(steps):.0f} | "
            f"{statistics.median([case['variable'][f'scale_{label:g}']['runtime_seconds'] for case in result['cases']]):.6g} |"
        )
    lines.extend(
        [
            "",
            "## Worst grid differences by case",
            "",
            "| Object | Start | Historical fixed vs fine, km | Variable scale 1 vs fine, km | Variable scale 0.5 vs fine, km | Variable scale 0.25 vs fine, km |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for case in result["cases"]:
        values = []
        for scale in result["variable_scale_factors"][:3]:
            values.append(case["variable"][f"scale_{scale:g}"]["vs_fine_fixed"]["max_position_difference_km"])
        while len(values) < 3:
            values.append(float("nan"))
        lines.append(
            f"| {case['object_name']} | {case['start_date']} | {case['fixed_vs_fine_fixed']['max_position_difference_km']:.6g} | "
            + " | ".join(f"{value:.6g}" for value in values)
            + " |"
        )
    lines.extend(["", "## Empirical convergence", "", "| Pair | Median max position shift, km | Max shift, km | Median empirical order |", "| --- | ---: | ---: | ---: |"])
    for pair, rows in result["pairwise_summary"].items():
        shifts = [row["max_position_difference_km"] for row in rows]
        orders = [row["empirical_order_position"] for row in rows if row["empirical_order_position"] is not None and math.isfinite(row["empirical_order_position"])]
        lines.append(f"| {pair} | {statistics.median(shifts):.6g} | {max(shifts):.6g} | {statistics.median(orders):.4g} |" if orders else f"| {pair} | {statistics.median(shifts):.6g} | {max(shifts):.6g} | — |")
    recommendation = result["recommendation"]
    recommendation_text = (
        "No production policy met the empirical budget against a distinct finer run; further refinement is required."
        if recommendation["scale_factor"] is None else
        f"The tested variable policy with fewest RK4 steps satisfying the provisional {result['provisional_position_budget_km'] * 1000:g} m grid budget against a distinct finest run is **scale {recommendation['scale_factor']:g}**. Its worst difference is {recommendation['max_position_difference_km']:.6g} km, median RK4 count is {recommendation['median_rk4_steps']:.0f}, and median runtime is {recommendation['median_runtime_seconds']:.6g} s per window."
    )
    lines.extend(
        [
            "",
            "## Provisional policy",
            "",
            recommendation_text,
            "This recommendation is conditional on the six-object engineering set and the two-body force model. It should be rechecked after adding planetary perturbers and encounter windows; the observed differences are convergence diagnostics, not a formal error bound.",
            "",
        ]
    )
    return "\n".join(lines)


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
    objects_by_id = {row["id"]: row for row in data_config["asteroids"]}
    object_ids = config.get("object_ids", [row["id"] for row in data_config["asteroids"]])
    start_dates = config.get("start_dates", baseline["evaluation_start_dates"])
    target_times = daily_targets(int(config["max_horizon_days"]))
    acceleration = two_body_acceleration(mu_sun)
    cases = []
    for object_id in object_ids:
        object_config = objects_by_id[object_id]
        by_date = indexed_series(all_series[object_id])
        for start_date in start_dates:
            cases.append(
                audit_case(
                    object_id=object_id,
                    object_name=object_config["name"],
                    start_date=start_date,
                    start_row=by_date[start_date],
                    target_times=target_times,
                    acceleration=acceleration,
                    fixed_step_days=float(config["fixed_step_days"]),
                    fine_fixed_step_days=float(config["fine_fixed_step_days"]),
                    variable_scale_factors=[float(value) for value in config["variable_scale_factors"]],
                    au_km=au_km,
                    day_s=day_s,
                )
            )
    variable_scales = [float(value) for value in config["variable_scale_factors"]]
    pairwise_summary = {}
    for left_scale, right_scale in zip(variable_scales, variable_scales[1:]):
        pair = f"scale_{left_scale:g}_vs_scale_{right_scale:g}"
        rows = []
        for case in cases:
            difference = case["variable_pairwise"][pair]
            rows.append(
                {
                    **difference,
                    "object_id": case["object_id"],
                    "start_date": case["start_date"],
                    "empirical_order_position": empirical_order(
                        difference["max_position_difference_km"],
                        case["variable_pairwise"].get(
                            f"scale_{right_scale:g}_vs_scale_{variable_scales[variable_scales.index(right_scale) + 1]:g}",
                            {},
                        ).get("max_position_difference_km", float("nan")),
                    )
                    if variable_scales.index(right_scale) + 1 < len(variable_scales)
                    else None,
                }
            )
        pairwise_summary[pair] = rows
    budget_km = float(config.get("provisional_position_budget_km", 0.01))
    recommendation = recommend_variable_policy(cases, variable_scales, budget_km)
    policy_summary = {
        "fixed": {
            "rk4_steps_median": statistics.median([case["fixed"]["rk4_steps"] for case in cases]),
            "runtime_seconds_median": statistics.median([case["fixed"]["runtime_seconds"] for case in cases]),
            "max_position_difference_km": max(case["fixed_vs_fine_fixed"]["max_position_difference_km"] for case in cases),
        },
        "fine_fixed": {
            "rk4_steps_median": statistics.median([case["fine_fixed"]["rk4_steps"] for case in cases]),
            "runtime_seconds_median": statistics.median([case["fine_fixed"]["runtime_seconds"] for case in cases]),
        },
    }
    for scale in variable_scales:
        label = f"scale_{scale:g}"
        policy_summary[label] = {
            "rk4_steps_median": statistics.median([case["variable"][label]["rk4_steps"] for case in cases]),
            "runtime_seconds_median": statistics.median([case["variable"][label]["runtime_seconds"] for case in cases]),
            "max_position_difference_vs_fine_fixed_km": max(case["variable"][label]["vs_fine_fixed"]["max_position_difference_km"] for case in cases),
            "max_position_difference_vs_finest_variable_km": max(case["variable"][label]["vs_finest_variable"]["max_position_difference_km"] for case in cases),
        }
    result = {
        "schema_version": 1,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "config": args.config.resolve().relative_to(root).as_posix(),
        "data_config": config["data_config"],
        "object_ids": object_ids,
        "start_dates": start_dates,
        "max_horizon_days": int(config["max_horizon_days"]),
        "fixed_step_days": float(config["fixed_step_days"]),
        "fine_fixed_step_days": float(config["fine_fixed_step_days"]),
        "variable_scale_factors": variable_scales,
        "provisional_position_budget_km": budget_km,
        "cases": cases,
        "pairwise_summary": pairwise_summary,
        "recommendation": recommendation,
        "policy_summary": policy_summary,
    }
    output_json = root / config["artifacts"]["output_json"]
    report_path = root / config["artifacts"]["report"]
    output_json.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(build_report(result), encoding="utf-8")
    print(json.dumps({"output_json": output_json.as_posix(), "report": report_path.as_posix(), "recommendation": result["recommendation"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

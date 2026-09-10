"""Audit Earth J2 and Earth/Moon ephemeris interpolation on pilot6 cases.

This runner is deliberately a small, reproducible force-model ablation.  It
keeps the existing GR+SB16 model fixed, evaluates both nominal non-gravitational
branches, and compares daily-only interpolation with daily rows augmented only
inside the stored 36-hour Apophis refinement.  The output is an audit artifact,
not an operational closest-approach solution.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import time

from earth_oblateness import earth_j2_acceleration
from orbit_baselines import (
    Acceleration,
    State,
    norm,
    propagate_variable_step,
    subtract,
)
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
from run_b3plus_ablation import load_non_gravitational_parameters, load_small_body_perturbers
from run_eda import load_json, parse_horizons
from run_nbody_baseline import load_perturbers
from run_physics_baselines import errors, load_asteroid_series, state_from_row


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument(
        "--short-only",
        action="store_true",
        help="Evaluate only the stored 36-hour Apophis event.",
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path("outputs/earth_j2"),
        help="Directory for pilot6_results.json and pilot6_report.md.",
    )
    return parser.parse_args()


def merge_ephemeris_rows(daily_rows: list[dict], refined_rows: list[dict]) -> list[dict]:
    """Merge rows by JD, letting the stored high-cadence row replace a daily row."""

    by_epoch = {row["epoch_jd_tdb"]: row for row in daily_rows}
    by_epoch.update({row["epoch_jd_tdb"]: row for row in refined_rows})
    return [by_epoch[epoch] for epoch in sorted(by_epoch)]


def _event_rows(root: Path, event_config: dict) -> dict[str, list[dict]]:
    event_id = event_config["event_id"].replace("-", "_")
    rows_by_id: dict[str, list[dict]] = {}
    targets = [event_config["asteroid"], *event_config["bodies"]]
    for target in targets:
        _, rows = parse_horizons(
            root / "data" / "raw" / "horizons_refined" / f"{event_id}_{target['id']}.json",
            target["id"],
            target["name"],
        )
        rows_by_id[target["id"]] = rows
    return rows_by_id


def _state_rows_for_long_window(
    daily_rows: list[dict],
    refined_rows: list[dict],
    start_jd: float,
    horizon_days: int,
) -> list[dict]:
    merged = merge_ephemeris_rows(daily_rows, refined_rows)
    stop_jd = start_jd + horizon_days
    selected = [row for row in merged if start_jd <= row["epoch_jd_tdb"] <= stop_jd]
    if not selected or selected[0]["epoch_jd_tdb"] != start_jd:
        raise ValueError("Long-window asteroid grid is missing its exact start row")
    if selected[-1]["epoch_jd_tdb"] != stop_jd:
        raise ValueError("Long-window asteroid grid is missing its exact endpoint row")
    return selected


def _earth_and_moon_rows(
    root: Path,
    data_config: dict,
    refined_rows: dict[str, list[dict]],
) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    daily: dict[str, list[dict]] = {}
    merged: dict[str, list[dict]] = {}
    configured = {body["id"]: body for body in data_config["perturbers"]}
    for body_id in ("399", "301"):
        body = configured[body_id]
        _, daily_rows = parse_horizons(
            root / "data" / "raw" / "horizons" / f"body_{body_id}.json",
            body_id,
            body["name"],
        )
        daily[body_id] = daily_rows
        merged[body_id] = merge_ephemeris_rows(daily_rows, refined_rows.get(body_id, []))
    return daily, merged


def _variant_acceleration(
    model_name: str,
    start_jd: float,
    model_perturbers: tuple[Perturber, ...],
    earth: Perturber,
    non_grav: dict[str, NonGravitationalParameters],
    object_id: str,
    mu_sun: float,
    speed_of_light_au_d: float,
    reference_radius_au: float,
    j2: float,
    pole_model: str | None,
) -> Acceleration:
    terms: list[Acceleration] = [
        restricted_n_body_acceleration(mu_sun, model_perturbers, start_jd),
        solar_schwarzschild_acceleration(mu_sun, speed_of_light_au_d),
    ]
    if model_name.endswith("+NG") and object_id in non_grav:
        terms.append(non_gravitational_acceleration(non_grav[object_id]))
    if pole_model is not None:
        terms.append(
            earth_j2_acceleration(
                earth,
                start_jd,
                reference_radius_au,
                j2,
                pole_model=pole_model,
            )
        )
    return combine_accelerations(*terms)


def _run_variant(
    initial: State,
    start_jd: float,
    target_times: list[float],
    model_name: str,
    model_perturbers: tuple[Perturber, ...],
    earth: Perturber,
    non_grav: dict[str, NonGravitationalParameters],
    object_id: str,
    mu_sun: float,
    speed_of_light_au_d: float,
    reference_radius_au: float,
    j2: float,
    pole_model: str | None,
    default_step_days: float,
    step_scale: float,
) -> tuple[list[State], dict]:
    started = time.perf_counter()
    acceleration = _variant_acceleration(
        model_name,
        start_jd,
        model_perturbers,
        earth,
        non_grav,
        object_id,
        mu_sun,
        speed_of_light_au_d,
        reference_radius_au,
        j2,
        pole_model,
    )
    selector = encounter_aware_step_selector(
        model_perturbers,
        start_jd,
        default_step_days=default_step_days,
        scale_factor=step_scale,
    )
    predictions, steps = propagate_variable_step(initial, target_times, acceleration, selector)
    return predictions, {
        "runtime_seconds": time.perf_counter() - started,
        "rk4_steps": steps,
        "force_evaluations": steps * 4,
        "step_scale": step_scale,
        "point_mass_perturbers": len(model_perturbers),
    }


def _grid_summary(
    predictions: list[State],
    truth: list[State],
    earth_truth: list[State],
    target_rows: list[dict],
    au_km: float,
    day_s: float,
) -> dict:
    position_errors = [errors(predicted, actual, au_km, day_s)[0] for predicted, actual in zip(predictions, truth)]
    velocity_errors = [errors(predicted, actual, au_km, day_s)[1] for predicted, actual in zip(predictions, truth)]
    separations = [
        norm(subtract(predicted.position, earth.position)) * au_km
        for predicted, earth in zip(predictions, earth_truth)
    ]
    closest_index = min(range(len(separations)), key=separations.__getitem__)
    reference_closest = _truth_closest_grid(truth, earth_truth, target_rows, au_km)
    return {
        "max_grid_position_error_km": max(position_errors),
        "max_grid_position_error_time_tdb": target_rows[position_errors.index(max(position_errors))]["epoch_tdb"],
        "endpoint_position_error_km": position_errors[-1],
        "endpoint_velocity_error_m_s": velocity_errors[-1],
        "max_grid_velocity_error_m_s": max(velocity_errors),
        "closest_grid_min_distance_km": separations[closest_index],
        "closest_grid_min_time_tdb": target_rows[closest_index]["epoch_tdb"],
        "closest_grid_min_jd_tdb": target_rows[closest_index]["epoch_jd_tdb"],
        "closest_grid_min_distance_error_km": separations[closest_index] - reference_closest["closest_grid_min_distance_km"],
        "closest_grid_min_time_error_s": (target_rows[closest_index]["epoch_jd_tdb"] - reference_closest["closest_grid_min_jd_tdb"]) * day_s,
    }


def _shift_summary(
    left: list[State],
    right: list[State],
    au_km: float,
    day_s: float,
) -> dict:
    position = [norm(subtract(a.position, b.position)) * au_km for a, b in zip(left, right)]
    velocity = [norm(subtract(a.velocity, b.velocity)) * au_km * 1000.0 / day_s for a, b in zip(left, right)]
    return {
        "max_position_shift_km": max(position),
        "endpoint_position_shift_km": position[-1],
        "max_velocity_shift_m_s": max(velocity),
        "endpoint_velocity_shift_m_s": velocity[-1],
    }


def _truth_closest_grid(
    truth: list[State],
    earth_truth: list[State],
    target_rows: list[dict],
    au_km: float,
) -> dict:
    distances = [norm(subtract(state.position, earth.position)) * au_km for state, earth in zip(truth, earth_truth)]
    index = min(range(len(distances)), key=distances.__getitem__)
    return {
        "closest_grid_min_distance_km": distances[index],
        "closest_grid_min_time_tdb": target_rows[index]["epoch_tdb"],
        "closest_grid_min_jd_tdb": target_rows[index]["epoch_jd_tdb"],
    }


def _variant_plan() -> list[dict]:
    plan = []
    for branch in ("baseGR+SB16", "baseGR+SB16+NG"):
        for interpolation in ("daily", "merged"):
            plan.append({"variant_id": f"{branch}:{interpolation}", "branch": branch, "interpolation": interpolation, "pole_model": None})
        for pole_model in ("fixed_j2000", "iau"):
            plan.append({"variant_id": f"{branch}:merged+J2{pole_model}", "branch": branch, "interpolation": "merged", "pole_model": pole_model})
    return plan


def evaluate_window(
    window_id: str,
    target_rows: list[dict],
    daily_planets: tuple[Perturber, ...],
    merged_planets: tuple[Perturber, ...],
    non_grav: dict[str, NonGravitationalParameters],
    mu_sun: float,
    speed_of_light_au_d: float,
    default_step_days: float,
    step_scale: float,
    fine_scale: float,
    reference_radius_au: float,
    j2: float,
    au_km: float,
    day_s: float,
) -> dict:
    object_id = target_rows[0]["target_id"]
    start_jd = target_rows[0]["epoch_jd_tdb"]
    target_times = [row["epoch_jd_tdb"] - start_jd for row in target_rows]
    initial = state_from_row(target_rows[0])
    truth = [state_from_row(row) for row in target_rows]
    merged_earth = next(body for body in merged_planets if body.body_id == "399")
    daily_earth = next(body for body in daily_planets if body.body_id == "399")
    earth_truth = [merged_earth.ephemeris.state_at(row["epoch_jd_tdb"]) for row in target_rows]
    truth_closest = _truth_closest_grid(truth, earth_truth, target_rows, au_km)

    variants = []
    predictions: dict[str, list[State]] = {}
    runtimes: dict[str, dict] = {}
    for spec in _variant_plan():
        planets = daily_planets if spec["interpolation"] == "daily" else merged_planets
        earth = daily_earth if spec["interpolation"] == "daily" else merged_earth
        predicted, runtime = _run_variant(
            initial,
            start_jd,
            target_times,
            spec["branch"],
            planets,
            earth,
            non_grav,
            object_id,
            mu_sun,
            speed_of_light_au_d,
            reference_radius_au,
            j2,
            spec["pole_model"],
            default_step_days,
            step_scale,
        )
        variant = {
            **spec,
            **runtime,
            **_grid_summary(predicted, truth, earth_truth, target_rows, au_km, day_s),
        }
        variants.append(variant)
        predictions[spec["variant_id"]] = predicted
        runtimes[spec["variant_id"]] = runtime
        print(json.dumps({"window": window_id, "completed": spec["variant_id"], "endpoint_error_km": variant["endpoint_position_error_km"]}), flush=True)

    shifts_daily_merged = []
    shifts_frozen_iau = []
    for branch in ("baseGR+SB16", "baseGR+SB16+NG"):
        shifts_daily_merged.append(
            {
                "branch": branch,
                **_shift_summary(
                    predictions[f"{branch}:daily"],
                    predictions[f"{branch}:merged"],
                    au_km,
                    day_s,
                ),
            }
        )
        shifts_frozen_iau.append(
            {
                "branch": branch,
                **_shift_summary(
                    predictions[f"{branch}:merged+J2fixed_j2000"],
                    predictions[f"{branch}:merged+J2iau"],
                    au_km,
                    day_s,
                ),
            }
        )

    numerical_sensitivity = []
    for branch in ("baseGR+SB16", "baseGR+SB16+NG"):
        spec = next(item for item in _variant_plan() if item["variant_id"] == f"{branch}:merged+J2iau")
        fine_predictions, fine_runtime = _run_variant(
            initial,
            start_jd,
            target_times,
            branch,
            merged_planets,
            merged_earth,
            non_grav,
            object_id,
            mu_sun,
            speed_of_light_au_d,
            reference_radius_au,
            j2,
            "iau",
            default_step_days,
            fine_scale,
        )
        numerical_sensitivity.append(
            {
                "variant_id": spec["variant_id"],
                "coarse_step_scale": step_scale,
                "fine_step_scale": fine_scale,
                "coarse_runtime_seconds": runtimes[spec["variant_id"]]["runtime_seconds"],
                "coarse_rk4_steps": runtimes[spec["variant_id"]]["rk4_steps"],
                "fine_runtime_seconds": fine_runtime["runtime_seconds"],
                "fine_rk4_steps": fine_runtime["rk4_steps"],
                **_shift_summary(predictions[spec["variant_id"]], fine_predictions, au_km, day_s),
            }
        )
        print(json.dumps({"window": window_id, "fine_check_completed": branch}), flush=True)

    return {
        "window_id": window_id,
        "object_id": object_id,
        "start_tdb": target_rows[0]["epoch_tdb"],
        "stop_tdb": target_rows[-1]["epoch_tdb"],
        "samples": len(target_rows),
        "grid": {
            "contains_daily_backbone": window_id == "apophis_2029_long365d",
            "contains_stored_refined_rows": window_id == "apophis_earth_2029_refined36h" or window_id == "apophis_2029_long365d",
            "high_cadence_scope": "stored Apophis-Earth 36-hour window only",
        },
        "truth_closest_grid": truth_closest,
        "variants": variants,
        "shifts_daily_vs_merged": shifts_daily_merged,
        "shifts_frozen_j2000_vs_iau": shifts_frozen_iau,
        "numerical_sensitivity_j2iau": numerical_sensitivity,
    }


def _report(result: dict) -> str:
    lines = [
        "# Earth J2 audit — pilot6",
        "",
        f"> Generated {result['generated_at_utc']}. This is a force-model and interpolation audit on the engineering pilot.",
        "",
        "## Scope and conventions",
        "",
        "- Base force is solar GR + the 16 SB441-N16 massive asteroid perturbers plus the nine configured planetary perturbers.",
        "- Both no-NG and nominal Horizons NG matching branches are evaluated; NG is not a primary selector feature.",
        "- Daily-only interpolation uses the daily body rows. Merged interpolation replaces only Earth/Moon rows inside the stored 36-hour refinement and uses daily rows outside it.",
        "- Earth J2 uses the supplied IERS 2010 J2 and equatorial reference radius with either a fixed J2000 pole or the approximate IAU mean-pole model.",
        "- Closest-approach values are sampled grid minima, not continuous optimization or an operational hazard product.",
        "",
        "## Constants and provenance",
        "",
        f"- J2: `{result['constants']['j2']}`; reference radius: `{result['constants']['reference_radius_km']} km` (`{result['constants']['reference_radius_au']:.12g} AU`).",
        f"- Pole source: [NAIF text PCK](https://naif.jpl.nasa.gov/pub/naif/toolkit_docs/C/req/pck.html); this is an approximate IAU mean pole in TDB, without nutation/EOP or high-precision ITRF orientation.",
        f"- Radius source: [{result['constants']['radius_source_url']}]({result['constants']['radius_source_url']}).",
        "- Reference states are stored Horizons model-derived ephemerides; results should be read as propagation/model matching diagnostics.",
        "",
    ]
    for window in result["windows"]:
        lines.extend(
            [
                f"## {window['window_id']}",
                "",
                f"Grid: {window['samples']} samples from {window['start_tdb']} to {window['stop_tdb']} TDB; reference grid minimum is {window['truth_closest_grid']['closest_grid_min_distance_km']:.3f} km at {window['truth_closest_grid']['closest_grid_min_time_tdb']}.",
                "",
                "| Variant | Max position error, km | Endpoint error, km | Grid min, km | Runtime, s | RK4 steps |",
                "| --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for variant in window["variants"]:
            lines.append(
                f"| {variant['variant_id']} | {variant['max_grid_position_error_km']:.6g} | {variant['endpoint_position_error_km']:.6g} | {variant['closest_grid_min_distance_km']:.6g} | {variant['runtime_seconds']:.3f} | {variant['rk4_steps']:,} |"
            )
        lines.extend(["", "### Interpolation and pole shifts", "", "| Comparison | Branch | Max position shift, km | Endpoint shift, km |", "| --- | --- | ---: | ---: |"])
        for row in window["shifts_daily_vs_merged"]:
            lines.append(f"| daily → merged | {row['branch']} | {row['max_position_shift_km']:.6g} | {row['endpoint_position_shift_km']:.6g} |")
        for row in window["shifts_frozen_j2000_vs_iau"]:
            lines.append(f"| fixed J2000 → IAU | {row['branch']} | {row['max_position_shift_km']:.6g} | {row['endpoint_position_shift_km']:.6g} |")
        lines.extend(["", "### IAU pole half-step sensitivity", "", "| Branch variant | Max position difference, km | Endpoint difference, km | Coarse/fine RK4 steps |", "| --- | ---: | ---: | ---: |"])
        for row in window["numerical_sensitivity_j2iau"]:
            lines.append(f"| {row['variant_id']} | {row['max_position_shift_km']:.6g} | {row['endpoint_position_shift_km']:.6g} | {row['coarse_rk4_steps']:,}/{row['fine_rk4_steps']:,} |")
        lines.append("")
    lines.extend(
        [
            "## Limitations",
            "",
            "The pole convention is an approximate mean-pole text-PCK model and does not reproduce full Earth orientation, precession-nutation, EOP, or exact Horizons force/rotation conventions. Earth and Moon interpolation is cubic Hermite over the supplied rows. The high-cadence rows are used only in the existing 36-hour window; the long case retains its daily 2029 backbone elsewhere. Numerical half-step differences are empirical sensitivity checks rather than rigorous integration error bounds.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    config = load_json(args.config.resolve())
    data_config = load_json(root / config["data_config"])
    force_config = load_json(root / config["force_config"])
    event_config = load_json(root / config["event_config"])
    constants = data_config["constants"]
    au_km = constants["au_km"]
    day_s = constants["day_s"]
    mu_sun = constants["mu_sun_km3_s2"] * day_s**2 / au_km**3
    speed_of_light_au_d = force_config["speed_of_light_km_s"] * day_s / au_km
    earth_j2 = config["earth_j2"]
    reference_radius_au = earth_j2["reference_radius_km"] / au_km
    all_series = load_asteroid_series(root, data_config)
    refined = _event_rows(root, event_config)
    _daily_body_rows, merged_body_rows = _earth_and_moon_rows(root, data_config, refined)
    daily_planets = load_perturbers(root, data_config)
    merged_planets = load_perturbers(root, data_config, merged_body_rows)
    small_bodies = load_small_body_perturbers(root, force_config, mu_sun)
    daily_planets = daily_planets + small_bodies
    merged_planets = merged_planets + small_bodies
    non_grav, _non_grav_audit = load_non_gravitational_parameters(root, data_config)

    short_rows = refined[event_config["asteroid"]["id"]]
    windows = [
        (
            "apophis_earth_2029_refined36h",
            short_rows,
        )
    ]
    if not args.short_only:
        start_row = next(row for row in all_series["99942"] if row["epoch_tdb"][:10] == "2029-01-01")
        long_rows = _state_rows_for_long_window(
            all_series["99942"],
            short_rows,
            start_row["epoch_jd_tdb"],
            365,
        )
        windows.append(("apophis_2029_long365d", long_rows))

    evaluated = []
    for window_id, target_rows in windows:
        evaluated.append(
            evaluate_window(
                window_id,
                target_rows,
                daily_planets,
                merged_planets,
                non_grav,
                mu_sun,
                speed_of_light_au_d,
                config["default_step_days"],
                config["step_scale"],
                config["step_scale"] * config["sensitivity_scale_factor"],
                reference_radius_au,
                earth_j2["j2"],
                au_km,
                day_s,
            )
        )

    result = {
        "schema_version": 1,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "config": args.config.resolve().relative_to(root).as_posix(),
        "data_config": config["data_config"],
        "force_config": config["force_config"],
        "event_config": config["event_config"],
        "short_only": args.short_only,
        "config_snapshot": config,
        "source_sha256": {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [args.config.resolve(), *sorted((root / "src").glob("*.py")), *sorted((root / "data/checksums").glob("*.json"))]
        },
        "constants": {
            "j2": earth_j2["j2"],
            "reference_radius_km": earth_j2["reference_radius_km"],
            "reference_radius_au": reference_radius_au,
            "radius_source_url": earth_j2["radius_source_url"],
            "pole_source_url": earth_j2["pole_source_url"],
            "pole_model_default": earth_j2["pole_model"],
            "time_scale": "TDB",
            "coordinate_convention": data_config["coordinates"],
        },
        "protocol": config["protocol"],
        "windows": evaluated,
    }
    output_directory = args.output_directory
    if not output_directory.is_absolute():
        output_directory = root / output_directory
    output_directory.mkdir(parents=True, exist_ok=True)
    output_json = output_directory / "pilot6_results.json"
    report_path = output_directory / "pilot6_report.md"
    output_json.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    report_path.write_text(_report(result), encoding="utf-8")
    print(json.dumps({"output_json": output_json.as_posix(), "report": report_path.as_posix(), "windows": [row["window_id"] for row in evaluated]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

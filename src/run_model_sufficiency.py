"""Sampled trajectory/cost benchmark on the inspected engineering cohort.

No learned selector is trained here. Timing at each horizon is the measured
causal prefix of an instrumented rollout; all later output times are unused
until the current prefix has completed. Fine-step runs are offline diagnostics.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import platform
from pathlib import Path
import statistics
import sys
import time

from earth_oblateness import earth_j2_acceleration
from orbit_baselines import State, norm, propagate_variable_step, subtract
from planetary_dynamics import (
    combine_accelerations, encounter_aware_step_selector,
    restricted_n_body_acceleration, solar_schwarzschild_acceleration,
)
from run_b3plus_ablation import load_small_body_perturbers
from run_eda import load_json, parse_horizons
from run_nbody_baseline import load_perturbers
from run_physics_baselines import load_asteroid_series, state_from_row
from trajectory_metrics import trajectory_error_rows, summarize_error_rows, offline_oracle


def merge_rows(daily: list[dict], refined: list[dict]) -> list[dict]:
    """Replace only coincident daily nodes; retain coverage outside refinement."""
    by_epoch = {row["epoch_jd_tdb"]: row for row in daily}
    by_epoch.update({row["epoch_jd_tdb"]: row for row in refined})
    return [by_epoch[epoch] for epoch in sorted(by_epoch)]


def load_context(root: Path, config: dict) -> dict:
    started = time.perf_counter()
    data_config = load_json(root / config["data_config"])
    force_config = load_json(root / config["force_config"])
    event_config = load_json(root / config["event_config"])
    constants = data_config["constants"]
    au_km, day_s = constants["au_km"], constants["day_s"]
    mu = constants["mu_sun_km3_s2"] * day_s**2 / au_km**3
    series = load_asteroid_series(root, data_config)
    event_slug = event_config["event_id"].replace("-", "_")
    obj = event_config["asteroid"]
    _, refined = parse_horizons(
        root / "data/raw/horizons_refined" / f"{event_slug}_{obj['id']}.json",
        obj["id"], obj["name"],
    )
    series[obj["id"]] = merge_rows(series[obj["id"]], refined)
    overrides = {}
    for body in event_config["bodies"]:
        _, daily = parse_horizons(root / "data/raw/horizons" / f"body_{body['id']}.json", body["id"], body["name"])
        _, high = parse_horizons(root / "data/raw/horizons_refined" / f"{event_slug}_{body['id']}.json", body["id"], body["name"])
        overrides[body["id"]] = merge_rows(daily, high)
    planets = load_perturbers(root, data_config, overrides)
    small = load_small_body_perturbers(root, force_config, mu)
    return {
        "data_config": data_config, "series": series, "planets": planets,
        "small": small, "mu": mu, "au_km": au_km, "day_s": day_s,
        "c_au_d": force_config["speed_of_light_km_s"] * day_s / au_km,
        "load_seconds": time.perf_counter() - started,
        "refined_start_jd": refined[0]["epoch_jd_tdb"],
        "refined_stop_jd": refined[-1]["epoch_jd_tdb"],
    }


def propagate_candidate(initial: State, start_jd: float, targets: list[float], model: dict,
                        context: dict, config: dict, step_scale: float) -> tuple[list[State], dict]:
    started = time.perf_counter()
    bodies = context["planets"] if model["planets"] else ()
    if model["small_bodies"]:
        bodies += context["small"]
    terms = [restricted_n_body_acceleration(context["mu"], bodies, start_jd)]
    if model["solar_gr"]:
        terms.append(solar_schwarzschild_acceleration(context["mu"], context["c_au_d"]))
    if model["earth_j2"]:
        earth = next(body for body in context["planets"] if body.body_id == "399")
        j2 = config["earth_j2"]
        terms.append(earth_j2_acceleration(earth, start_jd, j2["reference_radius_km"] / context["au_km"], j2["j2"], pole_model=j2["pole_model"]))
    selector = encounter_aware_step_selector(bodies, start_jd, default_step_days=config["default_step_days"], scale_factor=step_scale)
    snapshots = {}
    def capture(epoch: float, _state: State, steps: int) -> None:
        snapshots[epoch] = {"runtime_seconds": time.perf_counter() - started, "rk4_steps": steps}
    predictions, _ = propagate_variable_step(initial, targets, combine_accelerations(*terms), selector, on_output=capture)
    return predictions, snapshots


def source_provenance(root: Path, config_path: Path) -> dict:
    paths = [config_path, *sorted((root / "configs").glob("*.json")), *sorted((root / "src").glob("*.py")), *sorted((root / "data/checksums").glob("*.json"))]
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def build_report(result: dict) -> str:
    lines = ["# Первая карта достаточности моделей — pilot 6", "",
        f"Generated {result['generated_at_utc']}. Engineering regression set; no final test or trained selector.", "",
        "## Протокол", "",
        f"- {len(result['records'])} model/case records; starts {result['config']['start_dates']}; horizons {result['config']['horizons_days']} days.",
        "- Primary error: maximum over the daily output grid, with the fixed existing Apophis five-minute refinement when included. Not a continuous-time bound.",
        "- All candidates start from the same supplied Horizons state and use recursive rollout. Nominal fitted NG parameters are excluded.",
        "- Earth/Moon ephemerides merge daily and existing refined nodes; J2 uses an approximate epoch-dependent IAU mean pole.",
        f"- Timing: {result['config']['timing_repeats']} repeated causal prefixes; resident ephemerides. Shared load {result['shared_load_seconds']:.3f} s is reported separately.",
        "- Timing includes force construction, integration, interpolation and output instrumentation. Feature/selector costs do not yet exist and are not included in oracle cost.",
        "- Numerical eligibility: observed coarse/fine maximum <= 10% of requested tolerance; this is a sensitivity check, not a rigorous error guarantee.", "",
        "## Ошибка на всём интервале", "",
        "| Model | Horizon, d | Cases | Median max error, km | Worst max error, km | Median runtime, s | Worst step difference, km |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for model in result["config"]["models"]:
        for horizon in result["config"]["horizons_days"]:
            rows = [r for r in result["records"] if r["model_id"] == model["model_id"] and r["horizon_days"] == horizon]
            lines.append(f"| {model['model_id']} | {horizon} | {len(rows)} | {statistics.median(r['max_position_error_km'] for r in rows):.6g} | {max(r['max_position_error_km'] for r in rows):.6g} | {statistics.median(r['runtime_median_seconds'] for r in rows):.4g} | {max(r['numerical_difference_km'] for r in rows):.4g} |")
    lines += ["", "## Offline oracle", "", "Oracle uses reference errors and is not an implemented predictor of model choice.", ""]
    for oracle in result["oracles"]:
        lines += [f"### Position tolerance {oracle['position_tolerance_km']:g} km", "",
                  f"Feasible cases: {oracle['feasible_case_count']}/{oracle['case_count']}; no eligible model: {oracle['infeasible_case_count']}.", "",
                  "| Fixed model | Feasible cases | Fixed cost on these cases, s | Oracle cost on the same cases, s |",
                  "| --- | ---: | ---: | ---: |"]
        for model, row in oracle["fixed_model_summaries"].items():
            lines.append(f"| {model} | {row['feasible_case_count']} | {row['total_cost_on_feasible_cases_seconds']:.4g} | {row['oracle_cost_on_model_feasible_cases_seconds']:.4g} |")
        counts = {}
        for model in oracle["selections"].values():
            counts[model or "none"] = counts.get(model or "none", 0) + 1
        lines += ["", f"Selection counts: `{json.dumps(counts, sort_keys=True)}`.", ""]
    lines += ["## Ограничения и воспроизведение", "",
        "- Objects and dates were already inspected; nested horizons are dependent. No object-generalization or rare-failure guarantee is estimated.",
        "- The strongest force model may also fail a requested tolerance. Failures are retained, including post-encounter cases.",
        "- Small runtime differences can reflect timing noise. This is a comparison of the present standard-library implementation.",
        "- Full EIH, high-precision Earth rotation and an independent numerical solver have not been validated here.",
        "- State/error samples, numerical differences and per-repeat prefix timings are saved in the ignored output directory.", "",
        "```bash", "env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never uv run --no-project --python-preference only-system python -B src/run_model_sufficiency.py --root . --config configs/model_sufficiency_pilot6.json", "```", ""]
    return "\n".join(lines)


def run(root: Path, config_path: Path, *, smoke: bool = False) -> dict:
    config = load_json(config_path)
    if smoke:
        config = {**config, "horizons_days": [7], "timing_repeats": 1}
    context = load_context(root, config)
    out = root / config["artifacts"]["output_directory"]
    if smoke:
        out = out / "smoke"
    out.mkdir(parents=True, exist_ok=True)
    records = []
    objects = context["data_config"]["asteroids"][:1] if smoke else context["data_config"]["asteroids"]
    max_horizon = max(config["horizons_days"])
    for asteroid in objects:
        for start_date in config["start_dates"]:
            series = context["series"][asteroid["id"]]
            # A date index would select the final refined intra-day state on an
            # event date. Exact midnight is part of this benchmark's contract.
            start = next(row for row in series if row["epoch_tdb"] == start_date + "T00:00:00")
            start_jd = start["epoch_jd_tdb"]
            selected = [row for row in series if 0 <= row["epoch_jd_tdb"] - start_jd <= max_horizon]
            targets = [row["epoch_jd_tdb"] - start_jd for row in selected]
            for horizon in config["horizons_days"]:
                if float(horizon) not in targets:
                    raise ValueError(f"Missing exact horizon {horizon} for {asteroid['id']}")
            reference = [state_from_row(row) for row in selected]
            trial_runs = {model["model_id"]: [] for model in config["models"]}
            first_predictions = {}
            for repeat in range(config["timing_repeats"]):
                models = config["models"][repeat:] + config["models"][:repeat]
                for model in models:
                    predictions, snapshots = propagate_candidate(reference[0], start_jd, targets, model, context, config, config["step_scale"])
                    if repeat == 0:
                        first_predictions[model["model_id"]] = predictions
                    elif predictions != first_predictions[model["model_id"]]:
                        raise RuntimeError("Repeated predictions are not deterministic")
                    trial_runs[model["model_id"]].append(snapshots)
                    print(json.dumps({"object": asteroid["id"], "start": start_date, "repeat": repeat + 1, "model": model["model_id"]}), flush=True)
            for model in config["models"]:
                model_id = model["model_id"]
                predictions = first_predictions[model_id]
                fine, _ = propagate_candidate(reference[0], start_jd, targets, model, context, config, config["step_scale"] * config["sensitivity_scale_factor"])
                error_rows = trajectory_error_rows(targets, predictions, reference, context["au_km"], context["day_s"])
                differences = [norm(subtract(a.position, b.position)) * context["au_km"] for a, b in zip(predictions, fine)]
                trace = []
                for row, epoch, pred, ref, difference in zip(error_rows, selected, predictions, reference, differences):
                    trace.append({**row, "epoch_jd_tdb": epoch["epoch_jd_tdb"], "prediction_r_au": pred.position, "prediction_v_au_d": pred.velocity,
                                  "reference_r_au": ref.position, "reference_v_au_d": ref.velocity, "numerical_difference_km": difference})
                name = f"{asteroid['id']}_{start_date}_{model_id.replace('+', '_')}"
                (out / f"{name}_trajectory.json").write_text(json.dumps(trace, allow_nan=False) + "\n")
                for horizon in config["horizons_days"]:
                    stop = targets.index(float(horizon)) + 1
                    costs = [trial[float(horizon)]["runtime_seconds"] for trial in trial_runs[model_id]]
                    steps = trial_runs[model_id][0][float(horizon)]["rk4_steps"]
                    records.append({"case_id": f"{asteroid['id']}:{start_date}:{horizon}", "object_id": asteroid["id"], "object_name": asteroid["name"],
                                    "regime": asteroid["regime"], "split": "engineering_regression", "start_date": start_date, "horizon_days": horizon,
                                    "model_id": model_id, **summarize_error_rows(error_rows[:stop]), "numerical_difference_km": max(differences[:stop]),
                                    "runtime_median_seconds": statistics.median(costs), "runtime_min_seconds": min(costs), "runtime_max_seconds": max(costs),
                                    "runtime_trials_seconds": costs, "rk4_steps": steps, "force_evaluations": 4 * steps,
                                    "point_mass_perturbers": (len(context['planets']) if model['planets'] else 0) + (len(context['small']) if model['small_bodies'] else 0)})
                print(json.dumps({"completed": name, "horizons": config["horizons_days"]}), flush=True)
            (out / "checkpoint_records.json").write_text(json.dumps(records, allow_nan=False) + "\n")
    oracles = [offline_oracle(records, tolerance, config["numerical_budget_fraction"]) for tolerance in config["exploratory_position_tolerances_km"]]
    result = {"schema_version": 1, "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "config": config,
              "smoke": smoke, "shared_load_seconds": context["load_seconds"], "python": sys.version.split()[0],
              "platform": {"system": platform.system(), "machine": platform.machine()}, "source_sha256": source_provenance(root, config_path),
              "records": records, "oracles": oracles}
    (out / "results.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    with (out / "metrics.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    report = build_report(result)
    (out / "report.md").write_text(report)
    if not smoke:
        (root / config["artifacts"]["report"]).write_text(report)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--smoke", action="store_true", help="One object, seven days; separate output subdirectory")
    args = parser.parse_args()
    result = run(args.root.resolve(), args.config.resolve(), smoke=args.smoke)
    print(json.dumps({"records": len(result["records"]), "smoke": result["smoke"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

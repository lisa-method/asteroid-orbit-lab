"""Bounded post-hoc force audit for development30 outliers.

This module is deliberately separate from the frozen development30 runner.  It
reuses its loaders and numerical path, but writes only to the new
``outputs/development30_outlier_audit`` directory when invoked as a script.
The audit is conditional teacher matching: the fitted non-gravitational terms
are metadata-derived post-hoc diagnostics, not forecast-time features.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Mapping

from earth_oblateness import earth_j2_acceleration
from orbit_baselines import State, norm, propagate_variable_step, subtract
from planetary_dynamics import (
    EphemerisInterpolator,
    NonGravitationalParameters,
    Perturber,
    combine_accelerations,
    encounter_aware_step_selector,
    non_gravitational_acceleration,
)
from run_development_benchmark import (
    _interpolate_endpoints,
    _model_force,
    _reference_grid,
    _runtime_environment,
    load_development_context,
    load_object_rows,
    source_hashes,
    state_from_row,
)
from run_eda import load_json, parse_horizons
from trajectory_metrics import summarize_error_rows, trajectory_error_rows


OBJECTS = ("153814", "613569")
HORIZONS = (7.0, 30.0, 90.0, 180.0, 365.0)
BASE_MODEL = {"model_id": "B3+GR+SB16"}
BASELINE_BUDGET_KM = 1.0e-5


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{__import__('os').getpid()}")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _state_json(state: State) -> dict[str, list[float]]:
    return {"r": list(state.position), "v": list(state.velocity)}


def _trace_json(run: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"time_days": float(x["time_days"]), "state": _state_json(x["state"])} for x in run["accepted_states"]]


def parse_non_grav(path: Path) -> tuple[NonGravitationalParameters | None, dict[str, Any]]:
    """Parse Horizons' explicit NG header, retaining exact source values."""
    result = load_json(path)["result"].split("$$SOE", 1)[0]
    if "Asteroid non-gravitational force model" not in result:
        return None, {"present": False, "source": "Horizons header", "reason": "no NG header"}
    patterns = {
        "a1_au_d2": r"A1\s*=\s*([-+0-9.Ee]+)",
        "a2_au_d2": r"A2\s*=\s*([-+0-9.Ee]+)",
        "a3_au_d2": r"A3\s*=\s*([-+0-9.Ee]+)",
        "alpha": r"ALN\s*=\s*([-+0-9.Ee]+)",
        "exponent_k": r"NK\s*=\s*([-+0-9.Ee]+)",
        "exponent_m": r"NM\s*=\s*([-+0-9.Ee]+)",
        "exponent_n": r"NN\s*=\s*([-+0-9.Ee]+)",
        "r0_au": r"R0\s*=\s*([-+0-9.Ee]+)",
    }
    values: dict[str, float] = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, result, re.IGNORECASE)
        if not match:
            raise ValueError(f"Incomplete non-gravitational header: {key}")
        values[key] = float(match.group(1))
    return NonGravitationalParameters(**values), {"present": True, "source": "Horizons header", "values": values}


def _pluto_force(body: Perturber, start_jd: float):
    """Heliocentric direct-minus-indirect point-mass acceleration for Pluto."""
    def acceleration(time_days: float, position: tuple[float, float, float], _velocity: tuple[float, float, float]):
        body_position = body.ephemeris.state_at(start_jd + time_days).position
        direct_delta = subtract(body_position, position)
        direct_distance = norm(direct_delta)
        body_distance = norm(body_position)
        if direct_distance == 0.0 or body_distance == 0.0:
            raise ValueError("Pluto point-mass force is singular")
        return subtract(
            tuple(component * body.mu_au3_d2 / direct_distance**3 for component in direct_delta),
            tuple(component * body.mu_au3_d2 / body_distance**3 for component in body_position),
        )
    return acceleration


def _propagate(initial: State, start_jd: float, context: Mapping[str, Any], config: Mapping[str, Any], extras: list[Any], scale: float) -> dict[str, Any]:
    base, bodies = _model_force(initial, start_jd, BASE_MODEL, context, config)
    terms = [base]
    selector_bodies = tuple(bodies)
    for extra, _selector_body in extras:
        terms.append(extra)
    # Preserve the frozen step rule; Earth is already present and distant
    # Pluto does not need a separate encounter threshold in this audit.
    acceleration = combine_accelerations(*terms)
    selector = encounter_aware_step_selector(
        selector_bodies, start_jd,
        default_step_days=float(config.get("default_step_days", 0.0625)),
        scale_factor=float(scale),
    )
    targets = [float(x) for x in range(1, 366)]
    accepted: dict[float, State] = {0.0: initial}
    def on_step(left: float, left_state: State, right: float, right_state: State) -> None:
        accepted[float(left)] = left_state
        accepted[float(right)] = right_state
    predictions, steps = propagate_variable_step(initial, targets, acceleration, selector, on_step=on_step)
    return {"predictions": predictions, "accepted_states": [{"time_days": t, "state": accepted[t]} for t in sorted(accepted)], "rk4_steps": steps}


def _make_extras(variant: str, context: Mapping[str, Any], start_jd: float, ng: NonGravitationalParameters | None, pluto: Perturber | None, config: Mapping[str, Any]) -> list[tuple[Any, Perturber | None]]:
    extras: list[tuple[Any, Perturber | None]] = []
    if "J2" in variant:
        earth = next((x for x in context["planets"] if x.body_id == "399"), None)
        if earth is None:
            raise ValueError("Earth perturber (399) is required for J2")
        j2 = config["earth_j2"]
        extras.append((earth_j2_acceleration(earth, start_jd, float(j2["reference_radius_km"]) / float(context["au_km"]), float(j2["j2"]), pole_model=str(j2.get("pole_model", "iau"))), earth))
    if "NG" in variant:
        if ng is None:
            raise ValueError("NG variant requested but Horizons header has no NG parameters")
        extras.append((non_gravitational_acceleration(ng), None))
    if "Pluto" in variant:
        if pluto is None:
            raise ValueError("Pluto variant requested without --pluto-path")
        extras.append((_pluto_force(pluto, start_jd), None))
    return extras


def _variant_metrics(variant: str, object_id: str, start_jd: float, rows: list[dict], production: Mapping[str, Any], fine: Mapping[str, Any], baseline: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    p_at, f_at, b_at = (_interpolate_endpoints(x["accepted_states"]) for x in (production, fine, baseline))
    horizons: dict[str, Any] = {}
    for horizon in HORIZONS:
        times, refs = _reference_grid(rows, start_jd, horizon)
        pred = [p_at(t) for t in times]
        fine_states = [f_at(t) for t in times]
        base_states = [b_at(t) for t in times]
        summary = summarize_error_rows(trajectory_error_rows(times, pred, refs, context["au_km"], context["day_s"]))
        fine_diff = max(norm(subtract(a.position, b.position)) * context["au_km"] for a, b in zip(pred, fine_states))
        shift = max(norm(subtract(a.position, b.position)) * context["au_km"] for a, b in zip(pred, base_states))
        horizons[str(int(horizon))] = {
            **summary,
            "max_position_error_km": summary["max_position_error_km"],
            "production_fine_difference_km": fine_diff,
            "max_shift_vs_baseline_km": shift,
            "eligibility": {str(t): summary["max_position_error_km"] <= t and fine_diff <= 0.1 * t for t in (0.1, 1.0, 10.0)},
            "reference_sample_count": len(times),
        }
    return {"variant": variant, "object_id": object_id, "horizons": horizons}


def run_audit(root: Path, *, pluto_path: Path | None = None, pluto_gm_km3_s2: float | None = None, output: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    config_path = root / "configs/development30.json"
    config = load_json(config_path)
    audit_config = load_json(root / "configs/model_sufficiency_pilot6.json")
    context = load_development_context(root, config)
    raw = root / str(config["raw_directory"])
    sample = load_json(root / str(config["sample_path"]))
    objects = {str(o["id"]): o for o in sample["objects"] if str(o["id"]) in OBJECTS}
    if set(objects) != set(OBJECTS):
        raise ValueError("sample is missing one of the two outlier objects")
    if pluto_path is not None and (pluto_gm_km3_s2 is None or not math.isfinite(pluto_gm_km3_s2) or pluto_gm_km3_s2 <= 0.0):
        raise ValueError("--pluto-gm-km3-s2 must be supplied and positive with --pluto-path")
    pluto = None
    if pluto_path is not None:
        pluto_path = (root / pluto_path).resolve()
        header, pluto_rows = parse_horizons(pluto_path, "9", "Pluto system")
        if not ("(9)" in header["target"] and header["center"].startswith("Sun (10)")
                and header["geometric"] and header["tdb"] and header["units"] == "AU-D" and header["reference_frame"] == "ICRF"):
            raise ValueError("Pluto input must be geometric Sun-centred ICRF/TDB/AU-D system barycentre 9")
        conversion = float(context["day_s"]) ** 2 / float(context["au_km"]) ** 3
        pluto = Perturber("9", "Pluto system", float(pluto_gm_km3_s2) * conversion, EphemerisInterpolator.from_rows(pluto_rows))
    out = (output or root / "outputs/development30_outlier_audit").resolve()
    base_hashes = source_hashes(root, config_path, root / str(config["sample_path"]), raw)
    for extra in (root / "src/earth_oblateness.py", root / "src/planetary_dynamics.py", root / "src/orbit_baselines.py", root / "outputs/development30/validation/results.json"):
        if extra.exists(): base_hashes[str(extra.relative_to(root))] = _sha256(extra)
    own_source = root / "src/audit_development_outlier_forces.py"
    if own_source.exists(): base_hashes[str(own_source.relative_to(root))] = _sha256(own_source)
    j2_config = root / "configs/model_sufficiency_pilot6.json"
    if j2_config.exists(): base_hashes[str(j2_config.relative_to(root))] = _sha256(j2_config)
    if pluto_path is not None: base_hashes[str(pluto_path.resolve().relative_to(root))] = _sha256(pluto_path)
    results: dict[str, Any] = {"schema_version": 1, "audit": "development30_outlier_forces", "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "runtime_environment": _runtime_environment(), "provenance": {"source_hashes": dict(sorted(base_hashes.items())), "teacher_matching": True, "note": "Post-hoc audit; nominal NG is parsed from teacher metadata. Rules and original validation outcomes stay frozen."}, "settings": {"pluto_gm_km3_s2": pluto_gm_km3_s2, "pluto_gm_source": "https://ssd.jpl.nasa.gov/astro_par.html", "earth_j2": audit_config["earth_j2"], "production_scale": config["step_scale"], "fine_scale": config["step_scale"]*config["sensitivity_scale_factor"], "independent_solver": False}, "objects": {}}
    frozen_results_path = root / "outputs/development30/validation/results.json"
    frozen_results = load_json(frozen_results_path)
    for relative, expected in frozen_results["provenance"]["source_sha256_at_start"].items():
        if _sha256(root / relative) != expected:
            raise ValueError(f"Frozen baseline input/source changed: {relative}")
    frozen_records = frozen_results["records"]
    for object_id in OBJECTS:
        obj = objects[object_id]
        daily, merged = load_object_rows(root, raw, obj)
        start_jd = float(daily[0]["epoch_jd_tdb"])
        initial = state_from_row(daily[0])
        ng, ng_meta = parse_non_grav(raw / "asteroids" / f"asteroid_{object_id}_daily.json")
        baseline_p = _propagate(initial, start_jd, context, config, [], float(config["step_scale"]))
        baseline_f = _propagate(initial, start_jd, context, config, [], float(config["step_scale"]) * float(config["sensitivity_scale_factor"]))
        baseline_metrics = _variant_metrics("baseline", object_id, start_jd, merged, baseline_p, baseline_f, baseline_p, context)
        for horizon_key, metric in baseline_metrics["horizons"].items():
            old = next(r for r in frozen_records if str(r["object_id"]) == object_id and r["horizon_days"] == float(horizon_key) and r["model_id"] == BASE_MODEL["model_id"])
            if abs(metric["max_position_error_km"]-old["max_position_error_km"]) > BASELINE_BUDGET_KM or abs(metric["production_fine_difference_km"]-old["numerical_difference_km"]) > BASELINE_BUDGET_KM:
                raise ValueError(f"Baseline reproduction failed: {object_id}/{horizon_key}")
        print(json.dumps({"baseline_verified": object_id}), flush=True)
        variants = ["baseline", "EarthJ2" if object_id == "153814" else "nominalNG"]
        if pluto is not None: variants.extend(["Pluto", "EarthJ2+Pluto" if object_id == "153814" else "nominalNG+Pluto"])
        object_result = {"metadata": {"sample": obj, "start_jd_tdb": start_jd, "non_gravitational": ng_meta}, "variants": {}}
        traces: dict[str, Any] = {"object_id": object_id, "baseline": {"production": _trace_json(baseline_p), "fine": _trace_json(baseline_f)}, "variants": {}}
        for variant in variants:
            extras = [] if variant == "baseline" else _make_extras(variant, context, start_jd, ng, pluto, audit_config)
            prod = baseline_p if variant == "baseline" else _propagate(initial, start_jd, context, config, extras, float(config["step_scale"]))
            fine = baseline_f if variant == "baseline" else _propagate(initial, start_jd, context, config, extras, float(config["step_scale"]) * float(config["sensitivity_scale_factor"]))
            object_result["variants"][variant] = _variant_metrics(variant, object_id, start_jd, merged, prod, fine, baseline_p, context)
            if variant != "baseline": traces["variants"][variant] = {"production": _trace_json(prod), "fine": _trace_json(fine)}
            print(json.dumps({"object_id": object_id, "variant": variant, "annual_error_km": object_result["variants"][variant]["horizons"]["365"]["max_position_error_km"]}), flush=True)
        frozen = {(str(r.get("object_id")), float(r.get("horizon_days"))): r for r in frozen_records if r.get("model_id") == BASE_MODEL["model_id"]}
        checks = {}
        for horizon_key, metric in object_result["variants"]["baseline"]["horizons"].items():
            old = frozen.get((object_id, float(horizon_key)))
            if old is None:
                checks[horizon_key] = {"status": "missing_frozen_record"}
                continue
            delta = abs(float(metric["max_position_error_km"]) - float(old["max_position_error_km"]))
            checks[horizon_key] = {"max_position_error_delta_km": delta, "budget_km": BASELINE_BUDGET_KM, "within_budget": delta <= BASELINE_BUDGET_KM}
        object_result["baseline_path_check"] = {"frozen_results": "outputs/development30/validation/results.json", "horizons": checks, "all_within_budget": all(x.get("within_budget", False) for x in checks.values())}
        results["objects"][object_id] = object_result
        _atomic_json(out / "traces" / f"object_{object_id}.json", traces)
        _atomic_json(out / "checkpoints" / f"object_{object_id}.json", {"schema_version": 1, "object_id": object_id, "source_hashes": results["provenance"]["source_hashes"], "result": object_result})
    _atomic_json(out / "forces.json", results)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--pluto-path", type=Path)
    parser.add_argument("--pluto-gm-km3-s2", type=float)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    run_audit(args.root, pluto_path=args.pluto_path, pluto_gm_km3_s2=args.pluto_gm_km3_s2, output=args.output)


if __name__ == "__main__":
    main()

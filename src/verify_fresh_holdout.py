"""Independent preflight verifier for the fresh-holdout v2 artifacts.

This module deliberately does not propagate a trajectory.  It validates the
matrix/checkpoint/trace/cost structure and rebuilds the frozen force metadata
from the raw initial state and daily NG header before delegating the numerical
error recomputation to :mod:`run_fresh_holdout`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
from typing import Any, Mapping

from force_models_v2 import build_force_model_v2
from ng_inputs_v2 import load_ng_input
from orbit_baselines import State
from prepare_fresh_holdout import DIRECTORY, MANIFEST, check_hashes, sha
from run_development_benchmark import _find_initial, load_development_context
import run_fresh_holdout as base
from run_force_models_v2 import _runtime_environment, eligible
from run_eda import parse_horizons
from selection_v2 import choose_model_v2


OUT = Path("outputs/fresh_holdout12")
RAW = Path("data/raw/fresh_holdout12")
FIXED = "V2-P-GR-SB16"
HORIZONS = (7, 30, 90, 180, 365)
TRACE_KINDS = ("production", "fine")


def _finite(value: Any, label: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be finite")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite") from exc
    if not math.isfinite(number) or (nonnegative and number < 0.0):
        raise ValueError(f"{label} must be finite and non-negative" if nonnegative else f"{label} must be finite")
    return number


def _state(value: Any, label: str) -> State:
    if not isinstance(value, Mapping) or set(value) != {"r", "v"}:
        raise ValueError(f"{label} must contain exactly r and v")
    r, v = tuple(value["r"]), tuple(value["v"])
    if len(r) != 3 or len(v) != 3:
        raise ValueError(f"{label} must contain three position and velocity components")
    for index, component in enumerate((*r, *v)):
        _finite(component, f"{label}[{index}]")
    return State(tuple(float(component) for component in r), tuple(float(component) for component in v))


def _expected_models(config: Mapping[str, Any]) -> tuple[str, ...]:
    models = config.get("models")
    if not isinstance(models, list) or not models:
        raise ValueError("config.models must be a non-empty list")
    ids = tuple(item.get("model_id") for item in models if isinstance(item, Mapping))
    if len(ids) != len(models) or any(not isinstance(item, str) for item in ids):
        raise ValueError("every configured model must have a model_id")
    if len(set(ids)) != len(ids):
        raise ValueError("configured model IDs must be unique")
    return ids


def expected_raw_paths(sample: Mapping[str, Any]) -> set[str]:
    """Return the exact daily/refined raw path set required by the sample."""
    paths: set[str] = set()
    for obj in sample.get("objects", []):
        object_id = str(obj["id"])
        paths.add(f"{RAW.as_posix()}/asteroids/asteroid_{object_id}_daily.json")
        if obj.get("event"):
            paths.add(f"{RAW.as_posix()}/asteroids/asteroid_{object_id}_refined.json")
    return paths


def check_manifest_paths(rows: Any, sample: Mapping[str, Any]) -> set[str]:
    """Check manifest cardinality, uniqueness, and its exact sample path set."""
    if not isinstance(rows, list):
        raise ValueError("fresh manifest files must be a list")
    paths = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
            raise ValueError(f"manifest.files[{index}] must contain a path")
        paths.append(Path(row["path"]).as_posix())
    expected = expected_raw_paths(sample)
    if len(paths) != len(set(paths)) or set(paths) != expected:
        raise ValueError("fresh manifest must contain exactly the unique daily/refined sample paths")
    return expected


def _trace_rel(object_id: str, model_id: str, kind: str) -> str:
    return f"{OUT.as_posix()}/traces/{object_id}_{model_id}_{kind}.json"


def expected_pairs(sample: Mapping[str, Any], config: Mapping[str, Any]) -> set[tuple[str, str]]:
    models = _expected_models(config)
    return {(str(obj["id"]), model_id) for obj in sample.get("objects", []) for model_id in models}


def check_matrix_shape(result: Mapping[str, Any], sample: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[set[tuple[str, str]], dict[tuple[str, float, str], Mapping[str, Any]]]:
    """Validate flat record coverage and nested object/model coverage."""
    pairs = expected_pairs(sample, config)
    if len(pairs) != 48:
        raise ValueError("fresh matrix must contain 48 object/model pairs")
    records = result.get("records")
    if not isinstance(records, list) or len(records) != 240:
        raise ValueError("fresh matrix must contain exactly 240 flat records")
    horizons = {float(value) for value in config.get("horizons_days", HORIZONS)}
    by_key: dict[tuple[str, float, str], Mapping[str, Any]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise ValueError(f"records[{index}] must be a mapping")
        key = (str(record.get("object_id")), float(record.get("horizon_days")), str(record.get("model_id")))
        if key in by_key or key[0] not in {pair[0] for pair in pairs} or (key[0], key[2]) not in pairs or key[1] not in horizons:
            raise ValueError("flat matrix records contain an unknown or duplicate key")
        if record.get("split") != "fresh_holdout" or record.get("evaluation_scope") != "fresh_holdout12_frozen_v2":
            raise ValueError("fresh matrix record has an invalid split or evaluation scope")
        by_key[key] = record
    expected_keys = {(object_id, horizon, model_id) for object_id, model_id in pairs for horizon in horizons}
    if set(by_key) != expected_keys:
        raise ValueError("flat matrix records do not cover every object/model/horizon")

    items = result.get("object_model_results")
    if not isinstance(items, list) or len(items) != len(pairs):
        raise ValueError("matrix must contain exactly 48 nested object/model results")
    found: set[tuple[str, str]] = set()
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("object_model_results entries must be mappings")
        pair = (str(item.get("object_id")), str(item.get("model_id")))
        if pair in found or pair not in pairs:
            raise ValueError("nested object/model results contain an unknown or duplicate pair")
        found.add(pair)
        expected_paths = [_trace_rel(pair[0], pair[1], kind) for kind in TRACE_KINDS]
        if item.get("trace_paths") != expected_paths:
            raise ValueError(f"trace paths do not match {pair}")
        hashes = item.get("trace_hashes")
        if not isinstance(hashes, Mapping) or set(hashes) != set(expected_paths):
            raise ValueError(f"trace hashes do not match {pair}")
    if found != pairs:
        raise ValueError("nested object/model results do not cover all 48 pairs")
    return pairs, by_key


def _check_header(root: Path, path: Path, object_id: str, object_name: str) -> int:
    document = json.loads(path.read_text(encoding="utf-8"))
    header, rows = parse_horizons(path, object_id, object_name)
    if document.get("signature", {}).get("source") != "NASA/JPL Horizons API" or document.get("signature", {}).get("version") != "1.2":
        raise ValueError(f"unsupported Horizons signature: {path}")
    if not (header["target"].split()[0] == object_id and header["center"].startswith("Sun")
            and header["units"] == "AU-D" and header["reference_frame"] == "ICRF"
            and header["output_type"] == "GEOMETRIC cartesian states" and header["geometric"] and header["tdb"]):
        raise ValueError(f"raw coordinate/time contract mismatch: {path}")
    return len(rows)


def check_fresh_manifest(root: Path, sample: Mapping[str, Any]) -> dict[str, Any]:
    manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    if manifest.get("complete") is not True:
        raise ValueError("fresh raw manifest is incomplete")
    expected = check_manifest_paths(manifest.get("files"), sample)
    by_path = {Path(row["path"]).as_posix(): row for row in manifest["files"]}
    counts = {}
    for obj in sample["objects"]:
        object_id = str(obj["id"])
        for kind in ("daily", "refined"):
            relative = f"{RAW.as_posix()}/asteroids/asteroid_{object_id}_{kind}.json"
            if relative not in expected:
                if kind == "refined" and not obj.get("event"):
                    continue
                raise ValueError(f"missing manifest path: {relative}")
            path = root / relative
            row = by_path[relative]
            if sha(path) != row.get("sha256") or path.stat().st_size != row.get("bytes"):
                raise ValueError(f"manifest hash/size mismatch: {relative}")
            counts[relative] = _check_header(root, path, object_id, str(obj["name"]))
    if len(counts) != 20:
        raise ValueError("fresh manifest must validate 20 raw files")
    return {"files": len(counts), "paths": sorted(counts), "rows": counts}


def _check_trace(root: Path, path: Path, expected_model: str, expected_force: Mapping[str, Any], initial: State) -> dict[str, Any]:
    trace = json.loads(path.read_text(encoding="utf-8"))
    required = {"trace_schema_version", "model_id", "accepted_states", "force_metadata", "runtime_seconds", "rk4_steps"}
    if set(trace) != required or trace["trace_schema_version"] != 1 or trace["model_id"] != expected_model:
        raise ValueError(f"trace schema/model mismatch: {path}")
    if trace["force_metadata"] != expected_force:
        raise ValueError(f"trace force metadata mismatch: {path}")
    runtime = _finite(trace["runtime_seconds"], f"{path}.runtime_seconds", nonnegative=True)
    steps = trace["rk4_steps"]
    if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
        raise ValueError(f"invalid rk4_steps: {path}")
    accepted = trace["accepted_states"]
    if not isinstance(accepted, list) or len(accepted) < 2 or len(accepted) - 1 != steps:
        raise ValueError(f"accepted endpoint/step count mismatch: {path}")
    times = []
    states = []
    for index, item in enumerate(accepted):
        if not isinstance(item, Mapping) or set(item) != {"time_days", "state"}:
            raise ValueError(f"invalid accepted state schema: {path}")
        times.append(_finite(item["time_days"], f"{path}.accepted_states[{index}].time_days"))
        states.append(_state(item["state"], f"{path}.accepted_states[{index}].state"))
    if times[0] != 0.0 or times[-1] != 365.0 or states[0] != initial or any(right <= left for left, right in zip(times, times[1:])):
        raise ValueError(f"trace endpoint/time mismatch: {path}")
    return {"runtime_seconds": runtime, "rk4_steps": steps, "states": len(states)}


def check_traces_and_checkpoints(root: Path, result: Mapping[str, Any], sample: Mapping[str, Any], config: Mapping[str, Any], provenance: Mapping[str, Any], context: Mapping[str, Any], by_key: Mapping[tuple[str, float, str], Mapping[str, Any]]) -> dict[str, Any]:
    model_by_id = {str(model["model_id"]): model for model in config["models"]}
    object_by_id = {str(obj["id"]): obj for obj in sample["objects"]}
    production_steps = fine_steps = 0
    for item in result["object_model_results"]:
        object_id, model_id = str(item["object_id"]), str(item["model_id"])
        obj, model = object_by_id[object_id], model_by_id[model_id]
        daily_path = root / RAW / "asteroids" / f"asteroid_{object_id}_daily.json"
        daily, _ = base.load_object_rows(root, root / RAW, obj)
        start, initial = _find_initial(daily, obj["start_date"])
        ng = load_ng_input(daily_path)
        _, _, expected_force = build_force_model_v2(start, model, context, config, ng=ng)
        paths = item["trace_paths"]
        checked = []
        for kind, relative in zip(TRACE_KINDS, paths):
            path = root / relative
            if not path.is_file() or sha(path) != item["trace_hashes"][relative]:
                raise ValueError(f"trace hash/path mismatch: {relative}")
            checked.append(_check_trace(root, path, model_id, expected_force, initial))
        if checked[1]["rk4_steps"] < checked[0]["rk4_steps"]:
            raise ValueError(f"fine trace has fewer steps than production: {object_id}/{model_id}")
        production_steps += checked[0]["rk4_steps"]
        fine_steps += checked[1]["rk4_steps"]
        for horizon in config["horizons_days"]:
            record = by_key[(object_id, float(horizon), model_id)]
            if record.get("force_metadata") != expected_force:
                raise ValueError(f"record force metadata mismatch: {object_id}/{model_id}")
            trials = record.get("runtime_trials_seconds")
            if not isinstance(trials, list) or len(trials) != 3 or any(_finite(value, "runtime trial", nonnegative=True) != float(value) for value in trials):
                raise ValueError(f"invalid runtime trials: {object_id}/{model_id}/{horizon}")
            median = statistics.median(trials)
            if record.get("runtime_median_seconds") != median:
                raise ValueError(f"runtime median mismatch: {object_id}/{model_id}/{horizon}")
        checkpoint_path = root / OUT / "matrix_checkpoints" / f"{object_id}_{model_id}.json"
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint.get("provenance") != provenance or checkpoint.get("object_id") != object_id or checkpoint.get("model_id") != model_id:
            raise ValueError(f"checkpoint identity/provenance mismatch: {checkpoint_path}")
        if checkpoint.get("trace_paths") != item["trace_paths"] or checkpoint.get("trace_hashes") != item["trace_hashes"]:
            raise ValueError(f"checkpoint trace mismatch: {checkpoint_path}")
        cp_records = checkpoint.get("records")
        if not isinstance(cp_records, list) or len(cp_records) != len(config["horizons_days"]):
            raise ValueError(f"checkpoint record coverage mismatch: {checkpoint_path}")
        for cp_record in cp_records:
            key = (object_id, float(cp_record["horizon_days"]), model_id)
            if dict(cp_record) != dict(by_key[key]):
                raise ValueError(f"checkpoint/flat record mismatch: {checkpoint_path}")
    return {"object_model_pairs": len(result["object_model_results"]), "traces": 2 * len(result["object_model_results"]),
            "production_steps_total": production_steps, "fine_steps_total": fine_steps}


def check_cost(root: Path, timing: Mapping[str, Any], sample: Mapping[str, Any], config: Mapping[str, Any], artifact: Mapping[str, Any], matrix: Mapping[str, Any], by_key: Mapping[tuple[str, float, str], Mapping[str, Any]]) -> dict[str, Any]:
    rows = timing.get("timings")
    if not isinstance(rows, list) or len(rows) != 120:
        raise ValueError("direct cost must contain exactly 120 timing rows")
    expected = {(str(obj["id"]), float(h), method) for obj in sample["objects"] for h in config["horizons_days"] for method in ("selector", "fixed_full")}
    found = set()
    for row in rows:
        key = (str(row.get("object_id")), float(row.get("horizon_days")), row.get("method"))
        if key in found or key not in expected:
            raise ValueError("direct timing rows contain an unknown or duplicate key")
        found.add(key)
        if float(row.get("tolerance_km")) != 1.0:
            raise ValueError("direct timing tolerance must be 1 km")
        model_id = FIXED if row["method"] == "fixed_full" else choose_model_v2(row["horizon_days"], 1.0, artifact, config)["model_id"]
        if row.get("model_id") != model_id:
            raise ValueError("timing row model does not match fixed/selector contract")
        matrix_record = by_key[(key[0], key[1], model_id)]
        expected_eligible = eligible(matrix_record, 1.0, config["numerical_budget_fraction"])
        if row.get("actual_eligible") != expected_eligible or not math.isclose(float(row.get("max_position_error_km")), float(matrix_record["max_position_error_km"]), rel_tol=0.0, abs_tol=1e-5):
            raise ValueError("direct timing accuracy does not match matrix record")
        trials = row.get("runtime_trials_seconds")
        if not isinstance(trials, list) or len(trials) != 3 or any(not math.isfinite(float(value)) or float(value) < 0.0 for value in trials):
            raise ValueError("invalid direct timing trials")
        if row.get("runtime_median_seconds") != statistics.median(trials):
            raise ValueError("direct timing median mismatch")
    if found != expected or timing.get("fixed_model_id") != FIXED:
        raise ValueError("direct timing coverage/fixed model mismatch")
    if timing.get("matrix_sha256") != sha(root / OUT / "matrix.json"):
        raise ValueError("direct timing matrix hash mismatch")
    totals = {}
    for method in ("selector", "fixed_full"):
        subset = [row for row in rows if row["method"] == method]
        totals[method] = {"total_seconds": sum(row["runtime_median_seconds"] for row in subset),
                          "eligible_cases": sum(bool(row["actual_eligible"]) for row in subset)}
    if timing.get("full_cost") != totals:
        raise ValueError("direct timing full_cost totals mismatch")
    return {"rows": len(rows), "methods": sorted({row["method"] for row in rows}), "matrix_sha256": timing["matrix_sha256"]}


def preflight(root: Path) -> dict[str, Any]:
    """Run all structural/metadata checks without trajectory propagation."""
    root = Path(root).resolve()
    sample, config, artifact, provenance = base.setup(root)
    manifest_check = check_fresh_manifest(root, sample)
    matrix = json.loads((root / OUT / "matrix.json").read_text(encoding="utf-8"))
    if matrix.get("provenance") != provenance:
        raise ValueError("matrix provenance mismatch")
    _, by_key = check_matrix_shape(matrix, sample, config)
    context = load_development_context(root, config)
    trace_check = check_traces_and_checkpoints(root, matrix, sample, config, provenance, context, by_key)
    timing = json.loads((root / OUT / "direct_cost.json").read_text(encoding="utf-8"))
    if timing.get("provenance") != provenance:
        raise ValueError("direct cost provenance mismatch")
    cost_check = check_cost(root, timing, sample, config, artifact, matrix, by_key)
    return {"manifest": manifest_check, "matrix": {"records": len(matrix["records"]), "choices": len(matrix.get("choices", []))},
            "traces": trace_check, "cost": cost_check,
            "runtime_environment": _runtime_environment(),
            "verifier_source_sha256": sha(root / "src/verify_fresh_holdout.py")}


def verify(root: Path) -> dict[str, Any]:
    """Preflight artifacts, then run the frozen numerical verifier."""
    root = Path(root).resolve()
    checks = preflight(root)
    summary = base.verify(root)
    path = root / OUT / "verification.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["checks"] = checks
    # The base verifier is the only component that recomputes trajectory errors;
    # this final write merely annotates its same-namespace result.
    base._atomic_json(path, saved)
    return saved


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(verify(args.root), indent=2, sort_keys=True))

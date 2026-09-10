"""Forecast from one initial state using calibrated v2 rules or an explicit model."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from force_models_v2 import forecast_candidate_v2
from ng_inputs_v2 import load_ng_input
from orbit_baselines import State
from run_development_benchmark import load_development_context
from run_force_models_v2 import _atomic_json
from selection_v2 import forecast_with_selection_v2


def load_bound_ng(path, object_id):
    """Use target identity solely to prevent accidental parameter misassociation."""
    if not isinstance(object_id, str) or not object_id.strip():
        raise ValueError("--ng-header requires object_id in the forecast request")
    header = json.loads(path.read_text())["result"].split("$$SOE", 1)[0]
    match = re.search(r"Rec\s*#:\s*(\d+)\b", header)
    if match is None or match.group(1) != object_id:
        raise ValueError("NG header target does not match request object_id")
    return load_ng_input(path)


def forecast(root, input_path, output_path, *, model_id=None, ng_header=None):
    root = root.resolve()
    if output_path.exists():
        raise FileExistsError(f"output already exists: {output_path}")
    request = json.loads(input_path.read_text())
    expected = {"start_jd_tdb", "position_au", "velocity_au_per_day", "horizon_days", "tolerance_km", "coordinates"}
    if not expected <= set(request) or set(request) - expected - {"object_id"}:
        raise ValueError(f"forecast input requires {sorted(expected)}, with optional object_id")
    coordinates = {"center": "500@10", "reference_system": "ICRF", "reference_plane": "FRAME", "vector_corrections": "NONE", "time_scale": "TDB"}
    if request["coordinates"] != coordinates:
        raise ValueError("initial state must use Sun-centred ICRF/FRAME geometric vectors and TDB")
    initial = State(tuple(request["position_au"]), tuple(request["velocity_au_per_day"]))
    ng = load_bound_ng(ng_header, request.get("object_id")) if ng_header is not None else None
    config = json.loads((root / "configs/force_models_v2.json").read_text())
    context = load_development_context(root, config)
    if model_id is None:
        artifact = json.loads((root / config["output_directory"] / "rules.json").read_text())
        provenance = artifact["provenance"]
        sources = {**provenance["force_source_hashes"], "src/selection_v2.py": provenance["selection_source_sha256"]}
        for path, expected_hash in sources.items():
            if hashlib.sha256((root / path).read_bytes()).hexdigest() != expected_hash:
                raise ValueError(f"calibrated force/selection source changed: {path}")
        predictions, metadata, online = forecast_with_selection_v2(
            initial, request["start_jd_tdb"], request["horizon_days"], request["tolerance_km"],
            artifact, context, config, ng=ng)
        decision = online["decision"]
        runtime_seconds = online["runtime_seconds"]
    else:
        model = next((item for item in config["models"] if item["model_id"] == model_id), None)
        if model is None:
            raise ValueError(f"unknown configured model: {model_id}")
        predictions, metadata = forecast_candidate_v2(initial, request["start_jd_tdb"], request["horizon_days"],
                                                       model, context, config, config["step_scale"], ng=ng)
        decision = {"model_id": model_id, "method": "explicit_model", "accuracy_guaranteed": False}
        runtime_seconds = metadata["runtime_seconds"]
    def state_record(t, state):
        return {"time_days": t, "epoch_jd_tdb": request["start_jd_tdb"] + t,
                "position_au": list(state.position), "velocity_au_per_day": list(state.velocity)}
    result = {"schema_version": 2, "coordinates": coordinates, "request": request, "decision": decision,
              "force_metadata": metadata["force_metadata"], "runtime_seconds": runtime_seconds,
              "propagation_runtime_seconds": metadata["runtime_seconds"],
              "trajectory": [state_record(t, state) for t, state in zip(metadata["daily_times_days"], predictions)],
              "accepted_trajectory": [state_record(item["time_days"], item["state"]) for item in metadata["accepted_states"]]}
    _atomic_json(output_path, result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", help="Explicit candidate; otherwise use calibrated v2 horizon rule")
    parser.add_argument("--ng-header", type=Path, help="Optional local Horizons header source, availability checked at forecast start")
    args = parser.parse_args()
    result = forecast(args.root, args.input, args.output, model_id=args.model, ng_header=args.ng_header)
    print(json.dumps({"decision": result["decision"], "force_metadata": result["force_metadata"], "output": str(args.output)}, indent=2))

"""Check the new online orchestration only on an already inspected object."""
import argparse
import json
from pathlib import Path
from unittest.mock import patch

import run_selector_v4_holdout24 as runner
from ng_inputs_v2 import load_ng_input
from prepare_selector_holdout24 import immutable_json, sha
from run_development_benchmark import _find_initial, load_development_context, load_object_rows
from selector_v4 import choose_v4, features_v4
from selector_v3 import choose_v3


def verify(root):
    read = lambda p: json.loads((root / p).read_text())
    config = read("configs/force_models_v2.json")
    feature_config = read("configs/development30.json")
    obj = read("data/processed/development30/sample.json")["objects"][0]
    context = load_development_context(root, config)
    with patch.object(runner, "RAW_DIRECTORY", "data/raw/development30"):
        cases, prod, fine = runner._run_pair(root, obj, config["models"][-1], context, config, feature_config)
    old = {(r["object_id"], r["model_id"], r["horizon_days"]): r for r in read("outputs/force_models_v2/results.json")["records"]}
    for case in cases:
        expected = old[(obj["id"], case["model_id"], case["horizon_days"])]
        for field in ("max_position_error_km", "max_velocity_error_m_s", "numerical_difference_km"):
            if case["record"][field] != expected[field]:
                raise ValueError(f"Frozen physical prefix mismatch: {field}")
    v3, v4 = read("outputs/selector_v3/model.json"), read("outputs/selector_v4/model.json")
    daily, _ = load_object_rows(root, root / "data/raw/development30", obj)
    start, initial = _find_initial(daily, obj["start_date"])
    ng = load_ng_input(root / "data/raw/development30/asteroids" / f"asteroid_{obj['id']}_daily.json")
    outputs = []
    for method in runner.METHODS:
        _, meta, online = runner.forecast_online(initial, start, 7., 1., method, v3, v4, context, config, feature_config, ng)
        if method == "tree_v3":
            expected = choose_v3(cases[0]["feature"], 7., 1., v3)
        elif method == "fixed_full":
            expected = {"model_id": config["fallback_model_id"], "status": "fixed_full"}
        else:
            expected = choose_v4(cases[0]["feature"], 7., 1., v4, method)
        if any(online["decision"][key] != expected[key] for key in ("model_id", "status")):
            raise ValueError("Actual online selection differs from cached feature decision")
        if meta["accepted_states"][0]["state"] != initial or meta["accepted_states"][-1]["time_days"] != 7.:
            raise ValueError("Online forecast has wrong initial/final epoch")
        outputs.append({"method": method, "model_id": expected["model_id"], "status": expected["status"]})
    value = {"passed": True, "object_id": obj["id"], "scope": "inspected training regression only",
             "full_physics_prefixes_exact": 5, "online_api_calls": outputs,
             "runner_sha256": sha(root / "src/run_selector_v4_holdout24.py"),
             "selector_sha256": sha(root / "src/selector_v4.py"),
             "model_sha256": sha(root / "outputs/selector_v4/model.json")}
    immutable_json(root / "outputs/selector_v4/runner_preflight.json", value)
    return value


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path("."))
    print(json.dumps(verify(p.parse_args().root.resolve()), indent=2))

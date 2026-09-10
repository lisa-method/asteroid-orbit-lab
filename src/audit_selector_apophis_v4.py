"""Inspected Apophis warning replay; not part of the new-object score."""
import json
from pathlib import Path

from ng_inputs_v2 import load_ng_input
from prepare_fresh_holdout import check_hashes, immutable_json, sha
from run_development_benchmark import load_development_context
from run_eda import parse_horizons
from run_physics_baselines import state_from_row
from selector_v4 import features_v4, choose_v4


def run(root):
    config = json.loads((root / "configs/force_models_v2.json").read_text())
    features_config = json.loads((root / "configs/development30.json").read_text())
    artifact = json.loads((root / "outputs/selector_v4/model.json").read_text())
    check_hashes(root, artifact["provenance"]["hashes"])
    path = root / "data/raw/apophis_reference_time/asteroid_99942_annual_daily.json"
    first = parse_horizons(path, "99942", "Apophis")[1][0]
    initial, start = state_from_row(first), first["epoch_jd_tdb"]
    ng = load_ng_input(path)
    context = load_development_context(root, config)
    rows = []
    for h in config["horizons_days"]:
        feature = features_v4(initial, start, h, context, features_config, config, ng)
        rows.append({"horizon_days": h, "features": feature,
                     "choices": [choose_v4(feature, h, t, artifact) for t in config["position_tolerances_km"]]})
    value = {"scope": "inspected Apophis warning diagnostic, no fresh score or refit",
             "physical_rollout_not_run": True, "fallback_is_general_v2_not_specialized_apophis_backend": True,
             "initial": first, "reference_path": path.relative_to(root).as_posix(),
             "reference_sha256": sha(path), "model_sha256": sha(root / "outputs/selector_v4/model.json"),
             "script_sha256": sha(Path(__file__)), "rows": rows}
    immutable_json(root / "outputs/selector_v4/apophis_warning_diagnostic.json", value)
    for r in rows:
        print(json.dumps({"horizon": r["horizon_days"], "min_hill": r["features"]["min_planet_distance_over_hill"],
                          "max_strength": r["features"]["max_scattering_strength"],
                          "statuses": [c["status"] for c in r["choices"]]}))


if __name__ == "__main__":
    run(Path(".").resolve())

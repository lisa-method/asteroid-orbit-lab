import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from selection_v2 import config_digest
from verify_fresh_holdout import (
    FIXED,
    OUT,
    RAW,
    check_cost,
    check_matrix_shape,
    check_manifest_paths,
)


MODELS = ("V2-B2", "V2-P", "V2-P-GR", FIXED)
HORIZONS = (7, 30, 90, 180, 365)


def sample_fixture():
    return {"objects": [{"id": str(index), "event": index < 8} for index in range(12)]}


def config_fixture():
    return {"horizons_days": list(HORIZONS), "numerical_budget_fraction": 0.1,
            "models": [{"model_id": model_id} for model_id in MODELS]}


def matrix_fixture(sample, config):
    records = []
    for obj in sample["objects"]:
        for model in config["models"]:
            for horizon in config["horizons_days"]:
                records.append({"object_id": obj["id"], "model_id": model["model_id"],
                                "horizon_days": horizon, "split": "fresh_holdout",
                                "evaluation_scope": "fresh_holdout12_frozen_v2",
                                "max_position_error_km": 0.5,
                                "numerical_difference_km": 0.01,
                                "runtime_median_seconds": 1.0,
                                "force_metadata": {}})
    items = []
    for obj in sample["objects"]:
        for model in config["models"]:
            pair = (obj["id"], model["model_id"])
            paths = [f"{OUT.as_posix()}/traces/{pair[0]}_{pair[1]}_{kind}.json"
                     for kind in ("production", "fine")]
            items.append({"object_id": pair[0], "model_id": pair[1],
                          "trace_paths": paths, "trace_hashes": {path: "x" for path in paths}})
    return {"records": records, "object_model_results": items}


def artifact_fixture(config):
    return {"schema_version": 2, "method": "horizon_rule_v2",
            "config_digest": config_digest(config), "models": list(MODELS),
            "horizons_days": list(HORIZONS), "fallback_model_id": "V2-B2",
            "effective_error_caps_km": {model: {str(float(h)): 1.0 for h in HORIZONS} for model in MODELS},
            "median_cost_seconds": {model: {str(float(h)): index + 1.0 for h in HORIZONS}
                                     for index, model in enumerate(MODELS)}}


class FreshHoldoutVerifierTests(unittest.TestCase):
    def test_manifest_rejects_duplicate_or_missing_raw_path(self):
        sample = sample_fixture()
        paths = sorted(
            f"{RAW.as_posix()}/asteroids/asteroid_{obj['id']}_{kind}.json"
            for obj in sample["objects"]
            for kind in (("daily", "refined") if obj["event"] else ("daily",))
        )
        rows = [{"path": path} for path in paths]
        self.assertEqual(check_manifest_paths(rows, sample), set(paths))
        with self.assertRaises(ValueError):
            check_manifest_paths(rows[:-1] + [rows[0]], sample)

    def test_matrix_rejects_missing_nested_object_model_pair(self):
        sample, config = sample_fixture(), config_fixture()
        result = matrix_fixture(sample, config)
        result["object_model_results"].pop()
        with self.assertRaisesRegex(ValueError, "48 nested"):
            check_matrix_shape(result, sample, config)

    def test_cost_rejects_selector_model_different_from_frozen_rule(self):
        sample, config = sample_fixture(), config_fixture()
        matrix = matrix_fixture(sample, config)
        _, by_key = check_matrix_shape(matrix, sample, config)
        artifact = artifact_fixture(config)
        timings = []
        for obj in sample["objects"]:
            for horizon in HORIZONS:
                for method in ("selector", "fixed_full"):
                    model_id = FIXED if method == "fixed_full" else "V2-P"
                    timings.append({"object_id": obj["id"], "horizon_days": horizon,
                                    "method": method, "model_id": model_id,
                                    "tolerance_km": 1.0, "actual_eligible": True,
                                    "max_position_error_km": 0.5,
                                    "runtime_trials_seconds": [1.0, 1.0, 1.0],
                                    "runtime_median_seconds": 1.0})
        timing = {"timings": timings, "fixed_model_id": FIXED,
                  "matrix_sha256": "unused", "full_cost": {}}
        with self.assertRaisesRegex(ValueError, "model does not match"):
            check_cost(Path("."), timing, sample, config, artifact, matrix, by_key)


if __name__ == "__main__":
    unittest.main()

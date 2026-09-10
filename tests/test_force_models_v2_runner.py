import json
import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from orbit_baselines import State
from run_force_models_v2 import (
    MODEL_IDS,
    _ng_path,
    _model_defaults,
    _scope_output,
    _trace_payload,
    _verify_result_trace_hashes,
    eligible,
)


class ForceModelsV2RunnerTests(unittest.TestCase):
    def test_model_defaults_keep_b2_pure_and_add_j2_ng_to_planet_candidates(self):
        b2 = _model_defaults({"model_id": "V2-B2", "planets": False, "solar_gr": False,
                              "small_bodies": False, "earth_j2": False, "non_grav": "off"})
        self.assertFalse(b2["planets"])
        self.assertFalse(b2["earth_j2"])
        self.assertEqual(b2["non_grav"], "off")

        planet = _model_defaults({"model_id": "V2-P", "planets": True, "solar_gr": False,
                                  "small_bodies": False, "earth_j2": True, "non_grav": "if_available"})
        self.assertTrue(planet["planets"])
        self.assertTrue(planet["earth_j2"])
        self.assertEqual(planet["non_grav"], "if_available")

    def test_model_ids_are_the_v2_namespace(self):
        self.assertEqual(MODEL_IDS, ("V2-B2", "V2-P", "V2-P-GR", "V2-P-GR-SB16"))

    def test_eligibility_requires_error_and_numerical_budget(self):
        record = {"max_position_error_km": 0.9, "numerical_difference_km": 0.09}
        self.assertTrue(eligible(record, 1.0, 0.1))
        self.assertFalse(eligible({**record, "numerical_difference_km": 0.11}, 1.0, 0.1))
        self.assertFalse(eligible({**record, "max_position_error_km": 1.01}, 1.0, 0.1))

    def test_trace_payload_is_json_serializable_and_preserves_force_metadata(self):
        metadata = {
            "model_id": "V2-P",
            "accepted_states": [
                {"time_days": 0.0, "state": State((1.0, 0.0, 0.0), (0.0, 0.01, 0.0))},
                {"time_days": 1.0, "state": State((1.0, 0.01, 0.0), (0.0, 0.01, 0.0))},
            ],
            "force_metadata": {"ng_status": "unavailable", "applied_terms": ["solar"]},
            "runtime_seconds": 1.25,
            "rk4_steps": 16,
        }
        payload = _trace_payload(metadata)
        round_trip = json.loads(json.dumps(payload))
        self.assertEqual(round_trip["accepted_states"][1]["state"]["r"], [1.0, 0.01, 0.0])
        self.assertEqual(round_trip["force_metadata"]["ng_status"], "unavailable")

    def test_subset_scope_has_separate_output_namespace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {"output_directory": "outputs/force_models_v2"}
            full, full_scope = _scope_output(root, config, None)
            subset, subset_scope = _scope_output(root, config, "123")
            self.assertEqual(full, root / "outputs/force_models_v2")
            self.assertEqual(subset, root / "outputs/force_models_v2/subset_object_123")
            self.assertNotEqual(full_scope, subset_scope)

    def test_ng_path_defaults_to_daily_horizons_header(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "data/raw/development30/asteroids/asteroid_42_daily.json"
            path.parent.mkdir(parents=True)
            path.write_text("{}", encoding="utf-8")
            resolved = _ng_path(root, {"raw_directory": "data/raw/development30"}, {"id": "42"})
            self.assertEqual(resolved, path)

    def test_finished_result_trace_hash_is_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace = root / "outputs/force_models_v2/traces/model.json"
            trace.parent.mkdir(parents=True)
            trace.write_text("original\n", encoding="utf-8")
            import hashlib
            digest = hashlib.sha256(trace.read_bytes()).hexdigest()
            result = {"object_model_results": [{"trace_hashes": {"outputs/force_models_v2/traces/model.json": digest}}]}
            _verify_result_trace_hashes(root, result)
            trace.write_text("changed\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                _verify_result_trace_hashes(root, result)


if __name__ == "__main__":
    unittest.main()

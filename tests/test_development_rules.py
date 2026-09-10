from pathlib import Path
import json
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from development_rules import MODEL_IDS, STRONGEST_MODEL_ID, choose_model, fit_rules


def _training_data():
    features = []
    records = []
    tasks = [("quiet", "obj-a", 7.0), ("encounter", "obj-b", 30.0)]
    proxies = [(1.0, 0.5, 0.2), (100.0, 10.0, 2.0)]
    for (case_id, object_id, horizon), proxy in zip(tasks, proxies):
        features.append({
            "case_id": case_id,
            "object_id": object_id,
            "horizon_days": horizon,
            "split": "train",
            "features": {
                "planet_proxy_km": proxy[0],
                "gr_proxy_km": proxy[1],
                "small_body_proxy_km": proxy[2],
            },
            "runtime_median_seconds": 0.0,
        })
        errors = {
            "B2": (3.5 if horizon == 7.0 else 300.0, 0.1),
            "B3": (2.0 if horizon == 7.0 else 40.0, 0.2),
            "B3+GR": (1.0 if horizon == 7.0 else 20.0, 0.3),
            "B3+GR+SB16": (0.5 if horizon == 7.0 else 10.0, 0.4),
        }
        # Deliberately non-monotonic cost: the physics ladder is not a cost assumption.
        runtimes = {"B2": 1.0, "B3": 4.0, "B3+GR": 2.0, "B3+GR+SB16": 3.0}
        for model_id in MODEL_IDS:
            error, numerical = errors[model_id]
            records.append({
                "case_id": case_id,
                "object_id": object_id,
                "horizon_days": horizon,
                "split": "train",
                "model_id": model_id,
                "max_position_error_km": error,
                "numerical_difference_km": numerical,
                "runtime_median_seconds": runtimes[model_id],
            })
    return records, features


class DevelopmentRuleTests(unittest.TestCase):
    def test_fit_is_train_only_and_rejects_duplicate_or_missing_rows(self):
        records, features = _training_data()
        validation = dict(records[0])
        validation["split"] = "validation"
        with self.assertRaises(ValueError):
            fit_rules(records + [validation], features)

        with self.assertRaises(ValueError):
            fit_rules(records + [dict(records[0])], features)

        incomplete = records[:-1]
        with self.assertRaises(ValueError):
            fit_rules(incomplete, features)

    def test_horizon_rule_uses_empirical_caps_and_measured_cost_order(self):
        records, features = _training_data()
        artifact = fit_rules(records, features)
        # At 7 days B3+GR is the cheapest eligible model under a 3 km tolerance;
        # the result follows measured runtimes, whose order is intentionally not
        # the force-count order.
        result = choose_model(artifact, "horizon_rule", 7, 3.0)
        self.assertEqual(result["model_id"], "B3+GR")
        self.assertFalse(result["fallback"])
        self.assertTrue(result["predicted_eligible"])
        self.assertGreaterEqual(float(artifact["horizon_caps_km"]["B2"]["7"]), 3.0)

    def test_physics_rule_uses_only_causal_proxies_and_rejects_identity_metadata(self):
        records, features = _training_data()
        artifact = fit_rules(records, features)
        forecast_features = dict(features[0]["features"])
        result = choose_model(artifact, "physics_rule", 7, 1.5, forecast_features)
        self.assertIn(result["model_id"], MODEL_IDS)
        with self.assertRaises(ValueError):
            choose_model(artifact, "physics_rule", 7, 1.5, {**forecast_features, "object_id": "future-label"})

    def test_json_roundtrip_and_strongest_cap_are_horizon_specific(self):
        records, features = _training_data()
        artifact = json.loads(json.dumps(fit_rules(records, features)))
        zero_proxies = {
            "planet_proxy_km": 0.0,
            "gr_proxy_km": 0.0,
            "small_body_proxy_km": 0.0,
        }
        short = choose_model(artifact, "physics_rule", 7, 1e-9, zero_proxies)
        long = choose_model(artifact, "physics_rule", 30, 1e-9, zero_proxies)
        self.assertEqual(short["candidate_predictions_km"][STRONGEST_MODEL_ID], 4.0)
        self.assertEqual(long["candidate_predictions_km"][STRONGEST_MODEL_ID], 10.0)
        self.assertEqual(long["model_id"], STRONGEST_MODEL_ID)
        self.assertTrue(long["fallback"])

    def test_no_candidate_retains_strongest_fallback(self):
        records, features = _training_data()
        artifact = fit_rules(records, features)
        result = choose_model(artifact, "horizon_rule", 30, 0.001)
        self.assertEqual(result["model_id"], STRONGEST_MODEL_ID)
        self.assertFalse(result["predicted_eligible"])
        self.assertTrue(result["fallback"])

    def test_fit_requires_features_for_every_training_task(self):
        records, features = _training_data()
        with self.assertRaises(ValueError):
            fit_rules(records, features[:-1])


if __name__ == "__main__":
    unittest.main()

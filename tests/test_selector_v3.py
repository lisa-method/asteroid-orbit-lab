import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from orbit_baselines import State  # noqa: E402
from planetary_dynamics import EphemerisInterpolator, NonGravitationalParameters, Perturber  # noqa: E402
from selector_v3 import FEATURE_NAMES, choose_v3, features_v3, fit_selector  # noqa: E402


class SelectorV3Tests(unittest.TestCase):
    @staticmethod
    def _context():
        def body(body_id, radius):
            states = tuple(State((radius, 0.0, 0.0), (0.0, 0.0, 0.0)) for _ in range(3))
            return Perturber(body_id, body_id, 1.0e-10, EphemerisInterpolator((2450000.0, 2450001.0, 2450002.0), states))

        return {
            "mu": 0.0002959122082855911,
            "au_km": 149597870.7,
            "day_s": 86400.0,
            "c_au_d": 173.144632674240,
            "planets": (body("399", 1.5), body("499", 2.0)),
            "small": (),
        }

    def test_features_have_fixed_dimension_and_explicit_ng_unavailable(self):
        context = self._context()
        initial = State((1.0, 0.0, 0.0), (0.0, math.sqrt(context["mu"]), 0.0))
        result = features_v3(initial, 2450000.0, 1.0, context, {"default_step_days": 0.25})
        self.assertEqual(result["feature_names"], list(FEATURE_NAMES))
        self.assertEqual(len(result["vector"]), 9)
        self.assertEqual(result["ng"]["status"], "not_provided")
        self.assertFalse(result["ng"]["applied"])
        self.assertEqual(result["vector"][-1], -30.0)
        self.assertIn("not provided", result["warnings"][0])
        self.assertFalse(result["accuracy_guaranteed"])

    def test_available_ng_is_used_only_after_causal_status_check(self):
        context = self._context()
        initial = State((1.0, 0.0, 0.0), (0.0, math.sqrt(context["mu"]), 0.0))
        params = NonGravitationalParameters(a1_au_d2=2.0e-12, a2_au_d2=-3.0e-12, a3_au_d2=4.0e-12)
        available = __import__("ng_inputs_v2").NGInput(
            parameters=params, source="test", available_from_jd_tdb=2450000.0, availability_basis="test gate"
        )
        result = features_v3(initial, 2450000.0, 1.0, context, {}, ng=available)
        self.assertTrue(result["ng"]["applied"])
        self.assertAlmostEqual(result["ng"]["amplitude_au_d2"], 9.0e-12)
        self.assertAlmostEqual(result["vector"][-1], math.log10(9.0e-12))

    def test_fit_and_choose_use_train_cost_and_no_object_id(self):
        models = ["cheap", "full"]
        training = []
        for object_index in range(4):
            for horizon in (7.0, 30.0):
                vector = [float(object_index), math.log10(horizon)]
                training.extend([
                    {"object_id": f"train-{object_index}", "split": "train", "horizon_days": horizon,
                     "model_id": "cheap", "features": vector, "max_position_error_km": 0.1 + object_index * 0.01,
                     "numerical_difference_km": 0.01, "runtime_median_seconds": 1.0},
                    {"object_id": f"train-{object_index}", "split": "train", "horizon_days": horizon,
                     "model_id": "full", "features": vector, "max_position_error_km": 0.05,
                     "numerical_difference_km": 0.005, "runtime_median_seconds": 5.0},
                ])
        calibration = []
        for object_index in range(2):
            for horizon in (7.0, 30.0):
                vector = [float(object_index) + 0.25, math.log10(horizon)]
                calibration.extend([
                    {"object_id": f"cal-{object_index}", "split": "calibration", "horizon_days": horizon,
                     "model_id": model, "features": vector, "max_position_error_km": 0.2 if model == "cheap" else 0.1,
                     "numerical_difference_km": 0.01, "runtime_median_seconds": 1.0 if model == "cheap" else 5.0}
                    for model in models
                ])
        artifact = fit_selector(training, calibration, models)
        feature_result = {"vector": [1.25, math.log10(7.0)], "strong_encounter": False}
        decision = choose_v3(feature_result, 7.0, 1.0, artifact)
        self.assertEqual(decision["model_id"], "cheap")
        self.assertEqual(decision["status"], "predicted_feasible")
        self.assertFalse(decision["fallback"])
        self.assertEqual(set(decision["predicted_error_caps_km"]), set(models))
        self.assertFalse(decision["accuracy_guaranteed"])

    def test_no_candidate_and_strong_encounter_force_full_status(self):
        rows = []
        for object_index in range(4):
            rows.append({"object_id": f"t{object_index}", "horizon_days": 7.0, "model_id": "cheap",
                         "features": [float(object_index)], "max_position_error_km": 2.0,
                         "numerical_difference_km": 0.1, "runtime_median_seconds": 1.0})
            rows.append({"object_id": f"t{object_index}", "horizon_days": 7.0, "model_id": "full",
                         "features": [float(object_index)], "max_position_error_km": 3.0,
                         "numerical_difference_km": 0.1, "runtime_median_seconds": 4.0})
        calibration = [dict(row, object_id=row["object_id"].replace("t", "c"), max_position_error_km=2.0)
                       for row in rows]
        artifact = fit_selector(rows, calibration, ["cheap", "full"])
        normal = choose_v3({"vector": [1.5], "strong_encounter": False}, 7.0, 0.01, artifact)
        self.assertEqual(normal["status"], "no_candidate_predicted")
        self.assertEqual(normal["model_id"], "full")
        strong = choose_v3({"vector": [1.5], "strong_encounter": True}, 7.0, 100.0, artifact)
        self.assertEqual(strong["status"], "strong_encounter_unvalidated")
        self.assertEqual(strong["model_id"], "full")
        self.assertTrue(strong["fallback"])

    def test_training_calibration_overlap_and_bad_rows_are_rejected(self):
        row = {"object_id": "same", "horizon_days": 7.0, "model_id": "m", "features": [1.0],
               "max_position_error_km": 1.0, "numerical_difference_km": 0.1, "runtime_median_seconds": 1.0}
        with self.assertRaises(ValueError):
            fit_selector([row], [dict(row)], ["m"])
        with self.assertRaises(ValueError):
            fit_selector([dict(row, object_id="a"), dict(row, object_id="a")],
                         [dict(row, object_id="b")], ["m"])
        with self.assertRaises(ValueError):
            fit_selector([dict(row, features=[math.nan])], [dict(row, object_id="b")], ["m"])
        artifact = {
            "schema_version": 3, "method": "cart_selector_v3", "models": ["m"],
            "horizons_days": [7.0], "feature_dimension": 1,
            "trees": {"m": {"schema_version": 1, "model": "cart_regression_tree", "n_features": 1,
                              "root": {"type": "leaf", "prediction": 1.0}}},
            "calibration_margin_log10": {"m": 0.0}, "median_cost_seconds": {"m": {"7.0": 1.0}},
        }
        with self.assertRaises(ValueError):
            choose_v3({"vector": [1.0]}, 8.0, 1.0, artifact)


if __name__ == "__main__":
    unittest.main()

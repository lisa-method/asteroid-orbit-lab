from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from selection_v2 import choose_model_v2, fit_horizon_rule_v2


class SelectionV2Tests(unittest.TestCase):
    def setUp(self):
        self.config = {"horizons_days": [7], "numerical_budget_fraction": .1, "fallback_model_id": "full",
                       "models": [dict(model_id=label, planets=planets, solar_gr=False,
                                       small_bodies=False, earth_j2=False, non_grav="off")
                                  for label, planets in (("cheap", False), ("full", True))]}
        self.records = [dict(object_id=obj, original_split="train", horizon_days=7., model_id=model,
                             max_position_error_km=error, numerical_difference_km=step, runtime_median_seconds=cost)
                        for obj in ("a", "b") for model, error, step, cost in
                        (("cheap", 2., 0., 1.), ("full", .01, .05, 3.))]

    def test_choose_cheapest_eligible_and_respect_numerical_budget(self):
        artifact = fit_horizon_rule_v2(self.records, self.config)
        self.assertEqual(choose_model_v2(7., 3., artifact, self.config)["model_id"], "cheap")
        self.assertEqual(choose_model_v2(7., 1., artifact, self.config)["model_id"], "full")
        decision = choose_model_v2(7., .1, artifact, self.config)
        self.assertTrue(decision["fallback"])
        self.assertFalse(decision["accuracy_guaranteed"])

    def test_validation_labels_and_incomplete_training_are_rejected(self):
        changed = deepcopy(self.records)
        changed[0]["original_split"] = "validation"
        for rows in (changed, self.records[:-1], self.records + [self.records[0]]):
            with self.assertRaises(ValueError):
                fit_horizon_rule_v2(rows, self.config)
        changed = deepcopy(self.records)
        changed[0]["split"] = "validation"
        with self.assertRaises(ValueError):
            fit_horizon_rule_v2(changed, self.config)

    def test_fallback_does_not_depend_on_catalogue_order(self):
        changed = deepcopy(self.config)
        changed["models"].reverse()
        artifact = fit_horizon_rule_v2(self.records, changed)
        self.assertEqual(choose_model_v2(7., .01, artifact, changed)["model_id"], "full")

    def test_changed_force_and_untrained_horizon_require_new_calibration(self):
        artifact = fit_horizon_rule_v2(self.records, self.config)
        changed = deepcopy(self.config)
        changed["models"][1]["solar_gr"] = True
        with self.assertRaises(ValueError):
            choose_model_v2(7., 1., artifact, changed)
        with self.assertRaises(ValueError):
            choose_model_v2(30., 1., artifact, self.config)
        with self.assertRaises(ValueError):
            choose_model_v2(7., float("nan"), artifact, self.config)


if __name__ == "__main__":
    unittest.main()

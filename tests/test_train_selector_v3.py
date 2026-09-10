"""The trainer cannot silently change original whole-object membership."""
import copy
import unittest

from train_selector_v3 import validate_design


class TrainingDesignTests(unittest.TestCase):
    def setUp(self):
        self.sample = {"objects": [{"id": str(i), "split": "train" if i < 18 else "validation",
                                    "start_date": "2029-01-01"} for i in range(30)]}
        self.config = {"models": [{"model_id": str(i)} for i in range(4)], "horizons_days": [7, 30, 90, 180, 365]}
        self.rows = [{"object_id": o["id"], "split": o["split"], "start_date": o["start_date"],
                      "horizon_days": h, "model_id": m["model_id"]}
                     for o in self.sample["objects"] for h in self.config["horizons_days"] for m in self.config["models"]]

    def test_original_design_and_no_duplicate_missing_row_swap(self):
        train, calibration = validate_design(self.sample, self.config, self.rows)
        self.assertEqual((len(train), len(calibration), len(train & calibration)), (18, 12, 0))
        altered = self.rows[:-1] + [self.rows[0]]
        with self.assertRaises(ValueError):
            validate_design(self.sample, self.config, altered)

    def test_split_and_initial_epoch_must_match_frozen_sample(self):
        for field, value in (("split", "validation"), ("start_date", "2029-01-02")):
            rows = copy.deepcopy(self.rows)
            rows[0][field] = value
            with self.assertRaises(ValueError):
                validate_design(self.sample, self.config, rows)


if __name__ == "__main__":
    unittest.main()

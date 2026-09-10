from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from run_selection_v2 import validate_matrix


class SelectionV2RunnerTests(unittest.TestCase):
    def test_duplicate_and_conflicting_split_cannot_hide_missing_cases(self):
        sample = {"objects": [{"id": "a", "split": "train"}, {"id": "b", "split": "validation"}]}
        config = {"horizons_days": [7], "models": [{"model_id": "M"}]}
        train = {"object_id": "a", "split": "train", "horizon_days": 7, "model_id": "M"}
        validation = {"object_id": "b", "split": "validation", "horizon_days": 7, "model_id": "M"}
        validate_matrix([train, validation], sample, config)
        for rows in ([train, train], [train, {**validation, "original_split": "train"}], [train]):
            with self.assertRaises(ValueError):
                validate_matrix(rows, sample, config)


if __name__ == "__main__":
    unittest.main()

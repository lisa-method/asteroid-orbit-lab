"""Whole-object roles and fixed train cost source cannot silently change."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

import train_selector_v4 as trainer


class TrainingV4DesignTests(unittest.TestCase):
    def fixture(self):
        config = {"models": [{"model_id": str(i)} for i in range(4)], "horizons_days": [7, 30, 90, 180, 365]}
        docs = {trainer.CONFIG: config, trainer.OLD_METHOD: {"hashes": {"old": "hash"}},
                trainer.OLD_MODEL: {"training_object_ids": [str(i) for i in range(18)],
                    "median_cost_seconds": {str(i): {str(float(h)): i+1. for h in config["horizons_days"]} for i in range(4)}}}
        offset = 0
        for namespace, sample_path, matrix_path in trainer.SOURCES:
            count = {"development30": 30, "fresh_holdout12": 12, "selector_holdout24": 24}[namespace]
            objects = [{"id": str(offset+i), "split": ("train" if i < 18 else "validation") if namespace == "development30" else namespace,
                        "start_date": "2029-01-01"} for i in range(count)]
            records = [{"object_id": o["id"], "split": o["split"], "start_date": o["start_date"],
                        "horizon_days": h, "model_id": m["model_id"], "max_position_error_km": .1,
                        "numerical_difference_km": .001, "runtime_median_seconds": 999.}
                       for o in objects for h in config["horizons_days"] for m in config["models"]]
            docs[sample_path] = {"objects": objects}
            docs[matrix_path] = {"provenance": {}, "records": [{"record": r} for r in records] if namespace == "selector_holdout24" else records}
            offset += count
        return docs

    def load(self, docs):
        with patch.object(trainer, "read", side_effect=lambda root, rel: copy.deepcopy(docs[rel])), \
             patch.object(trainer, "sha", return_value="sha"), patch.object(trainer, "check_hashes"):
            return trainer.load_design(Path("/synthetic"))

    def test_roles_are_42_24_and_costs_use_original_training_only(self):
        _, objects, records, _ = self.load(self.fixture())
        self.assertEqual(sum(o["split"] == "train" for o in objects), 42)
        self.assertEqual(sum(o["split"] == "calibration" for o in objects), 24)
        self.assertEqual(len(records), 1320)
        self.assertTrue(all(r["runtime_median_seconds"] == int(r["model_id"])+1. for r in records))

    def test_duplicate_row_or_changed_target_epoch_rejected(self):
        path = trainer.SOURCES[0][2]
        for kind in ("duplicate", "epoch"):
            docs = self.fixture()
            if kind == "duplicate":
                docs[path]["records"][-1] = docs[path]["records"][0]
            else:
                docs[path]["records"][0]["start_date"] = "2029-01-02"
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.load(docs)


if __name__ == "__main__":
    unittest.main()

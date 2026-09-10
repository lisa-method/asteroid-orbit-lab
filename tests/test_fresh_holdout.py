import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from prepare_fresh_holdout import select_objects
from run_fresh_holdout import choices_and_summary
from selection_v2 import fit_horizon_rule_v2


class FreshHoldoutTests(unittest.TestCase):
    def test_sampling_is_object_disjoint_and_metadata_only(self):
        fields = ["des", "dist", "jd", "cd", "body"]
        rows = []
        for index, body in enumerate(("Earth", "Venus", "Mars", "Jupiter")):
            for k in range(4):
                rows.append([str(100+index*10+k), str(.1+k*.01), str(2461771.5+k), "2028-Jan-01 00:00", body])
        documents = {"cad_2026_2029_02au": {"fields": fields, "data": rows},
                     "cad_jupiter_2026_2029_05au": {"fields": fields, "data": rows}}
        for i, group in enumerate(("inner_controls", "outer_controls")):
            documents[group] = {"fields": ["pdes", "full_name"], "data": [[str(200+i*10+k), "control"] for k in range(4)]}
        objects = select_objects(documents, {"100", "110", "120", "130"})
        self.assertEqual(len(objects), 12)
        self.assertEqual(len({o["id"] for o in objects}), 12)
        self.assertFalse({o["id"] for o in objects} & {"100", "110", "120", "130"})
        self.assertEqual(objects, select_objects(documents, {"100", "110", "120", "130"}))
        self.assertTrue(all(o["split"] == "fresh_holdout" for o in objects))
        documents["cad_jupiter_2026_2029_05au"]["data"] = []
        with self.assertRaises(ValueError):
            select_objects(documents, set())

    def fixture(self):
        config = json.loads((Path(__file__).resolve().parents[1] / "configs/force_models_v2.json").read_text())
        train = []
        for h in config["horizons_days"]:
            for i, m in enumerate(config["models"]):
                train.append({"object_id": "train", "horizon_days": h, "split": "train", "model_id": m["model_id"],
                              "max_position_error_km": 100 if i < 2 else .01, "numerical_difference_km": .0001,
                              "runtime_median_seconds": i+1.})
        artifact = fit_horizon_rule_v2(train, config)
        sample = {"objects": [{"id": "freshA"}, {"id": "freshB"}]}
        rows = [{**r, "object_id": o["id"], "split": "fresh_holdout"} for o in sample["objects"] for r in train]
        return config, artifact, sample, rows

    def test_selector_miss_and_no_candidate_are_separate_and_preserved(self):
        config, artifact, sample, records = self.fixture()
        for row in records:
            if row["object_id"] == "freshA" and row["horizon_days"] == 90 and row["model_id"] == "V2-P-GR":
                row["max_position_error_km"] = .14
            if row["object_id"] == "freshB" and row["horizon_days"] == 365:
                row["max_position_error_km"] = 20.
        original = copy.deepcopy(artifact)
        choices, summaries = choices_and_summary(sample, config, artifact, records)
        strict = next(s for s in summaries if s["tolerance_km"] == .1)
        self.assertEqual(strict["cases"], 10)
        self.assertEqual(strict["avoidable_selection_misses"], 1)
        self.assertEqual(strict["no_candidate_cases"], 1)
        self.assertEqual(strict["no_candidate_flagged"], 0)
        self.assertEqual(strict["selector_eligible_cases"], 8)
        self.assertEqual(artifact, original)
        self.assertEqual(len(choices), 30)

    def test_incomplete_duplicate_nonfinite_matrix_rejected(self):
        config, artifact, sample, records = self.fixture()
        for invalid in (records[:-1], records+[records[0]]):
            with self.assertRaises(ValueError):
                choices_and_summary(sample, config, artifact, invalid)
        records[0]["max_position_error_km"] = float("nan")
        with self.assertRaises(ValueError):
            choices_and_summary(sample, config, artifact, records)


if __name__ == "__main__":
    unittest.main()

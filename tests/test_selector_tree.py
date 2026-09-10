import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from selector_tree import fit_tree, predict_tree  # noqa: E402


class SelectorTreeTests(unittest.TestCase):
    def test_nonlinear_regression_is_split_deterministically(self):
        features = [[float(index)] for index in range(8)]
        targets = [0.0, 0.0, 0.0, 0.0, 10.0, 10.0, 10.0, 10.0]
        ids = [f"object-{index}" for index in range(8)]
        tree = fit_tree(features, targets, ids, max_depth=2, min_leaf_objects=2)
        self.assertEqual(tree["root"]["type"], "split")
        self.assertEqual(tree["root"]["feature_index"], 0)
        self.assertEqual(tree["root"]["threshold"], 3.5)
        self.assertEqual(predict_tree(tree, [1.2]), 0.0)
        self.assertEqual(predict_tree(tree, [6.8]), 10.0)

    def test_minimum_distinct_objects_applies_to_both_children(self):
        features = [[0.0], [1.0], [2.0], [3.0], [4.0], [5.0]]
        targets = [0.0, 1.0, 2.0, 10.0, 11.0, 12.0]
        ids = ["a", "a", "b", "c", "d", "d"]
        tree = fit_tree(features, targets, ids, max_depth=3, min_leaf_objects=2)
        root = tree["root"]
        self.assertEqual(root["type"], "split")
        self.assertGreaterEqual(root["left"]["object_count"], 2)
        self.assertGreaterEqual(root["right"]["object_count"], 2)
        self.assertTrue(root["threshold"] in (2.5, 3.5))

    def test_json_round_trip_preserves_prediction(self):
        tree = fit_tree(
            [[0.0, 1.0], [1.0, 0.0], [2.0, 1.0], [3.0, 0.0]],
            [1.0, 2.0, 4.0, 8.0],
            ["a", "b", "c", "d"],
            max_depth=2,
            min_leaf_objects=1,
        )
        restored = json.loads(json.dumps(tree, sort_keys=True))
        for vector in ([0.1, 0.9], [1.9, 0.8], [2.9, 0.1]):
            self.assertEqual(predict_tree(tree, list(vector)), predict_tree(restored, list(vector)))

    def test_constant_target_and_duplicate_feature_values_are_valid(self):
        tree = fit_tree(
            [[1.0], [1.0], [2.0], [2.0]],
            [3.0, 3.0, 3.0, 3.0],
            ["a", "b", "c", "d"],
            max_depth=3,
            min_leaf_objects=1,
        )
        self.assertEqual(tree["root"]["type"], "leaf")
        self.assertEqual(predict_tree(tree, [1.5]), 3.0)

    def test_malformed_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            fit_tree([[0.0]], [], ["a"])
        with self.assertRaises(ValueError):
            fit_tree([[0.0], [float("nan")]], [0.0, 1.0], ["a", "b"])
        with self.assertRaises(ValueError):
            fit_tree([[0.0], [1.0, 2.0]], [0.0, 1.0], ["a", "b"])
        with self.assertRaises(ValueError):
            fit_tree([[0.0]], [0.0], ["a"], max_depth=True)
        with self.assertRaises(ValueError):
            fit_tree([[0.0]], [0.0], ["a"], min_leaf_objects=0)
        tree = fit_tree([[0.0], [1.0]], [0.0, 1.0], ["a", "b"], min_leaf_objects=1)
        with self.assertRaises(ValueError):
            predict_tree(tree, [float("inf")])
        with self.assertRaises(ValueError):
            predict_tree({"model": "cart_regression_tree"}, [0.0])


if __name__ == "__main__":
    unittest.main()

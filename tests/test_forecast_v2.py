import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from forecast_v2 import load_bound_ng


class ForecastV2InputTests(unittest.TestCase):
    def test_other_objects_header_is_rejected_before_loading_forces(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "header.json"
            path.write_text(json.dumps({"result": "Rec #: 123\n$$SOE\nRec #: 456"}))
            for object_id in (None, "", "456"):
                with self.assertRaises(ValueError):
                    load_bound_ng(path, object_id)
            self.assertEqual(load_bound_ng(path, "123").status(2462000.), "not_provided")


if __name__ == "__main__":
    unittest.main()

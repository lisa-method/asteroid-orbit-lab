import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from download_development_data import build_jobs
from prepare_development_sample import rows


class SampleTests(unittest.TestCase):
    def test_dynamic_fields_are_respected_and_mismatch_rejected(self):
        self.assertEqual(rows({"fields": ["dist", "des"], "data": [["0.2", "123"]]}), [{"dist": "0.2", "des": "123"}])
        with self.assertRaises(ValueError):
            rows({"fields": ["dist", "des"], "data": [["0.2"]]})

    def test_teacher_refinement_does_not_change_propagation_horizon(self):
        root = Path(__file__).resolve().parents[1]
        config = json.loads((root / "configs/development30.json").read_text())
        obj = {"id": "153814", "name": "example", "start_date": "2028-05-27",
               "event": {"cd": "2028-Jun-26 05:23"}}
        jobs = build_jobs(root, config, {"objects": [obj]})
        from urllib.parse import urlsplit, parse_qs
        daily = parse_qs(urlsplit(jobs[-2]["url"]).query)
        refined = parse_qs(urlsplit(jobs[-1]["url"]).query)
        self.assertEqual(daily["START_TIME"], ["'2028-05-27'"])
        self.assertEqual(daily["STOP_TIME"], ["'2029-05-27'"])
        self.assertEqual(refined["STEP_SIZE"], ["'5 m'"])
        self.assertEqual(refined["START_TIME"], ["'2028-06-24'"])
        self.assertEqual(len(jobs), 11)


if __name__ == "__main__":
    unittest.main()

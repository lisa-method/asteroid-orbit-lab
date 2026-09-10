from __future__ import annotations
import hashlib, json, sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import report_selector_confirmation as report

class ConfirmationReportSyntheticTests(unittest.TestCase):
    def test_report_uses_one_km_percentiles_failure_residual_and_timing(self):
        with TemporaryDirectory() as td:
            root = Path(td); out = root / report.OUT; out.mkdir(parents=True)
            objects = [{"id": f"O{i:03d}", "name": f"Object {i}", "stratum": f"S{i%9}", "start_date": "2029-01-01", "event": None} for i in range(100)]
            sample = {"schema_version": 1, "objects": objects, "timing_object_ids": [f"O{i:03d}" for i in range(24)]}
            (root / report.SAMPLE).parent.mkdir(parents=True); (root / report.SAMPLE).write_text(json.dumps(sample))
            records=[]; choices=[]
            models=("V2-B2", "V2-P", "V2-P-GR", "V2-P-GR-SB16")
            for obj in objects:
                for h in report.HORIZONS:
                    for model in models:
                        error = 0.2 if model == "V2-P-GR-SB16" and obj["id"] == "O000" and h == 90.0 else 0.01
                        rec={"max_position_error_km": error, "numerical_difference_km": 0.2 if error > .1 else .001, "eligibility": {str(t): error <= t and .2 <= .1*t for t in report.TOLS}}
                        records.append({"object_id": obj["id"], "model_id": model, "horizon_days": h, "record": rec})
            for obj in objects:
                for h in report.HORIZONS:
                    for method in report.METHODS:
                        for tol in report.TOLS:
                            model = "V2-P-GR-SB16"
                            rec = next(r["record"] for r in records if r["object_id"]==obj["id"] and r["horizon_days"]==h and r["model_id"]==model)
                            choices.append({"object_id":obj["id"],"horizon_days":h,"method":method,"tolerance_km":tol,"model_id":model,"max_position_error_km":rec["max_position_error_km"],"numerical_difference_km":rec["numerical_difference_km"],"actual_eligible":rec["max_position_error_km"]<=tol and rec["numerical_difference_km"]<=.1*tol,"warning":False,"strong_encounter":False,"outside_training_support":False,"no_candidate_truth":False,"no_candidate_predicted":False,"outside_training_support_reasons":[]})
            summaries=[{"method":m,"tolerance_km":t,"eligible_cases":len(objects)*len(report.HORIZONS),"warning_cases":0,"no_candidate_cases":0,"worst_position_error_km":.2} for m in report.METHODS for t in report.TOLS]
            matrix={"record_count":2000,"records":records,"choices":choices,"summaries":summaries}
            mp=out/"matrix.json"; mp.write_text(json.dumps(matrix)); cp=out/"direct_cost.json"
            timings=[{"object_id":f"O{i:03d}","horizon_days":h,"method":m,"runtime_median_seconds":1.0 if m=="fixed_full" else .5,"runtime_trials_seconds":[.5,.5,.5],"feature_runtime_trials_seconds":[]} for i in range(24) for h in report.HORIZONS for m in report.METHODS]
            cost={"timings":timings,"full_cost":{m:{"total_seconds":120.0 if m=="fixed_full" else 60.0} for m in report.METHODS}}; cp.write_text(json.dumps(cost))
            vp=out/"verification.json"; verification={"passed":True,"matrix_sha256":hashlib.sha256(mp.read_bytes()).hexdigest(),"timing_sha256":hashlib.sha256(cp.read_bytes()).hexdigest()}; vp.write_text(json.dumps(verification))
            (root/report.MANIFEST).parent.mkdir(parents=True); (root/report.MANIFEST).write_text(json.dumps({"complete":True,"sample_sha256":hashlib.sha256((root/report.SAMPLE).read_bytes()).hexdigest(),"experiment_freeze_sha256":"x"}))
            ep=out/"experiment_freeze.json"; ep.write_text(json.dumps({"hashes":{report.SAMPLE:hashlib.sha256((root/report.SAMPLE).read_bytes()).hexdigest()}})); manifest=json.loads((root/report.MANIFEST).read_text()); manifest["experiment_freeze_sha256"]=hashlib.sha256(ep.read_bytes()).hexdigest(); (root/report.MANIFEST).write_text(json.dumps(manifest))
            (out/"raw_verification.json").write_text(json.dumps({"passed":True,"manifest_sha256":{report.MANIFEST:hashlib.sha256((root/report.MANIFEST).read_bytes()).hexdigest()}}))
            result=report.report(root)
            text=(root/"docs/SELECTOR_CONFIRMATION100_REPORT.md").read_text()
            self.assertEqual(result["records"],2000); self.assertIn("0.2", text); self.assertIn("+50.00%", text)

if __name__ == "__main__": unittest.main()

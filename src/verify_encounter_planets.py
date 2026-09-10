"""Verify the completed paired experiment against its sources and B2 snapshot."""

import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess


def read(path):
    return json.loads(path.read_text())


GEOMETRY_KEYS = ("time_days", "distance_au", "distance_km", "relative_speed_km_s", "eta_helio_at_closest",
                 "rho_sun_proxy_at_closest", "boundary_minimum", "zero_separation",
                 "bracket_gap_days_at_minimum", "sample_count", "max_knot_gap_days")


def compare_geometry(current, previous, au_km, *, require_exact):
    """Apply the separately documented cross-runtime engineering budgets."""
    exact = all(current[k] == previous[k] for k in GEOMETRY_KEYS)
    differences = {"distance_km": abs(current["distance_km"] - previous["distance_km"]),
                   "time_seconds": abs(current["time_days"] - previous["time_days"]) * 86400,
                   "relative_speed_km_s": abs(current["relative_speed_km_s"] - previous["relative_speed_km_s"])}
    if require_exact:
        assert exact, "Same-runtime geometry regression changed"
    else:
        absolute = {"distance_km": 1e-5, "distance_au": 1e-5 / au_km,
                    "time_days": .01 / 86400, "relative_speed_km_s": 1e-8}
        for key in GEOMETRY_KEYS:
            actual, expected = current[key], previous[key]
            if actual == expected:
                continue
            assert actual is not None and expected is not None, key
            if key in absolute:
                assert abs(actual - expected) <= absolute[key], (key, actual, expected)
            elif key in ("eta_helio_at_closest", "rho_sun_proxy_at_closest"):
                assert math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-14), key
            else:
                raise AssertionError((key, actual, expected))
    return {"exact": exact, **differences}


def geometry_summary(comparisons):
    return {"rows": len(comparisons), "exact": all(r["exact"] for r in comparisons),
            "max_distance_difference_km": max(r["distance_km"] for r in comparisons),
            "max_time_difference_seconds": max(r["time_seconds"] for r in comparisons),
            "max_relative_speed_difference_km_s": max(r["relative_speed_km_s"] for r in comparisons)}


def verify(root: Path) -> dict:
    output = root / "outputs/encounter_screening/planets_pilot6"
    result = read(output / "results.json")
    features = read(output / "forecast_features.json")
    references = read(output / "reference_minima.json")
    audit = read(output / "numerical_audit.json")
    rows, timings, cases = result["evaluation_rows"], result["timings"], result["cases"]
    assert not result["smoke"]
    assert (len(cases), len(timings), len(rows), len(features), len(references), len(audit)) == (34, 136, 1224, 1224, 306, 54)
    assert audit == result["numerical_audit"]
    assert len({r["case_id"] for r in cases}) == len(cases)
    key = lambda r: (r["case_id"], r["strategy_id"], r["body_id"])
    assert len({key(r) for r in rows}) == len(rows)
    assert {key(r) for r in rows} == {key(r) for r in features}
    body_ids = {"199", "299", "399", "301", "4", "5", "6", "7", "8"}
    for case in cases:
        for strategy in result["config"]["strategies"]:
            selected = [r for r in rows if r["case_id"] == case["case_id"] and r["strategy_id"] == strategy["strategy_id"]]
            assert {r["body_id"] for r in selected} == body_ids
    forbidden = {"group", "lead_to_anchor_days", "event_date", "reference_distance_km", "outcomes", "distance_error_km", "split"}
    ref_by_key = {(r["case_id"], r["body_id"]): r for r in references}
    for f in features:
        assert not forbidden.intersection(f)
    def finite(value):
        if isinstance(value, float):
            assert math.isfinite(value)
        elif isinstance(value, dict):
            for v in value.values():
                finite(v)
        elif isinstance(value, list):
            for v in value:
                finite(v)
    finite(result)
    finite(features)
    row_by_key = {key(r): r for r in rows}
    assert len({key(r) for r in audit}) == len(audit)
    for entry in audit:
        production = row_by_key[key(entry)]
        fine = entry["fine_minimum"]
        assert entry["production_step_scale"] == result["config"]["step_scale"]
        assert entry["fine_step_scale"] == result["config"]["numerical_audit"]["fine_step_scale"]
        assert entry["production_distance_km"] == production["distance_km"]
        assert entry["fine_distance_km"] == fine["distance_km"]
        assert entry["production_minus_fine_distance_km"] == production["distance_km"] - fine["distance_km"]
        assert entry["production_minus_fine_time_seconds"] == (production["time_days"] - fine["time_days"]) * result["constants"]["day_s"]
    for row in rows:
        ref = ref_by_key[row["case_id"], row["body_id"]]
        assert row["distance_error_km"] == row["distance_km"] - ref["distance_km"]
        assert row["time_error_hours"] == (row["time_days"] - ref["time_days"]) * 24
        for threshold in result["config"]["distance_thresholds_au"]:
            true = ref["distance_au"] <= threshold
            pred = row["distance_au"] <= threshold
            outcome = {(True, True): "TP", (True, False): "FN", (False, True): "FP", (False, False): "TN"}[true, pred]
            assert row["outcomes"][f"{threshold:g}"] == outcome
    for summary in result["confusion"]:
        selected = [r for r in rows if r["group"] == summary["group"] and r["strategy_id"] == summary["strategy_id"]]
        for outcome in ("TP", "FN", "FP", "TN"):
            assert summary[outcome] == sum(r["outcomes"][f"{summary['threshold_au']:g}"] == outcome for r in selected)
    assert len({(r["case_id"], r["strategy_id"]) for r in timings}) == len(timings)
    for row in timings:
        trials = row["runtime_trials_seconds"]
        assert len(trials) == 3 and min(trials) > 0
        assert row["runtime_median_seconds"] == statistics.median(trials)
        assert row["runtime_min_seconds"] == min(trials)
        assert row["runtime_max_seconds"] == max(trials)
        assert row["force_evaluations"] == row["rk4_steps"] * 4
    verifier_revision = None
    for path, expected in result["source_sha256_at_start"].items():
        actual = hashlib.sha256((root / path).read_bytes()).hexdigest()
        if actual != expected and (root / path).resolve() == Path(__file__).resolve():
            # Only this verification tool may be revised after the measured
            # run; retain its original source. Numerical sources stay strict.
            archived = output / "provenance/verify_encounter_planets_at_run.py"
            assert hashlib.sha256(archived.read_bytes()).hexdigest() == expected, path
            verifier_revision = {"path": path, "at_run_sha256": expected, "current_sha256": actual,
                                 "archived_source": archived.relative_to(root).as_posix()}
        else:
            assert actual == expected, path
    previous = root / "outputs/encounter_screening/pilot6"
    previous_result = read(previous / "results.json")
    same_runtime = result["python"] == previous_result["python"]
    au_km = result["constants"]["au_km"]
    previous_features = {(r["case_id"], r["body_id"]): r for r in read(previous / "forecast_features.json")
                         if r["strategy_id"] == "daily_hermite"}
    b2_comparisons = []
    for row in features:
        if row["strategy_id"] == "B2_daily":
            old = previous_features[row["case_id"], row["body_id"]]
            b2_comparisons.append(compare_geometry(row, old, au_km, require_exact=same_runtime))
    old_references = read(previous / "reference_minima.json")
    assert len(references) == len(old_references)
    reference_comparisons = []
    for row, old in zip(references, old_references):
        assert {k:v for k,v in row.items() if k not in GEOMETRY_KEYS} == {k:v for k,v in old.items() if k not in GEOMETRY_KEYS}
        reference_comparisons.append(compare_geometry(row, old, au_km, require_exact=same_runtime))
    checked = {}
    for manifest in (root / "data/checksums").glob("*.json"):
        for record in read(manifest)["files"]:
            path = Path(record["path"])
            contents = path.read_bytes()
            assert len(contents) == record["bytes"], str(path)
            assert hashlib.sha256(contents).hexdigest() == record["sha256"], str(path)
            checked[str(path)] = record["sha256"]
    assert len(checked) == 40
    paths = [*checked, str(output / "results.json"), str(previous / "results.json")]
    ignored = subprocess.run(["git", "check-ignore", "--stdin"], input="\n".join(paths)+"\n",
                             text=True, cwd=root, check=True, capture_output=True)
    assert set(ignored.stdout.splitlines()) == set(paths)
    verification = {"status": "PASS", "cases": len(cases), "forecasts": len(timings),
                    "feature_rows": len(features), "reference_rows": len(references), "audit_rows": len(audit),
                    "source_hashes_checked": len(result["source_sha256_at_start"]), "raw_files_checked": len(checked),
                    "runtime_comparison": {"current_python": result["python"], "previous_python": previous_result["python"],
                                           "mode": "exact" if same_runtime else "bounded_cross_runtime",
                                           "protocol": "docs/ENCOUNTER_RUNTIME_COMPATIBILITY.md"},
                    "B2_geometry_regression": geometry_summary(b2_comparisons),
                    "reference_geometry_regression": geometry_summary(reference_comparisons),
                    "verifier_revision_after_run": verifier_revision,
                    "feature_boundary_alerts_costs_finite_coverage_git_ignore": "PASS",
                    "results_sha256": hashlib.sha256((output / "results.json").read_bytes()).hexdigest()}
    (output / "verification.json").write_text(json.dumps(verification, indent=2) + "\n")
    return verification


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(verify(args.root.resolve()), indent=2))


if __name__ == "__main__":
    main()

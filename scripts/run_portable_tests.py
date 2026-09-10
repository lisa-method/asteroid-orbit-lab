"""Run frozen tests with explicit skips for five unpublished-raw dependencies."""
from __future__ import annotations

import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_REQUIREMENTS = {
    "test_ng_inputs_v2.NGInputV2Tests.test_known_bound_requires_provenance":
        "data/raw/development30/asteroids/asteroid_613569_daily.json",
    **{
        f"test_sbdb_covariance.SBDBCovarianceTests.{name}":
            "data/raw/apophis_covariance/sbdb_cov_vec.json"
        for name in (
            "test_real_response_preserves_epoch_cross_terms_and_ng_separately",
            "test_ng_delta_does_not_change_kepler_state",
            "test_missing_covariance_and_bad_labels_are_rejected",
            "test_bad_units_kinds_duplicates_and_nonfinite_values_are_rejected",
        )
    },
}


def cases(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from cases(test)
        else:
            yield test


def main() -> int:
    os.chdir(ROOT)
    tests = list(cases(unittest.defaultTestLoader.discover(str(ROOT / "tests"))))
    known_ids = {test.id() for test in tests}
    if not RAW_REQUIREMENTS.keys() <= known_ids:
        raise RuntimeError("Raw dependency allowlist no longer matches the frozen suite")
    for test in tests:
        requirement = RAW_REQUIREMENTS.get(test.id())
        if requirement and not (ROOT / requirement).is_file():
            def unavailable(path=requirement):
                raise unittest.SkipTest(f"Historical raw archive not included: {path}")
            setattr(test, test._testMethodName, unavailable)
    result = unittest.TextTestRunner(verbosity=1).run(unittest.TestSuite(tests))
    print(f"Discovered {result.testsRun} tests; explicitly skipped {len(result.skipped)} raw-dependent cases.")
    for test, reason in result.skipped:
        print(f"SKIP {test.id()}: {reason}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())

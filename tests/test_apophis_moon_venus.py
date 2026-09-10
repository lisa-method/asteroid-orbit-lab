from __future__ import annotations

import json
import math
import sys
import unittest
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from orbit_baselines import State
from run_apophis_moon_venus import _decimate, _hermite, _normalize_trace, _matrix, _atomic_json


class ApophisMoonVenusTests(unittest.TestCase):
    def test_paired_shift_matches_solver_and_tolerance(self):
        rows=[]
        for arm,shift in (("old",0.),("changed",2.)):
            for label,origin in (("tight",10.),("tighter",100.)):
                rows.append({"arm":arm,"solver":"dopri54","setting":{"label":label},
                    "requested_states":[[origin+shift,0,0,0,0,0]]})
        matrix=_matrix(rows,1.,1.,{"arms":[{"id":"old"},{"id":"changed"}]})
        changed=[r for r in matrix["to_old_same_solver"] if r["arm"]=="changed"]
        self.assertEqual([r["shift"]["max_position_shift_km"] for r in changed],[2.,2.])

    def test_hermite_cubic_and_velocity(self):
        trace=[[0.,[0,0,0,0,0,0]],[2.,[8,0,0,12,0,0]]]
        state=_hermite(trace,[.75])[0]
        self.assertAlmostEqual(state.position[0],.75**3)
        self.assertAlmostEqual(state.velocity[0],3*.75**2)
        for bad in ([float("nan")],[-1.],[3.]):
            with self.assertRaises(ValueError):
                _hermite(trace,bad)
        with self.assertRaises(ValueError):
            _hermite(list(reversed(trace)),[1.])

    def test_frozen_artifact_refuses_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"freeze.json"
            _atomic_json(path,{"hash":"one"})
            before=path.stat().st_mtime_ns
            _atomic_json(path,{"hash":"one"})
            self.assertEqual(path.stat().st_mtime_ns,before)
            with self.assertRaises(RuntimeError):
                _atomic_json(path,{"hash":"two"})
            self.assertEqual(json.loads(path.read_text()),{"hash":"one"})

    def test_decimation_requires_endpoint(self) -> None:
        rows = [{"epoch_jd_tdb": float(i)} for i in range(9)]
        self.assertEqual([r["epoch_jd_tdb"] for r in _decimate(rows, 4)], [0.0, 4.0, 8.0])
        with self.assertRaises(ValueError):
            _decimate(rows[:-1], 4)

    def test_hermite_preserves_knots_and_linear_state(self) -> None:
        trace = [[0.0, [0.0, 0.0, 0.0, 1.0, 0.0, 0.0]], [1.0, [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]]]
        values = _hermite(trace, [0.0, 0.5, 1.0])
        self.assertEqual(values[0], State((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)))
        self.assertEqual(values[-1], State((1.0, 0.0, 0.0), (1.0, 0.0, 0.0)))
        self.assertAlmostEqual(values[1].position[0], 0.5)
        self.assertTrue(all(math.isfinite(x) for x in (*values[1].position, *values[1].velocity)))

    def test_rk4_trace_origin_is_explicit(self) -> None:
        initial = State((1.0, 2.0, 3.0), (4.0, 5.0, 6.0))
        normalized = _normalize_trace({"accepted_endpoints": [[0.25, [2, 3, 4, 4, 5, 6]]]}, 100.0, initial, "rk4")
        self.assertEqual(normalized[0][0], 100.0)
        self.assertEqual(normalized[1][0], 100.25)
        self.assertEqual(normalized[0][1], [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])

    def test_dp_trace_already_has_absolute_initial_endpoint(self) -> None:
        initial = State((1.0, 2.0, 3.0), (4.0, 5.0, 6.0))
        normalized = _normalize_trace({"accepted_endpoints": [[100.0, [1, 2, 3, 4, 5, 6]], [100.25, [2, 3, 4, 4, 5, 6]]]}, 100.0, initial, "dopri54")
        self.assertEqual(len(normalized), 2)
        self.assertEqual(normalized[0][0], 100.0)

    def test_config_has_nine_arms_and_twenty_two_runs(self) -> None:
        config = json.loads((ROOT / "configs/apophis_moon_venus.json").read_text())
        self.assertEqual(len(config["arms"]), 9)
        self.assertEqual(9 * len(config["dp_tolerance_labels"]) + len(config["rk4_arms"]) * len(config["rk4_scales"]), 22)
        self.assertEqual(config["download_windows"][0]["rows"], 2305)


if __name__ == "__main__":
    unittest.main()

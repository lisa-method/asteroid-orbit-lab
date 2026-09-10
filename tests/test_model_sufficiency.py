from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from orbit_baselines import State, propagate_variable_step, two_body_acceleration
from run_model_sufficiency import merge_rows


class BenchmarkProtocolTests(unittest.TestCase):
    def test_observer_preserves_integration_and_prefix_does_not_use_future(self):
        initial = State((1.0, 0.0, 0.0), (0.0, 0.0172, 0.0))
        force = two_body_acceleration(2.959122082855911e-4)
        snapshots = []
        observed, steps = propagate_variable_step(
            initial, [0.0, 1.0, 2.0], force, lambda _t, _s: 0.3,
            on_output=lambda t, state, count: snapshots.append((t, state, count)),
        )
        plain, plain_steps = propagate_variable_step(initial, [0.0, 1.0, 2.0], force, lambda _t, _s: 0.3)
        prefix, prefix_steps = propagate_variable_step(initial, [0.0, 1.0], force, lambda _t, _s: 0.3)
        self.assertEqual(observed, plain)
        self.assertEqual(steps, plain_steps)
        self.assertEqual(prefix, observed[:2])
        self.assertEqual(prefix_steps, snapshots[1][2])
        self.assertEqual([row[0] for row in snapshots], [0.0, 1.0, 2.0])
        self.assertEqual([row[1] for row in snapshots], observed)
        self.assertEqual(snapshots[0][2], 0)
        self.assertEqual(snapshots[-1][2], steps)

    def test_refinement_retains_outer_coverage_and_replaces_coincident_nodes(self):
        daily = [{"epoch_jd_tdb": t, "value": "daily"} for t in [0.0, 1.0, 2.0, 3.0]]
        high = [{"epoch_jd_tdb": t, "value": "refined"} for t in [1.0, 1.5, 2.0]]
        merged = merge_rows(daily, high)
        self.assertEqual([r["epoch_jd_tdb"] for r in merged], [0.0, 1.0, 1.5, 2.0, 3.0])
        self.assertEqual([r["value"] for r in merged], ["daily", "refined", "refined", "refined", "daily"])
        self.assertEqual(len(daily), 4)


if __name__ == "__main__":
    unittest.main()

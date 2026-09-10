import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from orbit_baselines import State
from planetary_dynamics import EphemerisInterpolator, Perturber
from run_development_benchmark import _object_checkpoint, forecast_candidate, load_object_checkpoint


class DevelopmentBenchmarkTests(unittest.TestCase):
    def _context(self, future_shift=0.0):
        epochs = tuple(float(i) for i in range(8))
        states = tuple(
            State((2.0 + future_shift * max(0.0, epoch - 3.0), 0.01 * epoch, 0.0), (0.0, 0.01, 0.0))
            for epoch in epochs
        )
        body = Perturber("399", "Earth", 1.0e-8, EphemerisInterpolator(epochs, states))
        return {
            "mu": 0.0003,
            "au_km": 1.0,
            "day_s": 1.0,
            "c_au_d": 1.0e8,
            "planets": (body,),
            "small": (),
        }

    def _model(self, model_id="B3"):
        return {"model_id": model_id, "planets": model_id != "B2", "solar_gr": False, "small_bodies": False}

    def test_rollout_prefix_does_not_depend_on_future_ephemeris(self):
        initial = State((1.0, 0.0, 0.0), (0.0, 0.017, 0.0))
        first = forecast_candidate(initial, 0.0, 2.0, self._model(), self._context(), {"default_step_days": 0.25}, 0.5)
        changed = forecast_candidate(initial, 0.0, 2.0, self._model(), self._context(future_shift=10.0), {"default_step_days": 0.25}, 0.5)
        for left, right in zip(first[0], changed[0]):
            self.assertEqual(left, right)
        self.assertEqual(first[1]["rk4_steps"], changed[1]["rk4_steps"])

    def test_forecast_candidate_has_no_reference_grid_input_and_retains_endpoints(self):
        initial = State((1.0, 0.0, 0.0), (0.0, 0.017, 0.0))
        predictions, metadata = forecast_candidate(
            initial, 100.0, 2.0, self._model("B2"), self._context(), {"default_step_days": 0.25}, 0.5
        )
        self.assertEqual(len(predictions), 3)
        self.assertNotIn("reference", metadata)
        self.assertEqual(metadata["accepted_states"][0]["time_days"], 0.0)
        self.assertEqual(metadata["accepted_states"][-1]["time_days"], 2.0)
        self.assertIn("2.0", metadata["snapshots"])

    def test_resume_rejects_changed_source_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            obj = {"id": "x"}
            expected = {"source.py": "abc"}
            _object_checkpoint(output, output, "train", obj, expected, {"records": [], "feature_rows": [], "references": {}})
            path = output / "train" / "checkpoints" / "object_x.json"
            self.assertIsNotNone(load_object_checkpoint(path, expected))
            with self.assertRaises(RuntimeError):
                load_object_checkpoint(path, {"source.py": "changed"})


if __name__ == "__main__":
    unittest.main()

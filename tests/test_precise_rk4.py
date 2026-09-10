import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from orbit_baselines import State, norm, subtract  # noqa: E402
from precise_rk4 import advance, rk4_increment  # noqa: E402


class PreciseRK4Tests(unittest.TestCase):
    def test_constant_velocity_at_nonzero_time_with_small_steps(self):
        initial = State((1.25, -2.0, 0.5), (0.003, -0.004, 0.002))
        targets = (100.0, 100.003)
        predictions, meta = advance(initial, targets, lambda _t, _r, _v: (0.0, 0.0, 0.0), lambda _t, _s: 0.01)
        expected = [State(tuple(initial.position[i] + initial.velocity[i] * t for i in range(3)), initial.velocity) for t in targets]
        for actual, reference in zip(predictions, expected):
            self.assertLess(norm(subtract(actual.position, reference.position)), 2.0e-12)
            self.assertLess(norm(subtract(actual.velocity, reference.velocity)), 2.0e-15)
        self.assertEqual(meta.steps, 10001)
        self.assertLess(meta.sum_step_mismatch_days, 1.0e-10)
        self.assertLess(meta.max_step_mismatch_days, 1.0e-13)

    def test_uniform_acceleration_matches_kinematics(self):
        initial = State((0.2, -0.5, 1.0), (0.1, -0.2, 0.3))
        acceleration = (0.4, 0.2, -0.3)
        targets = (0.5, 1.0, 2.5)
        predictions, _ = advance(initial, targets, lambda _t, _r, _v: acceleration, lambda _t, _s: 0.37)
        for target, actual in zip(targets, predictions):
            expected_position = tuple(initial.position[i] + initial.velocity[i] * target + 0.5 * acceleration[i] * target**2 for i in range(3))
            expected_velocity = tuple(initial.velocity[i] + acceleration[i] * target for i in range(3))
            self.assertLess(norm(subtract(actual.position, expected_position)), 2.0e-13)
            self.assertLess(norm(subtract(actual.velocity, expected_velocity)), 2.0e-14)

    def test_time_dependent_acceleration_uses_stage_times(self):
        initial = State((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        calls = []

        def acceleration(t, _r, _v):
            calls.append(t)
            return (t, 0.0, 0.0)

        predictions, _ = advance(initial, (0.75, 1.5, 2.0), acceleration, lambda _t, _s: 0.125)
        for target, actual in zip((0.75, 1.5, 2.0), predictions):
            self.assertAlmostEqual(actual.position[0], target**3 / 6.0, places=13)
            self.assertAlmostEqual(actual.velocity[0], target**2 / 2.0, places=13)
        self.assertIn(0.75, calls)
        self.assertIn(1.5, calls)
        self.assertIn(2.0, calls)

    def test_harmonic_oscillator_has_fourth_order_step_convergence(self):
        initial = State((1.0, 0.0, 0.0), (0.0, 0.0, 0.0))

        def acceleration(_t, position, _velocity):
            return (-position[0], 0.0, 0.0)

        coarse, _ = advance(initial, (1.0,), acceleration, lambda _t, _s: 0.2)
        fine, _ = advance(initial, (1.0,), acceleration, lambda _t, _s: 0.1)
        exact = State((math.cos(1.0), 0.0, 0.0), (-math.sin(1.0), 0.0, 0.0))
        coarse_error = norm(subtract(coarse[0].position, exact.position)) + norm(subtract(coarse[0].velocity, exact.velocity))
        fine_error = norm(subtract(fine[0].position, exact.position)) + norm(subtract(fine[0].velocity, exact.velocity))
        self.assertGreater(coarse_error / fine_error, 12.0)
        self.assertLess(coarse_error / fine_error, 20.0)

    def test_observer_receives_exact_native_endpoints_and_meta(self):
        observed = []
        predictions, meta = advance(State((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)), (0.3, 0.65), lambda _t, _r, _v: (0.0, 0.0, 0.0), lambda _t, _s: 0.2, on_step=lambda left_t, _left, right_t, _right: observed.append((left_t, right_t)))
        self.assertEqual(observed[-1][1], 0.65)
        self.assertEqual([item[1] for item in observed if item[1] in (0.3, 0.65)], [0.3, 0.65])
        self.assertEqual(meta.final_time_days, 0.65)
        self.assertEqual(meta.selected_step_sum_days, meta.actual_step_sum_days)
        self.assertEqual(len(predictions), 2)

    def test_kahan_mode_reduces_large_offset_accumulation(self):
        initial = State((1.0e12, -1.0e12, 3.0e11), (0.01, -0.02, 0.03))
        force = lambda _t, _r, _v: (0.0, 0.0, 0.0)
        ordinary, _ = advance(initial, (1000.0,), force, lambda _t, _s: 0.1)
        compensated, meta = advance(initial, (1000.0,), force, lambda _t, _s: 0.1, compensated=True)
        expected = tuple(initial.position[i] + initial.velocity[i] * 1000.0 for i in range(3))
        ordinary_error = norm(subtract(ordinary[0].position, expected))
        compensated_error = norm(subtract(compensated[0].position, expected))
        self.assertTrue(meta.compensated)
        self.assertLessEqual(compensated_error, ordinary_error)

    def test_invalid_time_selector_state_and_force_values_are_rejected(self):
        initial = State((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        force = lambda _t, _r, _v: (0.0, 0.0, 0.0)
        with self.assertRaises(ValueError):
            advance(initial, (), force, lambda _t, _s: 1.0)
        with self.assertRaises(ValueError):
            advance(initial, (-1.0,), force, lambda _t, _s: 1.0)
        with self.assertRaises(ValueError):
            advance(initial, (1.0, 0.5), force, lambda _t, _s: 1.0)
        with self.assertRaises(ValueError):
            advance(initial, (1.0,), force, lambda _t, _s: 0.0)
        with self.assertRaises(ValueError):
            advance(initial, (1.0,), force, lambda _t, _s: math.nan)
        with self.assertRaises(ValueError):
            advance(initial, (1.0,), lambda _t, _r, _v: (math.inf, 0.0, 0.0), lambda _t, _s: 1.0)
        with self.assertRaises(ValueError):
            advance(State((math.nan, 0.0, 0.0), (0.0, 0.0, 0.0)), (1.0,), force, lambda _t, _s: 1.0)

    def test_inconsistent_endpoint_and_step_budget_are_rejected(self):
        state = State((0.,0.,0.), (1.,0.,0.))
        force = lambda _t, _r, _v: (0.,0.,0.)
        with self.assertRaises(ValueError):
            rk4_increment(state, 100., .1, force, right_time_days=100.2)
        with self.assertRaises(RuntimeError):
            advance(state, (1.,), force, lambda _t, _s: .1, max_steps=2)
if __name__ == "__main__":
    unittest.main()

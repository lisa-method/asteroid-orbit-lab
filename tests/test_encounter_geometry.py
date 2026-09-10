import math
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from encounter_geometry import _real_roots_unit_interval, segment_closest_approach
from orbit_baselines import State


class EncounterGeometryTests(unittest.TestCase):
    def test_linear_flyby_has_interior_closest_approach(self):
        left = State((-1.0, 1.0, 0.0), (2.0, 0.0, 0.0))
        right = State((1.0, 1.0, 0.0), (2.0, 0.0, 0.0))

        fraction, state = segment_closest_approach(left, right, 1.0)

        self.assertAlmostEqual(fraction, 0.5, places=13)
        self.assertAlmostEqual(math.sqrt(sum(value * value for value in state.position)), 1.0, places=13)
        self.assertEqual(state.velocity, (2.0, 0.0, 0.0))

    def test_equal_zero_minima_choose_earliest_root(self):
        # The scalar Hermite curve has two exact zero crossings.  Both are
        # global minima; root-rounding residuals must not choose the later one.
        def curve(u):
            return ((u - 0.2) * (u - 0.7), 0.0, 0.0)

        def curve_derivative(u):
            return (2.0 * u - 0.9, 0.0, 0.0)

        left = State(curve(0.0), curve_derivative(0.0))
        right = State(curve(1.0), curve_derivative(1.0))
        fraction, state = segment_closest_approach(left, right, 1.0)

        self.assertAlmostEqual(fraction, 0.2, places=12)
        self.assertLess(abs(state.position[0]), 1.0e-12)
        self.assertTrue(all(math.isfinite(value) for value in state.velocity))

    def test_monotonic_segment_selects_left_endpoint(self):
        left = State((1.0, 0.0, 0.0), (1.0, 0.0, 0.0))
        right = State((3.0, 0.0, 0.0), (1.0, 0.0, 0.0))

        fraction, state = segment_closest_approach(left, right, 2.0)

        self.assertEqual(fraction, 0.0)
        self.assertEqual(state, left)

    def test_stationary_trajectory_chooses_earliest_time(self):
        state = State((0.3, -0.4, 0.5), (0.0, 0.0, 0.0))

        fraction, result = segment_closest_approach(state, state, 10.0)

        self.assertEqual(fraction, 0.0)
        self.assertEqual(result, state)

    def test_touching_stationary_root_is_isolated(self):
        # (u-.4)^2 is a touching root: it does not change sign.  The next
        # factor provides a separate sign-changing root for the same analytic
        # polynomial, exercising the derivative recursion and partitioning.
        quadratic = (-0.4 * -0.4, 2.0 * -0.4, 1.0)
        polynomial = [0.0] * 4
        for i, left_value in enumerate(quadratic):
            for j, right_value in enumerate((-0.8, 1.0)):
                polynomial[i + j] += left_value * right_value
        roots = _real_roots_unit_interval(tuple(polynomial))

        self.assertTrue(any(abs(root - 0.4) < 1.0e-12 for root in roots))
        self.assertTrue(any(abs(root - 0.8) < 1.0e-12 for root in roots))

    def test_curved_segment_finds_global_minimum_among_multiple_stationary_points(self):
        # Define a cubic curve directly, then encode its endpoint values and
        # derivatives as a Hermite segment.  It has a zero at u=.2 and a
        # second, higher local minimum near u=.7.
        def curve(u):
            return ((u - 0.2) * (u - 0.7), 0.15 * (u - 0.2), 0.0)

        def curve_derivative(u):
            return (2.0 * u - 0.9, 0.15, 0.0)

        left = State(curve(0.0), curve_derivative(0.0))
        right = State(curve(1.0), curve_derivative(1.0))
        fraction, state = segment_closest_approach(left, right, 1.0)

        dense_minimum = min(
            (
                sum(value * value for value in curve(index / 10000.0)),
                index / 10000.0,
            )
            for index in range(10001)
        )
        self.assertAlmostEqual(fraction, 0.2, places=12)
        self.assertAlmostEqual(fraction, dense_minimum[1], delta=1.0e-4)
        self.assertLess(sum(value * value for value in state.position), 1.0e-24)

    def test_scale_and_rotation_do_not_change_time_of_closest_approach(self):
        left = State((-2.0, 0.8, 0.3), (3.0, -0.1, 0.0))
        right = State((1.0, 0.7, 0.3), (3.0, -0.1, 0.0))
        base_fraction, base_state = segment_closest_approach(left, right, 1.0)

        factor = 1.0e9

        def rotate(vector):
            return (-vector[1], vector[0], vector[2])

        scaled_rotated_left = State(
            tuple(factor * value for value in rotate(left.position)),
            tuple(factor * value for value in rotate(left.velocity)),
        )
        scaled_rotated_right = State(
            tuple(factor * value for value in rotate(right.position)),
            tuple(factor * value for value in rotate(right.velocity)),
        )
        fraction, state = segment_closest_approach(scaled_rotated_left, scaled_rotated_right, 1.0)

        self.assertAlmostEqual(fraction, base_fraction, places=13)
        for actual, expected in zip(state.position, rotate(base_state.position)):
            self.assertAlmostEqual(actual, factor * expected, delta=1.0e-6 * factor)
        for actual, expected in zip(state.velocity, rotate(base_state.velocity)):
            self.assertAlmostEqual(actual, factor * expected, delta=1.0e-12 * factor)

    def test_rejects_nonfinite_states_and_invalid_duration(self):
        valid = State((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
        invalid_states = (
            State((math.nan, 0.0, 0.0), valid.velocity),
            State(valid.position, (0.0, math.inf, 0.0)),
        )
        for invalid in invalid_states:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    segment_closest_approach(invalid, valid, 1.0)
        for duration in (0.0, -1.0, math.nan, math.inf):
            with self.subTest(duration=duration):
                with self.assertRaises(ValueError):
                    segment_closest_approach(valid, valid, duration)

    def test_tiny_duration_never_returns_infinite_velocity(self):
        left = State((-1.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        right = State((1.0, 0.0, 0.0), (0.0, 0.0, 0.0))

        with self.assertRaises(ValueError):
            segment_closest_approach(left, right, 1.0e-320)


if __name__ == "__main__":
    unittest.main()

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from independent_rk import integrate_dopri54


class IndependentDopriTests(unittest.TestCase):
    @staticmethod
    def _kepler(_time, position, _velocity):
        radius_sq = sum(value * value for value in position)
        radius = math.sqrt(radius_sq)
        return tuple(-value / (radius_sq * radius) for value in position)

    def test_circular_kepler_accuracy_and_invariants(self):
        period = 2.0 * math.pi
        result = integrate_dopri54(
            self._kepler,
            1.0e6,
            (1.0, 0.0, 0.0, 0.0, 1.0, 0.0),
            [1.0e6 + period * fraction for fraction in (0.0, 0.25, 0.5, 0.75, 1.0)],
            atol_position=1.0e-12,
            atol_velocity=1.0e-12,
            rtol=1.0e-10,
            max_step=0.2,
        )
        self.assertEqual(len(result.samples), 5)
        for index, (_time, state) in enumerate(result.samples):
            angle = period * (index / 4.0)
            self.assertLess(math.hypot(state[0] - math.cos(angle), state[1] - math.sin(angle)), 2.0e-8)
            radius = math.sqrt(sum(value * value for value in state[:3]))
            speed_sq = sum(value * value for value in state[3:])
            self.assertAlmostEqual(radius, 1.0, places=7)
            self.assertAlmostEqual(0.5 * speed_sq - 1.0 / radius, -0.5, places=7)
            self.assertAlmostEqual(state[0] * state[4] - state[1] * state[3], 1.0, places=7)
        self.assertGreater(result.stats["accepted_steps"], 0)
        self.assertEqual(result.accepted_endpoints[0][0], 1.0e6)

    def test_eccentric_kepler_matches_eccentric_anomaly_solution(self):
        semi_major = 1.0
        eccentricity = 0.6
        mean_motion = 1.0
        initial = (semi_major * (1.0 - eccentricity), 0.0, 0.0, 0.0,
                   math.sqrt((1.0 + eccentricity) / (1.0 - eccentricity)), 0.0)
        times = [0.0, 0.3, 0.9, 1.7]
        result = integrate_dopri54(
            self._kepler,
            0.0,
            initial,
            times,
            atol_position=1.0e-12,
            atol_velocity=1.0e-12,
            rtol=1.0e-11,
            max_step=0.05,
        )
        for mean_anomaly, (_time, state) in zip(times, result.samples):
            eccentric_anomaly = mean_anomaly
            for _ in range(12):
                eccentric_anomaly -= (eccentric_anomaly - eccentricity * math.sin(eccentric_anomaly) - mean_anomaly) / (1.0 - eccentricity * math.cos(eccentric_anomaly))
            denominator = 1.0 - eccentricity * math.cos(eccentric_anomaly)
            expected_x = math.cos(eccentric_anomaly) - eccentricity
            expected_y = math.sqrt(1.0 - eccentricity**2) * math.sin(eccentric_anomaly)
            expected_vx = -math.sin(eccentric_anomaly) / denominator
            expected_vy = math.sqrt(1.0 - eccentricity**2) * math.cos(eccentric_anomaly) / denominator
            self.assertLess(math.hypot(state[0] - expected_x, state[1] - expected_y), 2.0e-9)
            self.assertLess(math.hypot(state[3] - expected_vx, state[4] - expected_vy), 2.0e-9)
            self.assertAlmostEqual(state[0] * state[4] - state[1] * state[3], math.sqrt(1.0 - eccentricity**2), places=8)

    def test_nonautonomous_rhs_receives_absolute_epoch_and_uses_local_step(self):
        epoch = 2.0e6

        def acceleration(time, _position, _velocity):
            return (time, 0.0, 0.0)

        result = integrate_dopri54(
            acceleration,
            epoch,
            (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            [epoch + 0.25, epoch + 1.0],
            atol_position=1.0e-11,
            atol_velocity=1.0e-11,
            rtol=1.0e-11,
            max_step=0.1,
        )
        for offset, (_time, state) in zip((0.25, 1.0), result.samples):
            expected_position = epoch * offset * offset / 2.0 + offset**3 / 6.0
            expected_velocity = epoch * offset + offset**2 / 2.0
            self.assertAlmostEqual(state[0], expected_position, places=5)
            self.assertAlmostEqual(state[3], expected_velocity, places=5)

    def test_tighter_tolerance_rejects_and_converges(self):
        def stiff(_time, position, _velocity):
            return (100.0 * position[0], 0.0, 0.0)

        initial = (1.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        loose = integrate_dopri54(stiff, 0.0, initial, [1.0], atol_position=1.0e-5, atol_velocity=1.0e-5, rtol=1.0e-5, max_step=1.0)
        tight = integrate_dopri54(stiff, 0.0, initial, [1.0], atol_position=1.0e-9, atol_velocity=1.0e-9, rtol=1.0e-9, max_step=1.0)
        exact = (math.cosh(10.0), 10.0 * math.sinh(10.0))
        loose_error = math.hypot(loose.samples[-1][1][0] - exact[0], loose.samples[-1][1][3] - exact[1])
        tight_error = math.hypot(tight.samples[-1][1][0] - exact[0], tight.samples[-1][1][3] - exact[1])
        self.assertGreater(tight.stats["rejected_steps"], 0)
        self.assertLess(tight_error, loose_error)
        self.assertGreater(tight.stats["accepted_steps"], loose.stats["accepted_steps"])

    def test_breakpoints_are_accepted_boundaries(self):
        result = integrate_dopri54(
            lambda _time, _position, _velocity: (0.0, 0.0, 0.0),
            10.0,
            (0.0, 0.0, 0.0, 1.0, 0.0, 0.0),
            [11.0],
            max_step=0.8,
            breakpoints=[10.3, 10.65],
        )
        times = [time for time, _state in result.accepted_endpoints]
        self.assertIn(10.3, times)
        self.assertIn(10.65, times)
        self.assertTrue(all(right > left for left, right in zip(times, times[1:])))

    def test_close_breakpoints_remain_distinct_at_jd_epoch(self):
        epoch = 2_460_000.0
        first = epoch + 0.123456
        second = first + 0.2 / 86400.0
        result = integrate_dopri54(
            lambda _time, _position, _velocity: (0.0, 0.0, 0.0),
            epoch,
            (0.0, 0.0, 0.0, 1.0, 0.0, 0.0),
            [epoch + 1.0],
            max_step=0.5,
            breakpoints=[first, second],
        )
        times = [time for time, _state in result.accepted_endpoints]
        self.assertIn(first, times)
        self.assertIn(second, times)
        self.assertGreater(second - first, 0.0)
        first_index, second_index = times.index(first), times.index(second)
        self.assertEqual(second_index, first_index + 1)

    def test_nonfinite_rhs_and_step_budget_are_guarded(self):
        def bad(time, _position, _velocity):
            return (math.nan if time > 0.1 else 0.0, 0.0, 0.0)

        with self.assertRaises(FloatingPointError):
            integrate_dopri54(bad, 0.0, (0.0, 0.0, 0.0, 0.0, 0.0, 0.0), [1.0], max_step=0.2)
        with self.assertRaises(RuntimeError):
            integrate_dopri54(self._kepler, 0.0, (1.0, 0.0, 0.0, 0.0, 1.0, 0.0), [1.0], max_step=0.01, max_steps=2)

    def test_input_validation_rejects_unsorted_times_and_bad_state(self):
        with self.assertRaises(ValueError):
            integrate_dopri54(self._kepler, 0.0, (0.0, 0.0, 0.0), [1.0])
        with self.assertRaises(ValueError):
            integrate_dopri54(self._kepler, 0.0, (1.0, 0.0, 0.0, 0.0, 1.0, 0.0), [1.0, 0.5])


if __name__ == "__main__":
    unittest.main()

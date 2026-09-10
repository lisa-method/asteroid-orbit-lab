import math
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from orbit_baselines import (
    State,
    central_acceleration,
    constant_velocity,
    fit_central_law,
    norm,
    propagate,
    propagate_variable_step,
    specific_angular_momentum,
    specific_energy,
    subtract,
    TrajectoryWindow,
    two_body_acceleration,
)


class OrbitBaselineTests(unittest.TestCase):
    def test_constant_velocity_is_exact(self):
        initial = State((1.0, 2.0, 3.0), (0.1, -0.2, 0.3))
        prediction = constant_velocity(initial, [10.0])[0]
        self.assertEqual(prediction.position, (2.0, 0.0, 6.0))
        self.assertEqual(prediction.velocity, initial.velocity)

    def test_general_central_law_matches_two_body_at_n_two(self):
        mu = 2.959122082855911e-4
        state = State((1.5, -0.25, 0.1), (0.0, 0.0, 0.0))
        general = central_acceleration(mu, 2.0)(0.0, state.position, state.velocity)
        fixed = two_body_acceleration(mu)(0.0, state.position, state.velocity)
        self.assertLess(norm(subtract(general, fixed)), 1.0e-18)

    def test_rk4_preserves_circular_two_body_invariants(self):
        mu = 2.959122082855911e-4
        initial = State((1.0, 0.0, 0.0), (0.0, math.sqrt(mu), 0.0))
        final = propagate(initial, [365.0], two_body_acceleration(mu), max_step_days=0.25)[0]
        energy_drift = abs((specific_energy(final, mu) - specific_energy(initial, mu)) / specific_energy(initial, mu))
        h_drift = abs(
            (specific_angular_momentum(final) - specific_angular_momentum(initial))
            / specific_angular_momentum(initial)
        )
        self.assertLess(energy_drift, 1.0e-10)
        self.assertLess(h_drift, 1.0e-10)

    def test_variable_step_hits_target_and_preserves_zero_force_motion(self):
        initial = State((1.0, -2.0, 0.5), (0.2, 0.1, -0.05))

        def zero_acceleration(_time, _position, _velocity):
            return (0.0, 0.0, 0.0)

        states, step_count = propagate_variable_step(
            initial,
            [1.0],
            zero_acceleration,
            lambda _time, _state: 0.3,
        )
        self.assertEqual(step_count, 4)
        for actual, expected in zip(states[0].position, (1.2, -1.9, 0.45)):
            self.assertAlmostEqual(actual, expected, places=14)
        self.assertEqual(states[0].velocity, initial.velocity)

    def test_fit_recovers_known_law_from_eccentric_trajectories(self):
        mu = 2.959122082855911e-4
        horizons = (30.0, 90.0, 180.0)
        windows = []
        for object_id, semi_major_axis, eccentricity in (
            ("inner", 1.4, 0.25),
            ("outer", 2.6, 0.18),
        ):
            radius = semi_major_axis * (1.0 - eccentricity)
            speed = math.sqrt(mu * (1.0 + eccentricity) / radius)
            initial = State((radius, 0.0, 0.0), (0.0, speed, 0.0))
            targets = propagate(
                initial,
                horizons,
                two_body_acceleration(mu),
                max_step_days=0.05,
            )
            windows.append(
                TrajectoryWindow(object_id, initial, horizons, tuple(targets))
            )
        fit = fit_central_law(
            windows, max_step_days=0.25, iterations=50
        )
        self.assertLess(abs(fit.reference_acceleration_au_d2 / mu - 1.0), 1.0e-5)
        self.assertLess(abs(fit.exponent - 2.0), 1.0e-4)


if __name__ == "__main__":
    unittest.main()

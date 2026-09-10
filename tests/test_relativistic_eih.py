from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from planetary_dynamics import solar_schwarzschild_acceleration
from relativistic_eih import eih_correction, prepare_sources


class RelativisticEIHTests(unittest.TestCase):
    def test_general_equation_reduces_to_stationary_sun_limit(self):
        mu = 1.234
        c = 987.0
        position = (1.2, 0.2, 0.1)
        velocity = (0.01, 0.02, -0.01)
        sources = prepare_sources((mu,), ((0.0, 0.0, 0.0),), ((0.0, 0.0, 0.0),))
        actual = eih_correction(position, velocity, sources, c)
        expected = solar_schwarzschild_acceleration(mu, c)(0.0, position, velocity)
        for a, b in zip(actual, expected):
            self.assertAlmostEqual(a, b, delta=abs(b)*3e-15)

    def test_two_body_com_relative_formula(self):
        mu1, mu2 = 2.0, 3.0
        total_mu = mu1 + mu2
        c = 1000.0
        relative_position = (1.2, -0.7, 0.3)
        relative_velocity = (0.03, 0.05, -0.01)
        position_1 = tuple(-mu2 / total_mu * component for component in relative_position)
        position_2 = tuple(mu1 / total_mu * component for component in relative_position)
        velocity_1 = tuple(-mu2 / total_mu * component for component in relative_velocity)
        velocity_2 = tuple(mu1 / total_mu * component for component in relative_velocity)
        sources = prepare_sources((mu1, mu2), (position_1, position_2), (velocity_1, velocity_2))
        acceleration_1 = eih_correction(position_1, velocity_1, sources, c, exclude_index=0)
        acceleration_2 = eih_correction(position_2, velocity_2, sources, c, exclude_index=1)
        actual = tuple(acceleration_2[index] - acceleration_1[index] for index in range(3))

        radius = math.sqrt(sum(component * component for component in relative_position))
        speed_squared = sum(component * component for component in relative_velocity)
        eta = mu1 * mu2 / total_mu**2
        radial_unit = tuple(component / radius for component in relative_position)
        radial_speed = sum(relative_velocity[index] * radial_unit[index] for index in range(3))
        position_velocity = sum(relative_position[index] * relative_velocity[index] for index in range(3))
        scalar = (4.0 + 2.0 * eta) * total_mu / radius - (1.0 + 3.0 * eta) * speed_squared + 1.5 * eta * radial_speed**2
        factor = total_mu / (c**2 * radius**3)
        expected = tuple(
            factor * (scalar * relative_position[index] + (4.0 - 2.0 * eta) * position_velocity * relative_velocity[index])
            for index in range(3)
        )
        for actual_component, expected_component in zip(actual, expected):
            self.assertAlmostEqual(actual_component, expected_component, delta=1e-20)

    def test_translation_rotation_and_permutation(self):
        mus = (2.0, 3.0, 0.5)
        positions = ((0.1, 0.2, -0.3), (1.2, -0.4, 0.8), (-0.7, 0.9, 0.5))
        velocities = ((0.01, -0.02, 0.03), (-0.04, 0.05, 0.02), (0.02, 0.01, -0.03))
        target = (0.4, -0.8, 0.6)
        target_velocity = (-0.02, 0.03, 0.04)
        c = 900.0
        baseline = eih_correction(target, target_velocity, prepare_sources(mus, positions, velocities), c)

        offset = (10.0, -4.0, 2.0)
        shifted_sources = prepare_sources(mus, (tuple(p[i] + offset[i] for i in range(3)) for p in positions), velocities)
        shifted_target = tuple(target[i] + offset[i] for i in range(3))
        shifted = eih_correction(shifted_target, target_velocity, shifted_sources, c)
        for actual, expected in zip(shifted, baseline):
            self.assertAlmostEqual(actual, expected, delta=1e-18)

        def rotate(vector):
            # A proper cyclic coordinate rotation: (x,y,z) -> (y,z,x).
            return (vector[1], vector[2], vector[0])

        rotated_sources = prepare_sources(mus, tuple(rotate(p) for p in positions), tuple(rotate(v) for v in velocities))
        rotated = eih_correction(rotate(target), rotate(target_velocity), rotated_sources, c)
        for actual, expected in zip(rotated, rotate(baseline)):
            self.assertAlmostEqual(actual, expected, delta=1e-18)

        order = (2, 0, 1)
        permuted_sources = prepare_sources(tuple(mus[i] for i in order), tuple(positions[i] for i in order), tuple(velocities[i] for i in order))
        permuted = eih_correction(target, target_velocity, permuted_sources, c)
        for actual, expected in zip(permuted, baseline):
            self.assertAlmostEqual(actual, expected, delta=1e-18)

    def test_inverse_c_squared_scaling_and_outer_sum(self):
        sources = prepare_sources((2.0, 3.0), ((0.0, 0.0, 0.0), (2.0, 0.0, 0.0)), ((0.0, 0.0, 0.0), (0.0, 0.02, 0.0)))
        target = (1.0, 0.3, 0.0)
        velocity = (0.01, -0.02, 0.03)
        all_terms = eih_correction(target, velocity, sources, 1000.0)
        all_terms_slower = eih_correction(target, velocity, sources, 2000.0)
        for actual, expected in zip(all_terms_slower, all_terms):
            self.assertAlmostEqual(actual, expected / 4.0, delta=1e-25)
        sun_only = eih_correction(target, velocity, sources, 1000.0, outer_indices=(0,))
        self.assertNotEqual(sun_only, all_terms)

    def test_validation_and_singular_inputs(self):
        with self.assertRaises(ValueError):
            prepare_sources((1.0,), ((0.0, 0.0, 0.0),), ((0.0, 0.0),))
        with self.assertRaises(ValueError):
            prepare_sources((1.0, -2.0), ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)), ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)))
        with self.assertRaises(ValueError):
            prepare_sources((1.0, 2.0), ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)), ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)))
        sources = prepare_sources((1.0,), ((0.0, 0.0, 0.0),), ((0.0, 0.0, 0.0),))
        with self.assertRaises(ValueError):
            eih_correction((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), sources, 100.0)
        with self.assertRaises(ValueError):
            eih_correction((1.0, 0.0, 0.0), (0.0, 0.0, 0.0), sources, 100.0, outer_indices=(True,))
        with self.assertRaises(ValueError):
            eih_correction((1.0, 0.0, 0.0), (0.0, 0.0, 0.0), sources, 100.0, outer_indices=(2,))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from orbit_baselines import State
from planetary_dynamics import Perturber
from relative_time_dynamics import RelativeEphemerisInterpolator
from relativistic_eih import eih_correction, prepare_sources
from barycentric_eih import build_barycentric_eih


class BarycentricEIHTests(unittest.TestCase):
    def setUp(self):
        self.origin = 2462500.5
        self.sun_r0 = (10.0, -2.0, 0.5)
        self.sun_v0 = (0.1, 0.2, -0.1)
        self.sun_a = (0.02, -0.01, 0.03)
        epochs = (0.0, 1.0, 2.0)
        sun_states = tuple(
            State(
                tuple(self.sun_r0[i] + self.sun_v0[i] * t + 0.5 * self.sun_a[i] * t * t for i in range(3)),
                tuple(self.sun_v0[i] + self.sun_a[i] * t for i in range(3)),
            )
            for t in epochs
        )
        self.sun = RelativeEphemerisInterpolator(epochs, sun_states)
        earth_ephem = RelativeEphemerisInterpolator(
            epochs,
            (State((2.0, 0.3, -0.2), (0.0, 0.01, 0.0)),) * 3,
        )
        mars_ephem = RelativeEphemerisInterpolator(
            epochs,
            (State((-1.0, 0.7, 0.4), (0.01, 0.0, -0.02)),) * 3,
        )
        self.earth = Perturber("399", "Earth", 3.0e-6, earth_ephem)
        self.mars = Perturber("499", "Mars", 3.2e-7, mars_ephem)
        self.planets = (self.earth, self.mars)
        self.mu_sun = 0.0003
        self.c = 173.0

    def _build(self, **kwargs):
        return build_barycentric_eih(
            self.origin,
            self.planets,
            self.mu_sun,
            self.c,
            None,
            4.0e-5,
            0.0,
            self.sun,
            **kwargs,
        )

    def test_uniform_frame_conversion_and_initial_identity(self):
        _, convert = self._build(gr="eih_all")
        q0 = State((0.3, -0.2, 0.4), (0.02, -0.03, 0.01))
        self.assertEqual(convert(0.0, q0), q0)
        time = 0.75
        q = State(
            tuple(q0.position[i] + q0.velocity[i] * time for i in range(3)),
            q0.velocity,
        )
        converted = convert(time, q)
        expected_position = tuple(q.position[i] - 0.5 * self.sun_a[i] * time * time for i in range(3))
        expected_velocity = tuple(q.velocity[i] - self.sun_a[i] * time for i in range(3))
        for actual, expected in zip(converted.position, expected_position):
            self.assertAlmostEqual(actual, expected, delta=1e-14)
        for actual, expected in zip(converted.velocity, expected_velocity):
            self.assertAlmostEqual(actual, expected, delta=1e-14)

    def test_direct_barycentric_newton_and_eih_at_nonzero_epoch(self):
        force, convert = self._build(gr="eih_all")
        time = 0.7
        q = (0.4, -0.6, 0.8)
        w = (0.03, -0.01, 0.02)
        actual = force(time, q, w)
        heliocentric = convert(time, State(q, w))
        sun_state = self.sun.state_at(time)
        source_positions = ((0.0, 0.0, 0.0),)
        source_velocities = (sun_state.velocity,)
        masses = (self.mu_sun,)
        for body in self.planets:
            body_state = body.ephemeris.state_at(time)
            source_positions += (body_state.position,)
            source_velocities += (tuple(sun_state.velocity[i] + body_state.velocity[i] for i in range(3)),)
            masses += (body.mu_au3_d2,)
        prepared = prepare_sources(masses, source_positions, source_velocities)
        expected = tuple(-self.mu_sun * heliocentric.position[i] / math.sqrt(sum(v * v for v in heliocentric.position)) ** 3 for i in range(3))
        for body in self.planets:
            body_position = body.ephemeris.state_at(time).position
            displacement = tuple(body_position[i] - heliocentric.position[i] for i in range(3))
            distance = math.sqrt(sum(v * v for v in displacement))
            expected = tuple(expected[i] + body.mu_au3_d2 * displacement[i] / distance**3 for i in range(3))
        target_bary_velocity = tuple(w[i] + self.sun_v0[i] for i in range(3))
        pn = eih_correction(heliocentric.position, target_bary_velocity, prepared, self.c)
        expected = tuple(expected[i] + pn[i] for i in range(3))
        for actual_component, expected_component in zip(actual, expected):
            self.assertAlmostEqual(actual_component, expected_component, delta=1e-20)

    def test_sun_only_outer_sum_is_forwarded_without_origin_subtraction(self):
        force_sun, _ = self._build(gr="eih_sun")
        force_all, _ = self._build(gr="eih_all")
        state = ((0.8, 0.1, -0.2), (0.03, 0.02, 0.01))
        self.assertNotEqual(force_sun(0.4, *state), force_all(0.4, *state))

    def test_stationary_sun_pn_limit(self):
        from planetary_dynamics import solar_schwarzschild_acceleration

        position = (1.2, 0.2, 0.1)
        velocity = (0.01, 0.02, -0.01)
        mu = 1.234
        c = 987.0
        prepared = prepare_sources((mu,), ((0.0, 0.0, 0.0),), ((0.0, 0.0, 0.0),))
        actual = eih_correction(position, velocity, prepared, c)
        expected = solar_schwarzschild_acceleration(mu, c)(0.0, position, velocity)
        for actual_component, expected_component in zip(actual, expected):
            self.assertAlmostEqual(actual_component, expected_component, delta=abs(expected_component) * 3e-15)

    def test_uniform_sun_converter_is_identity_at_all_times(self):
        epochs = (0.0, 1.0, 2.0)
        linear_sun = RelativeEphemerisInterpolator(
            epochs,
            tuple(State(tuple(self.sun_r0[i] + self.sun_v0[i] * t for i in range(3)), self.sun_v0) for t in epochs),
        )
        builder = build_barycentric_eih(self.origin, self.planets, self.mu_sun, self.c, None, 4.0e-5, 0.0, linear_sun)
        _, convert = builder
        initial = State((0.3, -0.2, 0.4), (0.02, -0.03, 0.01))
        for time in (0.0, 0.5, 1.5, 2.0):
            state = State(tuple(initial.position[i] + initial.velocity[i] * time for i in range(3)), initial.velocity)
            converted = convert(time, state)
            for actual, expected in zip((*converted.position, *converted.velocity), (*state.position, *state.velocity)):
                self.assertAlmostEqual(actual, expected, delta=1e-14)

    def test_invalid_frame_and_singular_target(self):
        with self.assertRaises(ValueError):
            self._build(gr="unknown")
        with self.assertRaises(ValueError):
            build_barycentric_eih(self.origin, (self.mars,), self.mu_sun, self.c, None, 4.0e-5, 0.0, self.sun)
        force, _ = self._build()
        with self.assertRaises(ValueError):
            force(0.0, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))


if __name__ == "__main__":
    unittest.main()

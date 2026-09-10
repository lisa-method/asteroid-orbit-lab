import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from precise_dopri import integrate_precise_dopri54 as integrate


class PreciseDPTests(unittest.TestCase):
    def test_small_increments_survive_large_coordinate(self):
        result = integrate(lambda t, r, v: (0., 0., 0.), 0.,
            (1e12, 0., 0., .001, 0., 0.), [0., 1.], max_step=.0003)
        self.assertEqual(result.samples[-1][1][0], 1e12 + .001)
        self.assertEqual(result.samples[0][1][0], 1e12)

    def test_time_dependent_force_and_exact_boundaries(self):
        result = integrate(lambda t, r, v: (t, 0., 0.), 0.,
            (0.,) * 6, [0., .13, 1.], breakpoints=[.111, .777],
            max_step=.003, rtol=1e-12, atol_position=1e-14, atol_velocity=1e-14)
        for t, s in result.samples:
            self.assertAlmostEqual(s[0], t**3 / 6, places=13)
            self.assertAlmostEqual(s[3], t*t / 2, places=13)
        native = dict(result.accepted_endpoints)
        for t, s in result.samples:
            self.assertEqual(native[t], s)
        self.assertIn(.111, native)
        self.assertIn(.777, native)

    def test_rejected_steps_do_not_commit_compensation(self):
        result = integrate(lambda t, r, v: (100*r[0], 0., 0.), 0.,
            (1., 0., 0., 0., 0., 0.), [1.], max_step=1.,
            rtol=1e-12, atol_position=1e-13, atol_velocity=1e-13)
        self.assertGreater(result.stats['rejected_steps'], 0)
        self.assertLess(abs(result.samples[-1][1][0] / math.cosh(10) - 1), 3e-12)

    def test_kepler_accuracy_and_tolerance_convergence(self):
        def force(t, r, v):
            radius = math.sqrt(sum(x*x for x in r))
            return tuple(-x / radius**3 for x in r)
        initial = (1., 0., 0., 0., 1., 0.)
        errors = []
        for tol in (1e-8, 1e-12):
            result = integrate(force, 0., initial, [2*math.pi],
                rtol=tol, atol_position=tol/10, atol_velocity=tol/10, max_step=.5)
            errors.append(math.dist(result.samples[-1][1], initial))
        self.assertLess(errors[1], errors[0]/100)
        self.assertLess(errors[1], 1e-10)

    def test_guards(self):
        zero = lambda t, r, v: (0., 0., 0.)
        with self.assertRaises(ValueError):
            integrate(zero, 2451545., (0.,)*6, [2451546.])
        with self.assertRaises(RuntimeError):
            integrate(zero, 0., (0.,)*6, [1.], max_step=.01, max_steps=2)
        with self.assertRaises(FloatingPointError):
            integrate(lambda t, r, v: (math.nan, 0., 0.), 0., (0.,)*6, [1.])


if __name__ == '__main__':
    unittest.main()

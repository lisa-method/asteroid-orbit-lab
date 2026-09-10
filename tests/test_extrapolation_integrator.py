import math
import unittest

from extrapolation_integrator import integrate_extrapolation


class ExtrapolationTests(unittest.TestCase):
    def test_non_autonomous_polynomial_and_boundaries(self):
        result = integrate_extrapolation(lambda t,r,v:(t*t, 0., 0.),
            (1.,0.,0.,2.,0.,0.), (0.,0.3,1.,2.), breakpoints=(0.7,1.4))
        for t,s in result.samples:
            self.assertAlmostEqual(s[0], 1+2*t+t**4/12, places=13)
            self.assertAlmostEqual(s[3], 2+t**3/3, places=13)
        times = [t for t,s in result.accepted_endpoints]
        self.assertIn(0.7, times); self.assertIn(1.4, times)

    def test_kepler_circle_over_many_periods(self):
        def force(t,r,v):
            rr = math.sqrt(sum(x*x for x in r))
            return tuple(-x/rr**3 for x in r)
        result = integrate_extrapolation(force, (1.,0.,0.,0.,1.,0.), (0.,20*math.pi),
            rtol=2e-14, atol_position=1e-16, atol_velocity=1e-16, max_step=0.3)
        error = math.dist(result.samples[-1][1], (1.,0.,0.,0.,1.,0.))
        self.assertLess(error, 2e-10)

    def test_velocity_dependent_damping(self):
        result = integrate_extrapolation(lambda t,r,v:tuple(-x for x in v),
            (0.,0.,0.,1.,2.,3.), (0.,5.), rtol=1e-14)
        expected = (*(v*(1-math.exp(-5)) for v in (1.,2.,3.)), *(v*math.exp(-5) for v in (1.,2.,3.)))
        self.assertLess(math.dist(result.samples[-1][1], expected), 1e-12)

    def test_invalid_order_and_nonfinite_force(self):
        with self.assertRaises(ValueError):
            integrate_extrapolation(lambda t,r,v:(0,0,0), (0,)*6, (1,0))
        with self.assertRaises(FloatingPointError):
            integrate_extrapolation(lambda t,r,v:(math.nan,0,0), (0,)*6, (1,))

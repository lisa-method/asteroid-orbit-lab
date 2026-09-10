import math
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from gravity_conventions import build_convention_force
from orbit_baselines import State
from planetary_dynamics import Perturber
from relative_time_dynamics import RelativeEphemerisInterpolator, build_relative_force
from run_apophis_gravity_conventions import de_constants


class GravityConventionsTest(unittest.TestCase):
    def setUp(self):
        ephem = RelativeEphemerisInterpolator((0.,10.),
            (State((1.,0.,0.),(0.,0.,0.)),State((1.,0.,0.),(0.,0.,0.))))
        self.args = (2462137.5,(Perturber('399','Earth',1e-9,ephem),),3e-4,173.,None,4e-5,1e-3)
        self.state = ((.7,.2,.1),(.001,.02,.003))

    def test_default_exactly_preserves_baseline(self):
        self.assertEqual(build_convention_force(*self.args)(3.,*self.state),
                         build_relative_force(*self.args)(3.,*self.state))

    def test_solar_term_matches_negative_potential_gradient(self):
        # Artificially large J2 makes the finite-difference check well resolved.
        # Potential U_J2 = mu J2 R^2 P2(z/r)/r^3, with axis along z.
        cfg = dict(j2=.1,radius_au=.2,pole_ra_deg=0.,pole_dec_deg=90.)
        base = build_convention_force(*self.args,earth_pole='fixed_j2000')
        full = build_convention_force(*self.args,earth_pole='fixed_j2000',solar_j2=cfg)
        r,v = self.state
        delta = tuple(a-b for a,b in zip(full(3.,r,v),base(3.,r,v)))
        def potential(q):
            radius = math.sqrt(sum(x*x for x in q))
            return 3e-4*.1*.2**2*.5*(3*(q[2]/radius)**2-1)/radius**3
        for i in range(3):
            left,right = list(r),list(r); left[i]-=1e-5; right[i]+=1e-5
            numeric = -(potential(right)-potential(left))/2e-5
            self.assertAlmostEqual(delta[i],numeric,delta=abs(numeric)*2e-8)

    def test_explicit_conventions_and_constants_validation(self):
        with self.assertRaises(ValueError): build_convention_force(*self.args,earth_pole='guess')
        with self.assertRaises(ValueError):
            build_convention_force(*self.args,solar_j2=dict(j2=1.,radius_au=-1.,pole_ra_deg=0.,pole_dec_deg=90.))
        near_earth = ((1.0003,.0002,.0001),self.state[1])
        self.assertNotEqual(build_convention_force(*self.args)(3.,*near_earth),
                            build_convention_force(*self.args,earth_pole='fixed_j2000')(3.,*near_earth))

    def test_de_header_count_integrity(self):
        self.assertEqual(de_constants('GROUP 1040\n2 A B\nGROUP 1041\n2 1D0 2D0'),{'A':'1D0','B':'2D0'})
        with self.assertRaises(ValueError):
            de_constants('GROUP 1040\n2 A B\nGROUP 1041\n2 1D0')


if __name__ == '__main__': unittest.main()

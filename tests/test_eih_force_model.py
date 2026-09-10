import math
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

from eih_force_model import build_eih_force_model
from gravity_conventions import build_convention_force
from orbit_baselines import State
from planetary_dynamics import Perturber
from relativistic_eih import prepare_sources,eih_correction


class FixedEphemeris:
    def __init__(self,r,v): self.state=State(r,v)
    def state_at(self,t): return self.state


class EIHFrameTests(unittest.TestCase):
    def test_direct_barycentric_minus_sun_matches_relative_composition(self):
        # Synthetic, deliberately large masses/velocities expose omitted frame terms.
        mu_s,mu_e,c=2.,.1,900.
        sun=FixedEphemeris((.2,-.4,.1),(.03,.04,-.02))
        earth=Perturber('399','Earth',mu_e,FixedEphemeris((1.,.2,-.3),(.01,.05,.02)))
        rh,vh=(.6,-.3,.5),(.04,-.03,.01)
        positions=(sun.state.position,tuple(a+b for a,b in zip(sun.state.position,earth.ephemeris.state.position)))
        velocities=(sun.state.velocity,tuple(a+b for a,b in zip(sun.state.velocity,earth.ephemeris.state.velocity)))
        rb=tuple(a+b for a,b in zip(rh,positions[0])); vb=tuple(a+b for a,b in zip(vh,velocities[0]))
        sources=prepare_sources((mu_s,mu_e),positions,velocities)
        def newton(r,exclude=None):
            terms=[]
            for i,(mu,q) in enumerate(zip((mu_s,mu_e),positions)):
                if i==exclude: continue
                delta=tuple(a-b for a,b in zip(q,r)); d=math.sqrt(sum(x*x for x in delta))
                terms.append(tuple(mu*x/d**3 for x in delta))
            return tuple(math.fsum(a[k] for a in terms) for k in range(3))
        ns=newton(positions[0],0); nt=newton(rb)
        ps=eih_correction(positions[0],velocities[0],sources,c,exclude_index=0)
        for mode,indices in [('eih_all',None),('eih_sun',(0,))]:
            pt=eih_correction(rb,vb,sources,c,outer_indices=indices)
            expected=tuple(a-b+d-e for a,b,d,e in zip(nt,ns,pt,ps))
            force=build_eih_force_model(2462137.5,(earth,),mu_s,c,None,.01,0.,sun,gr=mode)
            actual=force(3.,rh,vh)
            for a,b in zip(actual,expected): self.assertAlmostEqual(a,b,delta=abs(b)*2e-15)
            # Repeating epoch with a changed target must not reuse target-dependent PN.
            other=force(3.,(.61,-.3,.5),vh)
            self.assertNotEqual(actual,other)

    def test_schwarzschild_control_is_unchanged(self):
        earth=Perturber('399','Earth',1e-9,FixedEphemeris((1.,0.,0.),(0.,.01,0.)))
        args=(2462137.5,(earth,),3e-4,173.,None,4e-5,.001)
        new=build_eih_force_model(*args,FixedEphemeris((0.,0.,0.),(.01,0.,0.)),gr='schwarzschild')
        old=build_convention_force(*args,earth_pole='fixed_j2000')
        self.assertEqual(new(2.,(.9,.1,.1),(.01,.01,0.)),old(2.,(.9,.1,.1),(.01,.01,0.)))


if __name__=='__main__': unittest.main()

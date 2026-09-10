"""Regression for native accepted times that coalesce in absolute Julian Date."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from orbit_baselines import State
from run_apophis_moon_venus_v1_1 import _normalize_trace, _hermite


class NativeTimeTests(unittest.TestCase):
    def test_distinct_rk4_endpoints_survive_jd_quantization(self):
        start=2462137.5
        a,b=100.,100.+1e-11
        self.assertEqual(start+a,start+b)
        initial=State((0.,0.,0.),(1.,0.,0.))
        trace=_normalize_trace({"accepted_endpoints":[[a,[a,0,0,1,0,0]],
            [b,[b,0,0,1,0,0]]]},start,initial,"rk4")
        self.assertEqual([row[0] for row in trace],[0.,a,b])
        self.assertEqual(_hermite(trace,[a,b])[1].position[0],b)
        with self.assertRaises(ValueError):
            _normalize_trace({"accepted_endpoints":[[b,[b,0,0,1,0,0]],
                [a,[a,0,0,1,0,0]]]},start,initial,"rk4")


if __name__=="__main__":
    unittest.main()

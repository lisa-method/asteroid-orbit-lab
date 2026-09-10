"""Physical and input-boundary checks for the post-hoc outlier audit."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from audit_development_outlier_forces import _pluto_force, parse_non_grav
from orbit_baselines import State
from planetary_dynamics import EphemerisInterpolator, Perturber


class OutlierForceAuditTests(unittest.TestCase):
    def test_heliocentric_pluto_force_vanishes_at_sun(self):
        ephem = EphemerisInterpolator((100.,101.), (State((30.,0.,0.),(0.,1.,0.)), State((30.,1.,0.),(0.,1.,0.))))
        body = Perturber("9", "Pluto system", 1., ephem)
        self.assertEqual(_pluto_force(body,100.)(0.,(0.,0.,0.),(0.,0.,0.)), (0.,0.,0.))

    def test_force_closures_keep_their_own_epoch(self):
        ephem = EphemerisInterpolator((100.,101.), (State((30.,0.,0.),(0.,1.,0.)), State((30.,1.,0.),(0.,1.,0.))))
        body = Perturber("9", "Pluto system", 1., ephem)
        earlier = _pluto_force(body,100.)
        original = earlier(0.,(1.,0.,0.),(0.,0.,0.))
        later = _pluto_force(body,101.)
        self.assertNotEqual(original,later(0.,(1.,0.,0.),(0.,0.,0.)))
        self.assertEqual(original,earlier(0.,(1.,0.,0.),(0.,0.,0.)))

    def test_missing_ng_and_incomplete_ng_are_distinguished(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"header.json"
            path.write_text(json.dumps({"result":"No NG model here\n$$SOE\nA2=1"}))
            parameters, metadata = parse_non_grav(path)
            self.assertIsNone(parameters)
            self.assertFalse(metadata["present"])
            path.write_text(json.dumps({"result":"Asteroid non-gravitational force model\nA1=0 A2=1\n$$SOE\nA3=0"}))
            with self.assertRaises(ValueError):
                parse_non_grav(path)


if __name__ == "__main__":
    unittest.main()

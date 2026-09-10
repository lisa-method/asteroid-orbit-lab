from __future__ import annotations

import sys
import math
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from earth_oblateness import earth_pole_iau
from extended_body_forces import zonal_acceleration
from ng_inputs_v2 import NGInput
from orbit_baselines import State, add, norm, subtract
from planetary_dynamics import NonGravitationalParameters, Perturber
from relative_force_model import build_relative_force_model
from relative_time_dynamics import RelativeEphemerisInterpolator, build_relative_force


class RelativeForceModelTests(unittest.TestCase):
    ORIGIN = 2451545.0 + 730.5
    MU_SUN = 0.0002959122082855911
    C_AU_D = 173.144632674240
    J2 = {"reference_radius_au": 4.26352097804e-5, "j2": 0.00108262545}
    ZONALS = {
        "reference_radius_au": 4.263298e-5,
        "mu_au3_d2": 3.0001e-6,
        "c30_j2000": 9.571612e-7,
        "c40_j2000": 5.399659e-7,
        "c30_rate_per_julian_year": 4.9e-12,
        "c40_rate_per_julian_year": 4.7e-12,
    }

    @classmethod
    def setUpClass(cls):
        earth_eph = RelativeEphemerisInterpolator(
            (-1.0, 0.0, 1.0, 2.0),
            (
                State((0.011, -0.004, 0.002), (0.0, 0.0, 0.0)),
                State((0.011, -0.004, 0.002), (0.0, 0.0, 0.0)),
                State((0.011, -0.004, 0.002), (0.0, 0.0, 0.0)),
                State((0.011, -0.004, 0.002), (0.0, 0.0, 0.0)),
            ),
        )
        mars_eph = RelativeEphemerisInterpolator(
            (-1.0, 0.0, 1.0, 2.0),
            (
                State((-0.8, 0.25, 0.03), (0.0, 0.0, 0.0)),
                State((-0.8, 0.25, 0.03), (0.0, 0.0, 0.0)),
                State((-0.8, 0.25, 0.03), (0.0, 0.0, 0.0)),
                State((-0.8, 0.25, 0.03), (0.0, 0.0, 0.0)),
            ),
        )
        cls.earth = Perturber("399", "Earth", 3.0001e-6, earth_eph)
        cls.mars = Perturber("499", "Mars", 3.2e-7, mars_eph)
        cls.planets = (cls.earth, cls.mars)
        cls.state = State((1.2, 0.35, -0.12), (0.002, 0.011, 0.003))

    def make(self, *, ng=None, zonals=None, enabled=(), enabled_j3=False, enabled_j4=False, ng_policy="available"):
        return build_relative_force_model(
            self.ORIGIN,
            self.planets,
            self.MU_SUN,
            self.C_AU_D,
            ng,
            self.J2,
            zonals,
            enabled=enabled,
            enabled_j3=enabled_j3,
            enabled_j4=enabled_j4,
            ng_policy=ng_policy,
        )

    def test_no_optional_zonals_is_exact_frozen_relative_builder(self):
        actual, metadata = self.make()
        expected = build_relative_force(
            self.ORIGIN,
            self.planets,
            self.MU_SUN,
            self.C_AU_D,
            None,
            self.J2["reference_radius_au"],
            self.J2["j2"],
        )
        for time_days in (-0.5, 0.0, 0.75, 1.5):
            self.assertEqual(actual(time_days, self.state.position, self.state.velocity), expected(time_days, self.state.position, self.state.velocity))
        self.assertEqual(metadata["earth_zonals"]["enabled_degrees"], [])
        self.assertEqual(metadata["applied_forces"], ["solar_monopole", "planetary_monopoles", "solar_schwarzschild", "earth_j2"])
        self.assertFalse(metadata["ng_applied"])
        self.assertFalse(metadata["tdb_iers_scaling_approx"])

    def test_available_ng_is_gated_once_at_forecast_origin(self):
        params = NonGravitationalParameters(a1_au_d2=2e-10, a2_au_d2=-3e-10)
        ng = NGInput(parameters=params, source="synthetic", source_sha256="abc", available_from_jd_tdb=self.ORIGIN - 1.0, availability_basis="fixture")
        actual, metadata = self.make(ng=ng)
        expected = build_relative_force(self.ORIGIN, self.planets, self.MU_SUN, self.C_AU_D, params, self.J2["reference_radius_au"], self.J2["j2"])
        self.assertEqual(metadata["ng_status"], "available")
        self.assertTrue(metadata["ng_applied"])
        self.assertIn("non_gravitational", metadata["applied_terms"])
        self.assertEqual(actual(1.0, self.state.position, self.state.velocity), expected(1.0, self.state.position, self.state.velocity))

    def test_off_and_not_yet_available_do_not_apply_ng(self):
        params = NonGravitationalParameters(a2_au_d2=2e-10)
        ng = NGInput(parameters=params, source="future", available_from_jd_tdb=self.ORIGIN + 1.0, availability_basis="fixture")
        unavailable, unavailable_meta = self.make(ng=ng)
        off, off_meta = self.make(ng=ng, ng_policy="off")
        expected = build_relative_force(self.ORIGIN, self.planets, self.MU_SUN, self.C_AU_D, None, self.J2["reference_radius_au"], self.J2["j2"])
        for time_days in (0.0, 1.5):
            self.assertEqual(unavailable(time_days, self.state.position, self.state.velocity), expected(time_days, self.state.position, self.state.velocity))
            self.assertEqual(off(time_days, self.state.position, self.state.velocity), expected(time_days, self.state.position, self.state.velocity))
        self.assertEqual(unavailable_meta["ng_status"], "not_yet_available")
        self.assertEqual(off_meta["ng_status"], "not_yet_available")
        self.assertFalse(unavailable_meta["ng_applied"])
        self.assertFalse(off_meta["ng_applied"])
        with self.assertRaises(ValueError):
            self.make(ng=ng, ng_policy="required")

    def test_j3_j4_match_the_published_zonal_direct_indirect_terms(self):
        actual, metadata = self.make(zonals=self.ZONALS, enabled=("J3", "J4"))
        baseline = build_relative_force(self.ORIGIN, self.planets, self.MU_SUN, self.C_AU_D, None, self.J2["reference_radius_au"], self.J2["j2"])
        for time_days in (-0.25, 0.0, 0.5, 1.25):
            earth_position = self.earth.ephemeris.state_at(time_days).position
            pole = earth_pole_iau(self.ORIGIN + time_days)
            years = ((self.ORIGIN - 2451545.0) + time_days) / 365.25
            correction = (0.0, 0.0, 0.0)
            for degree in (3, 4):
                cn0 = self.ZONALS[f"c{degree}0_j2000"] + self.ZONALS[f"c{degree}0_rate_per_julian_year"] * years
                jn = -math.sqrt(2.0 * degree + 1.0) * cn0
                direct = zonal_acceleration(tuple(self.state.position[i] - earth_position[i] for i in range(3)), pole, self.ZONALS["mu_au3_d2"], self.ZONALS["reference_radius_au"], degree, jn)
                indirect = zonal_acceleration(tuple(-value for value in earth_position), pole, self.ZONALS["mu_au3_d2"], self.ZONALS["reference_radius_au"], degree, jn)
                correction = add(correction, subtract(direct, indirect))
            expected = add(baseline(time_days, self.state.position, self.state.velocity), correction)
            self.assertEqual(actual(time_days, self.state.position, self.state.velocity), expected)
        self.assertEqual(metadata["earth_zonals"]["enabled_degrees"], [3, 4])
        self.assertEqual(metadata["applied_terms"][-2:], ["earth_j3", "earth_j4"])
        self.assertTrue(metadata["tdb_iers_scaling_approx"])
        self.assertFalse(metadata["accuracy_guaranteed"])

    def test_requires_earth_and_explicit_zonal_coefficients(self):
        with self.assertRaises(ValueError):
            build_relative_force_model(self.ORIGIN, (self.mars,), self.MU_SUN, self.C_AU_D, None, self.J2)
        with self.assertRaises(ValueError):
            self.make(zonals=None, enabled=(3,))
        with self.assertRaises(ValueError):
            self.make(zonals={"reference_radius_au": 1e-4, "mu_au3_d2": 1e-6}, enabled=(3,))
        with self.assertRaises(ValueError):
            self.make(ng_policy="unknown")
        with self.assertRaises(ValueError):
            self.make(zonals=self.ZONALS, enabled=(3.9,))
        with self.assertRaises(ValueError):
            self.make(zonals=self.ZONALS, enabled_j3=1)
        with self.assertRaises(ValueError):
            self.make(zonals=self.ZONALS, enabled={"J3": 1})

    def test_relative_metadata_has_no_target_identity_or_reference_fields(self):
        _, metadata = self.make()
        self.assertNotIn("target_id", metadata)
        self.assertNotIn("reference", metadata)
        self.assertEqual(metadata["perturber_ids"], ["399", "499"])


if __name__ == "__main__":
    unittest.main()

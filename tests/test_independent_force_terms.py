from __future__ import annotations

from decimal import Decimal
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from independent_force_terms import decimal_force_terms


def d(value: object) -> Decimal:
    return Decimal(str(value))


def close_vector(actual, expected, tol=Decimal("1e-25")):
    for a, b in zip(actual, expected):
        if abs(a - b) > tol:
            raise AssertionError(f"{actual!r} != {expected!r}")


class IndependentForceTermsTests(unittest.TestCase):
    def test_published_static_sun_schwarzschild_limit(self):
        mu, c = d("1.234"), d("987")
        r = tuple(map(d, (1.2, 0.2, 0.1)))
        v = tuple(map(d, (0.01, 0.02, -0.01)))
        got = decimal_force_terms(r, v, (mu,), ((d(0), d(0), d(0)),), ((d(0), d(0), d(0)),), c)
        radius = sum(x * x for x in r).sqrt()
        vv = sum(x * x for x in v)
        rv = sum(x * y for x, y in zip(r, v))
        factor = mu / (c * c * radius**3)
        expected = tuple(factor * (r[i] * (d(4) * mu / radius - vv) + v[i] * d(4) * rv) for i in range(3))
        close_vector(tuple(got["total"][i] - got["newton"][i] for i in range(3)), expected)
        self.assertTrue(all(isinstance(x, Decimal) for x in got["total"]))

    def test_newtonian_two_source_sum(self):
        target = (d("0.3"), d("0.4"), d("0"))
        positions = ((d("0"), d("0"), d("0")), (d("1"), d("0"), d("0")))
        got = decimal_force_terms(target, (d("0"),) * 3, (d("2"), d("3")), positions, ((d("0"),) * 3, (d("0"),) * 3), d("100"))
        expected = (d("2") * (positions[0][0] - target[0]) / ((positions[0][0] - target[0]) ** 2 + (positions[0][1] - target[1]) ** 2) ** d("1.5") + d("3") * (positions[1][0] - target[0]) / ((positions[1][0] - target[0]) ** 2 + (positions[1][1] - target[1]) ** 2) ** d("1.5"), d("2") * (positions[0][1] - target[1]) / ((positions[0][0] - target[0]) ** 2 + (positions[0][1] - target[1]) ** 2) ** d("1.5") + d("3") * (positions[1][1] - target[1]) / ((positions[1][0] - target[0]) ** 2 + (positions[1][1] - target[1]) ** 2) ** d("1.5"), d("0"))
        close_vector(got["newton"], expected)

    def test_j2_equator_pole_and_earth_index(self):
        kwargs = {"mu": d("1"), "radius_au": d("0.1"), "j2": d("0.2")}
        equator = decimal_force_terms((d("2"), d("0"), d("0")), (d("0"), d("1"), d("0")), (d("1"),), ((d("0"),) * 3,), ((d("0"),) * 3,), d("100"), earth_j2=kwargs, earth_index=0)
        pole = decimal_force_terms((d("0"), d("0"), d("2")), (d("-1"), d("0"), d("0")), (d("1"),), ((d("0"),) * 3,), ((d("0"),) * 3,), d("100"), earth_j2=kwargs, earth_index=0)
        self.assertLess(equator["earth_j2"][0], d("0"))
        self.assertGreater(pole["earth_j2"][2], d("0"))
        self.assertEqual(equator["earth_j2"][1], d("0"))

    def test_general_solar_pole_rotation_is_finite(self):
        term = decimal_force_terms((d("1"), d("2"), d("3")), (d("0"), d("1"), d("0")), (d("2"),), ((d("0"),) * 3,), ((d("0"),) * 3,), d("100"), solar_j2={"mu": d("2"), "radius_au": d("0.01"), "j2": d("0.01"), "pole": (d("1"), d("1"), d("1"))})
        self.assertTrue(all(x.is_finite() for x in term["solar_j2"]))

    def test_ng_rtn_inverse_square(self):
        got = decimal_force_terms((d("2"), d("0"), d("0")), (d("0"), d("1"), d("0")), (d("1"),), ((d("0"),) * 3,), ((d("0"),) * 3,), d("100"), ng={"A1": d("1"), "A2": d("2"), "A3": d("3"), "r0_au": d("1")})
        close_vector(got["ng"], (d("0.25"), d("0.5"), d("0.75")))

    def test_translation_covariance_of_ppn_terms(self):
        target = (d("0.4"), d("-0.8"), d("0.6")); velocity = (d("-0.02"), d("0.03"), d("0.04"))
        mus = (d("2"), d("3"), d("0.5")); positions = ((d("0.1"), d("0.2"), d("-0.3")), (d("1.2"), d("-0.4"), d("0.8")), (d("-0.7"), d("0.9"), d("0.5"))); velocities = ((d("0.01"), d("-0.02"), d("0.03")), (d("-0.04"), d("0.05"), d("0.02")), (d("0.02"), d("0.01"), d("-0.03")))
        base = decimal_force_terms(target, velocity, mus, positions, velocities, d("900"))
        shift = (d("10"), d("-4"), d("2"))
        shifted = decimal_force_terms(tuple(target[i] + shift[i] for i in range(3)), velocity, mus, tuple(tuple(p[i] + shift[i] for i in range(3)) for p in positions), velocities, d("900"))
        for name in ("newton", "ppn_position", "ppn_velocity", "ppn_source_acceleration", "total"):
            close_vector(base[name], shifted[name])

    def test_ng_uses_relative_position_and_velocity_for_moving_sun(self):
        relative_r = (d("2"), d("0"), d("0")); relative_v = (d("0"), d("1"), d("0"))
        ng = {"A1": d("1"), "A2": d("2"), "A3": d("3"), "r0_au": d("1")}
        origin = decimal_force_terms(relative_r, relative_v, (d("1"),), ((d("0"),) * 3,), ((d("0"),) * 3,), d("100"), ng=ng)
        sun_r, sun_v = (d("10"), d("-4"), d("2")), (d("0.2"), d("-0.3"), d("0.4"))
        translated = decimal_force_terms(tuple(relative_r[i] + sun_r[i] for i in range(3)), tuple(relative_v[i] + sun_v[i] for i in range(3)), (d("1"),), (sun_r,), (sun_v,), d("100"), ng=ng)
        close_vector(origin["ng"], translated["ng"])

    def test_small_bodies_are_newtonian_only(self):
        target, velocity = (d("0.7"), d("0.2"), d("0.1")), (d("0.01"), d("-0.02"), d("0.03"))
        positions = ((d("0"),) * 3, (d("1"), d("0"), d("0")), (d("-0.4"), d("0.8"), d("0.2")))
        velocities = ((d("0"),) * 3, (d("0.01"), d("0"), d("0")), (d("0"), d("-0.01"), d("0")))
        without_sb = decimal_force_terms(target, velocity, (d("10"), d("2")), positions[:2], velocities[:2], d("100"), major_indices=(0, 1))
        with_sb = decimal_force_terms(target, velocity, (d("10"), d("2"), d("0.5")), positions, velocities, d("100"), major_indices=(0, 1))
        for name in ("ppn_position", "ppn_velocity", "ppn_source_acceleration"):
            close_vector(without_sb[name], with_sb[name])
        self.assertNotEqual(without_sb["newton"], with_sb["newton"])

    def test_validation_rejects_bad_inputs(self):
        with self.assertRaises(ValueError):
            decimal_force_terms((0, 0, 0), (0, 0, 0), (1,), ((0, 0, 0),), ((0, 0, 0),), 100)
        with self.assertRaises(ValueError):
            decimal_force_terms((1, 0, 0), (0, 1, 0), (1, 2), ((0, 0, 0), (0, 0, 0)), ((0, 0, 0), (0, 0, 0)), 100, outer_indices=(True,))
        with self.assertRaises(ValueError):
            decimal_force_terms((1, 0, 0), (0, 1, 0), (1, 2), ((0, 0, 0), (2, 0, 0)), ((0, 0, 0), (0, 0, 0)), 100, major_indices=(0,), outer_indices=(1,))
        with self.assertRaises(ValueError):
            decimal_force_terms((1, 0, 0), (0, 1, 0), (1,), ((0, 0, 0),), ((0, 0, 0),), 100, ng={"A1": float("nan")})
        with self.assertRaises(ValueError):
            decimal_force_terms((1, 0, 0), (0, 1, 0), (1,), ((0, 0, 0),), ((0, 0, 0),), 100, earth_j2={"mu": 1, "radius_au": 1, "j2": 1})


if __name__ == "__main__":
    unittest.main()

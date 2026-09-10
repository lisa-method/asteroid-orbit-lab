import copy
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from orbit_baselines import specific_energy
from sbdb_covariance import (
    MEAN_OBLIQUITY_RAD,
    SBDB_LABELS,
    elements_to_state,
    ecliptic_to_icrf,
    icrf_to_ecliptic,
    load_sbdb_covariance,
    state_from_covariance,
)


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/apophis_covariance/sbdb_cov_vec.json"
MU = 2.959122082855911e-4


def _document() -> dict:
    return json.loads(RAW.read_text(encoding="utf-8"))


def _load_modified(mutator):
    document = _document()
    mutator(document)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "sbdb.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return load_sbdb_covariance(path)


class SBDBCovarianceTests(unittest.TestCase):
    def test_real_response_preserves_epoch_cross_terms_and_ng_separately(self):
        parsed = load_sbdb_covariance(RAW)
        self.assertEqual(parsed.labels, SBDB_LABELS)
        self.assertEqual(parsed.covariance_epoch_jd_tdb, 2459215.5)
        self.assertEqual(parsed.solution_epoch_jd_tdb, 2461200.5)
        self.assertEqual(parsed.mean_elements["node"], 204.0389272089208)
        # data[1] is (row=0, column=1), while data[4] is (row=1, column=2).
        self.assertEqual(parsed.covariance[0][1], parsed.covariance[1][0])
        self.assertEqual(parsed.covariance[0][1], -3.913592780937238e-18)
        self.assertEqual(parsed.covariance[1][2], -1.046818639552038e-15)
        self.assertEqual(parsed.non_grav_nominal, (5.0e-13, -2.901766637153165e-14))
        self.assertGreater(parsed.non_grav_sigma[0], 0.0)
        self.assertGreater(parsed.non_grav_sigma[1], 0.0)
        self.assertEqual(parsed.ng_sigma, parsed.non_grav_sigma)
        self.assertEqual(parsed.non_grav_units, "au/d^2")
        self.assertIn("provenance", parsed.non_grav_availability_basis)
        with self.assertRaises(TypeError):
            parsed.mean_elements["e"] = 0.0  # type: ignore[index]

    def test_circular_state_energy_and_known_frame_rotation(self):
        elements = {"e": 0.0, "q": 1.0, "tp": 2450000.0, "node": 0.0, "peri": 0.0, "i": 0.0}
        period = 2.0 * math.pi / math.sqrt(MU)
        at_peri = elements_to_state(elements, elements["tp"], MU)
        quarter = elements_to_state(elements, elements["tp"] + period / 4.0, MU)
        self.assertAlmostEqual(at_peri.position[0], 1.0, places=14)
        self.assertAlmostEqual(at_peri.position[1], 0.0, places=14)
        self.assertAlmostEqual(math.sqrt(sum(value * value for value in at_peri.velocity)), math.sqrt(MU), places=14)
        self.assertAlmostEqual(at_peri.velocity[1], math.sqrt(MU) * math.cos(MEAN_OBLIQUITY_RAD), places=14)
        self.assertAlmostEqual(specific_energy(at_peri, MU), -MU / 2.0, places=14)
        self.assertAlmostEqual(quarter.position[0], 0.0, delta=5.0e-12)
        self.assertAlmostEqual(quarter.position[1], math.cos(MEAN_OBLIQUITY_RAD), places=13)
        self.assertAlmostEqual(quarter.position[2], math.sin(MEAN_OBLIQUITY_RAD), places=13)

    def test_eccentric_kepler_state_has_expected_specific_energy(self):
        elements = {"e": 0.42, "q": 0.73, "tp": 2450100.0, "node": 23.0, "peri": 71.0, "i": 17.0}
        state = elements_to_state(elements, 2450222.25, MU)
        semimajor = elements["q"] / (1.0 - elements["e"])
        self.assertAlmostEqual(specific_energy(state, MU), -MU / (2.0 * semimajor), places=13)

    def test_frame_rotation_has_a_numerical_inverse(self):
        vector = (0.37, -1.2, 4.5)
        self.assertEqual(icrf_to_ecliptic(ecliptic_to_icrf(vector)), vector)

    def test_tp_delta_has_minus_velocity_and_minus_acceleration_derivatives(self):
        elements = {"e": 0.21, "q": 0.82, "tp": 2450000.0, "node": 33.0, "peri": 12.0, "i": 9.0}
        epoch = 2450345.25
        h = 1.0e-5
        plus = elements_to_state(elements, epoch, MU, delta=(0.0, 0.0, h, 0.0, 0.0, 0.0, 0.0, 0.0))
        minus = elements_to_state(elements, epoch, MU, delta=(0.0, 0.0, -h, 0.0, 0.0, 0.0, 0.0, 0.0))
        central_position = tuple((a - b) / (2.0 * h) for a, b in zip(plus.position, minus.position))
        central_velocity = tuple((a - b) / (2.0 * h) for a, b in zip(plus.velocity, minus.velocity))
        nominal = elements_to_state(elements, epoch, MU)
        radius = math.sqrt(sum(value * value for value in nominal.position))
        acceleration = tuple(-MU * value / radius**3 for value in nominal.position)
        for actual, expected in zip(central_position, nominal.velocity):
            self.assertAlmostEqual(actual, -expected, delta=2.0e-10)
        for actual, expected in zip(central_velocity, acceleration):
            self.assertAlmostEqual(actual, -expected, delta=2.0e-12)

    def test_long_tp_token_is_subtracted_before_float_conversion(self):
        # Both absolute epochs round to the same binary float, but their
        # decimal tokens still describe a resolvable relative phase.
        elements = {"e": 0.0, "q": 1.0, "tp": "2450000.0000000001", "node": 0.0, "peri": 0.0, "i": 0.0}
        state = elements_to_state(elements, "2450000.0000000002", 1.0)
        self.assertAlmostEqual(state.position[1], math.cos(MEAN_OBLIQUITY_RAD) * 1.0e-10, delta=2.0e-17)
        self.assertAlmostEqual(state.position[0], 1.0, delta=2.0e-16)

    def test_ng_delta_does_not_change_kepler_state(self):
        parsed = load_sbdb_covariance(RAW)
        plain = state_from_covariance(parsed, MU)
        ng_only = state_from_covariance(parsed, MU, delta=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, -2.0))
        self.assertEqual(plain, ng_only)

    def test_missing_covariance_and_bad_labels_are_rejected(self):
        with self.assertRaises(ValueError):
            _load_modified(lambda document: document["orbit"].pop("covariance"))
        with self.assertRaises(ValueError):
            _load_modified(lambda document: document["orbit"]["covariance"]["labels"].__setitem__(1, "bad"))
        with self.assertRaises(ValueError):
            _load_modified(lambda document: document["signature"].__setitem__("version", "9.9"))
        with self.assertRaises(ValueError):
            _load_modified(lambda document: document["orbit"].__setitem__("equinox", "B1950"))

    def test_bad_units_kinds_duplicates_and_nonfinite_values_are_rejected(self):
        bad_mutators = (
            lambda document: document["orbit"]["covariance"]["elements"][1].__setitem__("units", "km"),
            lambda document: document["orbit"]["model_pars"][0].__setitem__("kind", "SET"),
            lambda document: document["orbit"]["model_pars"].append(copy.deepcopy(document["orbit"]["model_pars"][0])),
            lambda document: document["orbit"]["covariance"]["data"].__setitem__(0, "nan"),
        )
        for mutator in bad_mutators:
            with self.subTest(mutator=mutator):
                with self.assertRaises(ValueError):
                    _load_modified(mutator)

    def test_mapper_rejects_invalid_domain_and_delta(self):
        elements = {"e": 0.2, "q": 0.8, "tp": 2450000.0, "node": 0.0, "peri": 0.0, "i": 0.0}
        with self.assertRaises(ValueError):
            elements_to_state({**elements, "e": 1.0}, 2450001.0, MU)
        with self.assertRaises(ValueError):
            elements_to_state({**elements, "q": 0.0}, 2450001.0, MU)
        with self.assertRaises(ValueError):
            elements_to_state(elements, 2450001.0, MU, delta=(0.0,) * 7)
        with self.assertRaises(ValueError):
            elements_to_state(elements, 2450001.0, MU, delta=(True,) + (0.0,) * 7)


if __name__ == "__main__":
    unittest.main()

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ng_inputs_v2 import NGInput, load_ng_input
from planetary_dynamics import NonGravitationalParameters


HEADER = """JPL/HORIZONS  613569 (2006 TU7) 2026-Sep-07 05:23:36
Rec #: 613569 Soln.date: 2024-Sep-29_06:25:23 # obs: 196 (2002-2024)
EPOCH= 2459105.5 ! 2020-Sep-13.00 (TDB)
Asteroid non-gravitational force model (A1,A2,A3=au/d^2;R0=au):
 A1= 0. A2= 1.18779071272E-13 A3= 0.
 ALN= 1. NK= 0. NM= 2. NN= 5.093 R0= 1.
$$SOE
2461774.5, A.D. 2028-Jan-04 00:00:00.0000, 0,0,0,0,0,0,
$$EOE"""


def raw(path: Path, *, retrieved: str | None = None, sigma=None, text: str = HEADER) -> None:
    document = {"result": text, "signature": {"source": "NASA/JPL Horizons API", "version": "1.2"}}
    if retrieved is not None:
        document["retrieved_at_utc"] = retrieved
    if sigma is not None:
        document["sigma_au_d2"] = sigma
    path.write_text(json.dumps(document), encoding="utf-8")


class NGInputV2Tests(unittest.TestCase):
    def test_known_header_and_unknown_sigma(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.json"
            raw(path)
            result = load_ng_input(path)
            self.assertAlmostEqual(result.parameters.a2_au_d2, 1.18779071272e-13)
            self.assertIsNone(result.sigma_au_d2)
            self.assertEqual(result.solution_date, "2024-Sep-29_06:25:23")
            self.assertEqual(result.status(2461774.5), "availability_unknown")
            self.assertEqual(result.source_sha256, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_download_date_and_later_solution_date_use_conservative_maximum(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.json"
            raw(path, retrieved="2024-01-01T01:00:00+00:00")
            result = load_ng_input(path)
            expected = 2460584.5  # 2024-09-29 midnight JD + 2 d
            self.assertEqual(result.available_from_jd_tdb, expected)
            self.assertEqual(result.status(expected), "available")
            self.assertEqual(result.status(expected - 1.0), "not_yet_available")

    def test_solution_date_alone_never_proves_availability(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.json"
            raw(path)
            result = load_ng_input(path)
            self.assertIsNone(result.available_from_jd_tdb)
            self.assertEqual(result.status(2500000.0), "availability_unknown")

    def test_missing_header_is_not_provided(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.json"
            raw(path, text="Target body\n$$SOE\n1,2,3")
            result = load_ng_input(path)
            self.assertIsNone(result.parameters)
            self.assertEqual(result.status(0.0), "not_provided")

    def test_malformed_header_and_invalid_domain_raise(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.json"
            raw(path, text=HEADER.replace("R0= 1.", "R0= bad"))
            with self.assertRaises(ValueError):
                load_ng_input(path)

    def test_malformed_numeric_token_and_duplicate_key_raise(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.json"
            raw(path, text=HEADER.replace("A2= 1.18779071272E-13", "A2= 1e"))
            with self.assertRaises(ValueError):
                load_ng_input(path)
            duplicate = HEADER.replace("A2= 1.18779071272E-13", "A2= 1.18779071272E-13 A2= 2e-13")
            raw(path, text=duplicate)
            with self.assertRaises(ValueError):
                load_ng_input(path)
            raw(path, text=HEADER.replace("ALN= 1.", "ALN= -1."))
            with self.assertRaises(ValueError):
                load_ng_input(path)

    def test_explicit_sigma_is_preserved_and_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.json"
            raw(path, sigma=[1e-15, 2e-15, 0.0])
            self.assertEqual(load_ng_input(path).sigma_au_d2, (1e-15, 2e-15, 0.0))
            raw(path, sigma=[1.0, -1.0, 0.0])
            with self.assertRaises(ValueError):
                load_ng_input(path)

    def test_status_rejects_nonfinite_start(self):
        item = NGInput()
        with self.assertRaises(ValueError):
            item.status(float("nan"))

    def test_manifest_cannot_prove_availability_without_matching_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "data" / "raw" / "x.json"
            path.parent.mkdir(parents=True)
            raw(path)
            checksums = root / "data" / "checksums"
            checksums.mkdir()
            manifest = {"created_utc": "2026-09-07T12:00:00+00:00", "files": [{"path": "data/raw/x.json", "sha256": "wrong", "bytes": path.stat().st_size}]}
            (checksums / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            result = load_ng_input(path)
            self.assertIsNone(result.available_from_jd_tdb)
            self.assertEqual(result.status(2500000.0), "availability_unknown")
            manifest["files"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            (checksums / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            result = load_ng_input(path)
            self.assertIsNotNone(result.available_from_jd_tdb)
            self.assertIn("manifest_sha256=", result.availability_basis)

    def test_known_bound_requires_provenance(self):
        with self.assertRaises(ValueError):
            NGInput(parameters=load_ng_input(Path("data/raw/development30/asteroids/asteroid_613569_daily.json")).parameters,
                    source="unknown", available_from_jd_tdb=1.0)

    def test_bool_and_wrong_parameter_types_raise(self):
        with self.assertRaises(ValueError):
            NGInput(parameters=True)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            NGInput(parameters=NonGravitationalParameters(alpha=True))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()

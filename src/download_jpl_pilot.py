"""Download immutable JPL Horizons and SBDB responses for the EDA pilot.

The script uses only the Python standard library. Existing raw responses are
never overwritten: a repeated run verifies and reuses them instead.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import urllib.parse
import urllib.request


USER_AGENT = "astrophysics-ml-project/eda-pilot (anonymous academic use)"


def load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def build_horizons_url(config: dict, target_id: str, *, small_body: bool) -> str:
    time_range = config["time_range"]
    coordinates = config["coordinates"]
    command = f"{target_id};" if small_body else target_id
    params = {
        "format": "json",
        "COMMAND": f"'{command}'",
        "OBJ_DATA": "'YES'",
        "MAKE_EPHEM": "'YES'",
        "EPHEM_TYPE": "'VECTORS'",
        "CENTER": f"'{coordinates['center']}'",
        "START_TIME": f"'{time_range['start']}'",
        "STOP_TIME": f"'{time_range['stop']}'",
        "STEP_SIZE": f"'{time_range['step']}'",
        "TIME_TYPE": f"'{time_range['time_scale']}'",
        "REF_PLANE": f"'{coordinates['reference_plane']}'",
        "REF_SYSTEM": f"'{coordinates['reference_system']}'",
        "OUT_UNITS": f"'{coordinates['units']}'",
        "VEC_TABLE": "'2'",
        "VEC_CORR": f"'{coordinates['vector_corrections']}'",
        "CSV_FORMAT": "'YES'",
        "TIME_DIGITS": "'SECONDS'",
        "CAL_TYPE": f"'{coordinates['calendar']}'",
    }
    return config["provenance"]["horizons_api"] + "?" + urllib.parse.urlencode(params)


def build_sbdb_url(config: dict, target_id: str) -> str:
    params = {"sstr": target_id, "full-prec": "true", "phys-par": "true"}
    return config["provenance"]["sbdb_api"] + "?" + urllib.parse.urlencode(params)


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = response.read()
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status} for {url}")
    return payload


def validate_json(payload: bytes, *, horizons: bool) -> dict:
    document = json.loads(payload)
    if "error" in document:
        raise RuntimeError(str(document["error"]))
    if horizons:
        result = document.get("result", "")
        if "$$SOE" not in result or "$$EOE" not in result:
            raise RuntimeError("Horizons response contains no vector table")
        if document.get("signature", {}).get("source") != "NASA/JPL Horizons API":
            raise RuntimeError("Unexpected Horizons response signature")
    elif "object" not in document or "orbit" not in document:
        raise RuntimeError("Unexpected SBDB response schema")
    return document


def write_immutable(path: Path, payload: bytes) -> str:
    digest = hashlib.sha256(payload).hexdigest()
    if path.exists():
        existing = path.read_bytes()
        existing_digest = hashlib.sha256(existing).hexdigest()
        if existing_digest != digest:
            raise RuntimeError(f"Refusing to overwrite changed raw file: {path}")
        return digest
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(path)
    return digest


def download_one(item: dict) -> dict:
    if item["path"].exists():
        payload = item["path"].read_bytes()
        validate_json(payload, horizons=item["kind"].startswith("horizons"))
        digest = hashlib.sha256(payload).hexdigest()
        return {
            "kind": item["kind"],
            "target_id": item["target_id"],
            "target_name": item["target_name"],
            "path": item["path"].as_posix(),
            "source_url": item["url"],
            "sha256": digest,
            "bytes": len(payload),
            "reused": True,
        }
    payload = fetch(item["url"])
    validate_json(payload, horizons=item["kind"].startswith("horizons"))
    digest = write_immutable(item["path"], payload)
    return {
        "kind": item["kind"],
        "target_id": item["target_id"],
        "target_name": item["target_name"],
        "path": item["path"].as_posix(),
        "source_url": item["url"],
        "sha256": digest,
        "bytes": len(payload),
        "reused": False,
    }


def build_jobs(root: Path, config: dict) -> list[dict]:
    raw = root / "data" / "raw"
    jobs = []
    for asteroid in config["asteroids"]:
        asteroid_id = asteroid["id"]
        jobs.append(
            {
                "kind": "horizons_asteroid",
                "target_id": asteroid_id,
                "target_name": asteroid["name"],
                "url": build_horizons_url(config, asteroid_id, small_body=True),
                "path": raw / "horizons" / f"asteroid_{asteroid_id}.json",
            }
        )
        jobs.append(
            {
                "kind": "sbdb_object",
                "target_id": asteroid_id,
                "target_name": asteroid["name"],
                "url": build_sbdb_url(config, asteroid_id),
                "path": raw / "sbdb" / f"object_{asteroid_id}.json",
            }
        )
    for body in config["perturbers"]:
        body_id = body["id"]
        jobs.append(
            {
                "kind": "horizons_body",
                "target_id": body_id,
                "target_name": body["name"],
                "url": build_horizons_url(config, body_id, small_body=False),
                "path": raw / "horizons" / f"body_{body_id}.json",
            }
        )
    return jobs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    config = load_config(args.config.resolve())
    jobs = build_jobs(root, config)
    retrieved_at = dt.datetime.now(dt.timezone.utc).isoformat()
    records = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(download_one, job): job for job in jobs}
        for future in concurrent.futures.as_completed(futures):
            job = futures[future]
            try:
                record = future.result()
            except Exception as exc:
                print(f"FAILED {job['kind']} {job['target_id']}: {exc}", file=sys.stderr)
                return 1
            records.append(record)
            print(f"OK {record['kind']} {record['target_id']} {record['bytes']} bytes")

    records.sort(key=lambda row: (row["kind"], row["target_id"]))
    manifest = {
        "schema_version": 1,
        "retrieved_at_utc": retrieved_at,
        "config": args.config.resolve().as_posix(),
        "files": records,
    }
    manifest_filename = config.get("manifest_filename", "jpl_pilot_manifest.json")
    if Path(manifest_filename).name != manifest_filename:
        raise ValueError("manifest_filename must be a plain filename")
    manifest_path = root / "data" / "checksums" / manifest_filename
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

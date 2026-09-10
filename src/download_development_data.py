"""Download the frozen development cohort serially, with immutable raw files."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path

from download_jpl_pilot import build_horizons_url, download_one
from prepare_development_sample import atomic_json
from run_eda import parse_horizons


def build_jobs(root: Path, config: dict, sample: dict) -> list[dict]:
    base = json.loads((root / config["data_config"]).read_text())
    directory = root / config["raw_directory"]
    jobs = []
    for body in base["perturbers"]:
        query = {**base, "time_range": config["planet_ephemerides"]}
        jobs.append({"kind": "horizons_planet", "target_id": body["id"], "target_name": body["name"],
                     "url": build_horizons_url(query, body["id"], small_body=False),
                     "path": directory / "planets" / f"body_{body['id']}.json"})
    for obj in sample["objects"]:
        start = dt.date.fromisoformat(obj["start_date"])
        stop = start + dt.timedelta(days=max(config["horizons_days"]))
        periods = [("daily", {"start": start.isoformat(), "stop": stop.isoformat(), "step": "1 d", "time_scale": "TDB"})]
        if obj["event"]:
            event_date = dt.datetime.strptime(obj["event"]["cd"].split()[0], "%Y-%b-%d").date()
            delta = dt.timedelta(days=config["reference_refinement"]["half_window_days"])
            periods.append(("refined", {"start": (event_date-delta).isoformat(), "stop": (event_date+delta).isoformat(),
                                         "step": config["reference_refinement"]["step"], "time_scale": "TDB"}))
        for label, period in periods:
            query = {**base, "time_range": period}
            jobs.append({"kind": "horizons_asteroid", "target_id": obj["id"], "target_name": obj["name"],
                         "url": build_horizons_url(query, obj["id"], small_body=True),
                         "path": directory / "asteroids" / f"asteroid_{obj['id']}_{label}.json"})
    return jobs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--config", type=Path, default=Path("configs/development30.json"))
    args = parser.parse_args()
    root = args.root.resolve()
    config_path = root / args.config
    config = json.loads(config_path.read_text())
    sample_path = root / config["sample_path"]
    sample = json.loads(sample_path.read_text())
    if sample["config_sha256"] != hashlib.sha256(config_path.read_bytes()).hexdigest():
        raise ValueError("Config changed after sample freeze")
    manifest_path = root / "data/checksums/development30_data_manifest.json"
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"files": []}
    known = {record["path"]: record for record in previous["files"]}
    jobs = build_jobs(root, config, sample)
    records = []
    for index, job in enumerate(jobs):
        relpath = job["path"].relative_to(root).as_posix()
        if relpath in known:
            record = known[relpath]
            payload = job["path"].read_bytes()
            if record["sha256"] != hashlib.sha256(payload).hexdigest() or record["bytes"] != len(payload) or record["source_url"] != job["url"]:
                raise ValueError(f"Input provenance mismatch: {relpath}")
        else:
            record = download_one(job)
            record["path"] = relpath
            record.pop("reused", None)
            doc = json.loads(job["path"].read_text())
            if doc["signature"]["version"] != "1.2":
                raise ValueError(f"Review Horizons API version: {doc['signature']}")
            metadata, parsed = parse_horizons(job["path"], job["target_id"], job["target_name"])
            record.update({"signature": doc["signature"], "rows": len(parsed),
                           "first_jd_tdb": parsed[0]["epoch_jd_tdb"], "last_jd_tdb": parsed[-1]["epoch_jd_tdb"],
                           "coordinates": sample["coordinates"], "time_scale": "TDB"})
        records.append(record)
        manifest = {"schema_version": 1, "sample_sha256": hashlib.sha256(sample_path.read_bytes()).hexdigest(),
                    "created_utc": previous.get("created_utc", dt.datetime.now(dt.timezone.utc).isoformat()),
                    "complete": len(records) == len(jobs), "files": records}
        atomic_json(manifest_path, manifest)
        print(json.dumps({"completed": index+1, "total": len(jobs), "path": relpath, "rows": record["rows"]}), flush=True)


if __name__ == "__main__":
    main()

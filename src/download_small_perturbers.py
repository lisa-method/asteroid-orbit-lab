"""Download immutable Horizons ephemerides for the B3+ asteroid perturbers."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
from pathlib import Path
import sys

from download_jpl_pilot import build_horizons_url, download_one, load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--workers", default=4, type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    config_path = args.config.resolve()
    config = load_config(config_path)
    data_config = load_config(root / config["data_config"])
    raw_directory = root / config["small_body_ephemeris_directory"]
    jobs = []
    metadata = {}
    for body in config["small_body_perturbers"]:
        body_id = body["id"]
        jobs.append(
            {
                "kind": "horizons_small_body_perturber",
                "target_id": body_id,
                "target_name": body["name"],
                "url": build_horizons_url(data_config, body_id, small_body=True),
                "path": raw_directory / f"asteroid_{body_id}.json",
            }
        )
        metadata[body_id] = body

    records = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(download_one, job): job for job in jobs}
        for future in concurrent.futures.as_completed(futures):
            job = futures[future]
            try:
                record = future.result()
            except Exception as exc:
                print(f"FAILED {job['target_id']}: {exc}", file=sys.stderr)
                return 1
            body = metadata[record["target_id"]]
            record["gm_over_gm_sun"] = body["gm_over_gm_sun"]
            records.append(record)
            action = "reused" if record["reused"] else "downloaded"
            print(f"OK {record['target_name']} ({record['target_id']}): {action}")

    records.sort(key=lambda row: int(row["target_id"]))
    manifest = {
        "schema_version": 1,
        "retrieved_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "config": config_path.relative_to(root).as_posix(),
        "mass_source": config["small_body_mass_source"],
        "mass_source_url": config["small_body_mass_source_url"],
        "files": records,
    }
    manifest_path = root / config["small_body_manifest"]
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

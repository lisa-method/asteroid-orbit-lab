"""Download synchronized high-cadence Horizons states for one event window."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
from pathlib import Path

from download_jpl_pilot import build_horizons_url, download_one, load_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-config", type=Path, required=True)
    parser.add_argument("--event-config", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    base = load_config(args.base_config.resolve())
    event = load_config(args.event_config.resolve())
    query_config = dict(base)
    query_config["time_range"] = event["time_range"]
    event_id = event["event_id"].replace("-", "_")
    raw = root / "data" / "raw" / "horizons_refined"
    targets = [(event["asteroid"], True), *[(body, False) for body in event["bodies"]]]
    jobs = []
    for target, small_body in targets:
        jobs.append(
            {
                "kind": "horizons_refined_asteroid" if small_body else "horizons_refined_body",
                "target_id": target["id"],
                "target_name": target["name"],
                "url": build_horizons_url(query_config, target["id"], small_body=small_body),
                "path": raw / f"{event_id}_{target['id']}.json",
            }
        )
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        records = list(pool.map(download_one, jobs))
    records.sort(key=lambda row: (row["kind"], row["target_id"]))
    manifest = {
        "schema_version": 1,
        "event_id": event["event_id"],
        "retrieved_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "base_config": args.base_config.resolve().as_posix(),
        "event_config": args.event_config.resolve().as_posix(),
        "files": records,
    }
    manifest_path = root / "data" / "checksums" / f"{event_id}_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    for record in records:
        print(f"OK {record['target_name']} {record['bytes']} bytes")
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

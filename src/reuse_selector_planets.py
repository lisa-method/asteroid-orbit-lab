"""Seed the new manifest with byte-identical frozen public planet inputs.

Only exogenous files with exactly matching original request URLs are copied.
The selector still loads the original frozen development30 planetary inputs.
This avoids repeating nine already available public API downloads while
keeping the unchanged downloader's complete namespace and validation.
"""
import datetime as dt
import json
from pathlib import Path

from download_jpl_pilot import write_immutable
from prepare_development_sample import atomic_json
from prepare_selector_holdout24 import freeze, build_selector_jobs, MANIFEST_PATH, SAMPLE_PATH, sha


def run(root):
    sample = freeze(root)
    jobs = [j for j in build_selector_jobs(root, sample) if j["kind"] == "horizons_planet"]
    manifest_path = root / MANIFEST_PATH
    source_path = root / "data/checksums/development30_data_manifest.json"
    source = json.loads(source_path.read_text())
    by_id = {str(r["target_id"]): r for r in source["files"] if r["kind"] == "horizons_planet"}
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    known = {} if previous is None else {r["path"]: r for r in previous["files"]}
    if previous is not None and (previous["sample_sha256"] != sha(root / SAMPLE_PATH)
                                or previous["method_freeze_sha256"] != sample["method_freeze_sha256"]):
        raise ValueError("Existing manifest belongs to a different sample/method")
    if len(jobs) != 9:
        raise ValueError("Expected the unchanged nine major ephemeris requests")
    copied = []
    for job in jobs:
        old = by_id[str(job["target_id"])]
        raw = root / old["path"]
        if old["source_url"] != job["url"] or sha(raw) != old["sha256"] or raw.stat().st_size != old["bytes"]:
            raise ValueError("Original exogenous provenance or request differs")
        relative = job["path"].relative_to(root).as_posix()
        if relative in known:
            if known[relative]["sha256"] != old["sha256"] or sha(job["path"]) != old["sha256"]:
                raise ValueError("Previously copied ephemeris changed")
            copied.append(known[relative])
            continue
        record = {**old, "path": relative, "reused_from": old["path"],
                  "source_manifest_sha256": sha(source_path),
                  "original_manifest_created_utc": source.get("created_utc"),
                  "reuse_script_sha256": sha(Path(__file__))}
        record.pop("reused", None)
        write_immutable(job["path"], raw.read_bytes())
        known[relative] = record
        copied.append(record)
        atomic_json(manifest_path, {"schema_version": 1,
            "created_utc": previous["created_utc"] if previous else dt.datetime.now(dt.timezone.utc).isoformat(),
            "sample_sha256": sha(root / SAMPLE_PATH), "method_freeze_sha256": sample["method_freeze_sha256"],
            "complete": False, "files": list(known.values())})
    return {"reused_planet_files": len(copied), "new_network_requests": 0}


if __name__ == "__main__":
    print(json.dumps(run(Path(".").resolve())))

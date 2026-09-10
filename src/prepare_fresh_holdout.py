"""Freeze a small fresh object holdout, then fetch anonymous references serially."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import urllib.parse

from download_development_data import build_jobs
from download_jpl_pilot import download_one, fetch, write_immutable
from ng_inputs_v2 import load_ng_input
from prepare_development_sample import atomic_json, catalogue_queries, rows
from run_eda import parse_horizons

CONTRACT = "docs/FRESH_HOLDOUT12_CONTRACT.md"
DIRECTORY = "data/processed/fresh_holdout12"
MANIFEST = "data/checksums/fresh_holdout12_manifest.json"
SEED = "fresh-holdout12-2026-09-08-v1"
JUPITER = "data/raw/fresh_holdout12/catalogues/cad_jupiter_2026_2029_10au.json"
AMENDMENT = "docs/FRESH_HOLDOUT12_SAMPLING_AMENDMENT.md"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def immutable_json(path, value):
    return write_immutable(path, (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode())


def check_hashes(root, hashes):
    for relative, expected in hashes.items():
        if sha(root / relative) != expected:
            raise ValueError(f"Frozen input changed: {relative}")


def select_objects(documents, excluded):
    """Deterministic metadata-only sampling; no forecast/reference errors input."""
    seen, objects = set(map(str, excluded)), []
    bodies = {"Earth": "399", "Venus": "299", "Mars": "4", "Jupiter": "5"}
    for stratum in (*bodies, "inner_controls", "outer_controls"):
        group = []
        catalogue = ("cad_jupiter_2026_2029_05au" if stratum == "Jupiter" else "cad_2026_2029_02au") if stratum in bodies else stratum
        candidates = rows(documents[catalogue])
        if stratum in bodies:
            candidates = sorted(candidates, key=lambda r: (float(r["dist"]), float(r["jd"]), int(r["des"])))
        else:
            candidates = sorted(candidates, key=lambda r: hashlib.sha256(f"{SEED}:selection:{stratum}:{r['pdes']}".encode()).hexdigest())
        for row in candidates:
            object_id = str(row["des"] if stratum in bodies else row["pdes"])
            if object_id in seen:
                continue
            if stratum in bodies:
                if row.get("body", stratum) != stratum:
                    continue
                event_day = dt.datetime.strptime(row["cd"].split()[0], "%Y-%b-%d").date()
                start = (event_day - dt.timedelta(days=30)).isoformat()
                if not "2027-01-01" <= start <= "2029-12-31":
                    continue
                event = {"body_id": bodies[stratum], "body_name": stratum, "jd": float(row["jd"]),
                         "cd": row["cd"], "dist_au": float(row["dist"]), "catalogue_row": row}
            else:
                start, event = "2029-01-01", None
            group.append({"id": object_id, "name": row.get("full_name", object_id).strip(),
                          "stratum": stratum, "split": "fresh_holdout", "start_date": start,
                          "event": event, "source_catalogue": catalogue, "catalogue_row": row})
            seen.add(object_id)
            if len(group) == 2:
                break
        if len(group) != 2:
            raise ValueError(f"Fewer than two unused objects in {stratum}")
        objects.extend(group)
    return objects


def freeze(root):
    directory = root / DIRECTORY
    freeze_path = directory / "method_freeze_v2.json"
    paths = ["configs/force_models_v2.json", "outputs/force_models_v2/rules.json",
             "configs/eda_pilot_6.json", "configs/b3plus_pilot_6.json",
             "configs/development30.json", "data/processed/development30/sample.json",
             CONTRACT, AMENDMENT, "src/prepare_fresh_holdout.py"]
    rules = json.loads((root / paths[1]).read_text())
    paths += list(rules["provenance"]["force_source_hashes"])
    paths += ["src/selection_v2.py", "src/download_development_data.py", "src/download_jpl_pilot.py",
              "src/prepare_development_sample.py"]
    catalogues = {name: f"data/raw/development30/catalogues/{name}.json" for name, _, _ in catalogue_queries()}
    catalogues["cad_jupiter_2026_2029_05au"] = JUPITER
    paths += list(catalogues.values())
    frozen = {path: sha(root / path) for path in sorted(set(paths))}
    check_hashes(root, rules["provenance"]["force_source_hashes"])
    if freeze_path.exists():
        method = json.loads(freeze_path.read_text())
        if method["hashes"] != frozen:
            raise ValueError("Method/sample code changed after freeze")
    else:
        method = {"created_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "hashes": frozen,
                  "scope": "fresh_holdout12; no new calibration"}
        immutable_json(freeze_path, method)
    # Metadata selection happens strictly after the frozen method is on disk.
    dev = json.loads((root / "data/processed/development30/sample.json").read_text())
    old = json.loads((root / "configs/development30.json").read_text())
    excluded = set(old["excluded_object_ids"]) | {str(o["id"]) for o in dev["objects"]} | {"1620"}
    documents = {name: json.loads((root / path).read_text()) for name, path in catalogues.items()}
    selected = select_objects(documents, excluded)
    sample = {"schema_version": 1, "seed": SEED, "scope": "fresh whole-object holdout of frozen v2",
              "objects": selected, "excluded_object_ids": sorted(excluded, key=int),
              "method_freeze_sha256": sha(freeze_path),
              "coordinates": json.loads((root / "configs/eda_pilot_6.json").read_text())["coordinates"],
              "time_scale": "TDB"}
    immutable_json(directory / "sample.json", sample)
    return sample


def additional_catalogue(root):
    path = root / JUPITER
    manifest_path = root / "data/checksums/fresh_holdout12_catalogue_manifest.json"
    params = {"body": "Juptr", "date-min": "2026-01-01", "date-max": "2029-12-31",
              "dist-max": "1.0", "neo": "false", "kind": "an", "sort": "date"}
    url = "https://ssd-api.jpl.nasa.gov/cad.api?" + urllib.parse.urlencode(params)
    if manifest_path.exists():
        saved = json.loads(manifest_path.read_text())
        record = saved["files"][0]
        if record["sha256"] != sha(path) or record["source_url"] != url:
            raise ValueError("Extra catalogue provenance mismatch")
        return saved
    if path.exists():
        raise ValueError("Orphan catalogue without provenance")
    payload = fetch(url)
    doc = json.loads(payload)
    if doc.get("signature", {}).get("version") != "1.5" or "data" not in doc:
        raise ValueError("Review CAD schema or empty result")
    digest = write_immutable(path, payload)
    result = {"created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "amendment_sha256": sha(root / AMENDMENT), "files": [{"path": JUPITER,
              "source_url": url, "sha256": digest, "bytes": len(payload), "signature": doc["signature"],
              "rows": len(doc["data"])}]}
    immutable_json(manifest_path, result)
    return result


def download(root):
    sample = freeze(root)
    config = json.loads((root / "configs/development30.json").read_text())
    config["raw_directory"] = "data/raw/fresh_holdout12"
    jobs = [job for job in build_jobs(root, config, sample) if job["kind"] == "horizons_asteroid"]
    manifest_path = root / MANIFEST
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"files": []}
    known = {record["path"]: record for record in previous["files"]}
    created = previous.get("created_utc", dt.datetime.now(dt.timezone.utc).isoformat())
    records = []
    for index, job in enumerate(jobs):
        relative = job["path"].relative_to(root).as_posix()
        if relative in known:
            record = known[relative]
            if record["sha256"] != sha(job["path"]) or record["bytes"] != job["path"].stat().st_size or record["source_url"] != job["url"]:
                raise ValueError(f"Raw provenance mismatch: {relative}")
        else:
            if job["path"].exists():
                raise ValueError(f"Orphan raw without retrieval provenance: {relative}")
            record = download_one(job)
            record.update(path=relative, retrieved_at_utc=dt.datetime.now(dt.timezone.utc).isoformat())
            record.pop("reused", None)
            doc = json.loads(job["path"].read_text())
            if doc["signature"]["version"] != "1.2":
                raise ValueError("Review changed Horizons API version")
            metadata, parsed = parse_horizons(job["path"], job["target_id"], job["target_name"])
            if not parsed:
                raise ValueError("Empty reference")
            record.update(signature=doc["signature"], rows=len(parsed), coordinates=sample["coordinates"],
                          time_scale="TDB", first_jd_tdb=parsed[0]["epoch_jd_tdb"], last_jd_tdb=parsed[-1]["epoch_jd_tdb"])
        records.append(record)
        atomic_json(manifest_path, {"schema_version": 1, "created_utc": created,
                    "sample_sha256": sha(root / DIRECTORY / "sample.json"), "complete": len(records) == len(jobs), "files": records})
        print(json.dumps({"downloaded": index+1, "total": len(jobs), "path": relative, "rows": record["rows"]}), flush=True)
    # Coefficients must match between independently downloaded daily/refined tables.
    for obj in sample["objects"]:
        base = root / "data/raw/fresh_holdout12/asteroids"
        daily = load_ng_input(base / f"asteroid_{obj['id']}_daily.json")
        if obj["event"]:
            refined = load_ng_input(base / f"asteroid_{obj['id']}_refined.json")
            if (daily is None) != (refined is None) or (daily is not None and daily.parameters != refined.parameters):
                raise ValueError(f"Daily/refined NG mismatch: {obj['id']}")
    return {"files": len(records), "complete": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--catalogue", action="store_true")
    args = parser.parse_args()
    action = additional_catalogue if args.catalogue else download if args.download else freeze
    print(json.dumps(action(args.root.resolve()), indent=2))

"""Cache public JPL sampling metadata and freeze an object-disjoint cohort.

No trajectory errors enter selection. JPL requests are strictly serial and raw
responses immutable; reruns reuse cached responses. Python standard library.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import urllib.parse

from download_jpl_pilot import fetch, write_immutable


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def cached_query(root: Path, name: str, url: str, version: str) -> tuple[dict, dict]:
    path = root / "data/raw/development30/catalogues" / (name + ".json")
    payload = path.read_bytes() if path.exists() else fetch(url)
    document = json.loads(payload)
    if document.get("signature", {}).get("version") != version:
        raise ValueError(f"Review API version/schema: {document.get('signature')}")
    if "error" in document or "fields" not in document or "data" not in document:
        raise ValueError(f"Missing catalogue data: {document}")
    digest = write_immutable(path, payload)
    return document, {"path": path.relative_to(root).as_posix(), "source_url": url,
                      "sha256": digest, "bytes": len(payload), "signature": document["signature"]}


def catalogue_queries() -> list[tuple[str, str, str]]:
    cad = {"body": "ALL", "date-min": "2026-01-01", "date-max": "2029-12-31",
           "dist-max": "0.2", "neo": "false", "kind": "an", "sort": "date"}
    queries = [("cad_2026_2029_02au", "https://ssd-api.jpl.nasa.gov/cad.api?" + urllib.parse.urlencode(cad), "1.5")]
    jupiter = {**cad, "body": "Juptr", "dist-max": "0.5"}
    queries.append(("cad_jupiter_2026_2029_05au", "https://ssd-api.jpl.nasa.gov/cad.api?" + urllib.parse.urlencode(jupiter), "1.5"))
    for name, constraints in (("inner_controls", ["a|RG|2.1|2.5", "q|GT|1.8", "e|LT|0.2"]),
                              ("outer_controls", ["a|RG|3.0|3.4", "q|GT|2.5", "e|LT|0.2"])):
        params = {"fields": "pdes,full_name,a,e,q,i,epoch,orbit_id", "sb-ns": "n", "sb-kind": "a",
                  "sort": "spkid", "limit": "1000", "full-prec": "true",
                  "sb-cdata": json.dumps({"AND": constraints}, separators=(",", ":"))}
        queries.append((name, "https://ssd-api.jpl.nasa.gov/sbdb_query.api?" + urllib.parse.urlencode(params), "1.0"))
    return queries


def rows(document: dict) -> list[dict]:
    return [dict(zip(document["fields"], row, strict=True)) for row in document["data"]]


def freeze_sample(root: Path, config: dict) -> dict:
    catalogue_dir = root / "data/raw/development30/catalogues"
    documents = {name: json.loads((catalogue_dir / (name + ".json")).read_text()) for name, _, _ in catalogue_queries()}
    bodies = {"Earth": "399", "Venus": "299", "Mars": "4", "Jupiter": "5"}
    seen = set(config["excluded_object_ids"])
    objects = []
    def score(purpose: str, stratum: str, object_id: str) -> str:
        return hashlib.sha256(f"{config['seed']}:{purpose}:{stratum}:{object_id}".encode()).hexdigest()
    for stratum in config["sampling_order"]:
        group = []
        if stratum in bodies:
            catalogue = "cad_jupiter_2026_2029_05au" if stratum == "Jupiter" else "cad_2026_2029_02au"
            candidates = sorted(rows(documents[catalogue]), key=lambda r: (float(r["dist"]), float(r["jd"]), int(r["des"])))
            for candidate in candidates:
                object_id = candidate["des"]
                if candidate.get("body", stratum) != stratum or object_id in seen:
                    continue
                event_day = dt.datetime.strptime(candidate["cd"].split()[0], "%Y-%b-%d").date()
                event = {"body_id": bodies[stratum], "body_name": stratum,
                         "jd": float(candidate["jd"]), "cd": candidate["cd"],
                         "dist_au": float(candidate["dist"]), "catalogue_row": candidate}
                group.append({"id": object_id, "name": object_id, "stratum": stratum,
                              "start_date": (event_day - dt.timedelta(days=config["encounter_lead_days"])).isoformat(),
                              "event": event, "source_catalogue": catalogue})
                seen.add(object_id)
                if len(group) == config["objects_per_stratum"]:
                    break
        else:
            candidates = sorted(rows(documents[stratum]), key=lambda r: score("selection", stratum, r["pdes"]))
            for candidate in candidates:
                object_id = candidate["pdes"]
                if object_id in seen:
                    continue
                group.append({"id": object_id, "name": candidate["full_name"].strip(), "stratum": stratum,
                              "start_date": config["control_start_date"], "event": None,
                              "source_catalogue": stratum, "catalogue_row": candidate})
                seen.add(object_id)
                if len(group) == config["objects_per_stratum"]:
                    break
        if len(group) != config["objects_per_stratum"]:
            raise ValueError(f"Insufficient unique objects for {stratum}")
        for index, obj in enumerate(sorted(group, key=lambda r: score("split", stratum, r["id"]))):
            obj["split"] = "train" if index < config["train_per_stratum"] else "validation"
            obj["split_hash"] = score("split", stratum, obj["id"])
            objects.append(obj)
    sample = {"schema_version": 1, "seed": config["seed"], "scope": "development; no final test",
              "coordinates": json.loads((root / config["data_config"]).read_text())["coordinates"],
              "time_scale": "TDB", "objects": objects,
              "catalogue_sha256": {name: hashlib.sha256((catalogue_dir / (name + ".json")).read_bytes()).hexdigest() for name in documents},
              "config_sha256": hashlib.sha256((root / "configs/development30.json").read_bytes()).hexdigest(),
              "contract_sha256": hashlib.sha256((root / "docs/DEVELOPMENT30_CONTRACT.md").read_bytes()).hexdigest()}
    payload = (json.dumps(sample, indent=2, allow_nan=False) + "\n").encode()
    write_immutable(root / config["sample_path"], payload)
    return sample


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--freeze", action="store_true", help="Freeze selection from already cached catalogues; no network")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.freeze:
        config = json.loads((root / "configs/development30.json").read_text())
        sample = freeze_sample(root, config)
        print(json.dumps({"objects": sample["objects"], "frozen": config["sample_path"]}, indent=2))
        return
    records = []
    manifest_path = root / "data/checksums/development30_catalogue_manifest.json"
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    created = previous.get("created_utc", dt.datetime.now(dt.timezone.utc).isoformat())
    for name, url, version in catalogue_queries():
        document, record = cached_query(root, name, url, version)
        records.append(record)
        data = rows(document)
        counts = {}
        for row in data:
            body = row.get("body", "control")
            counts[body] = counts.get(body, 0) + 1
        print(json.dumps({"catalogue": name, "returned": len(data), "total_count": document.get("count"), "bodies": counts}), flush=True)
        atomic_json(manifest_path, {"schema_version": 1, "created_utc": created, "files": records})


if __name__ == "__main__":
    main()

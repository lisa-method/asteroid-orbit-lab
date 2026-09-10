"""Fetch four immutable anonymous Horizons inputs and check overlap compatibility."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path

from download_jpl_pilot import build_horizons_url, download_one
from ng_inputs_v2 import load_ng_input
from orbit_baselines import norm, subtract
from prepare_development_sample import atomic_json
from prepare_fresh_holdout import sha, immutable_json, check_hashes
from run_eda import parse_horizons

CONFIG = "configs/apophis_moon_venus.json"
VALIDATION = "data/processed/apophis_moon_venus/input_validation.json"


def filename(window):
    return ("asteroid_" if window["small_body"] else "body_") + window["id"] + ".json"


def validate_table(header, rows, window):
    if not (header["center"].startswith("Sun (10)") and header["units"] == "AU-D"
            and header["reference_frame"] == "ICRF" and header["geometric"] and header["tdb"]
            and header["api_version"] == "1.2"):
        raise ValueError(f"Incompatible Horizons coordinate/header: {header}")
    if len(rows) != window["rows"]:
        raise ValueError(f"Unexpected row count for {window['id']}: {len(rows)}")
    for key, row in (("start", rows[0]), ("stop", rows[-1])):
        if row["epoch_tdb"] != dt.datetime.fromisoformat(window[key]).isoformat():
            raise ValueError(f"Unexpected {key} for {window['id']}")
    expected_step = int(window["step"].split()[0]) / 1440.0
    epochs = [r["epoch_jd_tdb"] for r in rows]
    if any(abs(b-a-expected_step) > 1e-9 for a,b in zip(epochs,epochs[1:])):
        raise ValueError("Irregular, missing, or duplicated epochs")
    if any(not math.isfinite(v) for r in rows for v in (r["epoch_jd_tdb"], *r["r"], *r["v"])):
        raise ValueError("Nonfinite reference state")


def overlap(left, right, au, day):
    mapping = {r["epoch_jd_tdb"]: r for r in left}
    pairs = [(mapping[r["epoch_jd_tdb"]],r) for r in right if r["epoch_jd_tdb"] in mapping]
    if not pairs:
        raise ValueError("No common epochs for provenance overlap check")
    return {"samples":len(pairs),
            "max_position_difference_km":max(norm(subtract(a["r"],b["r"])) * au for a,b in pairs),
            "max_velocity_difference_m_s":max(norm(subtract(a["v"],b["v"])) * au*1000/day for a,b in pairs)}


def prepare(root, download=False):
    config = json.loads((root / CONFIG).read_text())
    baseline = json.loads((root / config["baseline_config"]).read_text())
    data = json.loads((root / baseline["data_config"]).read_text())
    directory = root / "data/processed/apophis_moon_venus"
    frozen_path = directory / "download_design_freeze.json"
    paths = [CONFIG, config["contract"], config["baseline_config"], baseline["data_config"],
             "src/prepare_apophis_moon_venus.py", "src/download_jpl_pilot.py", "src/run_eda.py",
             "src/ng_inputs_v2.py", "src/prepare_fresh_holdout.py", "src/prepare_development_sample.py",
             "src/orbit_baselines.py"]
    hashes = {p:sha(root/p) for p in paths}
    if frozen_path.exists():
        frozen = json.loads(frozen_path.read_text())
        check_hashes(root, frozen["hashes"])
        if frozen["hashes"] != hashes:
            raise ValueError("Download design changed")
    else:
        immutable_json(frozen_path, {"created_utc":dt.datetime.now(dt.timezone.utc).isoformat(),"hashes":hashes})
    manifest_path = root / config["manifest"]
    saved = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"files":[]}
    records = {r["path"]:r for r in saved["files"]}
    created = saved.get("created_utc",dt.datetime.now(dt.timezone.utc).isoformat())
    for window in config["download_windows"]:
        relative = config["raw_directory"] + "/" + filename(window)
        path = root / relative
        request_config = {**data,"time_range":{**window,"time_scale":"TDB"}}
        url = build_horizons_url(request_config,window["id"],small_body=window["small_body"])
        if relative in records:
            record = records[relative]
            if record["sha256"] != sha(path) or record["bytes"] != path.stat().st_size or record["source_url"] != url:
                raise ValueError(f"Raw provenance mismatch: {relative}")
        else:
            if path.exists():
                raise ValueError(f"Orphan raw without provenance: {relative}")
            if not download:
                raise ValueError("Missing raw inputs; use --download for explicit anonymous retrieval")
            record = download_one({"kind":"horizons_asteroid" if window["small_body"] else "horizons_body",
                "target_id":window["id"],"target_name":window["name"],"url":url,"path":path})
            record.pop("reused",None)
            record.update(path=relative,retrieved_at_utc=dt.datetime.now(dt.timezone.utc).isoformat())
            records[relative] = record
            # Preserve a successful retrieval even if subsequent schema validation fails.
            atomic_json(manifest_path,{"schema_version":1,"created_utc":created,"complete":False,
                "design_sha256":sha(frozen_path),"files":list(records.values())})
        header, rows = parse_horizons(path,window["id"],window["name"])
        validate_table(header,rows,window)
        record.update(rows=len(rows), signature=json.loads(path.read_text())["signature"],
            coordinates=data["coordinates"],time_scale="TDB",header=header,
            first_jd_tdb=rows[0]["epoch_jd_tdb"],last_jd_tdb=rows[-1]["epoch_jd_tdb"])
        if not saved.get("complete"):
            atomic_json(manifest_path,{"schema_version":1,"created_utc":created,"complete":False,
                "design_sha256":sha(frozen_path),"files":list(records.values())})
        print(json.dumps({"input":relative,"samples":len(rows)}),flush=True)
    comparisons = []
    au,day=data["constants"]["au_km"],data["constants"]["day_s"]
    consumed = {p:r["sha256"] for p,r in records.items()}
    for window in config["download_windows"]:
        new_path = root / config["raw_directory"] / filename(window)
        _, new_rows = parse_horizons(new_path,window["id"],window["name"])
        old_paths = ["data/raw/horizons/"+filename(window)]
        if window["id"] in {"399","301","99942"}:
            old_paths.append(f"data/raw/horizons_refined/apophis_earth_2029_{window['id']}.json")
        else:
            old_paths.append("data/raw/development30/planets/body_299.json")
        for relative in old_paths:
            path=root/relative
            consumed[relative]=sha(path)
            _,old_rows=parse_horizons(path,window["id"],window["name"])
            comparison={"body_id":window["id"],"old_path":relative,**overlap(old_rows,new_rows,au,day)}
            if (comparison["max_position_difference_km"] > config["raw_overlap_position_tolerance_km"]
                    or comparison["max_velocity_difference_m_s"] > config["raw_overlap_velocity_tolerance_m_s"]):
                raise ValueError(f"Changed source solution; do not splice: {comparison}")
            comparisons.append(comparison)
        if window["small_body"]:
            old_ng=load_ng_input(root/old_paths[0])
            new_ng=load_ng_input(new_path)
            if old_ng is None or new_ng is None or old_ng.parameters != new_ng.parameters:
                raise ValueError("Apophis NG header changed")
    manifest={"schema_version":1,"created_utc":created,"complete":True,
              "design_sha256":sha(frozen_path),"files":list(records.values())}
    if saved.get("complete"):
        if manifest != saved:
            raise ValueError("Completed manifest would change")
    else:
        atomic_json(manifest_path,manifest)
    result={"schema_version":1,"complete":True,"comparisons":comparisons,"raw_hashes":consumed,
            "manifest_sha256":sha(manifest_path),"design_sha256":sha(frozen_path),"ng_headers_equal":True}
    immutable_json(root/VALIDATION,result)
    return result


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=Path("."))
    parser.add_argument("--download",action="store_true")
    args=parser.parse_args()
    print(json.dumps(prepare(args.root.resolve(),args.download),indent=2))

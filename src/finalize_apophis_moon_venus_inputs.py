"""Complete input checks with separately labelled daily/refined target teachers."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from prepare_apophis_moon_venus import CONFIG, VALIDATION, filename, overlap, validate_table
from prepare_development_sample import atomic_json
from prepare_fresh_holdout import immutable_json, sha, check_hashes
from ng_inputs_v2 import load_ng_input
from run_eda import parse_horizons

AMENDMENT = "docs/APOPHIS_MOON_VENUS_INPUT_AMENDMENT.md"


def orbit_identity(path):
    header=json.loads(path.read_text())["result"].split("$$SOE")[0]
    # Include the complete osculating/NG block as well as the solution identity.
    block=header.split("Rec #:",1)[1].split("Ephemeris / API_USER",1)[0]
    return re.sub(r"\s+"," ",block).strip(), parse_horizons(path,"99942","Apophis")[0]["target"]


def finalize(root):
    config=json.loads((root/CONFIG).read_text())
    data=json.loads((root/"configs/eda_pilot_6.json").read_text())
    design_path=root/"data/processed/apophis_moon_venus/download_design_freeze.json"
    check_hashes(root,json.loads(design_path.read_text())["hashes"])
    manifest_path=root/config["manifest"]
    manifest=json.loads(manifest_path.read_text())
    if manifest["design_sha256"] != sha(design_path):
        raise ValueError("Design fingerprint changed")
    known={r["path"]:r for r in manifest["files"]}
    if len(known)!=4:
        raise ValueError("Incomplete/duplicate input inventory")
    consumed={p:r["sha256"] for p,r in known.items()}
    check_hashes(root,consumed)
    comparisons=[]
    for window in config["download_windows"]:
        relative=config["raw_directory"]+"/"+filename(window)
        new=root/relative
        h,nr=parse_horizons(new,window["id"],window["name"])
        validate_table(h,nr,window)
        if known[relative]["bytes"]!=new.stat().st_size:
            raise ValueError("Raw size mismatch")
        old_paths=["data/raw/horizons/"+filename(window)]
        old_paths.append(f"data/raw/horizons_refined/apophis_earth_2029_{window['id']}.json"
            if window["id"]!='299' else "data/raw/development30/planets/body_299.json")
        for path in old_paths:
            consumed[path]=sha(root/path)
            old_rows=parse_horizons(root/path,window["id"],window["name"])[1]
            diagnostic_only=window["id"]=="99942" and path.startswith("data/raw/horizons/")
            result={"body_id":window["id"],"old_path":path,
                "daily_target_separate_evaluator":diagnostic_only,
                **overlap(old_rows,nr,data["constants"]["au_km"],data["constants"]["day_s"])}
            if not diagnostic_only and (result["max_position_difference_km"] > config["raw_overlap_position_tolerance_km"]
                or result["max_velocity_difference_m_s"] > config["raw_overlap_velocity_tolerance_m_s"]):
                raise ValueError(f"Incompatible force/refined input: {result}")
            if window["small_body"]:
                if orbit_identity(root/path)!=orbit_identity(new):
                    raise ValueError("Apophis source orbit identity changed")
                if load_ng_input(root/path).parameters!=load_ng_input(new).parameters:
                    raise ValueError("Apophis NG changed")
            comparisons.append(result)
    amendment_hashes={p:sha(root/p) for p in [AMENDMENT,"src/finalize_apophis_moon_venus_inputs.py"]}
    amended={**manifest,"complete":True,"amendment_hashes":amendment_hashes}
    if manifest.get("complete"):
        if amended!=manifest:
            raise ValueError("Completed manifest changed")
    else:
        atomic_json(manifest_path,amended)
    result={"schema_version":1,"complete":True,"comparisons":comparisons,"raw_hashes":consumed,
        "manifest_sha256":sha(manifest_path),"design_sha256":sha(design_path),
        "amendment_hashes":amendment_hashes,"ng_headers_equal":True,"orbit_headers_equal":True,
        "primary_teacher":"unchanged old 797 rows; do not splice new target",
        "secondary_teacher":"new 2305 refined rows; daily/refined disagreement explicitly reported"}
    immutable_json(root/VALIDATION,result)
    return result


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root",type=Path,default=Path("."))
    print(json.dumps(finalize(p.parse_args().root.resolve()),indent=2))

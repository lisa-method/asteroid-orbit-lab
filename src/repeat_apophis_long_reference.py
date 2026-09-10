"""Repeat exactly one stored old JPL URL without overwriting its raw response."""
import argparse
import datetime as dt
import json
from pathlib import Path
from download_jpl_pilot import download_one
from finalize_apophis_moon_venus_inputs import orbit_identity
from prepare_apophis_moon_venus import overlap
from prepare_fresh_holdout import sha,immutable_json,check_hashes
from prepare_development_sample import atomic_json
from run_eda import parse_horizons


def run(root,download=False):
    old_manifest=root/"data/checksums/jpl_pilot_6_manifest.json"
    old_rel="data/raw/horizons/asteroid_99942.json"
    record=next(r for r in json.loads(old_manifest.read_text())["files"] if r["path"].endswith("/horizons/asteroid_99942.json"))
    if sha(root/old_rel)!=record["sha256"]:raise ValueError("Old raw changed")
    output_rel="data/raw/apophis_reference_time/asteroid_99942_long_repeat.json"
    path=root/output_rel
    manifest_path=root/"data/checksums/apophis_reference_time_repeat_manifest.json"
    amendment="docs/APOPHIS_REFERENCE_TIME_REPEAT_AMENDMENT.md"
    hashes={p:sha(root/p) for p in [amendment,"src/repeat_apophis_long_reference.py",old_manifest.relative_to(root).as_posix()]}
    if manifest_path.exists():
        manifest=json.loads(manifest_path.read_text());check_hashes(root,manifest["hashes"])
        rec=manifest["files"][0]
        if sha(path)!=rec["sha256"] or path.stat().st_size!=rec["bytes"] or rec["source_url"]!=record["source_url"]:
            raise ValueError("Repeat raw provenance changed")
    else:
        if path.exists():raise ValueError("Orphan repeat raw")
        if not download:raise ValueError("Explicit --download needed")
        rec=download_one({"kind":"horizons_asteroid","target_id":"99942","target_name":"Apophis","path":path,"url":record["source_url"]})
        rec.pop("reused",None)
        rec.update(path=output_rel,retrieved_at_utc=dt.datetime.now(dt.timezone.utc).isoformat())
        manifest={"hashes":hashes,"files":[rec],"complete":False}
        atomic_json(manifest_path,manifest)
    if orbit_identity(root/old_rel)!=orbit_identity(path):raise ValueError("Changed orbit header")
    h,new=parse_horizons(path,"99942","Apophis")
    old=parse_horizons(root/old_rel,"99942","Apophis")[1]
    if len(old)!=len(new) or any(a["epoch_tdb"]!=b["epoch_tdb"] for a,b in zip(old,new)):
        raise ValueError("Repeat grid changed")
    data=json.loads((root/"configs/eda_pilot_6.json").read_text())
    rec.update(rows=len(new),header=h,signature=json.loads(path.read_text())["signature"],coordinates=data["coordinates"],time_scale="TDB")
    out={"old_vs_exact_repeat":overlap(old,new,data["constants"]["au_km"],data["constants"]["day_s"]),
         "same_source_url":True,"same_orbit_header":True,"old_sha256":sha(root/old_rel),"new_sha256":sha(path)}
    if not manifest.get("complete"):
        manifest.update(complete=True,comparison=out);atomic_json(manifest_path,manifest)
    elif manifest["comparison"]!=out:raise ValueError("Repeat comparison changed")
    immutable_json(root/"data/processed/apophis_reference_time/repeat_validation.json",out)
    return out


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--root",type=Path,default=Path("."));p.add_argument("--download",action="store_true")
    a=p.parse_args();print(json.dumps(run(a.root.resolve(),a.download),indent=2))

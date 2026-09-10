"""Four controlled anonymous target queries for cadence/bounds consistency."""
import argparse
import datetime as dt
import json
import math
from pathlib import Path

from download_jpl_pilot import build_horizons_url, download_one
from finalize_apophis_moon_venus_inputs import orbit_identity
from ng_inputs_v2 import load_ng_input
from prepare_development_sample import atomic_json
from prepare_fresh_holdout import sha, immutable_json, check_hashes
from prepare_apophis_moon_venus import overlap
from run_eda import parse_horizons

CONFIG="configs/apophis_reference_time.json"


def prepare(root,download=False):
    config=json.loads((root/CONFIG).read_text())
    data=json.loads((root/"configs/eda_pilot_6.json").read_text())
    design=root/"data/processed/apophis_reference_time/download_freeze.json"
    paths=[CONFIG,config["contract"],"src/prepare_apophis_reference_time.py",
        "src/download_jpl_pilot.py","src/finalize_apophis_moon_venus_inputs.py",
        "src/prepare_apophis_moon_venus.py","src/prepare_fresh_holdout.py",
        "src/prepare_development_sample.py","src/ng_inputs_v2.py","src/run_eda.py",
        "configs/eda_pilot_6.json"]
    hashes={p:sha(root/p) for p in paths}
    if design.exists():
        old=json.loads(design.read_text());check_hashes(root,old["hashes"])
        if hashes!=old["hashes"]:raise ValueError("Changed retrieval design")
    else:immutable_json(design,{"created_utc":dt.datetime.now(dt.timezone.utc).isoformat(),"hashes":hashes})
    manifest_path=root/config["manifest"]
    previous=json.loads(manifest_path.read_text()) if manifest_path.exists() else {"files":[]}
    records={r["name"]:r for r in previous["files"]}
    created=previous.get("created_utc",dt.datetime.now(dt.timezone.utc).isoformat())
    for q in config["queries"]:
        path=root/config["raw_directory"]/f"asteroid_99942_{q['name']}.json"
        url=build_horizons_url({**data,"time_range":{**q,"time_scale":"TDB"}},"99942",small_body=True)
        if q["name"] in records:
            rec=records[q["name"]]
            if sha(path)!=rec["sha256"] or path.stat().st_size!=rec["bytes"] or rec["source_url"]!=url:
                raise ValueError("Raw retrieval provenance mismatch")
        else:
            if path.exists():raise ValueError("Orphan raw without manifest")
            if not download:raise ValueError("Missing input; explicit --download required")
            rec=download_one({"kind":"horizons_asteroid","target_id":"99942","target_name":"Apophis","path":path,"url":url})
            rec.pop("reused",None)
            rec.update(name=q["name"],path=path.relative_to(root).as_posix(),retrieved_at_utc=dt.datetime.now(dt.timezone.utc).isoformat())
            records[q["name"]]=rec
            atomic_json(manifest_path,{"created_utc":created,"complete":False,"design_sha256":sha(design),"files":list(records.values())})
        header,rows=parse_horizons(path,"99942","Apophis")
        if len(rows)!=q["rows"] or rows[0]["epoch_tdb"]!=dt.datetime.fromisoformat(q["start"]).isoformat() or rows[-1]["epoch_tdb"]!=dt.datetime.fromisoformat(q["stop"]).isoformat():
            raise ValueError("Target reference coverage mismatch")
        if not(header["center"].startswith("Sun (10)") and header["reference_frame"]=="ICRF" and header["units"]=="AU-D" and header["geometric"] and header["tdb"] and header["api_version"]=="1.2"):
            raise ValueError("Coordinate/API mismatch")
        step=1. if q["step"]=="1 d" else 1./24
        if any(abs(b["epoch_jd_tdb"]-a["epoch_jd_tdb"]-step)>1e-9 for a,b in zip(rows,rows[1:])):
            raise ValueError("Nonuniform reference epochs")
        if any(not math.isfinite(v) for r in rows for v in (r["epoch_jd_tdb"],*r["r"],*r["v"])):
            raise ValueError("Nonfinite state")
        rec.update(rows=len(rows),header=header,signature=json.loads(path.read_text())["signature"],coordinates=data["coordinates"],time_scale="TDB")
        if not previous.get("complete"):
            atomic_json(manifest_path,{"created_utc":created,"complete":False,"design_sha256":sha(design),"files":list(records.values())})
        print(json.dumps({"reference":q["name"],"rows":len(rows)}),flush=True)
    reference_paths={name:rec["path"] for name,rec in records.items()}
    reference_paths.update(old_daily="data/raw/horizons/asteroid_99942.json",
        old_refined="data/raw/horizons_refined/apophis_earth_2029_99942.json",
        recent_refined="data/raw/apophis_moon_venus/asteroid_99942.json")
    identity=orbit_identity(root/reference_paths["old_daily"])
    ng=load_ng_input(root/reference_paths["old_daily"])
    tables={}
    for name,rel in reference_paths.items():
        if orbit_identity(root/rel)!=identity:raise ValueError("Changed orbit identity: "+name)
        source_ng=load_ng_input(root/rel)
        if source_ng is None or source_ng.parameters!=ng.parameters:raise ValueError("Changed NG: "+name)
        tables[name]=parse_horizons(root/rel,"99942","Apophis")[1]
    comparisons=[]
    for i,left in enumerate(tables):
        for right in list(tables)[i+1:]:
            a,b=tables[left],tables[right]
            # Every query intersects the old Apr13–14 refinement on an hour.
            comparisons.append({"left":left,"right":right,**overlap(a,b,data["constants"]["au_km"],data["constants"]["day_s"])})
    completed={"created_utc":created,"complete":True,"design_sha256":sha(design),"files":list(records.values())}
    if previous.get("complete"):
        if completed!=previous:raise ValueError("Completed manifest changed")
    else:atomic_json(manifest_path,completed)
    validation={"complete":True,"orbit_headers_equal":True,"ng_headers_equal":True,
        "paths":reference_paths,"raw_hashes":{p:sha(root/p) for p in reference_paths.values()},
        "comparisons":comparisons,"manifest_sha256":sha(manifest_path),"design_sha256":sha(design)}
    immutable_json(root/"data/processed/apophis_reference_time/validation.json",validation)
    return validation


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root",type=Path,default=Path("."));p.add_argument("--download",action="store_true")
    args=p.parse_args();print(json.dumps(prepare(args.root.resolve(),args.download),indent=2))

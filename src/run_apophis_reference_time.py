"""Frozen time-coordinate and initial/reference consistency ablation."""
from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import platform
import sys
import time

from ng_inputs_v2 import load_ng_input
from prepare_fresh_holdout import sha, immutable_json
from prepare_apophis_reference_time import prepare
from repeat_apophis_long_reference import run as check_repeat
from relative_time_dynamics import relative_rows, relative_perturbers, build_relative_force
from run_apophis_solver_audit import (_old_inputs,_old_planets,_target_rows,_merge_rows,
    _force,_run_dp,_run_rk4,_summary,_shift,_flat,_state)
from run_apophis_moon_venus_v1_1 import _new_rows,_make_arm_planets,_normalize_trace
from run_b3plus_ablation import load_small_body_perturbers
from run_eda import parse_horizons
from run_physics_baselines import state_from_row

CONFIG="configs/apophis_reference_time.json"
PREVIOUS="outputs/apophis_moon_venus/v1_1/apophis_moon_venus_results.json"


def source_closure(root,names):
    queue=list(names);seen=set()
    while queue:
        name=queue.pop();path=root/"src"/(name+".py")
        if path in seen or not path.exists():continue
        seen.add(path)
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node,ast.ImportFrom) and node.module:queue.append(node.module.split('.')[0])
            elif isinstance(node,ast.Import):queue.extend(a.name.split('.')[0] for a in node.names)
    return [p.relative_to(root).as_posix() for p in seen]


def context(root):
    config=json.loads((root/CONFIG).read_text())
    base=json.loads((root/config["baseline_config"]).read_text())
    force_config=json.loads((root/base["force_config"]).read_text())
    data=json.loads((root/base["data_config"]).read_text())
    event=json.loads((root/base["event_config"]).read_text())
    source=json.loads((root/config["source_config"]).read_text())
    inputs=prepare(root);repeat=check_repeat(root)
    daily,refined=_old_inputs(root,data,event)
    planets=_old_planets(root,data,daily,refined)
    constants=data["constants"];au,day=constants["au_km"],constants["day_s"]
    mu=constants["mu_sun_km3_s2"]*day**2/au**3
    planets+=load_small_body_perturbers(root,force_config,mu)
    new=_new_rows(root,source)
    arm=next(a for a in source["arms"] if a["id"]=="earthmoon_venus15m")
    planets=_make_arm_planets(arm,planets,new,daily,constants)
    entries=[]
    for p in planets:
        if p.body_id.startswith("sb:"):
            rows=parse_horizons(root/force_config["small_body_ephemeris_directory"]/f"asteroid_{p.body_id[3:]}.json",p.body_id[3:],p.name)[1]
        elif p.body_id in ("399","301"):rows=_merge_rows(daily[p.body_id],new[p.body_id])
        elif p.body_id=="299":rows=new["299"]
        else:rows=daily[p.body_id]
        if tuple(r["epoch_jd_tdb"] for r in rows)!=p.ephemeris.epochs_jd_tdb:
            raise ValueError("Relative input pairing differs from absolute baseline")
        if tuple(state_from_row(r) for r in rows)!=p.ephemeris.states:
            raise ValueError("Relative state pairing mismatch")
        entries.append((p,rows))
    annual,_=_target_rows(root,data,event,365)
    refs={name:parse_horizons(root/path,"99942","Apophis")[1] for name,path in inputs["paths"].items()}
    refs["long_repeat"]=parse_horizons(root/"data/raw/apophis_reference_time/asteroid_99942_long_repeat.json","99942","Apophis")[1]
    ng=load_ng_input(root/"data/raw/horizons/asteroid_99942.json")
    if ng is None:raise ValueError("Nominal NG missing")
    paths=[CONFIG,config["contract"],config["baseline_config"],config["source_config"],
        base["force_config"],base["data_config"],base["event_config"],
        "docs/APOPHIS_REFERENCE_TIME_REPEAT_AMENDMENT.md",
        "data/processed/apophis_reference_time/download_freeze.json",
        "data/processed/apophis_reference_time/validation.json",
        "data/processed/apophis_reference_time/repeat_validation.json",PREVIOUS]
    paths+=source_closure(root,["run_apophis_reference_time"])
    paths+=[p.relative_to(root).as_posix() for p in (root/"data/checksums").glob("*manifest.json")]
    paths+=list(inputs["raw_hashes"])
    paths+=["data/raw/apophis_reference_time/asteroid_99942_long_repeat.json"]
    paths+=[f"data/raw/horizons/asteroid_{a['id']}.json" for a in data["asteroids"]]
    paths+=[f"data/raw/horizons/body_{p['id']}.json" for p in data["perturbers"]]
    paths+=[f"data/raw/horizons_refined/apophis_earth_2029_{i}.json" for i in ("399","301","99942")]
    paths+=[source["raw_directory"]+"/"+("asteroid_" if i=="99942" else "body_")+i+".json" for i in ("399","301","299","99942")]
    paths+=[force_config["small_body_ephemeris_directory"]+f"/asteroid_{b['id']}.json" for b in force_config["small_body_perturbers"]]
    hashes={p:sha(root/p) for p in sorted(set(paths))}
    runtime={"executable":sys.executable,"version":sys.version,"platform":platform.platform()}
    freeze_payload={"hashes":hashes,"runtime":runtime}
    fingerprint=hashlib.sha256(json.dumps(freeze_payload,sort_keys=True).encode()).hexdigest()
    freeze_path=root/config["output_directory"]/"freeze.json"
    if freeze_path.exists():
        freeze=json.loads(freeze_path.read_text())
        if freeze["fingerprint"]!=fingerprint or freeze_payload!={k:freeze[k] for k in freeze_payload}:
            raise ValueError("Frozen experiment source/runtime changed")
    else:immutable_json(freeze_path,{**freeze_payload,"fingerprint":fingerprint,"created_utc":dt.datetime.now(dt.timezone.utc).isoformat()})
    return dict(config=config,base=base,force_config=force_config,data=data,planets=planets,entries=entries,
        annual=annual,refs=refs,ng=ng,mu=mu,au=au,day=day,c=force_config["speed_of_light_km_s"]*day/au,
        radius=base["earth_j2"]["reference_radius_km"]/au,j2=base["earth_j2"]["j2"],fingerprint=fingerprint,
        input_validation=inputs,repeat_validation=repeat,freeze_sha256=sha(freeze_path))


def evaluate(pred,rows,refs,au,day):
    if len(pred)!=len(rows):raise ValueError("Prediction length mismatch")
    primary=_summary(pred,[state_from_row(r) for r in rows],au,day)
    predictions={r["epoch_tdb"]:s for r,s in zip(rows,pred)}
    compared={}
    for name,reference in refs.items():
        pairs=[(predictions[r["epoch_tdb"]],state_from_row(r)) for r in reference if r["epoch_tdb"] in predictions]
        if not pairs:raise ValueError("Reference has no matching evaluation dates")
        compared[name]={"samples":len(pairs),**_summary([p[0] for p in pairs],[p[1] for p in pairs],au,day)}
    return primary,compared


def run_one(root,ctx,mode,solver,setting,initial_source,previous):
    label=setting["label"] if isinstance(setting,dict) else str(setting)
    key=f"{mode}__{solver}__{label}__{initial_source}"
    path=root/ctx["config"]["output_directory"]/"checkpoints"/(key+".json")
    if path.exists():
        record=json.loads(path.read_text())
        if record["fingerprint"]!=ctx["fingerprint"]:raise ValueError("Checkpoint fingerprint mismatch")
        return record
    rows=ctx["annual"];origin=rows[0]["epoch_jd_tdb"];calendar=rows[0]["epoch_tdb"]
    initial_row=rows[0] if initial_source=="old" else ctx["refs"]["annual_hourly"][0]
    if initial_row["epoch_tdb"]!=calendar:raise ValueError("Different initial epoch")
    initial=state_from_row(initial_row)
    if mode=="legacy":
        planets=ctx["planets"]
        force=_force(origin,planets,next(p for p in planets if p.body_id=="399"),ctx["mu"],ctx["c"],ctx["ng"].parameters,ctx["radius"],ctx["j2"])
        times=[r["epoch_jd_tdb"]-origin for r in rows]
    else:
        representation="float" if mode=="relative_float_knots" else "calendar"
        planets=relative_perturbers(ctx["entries"],origin,calendar,representation)
        force=build_relative_force(origin,planets,ctx["mu"],ctx["c"],ctx["ng"].parameters,ctx["radius"],ctx["j2"])
        times=[r["epoch_relative_days"] for r in relative_rows(rows,origin,calendar,representation)]
    started=time.perf_counter()
    if solver=="rk4":
        pred,meta=_run_rk4(initial,origin if mode=="legacy" else 0.,times,force,planets,setting,ctx["force_config"]["default_step_days"])
        basis="relative_days_since_start"
    else:
        dp_origin=origin if mode=="legacy" else 0.
        dp_times=[r["epoch_jd_tdb"] for r in rows] if mode=="legacy" else times
        pred,meta=_run_dp(initial,dp_origin,dp_times,force,setting,ctx["base"]["dopri54_max_step_days"])
        basis="absolute_jd_tdb" if mode=="legacy" else "relative_days_since_start"
    runtime=time.perf_counter()-started
    native_origin=origin if basis=="absolute_jd_tdb" else 0.
    accepted=_normalize_trace(meta,native_origin,initial,solver)
    if len(pred)!=797 or _flat(pred[0])!=_flat(initial):raise ValueError("Wrong initial/length")
    if any(not math.isfinite(x) for s in pred for x in _flat(s)):raise ValueError("Nonfinite forecast")
    expected_end=rows[-1]["epoch_jd_tdb"] if basis=="absolute_jd_tdb" else times[-1]
    if accepted[-1][0]!=expected_end:raise ValueError("Native trace does not end at requested epoch")
    primary,refs=evaluate(pred,rows,ctx["refs"],ctx["au"],ctx["day"])
    result={"key":key,"mode":mode,"solver":solver,"setting":setting,"initial_source":initial_source,
        "initial_state":list(_flat(initial)),"origin_jd_tdb":origin,"origin_calendar_tdb":calendar,
        "requested_times_relative_days":times,"requested_states":[list(_flat(s)) for s in pred],
        "accepted_time_basis":basis,"accepted_endpoints":accepted,"primary_old_grid":primary,
        "reference_comparisons":refs,"runtime_seconds":runtime,"fingerprint":ctx["fingerprint"],
        "solver_stats":{k:v for k,v in meta.items() if k not in ("requested_states","accepted_endpoints")}}
    if mode=="legacy":
        baseline=next(r for r in previous["runs"] if r["arm"]=="earthmoon_venus15m" and r["solver"]==solver and r["setting"]==setting)
        diffs={k:abs(v-baseline["summary_old_grid"][k]) for k,v in primary.items()}
        if max(diffs.values())>ctx["config"]["baseline_absolute_tolerance"]:raise ValueError("Legacy baseline changed")
        result["baseline_differences"]=diffs
    immutable_json(path,result)
    print(json.dumps({"completed":key,"primary_km":primary["max_grid_position_error_km"],
        "annual_new_km":refs["annual_daily"]["max_grid_position_error_km"]}),flush=True)
    return result


def run(root):
    ctx=context(root);config=ctx["config"]
    previous=json.loads((root/PREVIOUS).read_text())
    tolerances={t["label"]:t for t in ctx["base"]["dopri54_tolerances"]}
    records=[]
    for mode in ["legacy",*config["relative_modes"]]:
        prefix="legacy" if mode=="legacy" else "relative"
        for label in config[prefix+"_dp"]:
            records.append(run_one(root,ctx,mode,"dopri54",tolerances[label],"old",previous))
        for scale in config[prefix+"_rk4"]:
            records.append(run_one(root,ctx,mode,"rk4",scale,"old",previous))
    initial_same=state_from_row(ctx["annual"][0])==state_from_row(ctx["refs"]["annual_hourly"][0])
    if not initial_same:
        for label in config["new_initial_dp"]:
            records.append(run_one(root,ctx,"relative_calendar_knots","dopri54",tolerances[label],"annual_hourly",previous))
    pairs=[]
    for i,a in enumerate(records):
        for b in records[i+1:]:
            if (a["mode"]==b["mode"] and a["initial_source"]==b["initial_source"]) or (a["solver"]==b["solver"] and a["setting"]==b["setting"]):
                pairs.append({"left":a["key"],"right":b["key"],**_shift([_state(s) for s in a["requested_states"]],[_state(s) for s in b["requested_states"]],ctx["au"],ctx["day"])})
    result={"fingerprint":ctx["fingerprint"],"freeze_sha256":ctx["freeze_sha256"],
        "input_validation":ctx["input_validation"],"repeat_validation":ctx["repeat_validation"],
        "new_initial_exactly_same":initial_same,"records":records,"paired_shifts":pairs,
        "scope":"post-hoc reference/time arithmetic audit; no new force or selector validation"}
    immutable_json(root/config["output_directory"]/"matrix.json",result)
    print(json.dumps({"completed_runs":len(records),"paired_shifts":len(pairs)}),flush=True)
    return result


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--root",type=Path,default=Path("."))
    run(p.parse_args().root.resolve())

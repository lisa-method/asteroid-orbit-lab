"""Recompute outlier audit errors, paired Pluto shifts, and immutable hashes."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import subprocess

from orbit_baselines import State, norm, subtract
from planetary_dynamics import EphemerisInterpolator
from prepare_development_sample import atomic_json
from run_development_benchmark import load_object_rows, _find_initial, _reference_grid


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def interp(trace):
    return EphemerisInterpolator(tuple(r["time_days"] for r in trace),
           tuple(State(tuple(r["state"]["r"]),tuple(r["state"]["v"])) for r in trace))


def near(a,b):
    assert math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-8), (a,b)


def verify(root):
    out = root/"outputs/development30_outlier_audit"
    audit = json.loads((out/"forces.json").read_text())
    geo = json.loads((out/"geography.json").read_text())
    config = json.loads((root/"configs/development30.json").read_text())
    base = json.loads((root/config["data_config"]).read_text())
    au, day_s = base["constants"]["au_km"],base["constants"]["day_s"]
    for path,expected in audit["provenance"]["source_hashes"].items():
        assert digest(root/path)==expected, path
    assert geo["provenance"]["source_sha256"]==digest(root/"src/diagnose_development_outlier_geometry.py")
    assert audit["settings"]["pluto_gm_km3_s2"]==975.5
    assert set(audit["objects"])=={"153814","613569"}
    count=0
    outcomes=[]
    pluto_shifts=[]
    traces_hashes={}
    for object_id, obj in audit["objects"].items():
        primary="EarthJ2" if object_id=="153814" else "nominalNG"
        assert set(obj["variants"])=={"baseline",primary,"Pluto",primary+"+Pluto"}
        assert obj["baseline_path_check"]["all_within_budget"]
        traces_path=out/"traces"/f"object_{object_id}.json"
        traces=json.loads(traces_path.read_text())
        traces_hashes[str(traces_path.relative_to(root))]=digest(traces_path)
        sample=obj["metadata"]["sample"]
        daily,raw=load_object_rows(root,root/config["raw_directory"],sample)
        start,_=_find_initial(daily,sample["start_date"])
        baseline_p,baseline_f=interp(traces["baseline"]["production"]),interp(traces["baseline"]["fine"])
        for variant, result in obj["variants"].items():
            trace=traces["baseline"] if variant=="baseline" else traces["variants"][variant]
            prod,fine=interp(trace["production"]),interp(trace["fine"])
            assert prod.epochs_jd_tdb[0]==fine.epochs_jd_tdb[0]==0 and prod.epochs_jd_tdb[-1]==fine.epochs_jd_tdb[-1]==365
            for horizon,metrics in result["horizons"].items():
                times,refs=_reference_grid(raw,start,float(horizon))
                predicted=[prod.state_at(t) for t in times]
                p_error=max(norm(subtract(p.position,r.position))*au for p,r in zip(predicted,refs))
                v_error=max(norm(subtract(p.velocity,r.velocity))*au*1000/day_s for p,r in zip(predicted,refs))
                step=max(norm(subtract(p.position,fine.state_at(t).position))*au for p,t in zip(predicted,times))
                shift=max(norm(subtract(p.position,baseline_p.state_at(t).position))*au for p,t in zip(predicted,times))
                near(p_error,metrics["max_position_error_km"])
                near(v_error,metrics["max_velocity_error_m_s"])
                near(step,metrics["production_fine_difference_km"])
                near(shift,metrics["max_shift_vs_baseline_km"])
                for tol in (.1,1.,10.):
                    assert metrics["eligibility"][str(tol)] == (p_error<=tol and step<=.1*tol)
                count+=len(times)
                if variant==primary and ((object_id=="153814" and float(horizon) in (90,180,365))
                                          or (object_id=="613569" and float(horizon) in (180,365))):
                    outcomes.append({"object_id":object_id,"variant":variant,"horizon_days":int(horizon),
                                     "old_error_km":obj["variants"]["baseline"]["horizons"][horizon]["max_position_error_km"],
                                     "new_error_km":p_error,"step_difference_km":step,
                                     "eligible_0.1km":p_error<=.1 and step<=.01,"eligible_1km":p_error<=1 and step<=.1})
            if variant=="Pluto":
                times,_=_reference_grid(raw,start,365)
                paired_prod=[subtract(prod.state_at(t).position,baseline_p.state_at(t).position) for t in times]
                paired_fine=[subtract(fine.state_at(t).position,baseline_f.state_at(t).position) for t in times]
                pluto_shifts.append({"object_id":object_id,"max_production_shift_m":max(map(norm,paired_prod))*au*1000,
                                     "max_fine_shift_m":max(map(norm,paired_fine))*au*1000,
                                     "max_paired_step_difference_m":max(norm(subtract(p,f)) for p,f in zip(paired_prod,paired_fine))*au*1000})
    assert len(outcomes)==5
    raw_files={}
    for path in sorted((root/"data/checksums").glob("*manifest.json")):
        manifest=json.loads(path.read_text())
        for entry in manifest["files"]:
            raw_path=(root/entry["path"]).resolve()
            assert raw_path.stat().st_size==entry["bytes"] and digest(raw_path)==entry["sha256"], str(raw_path)
            raw_files[str(raw_path.relative_to(root))]=entry["sha256"]
    ignored=subprocess.run(["git","check-ignore","--stdin"],cwd=root,input="\n".join(raw_files)+"\n",text=True,capture_output=True,check=True)
    assert set(ignored.stdout.splitlines())==set(raw_files)
    result={"passed":True,"independent_solver":False,"recomputed_position_and_velocity_samples":count,
            "raw_files_verified":len(raw_files),"raw_all_ignored":True,"failed_windows_rechecked":outcomes,
            "resolved_at_1km":sum(r["eligible_1km"] for r in outcomes),
            "resolved_at_0.1km":sum(r["eligible_0.1km"] for r in outcomes),"pluto_shifts":pluto_shifts,
            "trace_sha256":traces_hashes,"forces_sha256":digest(out/"forces.json"),"geography_sha256":digest(out/"geography.json"),
            "contract_sha256":digest(root/"docs/DEVELOPMENT30_OUTLIER_AUDIT_CONTRACT.md"),
            "source_sha256":digest(Path(__file__))}
    atomic_json(out/"verification.json",result)
    return result


if __name__ == "__main__":
    print(json.dumps(verify(Path(__file__).resolve().parents[1]),indent=2))

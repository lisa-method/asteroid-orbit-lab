"""Recompute compact paired effects and time profiles from frozen audit states."""
import json
from pathlib import Path

from orbit_baselines import norm, subtract
from prepare_fresh_holdout import immutable_json, sha
from run_apophis_solver_audit import _target_rows
from run_eda import parse_horizons


def run(root):
    matrix_rel="outputs/apophis_moon_venus/v1_1/apophis_moon_venus_results.json"
    matrix=json.loads((root/matrix_rel).read_text())
    data=json.loads((root/"configs/eda_pilot_6.json").read_text())
    event=json.loads((root/"configs/eda_apophis_2029_refinement.json").read_text())
    annual,_=_target_rows(root,data,event,365)
    dense=parse_horizons(root/"data/raw/apophis_moon_venus/asteroid_99942.json","99942","Apophis")[1]
    au=data["constants"]["au_km"]
    runs={(r["arm"],r["solver"],r["setting"]["label"] if isinstance(r["setting"],dict) else str(r["setting"])):r for r in matrix["runs"]}
    pairs=[("old","moon5m"),("old","earthmoon5m"),("old","venus_hourly"),
           ("moon5m","moon_venus"),("earthmoon5m","earthmoon_venus"),
           ("earthmoon_venus","earthmoon_venus15m"),("old","earthmoon_venus15m"),
           ("earthmoon_venus15m","no_venus"),("earthmoon_venus15m","no_moon")]
    pair_results=[]
    for left,right in pairs:
        for solver,label in [("dopri54","tight"),("dopri54","tighter"),("rk4","0.5"),("rk4","0.25")]:
            if (left,solver,label) not in runs or (right,solver,label) not in runs:
                continue
            a,b=runs[left,solver,label],runs[right,solver,label]
            values=[norm(subtract(x[:3],y[:3]))*au for x,y in zip(a["requested_states"],b["requested_states"])]
            pair_results.append({"left":left,"right":right,"solver":solver,"setting":label,
                                 "max_shift_km":max(values),"final_shift_km":values[-1]})
    dates=["2029-04-10T00:00:00","2029-04-13T21:45:00","2029-04-14T12:00:00",
           "2029-04-14T14:30:00","2029-04-15T00:00:00","2029-04-18T00:00:00",
           "2029-05-01T00:00:00","2029-07-01T00:00:00","2030-01-01T00:00:00"]
    profiles=[]
    for key in runs:
        if key[0] not in ["old","moon5m","earthmoon_venus15m"] or key[1]!="dopri54":
            continue
        run=runs[key]
        snapshots=[]
        for date in dates:
            values={"epoch_tdb":date}
            for label,rows,states in [("primary_old",annual,run["requested_states"]),("secondary_refined",dense,run["dense_states"])]:
                hits=[(r,s) for r,s in zip(rows,states) if r["epoch_tdb"]==date]
                if hits:
                    r,s=hits[0]
                    values[label+"_error_km"]=norm(subtract(r["r"],s[:3]))*au
            snapshots.append(values)
        profiles.append({"arm":key[0],"solver":key[1],"setting":key[2],"snapshots":snapshots})
    result={"paired_effects":pair_results,"profiles":profiles,
        "sources":{"matrix":sha(root/matrix_rel),"analysis":sha(Path(__file__)),
                   "geometry":sha(root/"outputs/apophis_moon_venus/geometry.json")},
        "interpretation":"Paired shifts, not differences of scalar max errors. Two distinct teacher grids retain their labels."}
    immutable_json(root/"outputs/apophis_moon_venus/v1_1/analysis.json",result)
    return result


if __name__=="__main__":
    print(json.dumps(run(Path(__file__).resolve().parents[1]),indent=2))

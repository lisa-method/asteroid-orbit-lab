"""Reference geometry and planetary interpolation errors; no propagation."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from orbit_baselines import norm, subtract
from planetary_dynamics import EphemerisInterpolator
from prepare_fresh_holdout import sha, immutable_json
from run_apophis_solver_audit import _old_inputs, _old_planets
from run_eda import parse_horizons


def calendar(jd):
    return (dt.datetime(2000,1,1,12)+dt.timedelta(days=jd-2451545.)).isoformat(timespec="milliseconds")


def run(root):
    data=json.loads((root/"configs/eda_pilot_6.json").read_text())
    event=json.loads((root/"configs/eda_apophis_2029_refinement.json").read_text())
    inputs=["configs/eda_pilot_6.json","configs/eda_apophis_2029_refinement.json",
            "src/diagnose_apophis_moon_venus_geometry.py","src/run_apophis_solver_audit.py",
            "src/run_nbody_baseline.py","src/run_physics_baselines.py","src/run_b3plus_ablation.py",
            "src/run_eda.py","src/planetary_dynamics.py","src/orbit_baselines.py",
            "src/prepare_fresh_holdout.py","data/processed/apophis_moon_venus/input_validation.json"]
    new={}
    for ident in ["399","301","299","99942"]:
        rel="data/raw/apophis_moon_venus/"+("asteroid_" if ident=="99942" else "body_")+ident+".json"
        inputs.append(rel)
        new[ident]=parse_horizons(root/rel,ident,ident)[1]
    inputs += [f"data/raw/horizons/body_{b['id']}.json" for b in data["perturbers"]]
    inputs += [f"data/raw/horizons_refined/apophis_earth_2029_{i}.json" for i in ["399","301","99942"]]
    old={p.body_id:p for p in _old_planets(root,data,*_old_inputs(root,data,event))}
    interps={key:EphemerisInterpolator.from_rows(rows) for key,rows in new.items()}
    au,day=data["constants"]["au_km"],data["constants"]["day_s"]
    def geom(ident,t):
        a=interps["99942"].state_at(t)
        b=interps[ident].state_at(t)
        r,v=subtract(a.position,b.position),subtract(a.velocity,b.velocity)
        d=norm(r)*au
        mu=next(x["mu_km3_s2"] for x in data["perturbers"] if x["id"]==ident)
        return {"epoch_jd_tdb":t,"epoch_tdb":calendar(t),"distance_km":d,
                "relative_speed_km_s":norm(v)*au/day,
                "radial_speed_km_s":sum(x*y for x,y in zip(r,v))/norm(r)*au/day,
                "direct_acceleration_m_s2":mu/d**2*1000,
                "direct_to_solar_acceleration_ratio":(mu/d**2)/(data["constants"]["mu_sun_km3_s2"]/(norm(a.position)*au)**2)}
    minima=[]
    for ident in ["399","301"]:
        rows=new["99942"]
        index=min(range(len(rows)),key=lambda i:geom(ident,rows[i]["epoch_jd_tdb"])["distance_km"])
        if index in [0,len(rows)-1]:
            raise ValueError("Closest point is still at boundary")
        lo,hi=rows[index-1]["epoch_jd_tdb"],rows[index+1]["epoch_jd_tdb"]
        bracket=[lo,hi]
        for _ in range(60):
            if hi-lo<1e-9:
                break
            x=lo+(hi-lo)*.3819660112501051
            y=lo+(hi-lo)*.6180339887498949
            if geom(ident,x)["distance_km"] < geom(ident,y)["distance_km"]:
                hi=y
            else:
                lo=x
        t=(lo+hi)/2
        minima.append({"body_id":ident,"sampled":geom(ident,rows[index]["epoch_jd_tdb"]),
            "hermite_refined":geom(ident,t),"local_bracket_jd_tdb":bracket,
            "other_body_distance_km":geom("301" if ident=="399" else "399",t)["distance_km"]})
    interpolation=[]
    for ident in ["399","301","299"]:
        rows=new[ident]
        candidates={"old":old[ident].ephemeris}
        if ident=="299":
            candidates["new_hourly"]=EphemerisInterpolator.from_rows(rows[::4])
        for name,interp in candidates.items():
            values=[norm(subtract(interp.state_at(r["epoch_jd_tdb"]).position,r["r"]))*au for r in rows]
            worst=max(range(len(values)),key=values.__getitem__)
            interpolation.append({"body_id":ident,"candidate":name,"samples":len(rows),
                "max_planet_position_difference_km":values[worst],"at_tdb":rows[worst]["epoch_tdb"],
                "rms_planet_position_difference_km":(sum(v*v for v in values)/len(values))**.5})
    result={"scope":"reference-only geometry and planetary interpolation diagnostic; no forecast",
        "reference_minima":minima,"interpolation":interpolation,
        "interpretation":"Hermite minima are estimates between 5-minute raw knots; not independent observations. Planet interpolation errors are not asteroid forecast errors.",
        "input_validation_sha256":sha(root/"data/processed/apophis_moon_venus/input_validation.json"),
        "hashes":{p:sha(root/p) for p in inputs}}
    immutable_json(root/"outputs/apophis_moon_venus/geometry.json",result)
    return result


if __name__=="__main__":
    print(json.dumps(run(Path(__file__).resolve().parents[1]),indent=2))

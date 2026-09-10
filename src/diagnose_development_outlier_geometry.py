"""Offline orbital geography and error onset for two development30 outliers."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re

from encounter_screening import scan_body
from orbit_baselines import norm, subtract
from planetary_dynamics import EphemerisInterpolator, Perturber
from prepare_development_sample import atomic_json
from run_development_benchmark import load_development_context, load_object_rows, _find_initial, _reference_grid
from run_eda import parse_horizons


def main():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "configs/development30.json").read_text())
    sample = json.loads((root / config["sample_path"]).read_text())
    validation_path = root / "outputs/development30/validation/results.json"
    validation = json.loads(validation_path.read_text())
    for relative, digest in validation["provenance"]["source_sha256_at_start"].items():
        assert hashlib.sha256((root/relative).read_bytes()).hexdigest() == digest, relative
    context = load_development_context(root, config)
    pluto_path = root / "data/raw/development30_outlier_audit/pluto_system_daily.json"
    _, pluto_rows = parse_horizons(pluto_path, "9", "Pluto system")
    pluto = Perturber("9", "Pluto system", 975.5*context["day_s"]**2/context["au_km"]**3,
                     EphemerisInterpolator.from_rows(pluto_rows))
    result = {"purpose": "Post-hoc reference geography; not forecast inputs", "objects": {},
              "provenance": {"validation_sha256": hashlib.sha256(validation_path.read_bytes()).hexdigest(),
                             "pluto_sha256": hashlib.sha256(pluto_path.read_bytes()).hexdigest(),
                             "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}}
    for object_id in ("153814", "613569"):
        obj = next(o for o in sample["objects"] if o["id"] == object_id)
        daily, reference = load_object_rows(root, root/config["raw_directory"], obj)
        start_jd, initial = _find_initial(daily, obj["start_date"])
        times, states = _reference_grid(reference, start_jd, 365)
        row = next(r for r in validation["records"] if r["object_id"] == object_id
                   and r["horizon_days"] == 365 and r["model_id"] == "B3+GR+SB16")
        header = json.loads((root/config["raw_directory"]/"asteroids"/f"asteroid_{object_id}_daily.json").read_text())["result"].split("$$SOE",1)[0]
        def field(name):
            match = re.search(r"\b"+re.escape(name)+r"=\s*([-+.0-9Ee]+)", header)
            return float(match.group(1)) if match else None
        radius = [norm(s.position) for s in states]
        min_index, max_index = min(range(len(radius)), key=radius.__getitem__), max(range(len(radius)), key=radius.__getitem__)
        planets = [g["reference"] for g in row["geometry_compare"].values()]
        extra = []
        for body in (*context["small"], pluto):
            planet_states = [body.ephemeris.state_at(start_jd+t) for t in times]
            extra.append(scan_body(times, states, planet_states, body, context["mu"], context["au_km"], context["day_s"], refine=True))
        errors = row["error_rows"]
        first = next((e for e in errors if e["position_error_km"] > 1), None)
        checkpoints = []
        for day in (0,7,28,29,30,31,32,35,45,60,90,180,365):
            e = next(e for e in errors if abs(e["time_days"]-day) < 1e-9)
            checkpoints.append({k:e[k] for k in ("time_days", "position_error_km", "rtn_position_error_km", "velocity_error_m_s")})
        value = {"name": header.splitlines()[1].strip(), "start_date": obj["start_date"], "event": obj["event"],
                 "header_orbit": {k:field(k) for k in ("EPOCH","A","QR","ADIST","EC","IN","A1","A2","A3")},
                 "sampled_solar_min_au": radius[min_index], "solar_min_day": times[min_index],
                 "sampled_solar_max_au": radius[max_index], "solar_max_day": times[max_index],
                 "planet_minima": sorted(planets,key=lambda g:g["distance_km"]),
                 "nearest_small_perturbers": sorted([g for g in extra if g["body_id"] != "9"],key=lambda g:g["distance_km"]),
                 "pluto_minimum": next(g for g in extra if g["body_id"] == "9"),
                 "first_sample_over_1km": first, "error_checkpoints": checkpoints}
        result["objects"][object_id] = value
        print(json.dumps({"object_id":object_id,"solar_range_au":[radius[min_index],radius[max_index]],
                          "first_over_1km_day":None if first is None else first["time_days"],
                          "nearest_planets":[[g["body_name"],g["distance_au"],g["time_days"]] for g in value["planet_minima"][:5]],
                          "nearest_small":[g["body_name"] for g in value["nearest_small_perturbers"][:3]],
                          "pluto_minimum_au":value["pluto_minimum"]["distance_au"]}),flush=True)
    atomic_json(root/"outputs/development30_outlier_audit/geography.json",result)


if __name__ == "__main__":
    main()

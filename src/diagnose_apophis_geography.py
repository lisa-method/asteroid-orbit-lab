"""Read-only orbit geography of the inspected Apophis 2029 reference window.

No propagation or fitting. Minima are sampled, using daily reference states
and the existing 5-minute Earth-encounter refinement. Distances are 3-D.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from orbit_baselines import norm, subtract
from prepare_fresh_holdout import immutable_json, sha
from run_apophis_solver_audit import _old_inputs, _old_planets, _target_rows
from run_eda import parse_horizons


def run(root: Path) -> dict:
    data = json.loads((root / "configs/eda_pilot_6.json").read_text())
    event = json.loads((root / "configs/eda_apophis_2029_refinement.json").read_text())
    planets = _old_planets(root, data, *_old_inputs(root, data, event))
    annual, short = _target_rows(root, data, event, 365)
    au, day = data["constants"]["au_km"], data["constants"]["day_s"]
    mu_sun = data["constants"]["mu_sun_km3_s2"]
    by_id = {p.body_id: p for p in planets}
    minima = []
    for p in planets:
        row = min(annual, key=lambda r: norm(subtract(tuple(r["r"]), p.ephemeris.state_at(r["epoch_jd_tdb"]).position)))
        state = p.ephemeris.state_at(row["epoch_jd_tdb"])
        distance = norm(subtract(tuple(row["r"]), state.position)) * au
        body_mu = p.mu_au3_d2 * au**3 / day**2
        solar_distance = norm(tuple(row["r"])) * au
        minima.append({"body_id": p.body_id, "body": p.name, "epoch_tdb": row["epoch_tdb"],
                       "distance_km": distance, "distance_au": distance / au,
                       "relative_speed_km_s": norm(subtract(tuple(row["v"]), state.velocity)) * au/day,
                       "body_direct_to_solar_acceleration_ratio": (body_mu/distance**2)/(mu_sun/solar_distance**2)})
    closest = next(m for m in minima if m["body_id"] == "399")
    encounter = next(r for r in annual if r["epoch_tdb"] == closest["epoch_tdb"])
    audit_path = root / "outputs/apophis_solver_audit/stage3/apophis_solver_audit.json"
    audit = json.loads(audit_path.read_text())
    saved = next(c for c in audit["cases"] if c["case_id"] == "apophis_2029_long365d")
    predictions = {r["id"]: r["requested_states"] for r in saved["solver_comparison"]}
    assert all(len(rows) == len(annual) for rows in predictions.values())
    errors = [{"epoch_tdb": row["epoch_tdb"], "dp_tight_error_km": norm(subtract(tuple(row["r"]), tuple(predictions["dopri54:tight"][i][:3]))) * au,
               "rk4_error_km": norm(subtract(tuple(row["r"]), tuple(predictions["rk4:0.5"][i][:3]))) * au}
              for i, row in enumerate(annual)]
    dates = ["2029-01-01T00:00:00", "2029-04-01T00:00:00", "2029-04-13T00:00:00", closest["epoch_tdb"],
             "2029-04-14T00:00:00", "2029-05-01T00:00:00", "2029-07-01T00:00:00", "2030-01-01T00:00:00"]
    daily = parse_horizons(root / "data/raw/horizons/asteroid_99942.json", "99942", "Apophis")[1]
    before = [r for r in daily if "2028-01-01" <= r["epoch_tdb"][:10] <= "2029-01-01"]
    today = next(r for r in daily if r["epoch_tdb"] == "2026-09-08T00:00:00")
    sun_range = lambda rows: [min(norm(tuple(r["r"])) for r in rows), max(norm(tuple(r["r"])) for r in rows)]
    # Fixed J2000 mean-obliquity rotation for an explanatory spatial projection.
    # Quantitative distances above use the original full 3-D ICRF states.
    eps = math.radians(23.439291111)
    def project(r):
        return [r[0], r[1]*math.cos(eps) + r[2]*math.sin(eps)]
    def point(row, scale=1., earth_relative=False):
        epoch = row["epoch_jd_tdb"]
        earth = by_id["399"].ephemeris.state_at(epoch).position
        def xy(r):
            q = subtract(tuple(r), earth) if earth_relative else tuple(r)
            return [round(x*scale, 7 if scale == 1 else 3) for x in project(q)]
        output = {"date": row["epoch_tdb"], "a": xy(row["r"]), "e": xy(earth),
                  "v": xy(by_id["299"].ephemeris.state_at(epoch).position),
                  "m": xy(by_id["301"].ephemeris.state_at(epoch).position),
                  "distance": round(norm(subtract(tuple(row["r"]), earth))*au, 3),
                  "sun": round(norm(tuple(row["r"])), 5)}
        if earth_relative:
            output.pop("v")
        return output
    payload = {"solar": [point(r) for r in annual if r["epoch_tdb"].endswith("T00:00:00")],
               "encounter": [point(r, au, True) for r in short],
               "closest_index": next(i for i,r in enumerate(short) if r["epoch_tdb"] == closest["epoch_tdb"]),
               "projection": "J2000 ecliptic XY; mean obliquity 23.439291111 deg; distances use original 3-D ICRF"}
    inputs = ["configs/eda_pilot_6.json", "configs/eda_apophis_2029_refinement.json", "src/diagnose_apophis_geography.py",
              "src/run_apophis_solver_audit.py", "src/planetary_dynamics.py", "src/run_eda.py", "src/orbit_baselines.py",
              "data/raw/horizons/asteroid_99942.json", str(audit_path.relative_to(root))]
    inputs += [f"data/raw/horizons/body_{p.body_id}.json" for p in planets]
    inputs += [f"data/raw/horizons_refined/apophis_earth_2029_{i}.json" for i in ("99942", "399", "301")]
    result = {"scope": "Inspected 2029 teacher geography; sampled minima, no new propagation",
              "window": [annual[0]["epoch_tdb"], annual[-1]["epoch_tdb"]], "reference_samples": len(annual),
              "heliocentric_range_au_2029": sun_range(annual), "heliocentric_range_au_2028": sun_range(before),
              "planet_minima": minima, "encounter_sun_distance_au": norm(tuple(encounter["r"])),
              "moon_distance_at_earth_encounter_km": norm(subtract(tuple(encounter["r"]), by_id["301"].ephemeris.state_at(encounter["epoch_jd_tdb"]).position))*au,
              "earth_radius_reference_km": 6378.1366,
              "approx_height_at_sampled_earth_minimum_km": closest["distance_km"]-6378.1366,
              "error_snapshots": [next(e for e in errors if e["epoch_tdb"] == date) for date in dates],
              "position_2026_09_08_00_tdb": {"sun_distance_au": norm(tuple(today["r"])),
                  "earth_distance_km": norm(subtract(tuple(today["r"]),by_id["399"].ephemeris.state_at(today["epoch_jd_tdb"]).position))*au},
              "source_sha256": {p: sha(root/p) for p in inputs}}
    immutable_json(root / "outputs/apophis_geography/diagnostic.json", result)
    immutable_json(root / "outputs/apophis_geography/visual_data.json", payload)
    return result


if __name__ == "__main__":
    print(json.dumps(run(Path(__file__).resolve().parents[1]), indent=2))

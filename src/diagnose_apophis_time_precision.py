"""Measure JD time resolution at an inspected event; never propagates an orbit."""
from __future__ import annotations

import json
import math
from pathlib import Path

from orbit_baselines import norm, subtract
from prepare_fresh_holdout import immutable_json, sha
from run_apophis_solver_audit import _old_inputs, _old_planets, _target_rows


def run(root):
    data = json.loads((root / "configs/eda_pilot_6.json").read_text())
    event = json.loads((root / "configs/eda_apophis_2029_refinement.json").read_text())
    planets = _old_planets(root, data, *_old_inputs(root, data, event))
    _, reference = _target_rows(root, data, event, 365)
    earth = next(p for p in planets if p.body_id == "399")
    row = min(reference, key=lambda r: norm(subtract(tuple(r["r"]), earth.ephemeris.state_at(r["epoch_jd_tdb"]).position)))
    epoch = row["epoch_jd_tdb"]
    next_epoch = math.nextafter(epoch, math.inf)
    au_m = data["constants"]["au_km"] * 1000.
    seconds = data["constants"]["day_s"]
    values = []
    for body in planets:
        if body.body_id not in ("399", "301"):
            continue
        states = [body.ephemeris.state_at(t) for t in (epoch, next_epoch)]
        relative = [subtract(s.position, tuple(row["r"])) for s in states]
        acceleration = [tuple(body.mu_au3_d2 * x / norm(r)**3 for x in r) for r in relative]
        values.append({"body_id": body.body_id, "distance_km": norm(relative[0])*au_m/1000.,
                       "direct_acceleration_m_s2": norm(acceleration[0])*au_m/seconds**2,
                       "adjacent_jd_position_shift_m": norm(subtract(states[1].position, states[0].position))*au_m,
                       "adjacent_jd_direct_acceleration_shift_m_s2": norm(subtract(acceleration[1], acceleration[0]))*au_m/seconds**2})
    inputs = ["configs/eda_pilot_6.json", "configs/eda_apophis_2029_refinement.json",
              "src/diagnose_apophis_time_precision.py", "src/planetary_dynamics.py"]
    inputs += [f"data/raw/horizons/body_{i}.json" for i in ("399", "301")]
    inputs += [f"data/raw/horizons_refined/apophis_earth_2029_{i}.json" for i in ("99942", "399", "301")]
    result = {"epoch_jd_tdb": epoch, "epoch_tdb": row["epoch_tdb"], "jd_ulp_days": math.ulp(epoch),
              "jd_ulp_seconds": math.ulp(epoch)*seconds, "bodies": values,
              "interpretation": "Adjacent-float epoch separation is a resolution diagnostic, not a measured interpolation error or an explanation of annual trajectory discrepancy. No new propagation.",
              "source_sha256": {p: sha(root / p) for p in inputs}}
    immutable_json(root / "outputs/apophis_time_quantization/diagnostic.json", result)
    return result


if __name__ == "__main__":
    print(json.dumps(run(Path(__file__).resolve().parents[1]), indent=2))

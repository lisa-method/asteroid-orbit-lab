"""Verify frozen sampling, raw hashes, coordinates and reference overlap."""
from __future__ import annotations

import collections
import hashlib
import json
from pathlib import Path
import re

from orbit_baselines import norm, subtract
from prepare_development_sample import atomic_json, freeze_sample
from run_eda import parse_horizons


def verify(root: Path) -> dict:
    config = json.loads((root / "configs/development30.json").read_text())
    sample = freeze_sample(root, config)
    counts = collections.Counter((o["stratum"], o["split"]) for o in sample["objects"])
    for stratum in config["sampling_order"]:
        assert counts[stratum, "train"] == 3 and counts[stratum, "validation"] == 2
    ids = [o["id"] for o in sample["objects"]]
    assert len(ids) == len(set(ids)) == 30
    assert not set(ids).intersection(config["excluded_object_ids"])
    manifest_path = root / "data/checksums/development30_data_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["complete"] and len(manifest["files"]) == 59
    assert manifest["sample_sha256"] == hashlib.sha256((root / config["sample_path"]).read_bytes()).hexdigest()
    astro_rows = {}
    source_models = collections.Counter()
    for record in manifest["files"]:
        path = root / record["path"]
        payload = path.read_bytes()
        assert len(payload) == record["bytes"]
        assert hashlib.sha256(payload).hexdigest() == record["sha256"]
        header, rows = parse_horizons(path, record["target_id"], record["target_name"])
        assert header["center"].startswith("Sun (10)")
        assert header["reference_frame"] == "ICRF"
        assert header["units"] == "AU-D"
        assert header["geometric"] and header["tdb"] and header["api_version"] == "1.2"
        assert len(rows) == record["rows"]
        assert all(b["epoch_jd_tdb"] > a["epoch_jd_tdb"] for a, b in zip(rows, rows[1:]))
        if record["kind"] == "horizons_asteroid":
            assert re.match(r"^" + re.escape(record["target_id"]) + r"\s", header["target"])
            astro_rows[path.name] = rows
            source_models[header["target"].split("{source:")[-1].rstrip("}").strip()] += 1
        else:
            assert f"({record['target_id']})" in header["target"]
    au = json.loads((root / config["data_config"]).read_text())["constants"]["au_km"]
    overlap_max_km = 0.0
    overlap_max_velocity_m_s = 0.0
    for obj in sample["objects"]:
        daily = astro_rows[f"asteroid_{obj['id']}_daily.json"]
        assert daily[0]["epoch_tdb"] == obj["start_date"] + "T00:00:00"
        assert len(daily) == 366
        assert daily[-1]["epoch_jd_tdb"] - daily[0]["epoch_jd_tdb"] == 365
        if obj["event"]:
            lookup = {r["epoch_jd_tdb"]: r for r in daily}
            refined = astro_rows[f"asteroid_{obj['id']}_refined.json"]
            assert len(refined) == 1153
            for row in refined:
                if row["epoch_jd_tdb"] in lookup:
                    distance = norm(subtract(row["r"], lookup[row["epoch_jd_tdb"]]["r"])) * au
                    overlap_max_km = max(overlap_max_km, distance)
                    assert distance <= 1e-5, "Daily/refined teacher mismatch"
                    velocity = norm(subtract(row["v"], lookup[row["epoch_jd_tdb"]]["v"])) * au * 1000 / 86400
                    overlap_max_velocity_m_s = max(overlap_max_velocity_m_s, velocity)
                    assert velocity <= 1e-8, "Daily/refined teacher velocity mismatch"
    result = {"passed": True, "objects": len(ids), "train": 18, "validation": 12,
              "raw_files": len(manifest["files"]), "raw_bytes": sum(r["bytes"] for r in manifest["files"]),
              "daily_refined_overlap_max_km": overlap_max_km,
              "daily_refined_overlap_max_velocity_m_s": overlap_max_velocity_m_s,
              "sample_sha256": manifest["sample_sha256"],
              "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest()}
    atomic_json(root / config["output_directory"] / "data_verification.json", result)
    return result


if __name__ == "__main__":
    print(json.dumps(verify(Path(__file__).resolve().parents[1]), indent=2))

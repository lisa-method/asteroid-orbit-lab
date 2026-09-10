"""Fetch one immutable, anonymous Horizons Pluto-system series for the audit."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

from download_jpl_pilot import build_horizons_url, download_one
from prepare_development_sample import atomic_json
from run_eda import parse_horizons


def main():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "configs/eda_pilot_6.json").read_text())
    period = {"start": "2027-12-30", "stop": "2029-06-01", "step": "1 d", "time_scale": "TDB"}
    config["time_range"] = period
    path = root / "data/raw/development30_outlier_audit/pluto_system_daily.json"
    manifest_path = root / "data/checksums/development30_outlier_audit_manifest.json"
    job = {"kind": "horizons_planet", "target_id": "9", "target_name": "Pluto system barycenter",
           "url": build_horizons_url(config, "9", small_body=False), "path": path}
    if manifest_path.exists():
        old = json.loads(manifest_path.read_text())
        record = old["files"][0]
        assert record["source_url"] == job["url"]
        assert record["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert record["bytes"] == path.stat().st_size
    else:
        record = download_one(job)
        record["path"] = str(path.relative_to(root))
        record.pop("reused", None)
    metadata, rows = parse_horizons(path, "9", "Pluto system")
    assert metadata["center"].startswith("Sun (10)")
    assert metadata["reference_frame"] == "ICRF" and metadata["units"] == "AU-D"
    assert metadata["geometric"] and metadata["tdb"] and "(9)" in metadata["target"]
    expected = (dt.date.fromisoformat(period["stop"])-dt.date.fromisoformat(period["start"])).days+1
    assert len(rows) == expected and rows[-1]["epoch_jd_tdb"]-rows[0]["epoch_jd_tdb"] == expected-1
    record.update({"rows": len(rows), "coordinates": config["coordinates"], "time_scale": "TDB"})
    if not manifest_path.exists():
        atomic_json(manifest_path, {"files": [record], "pluto_gm_km3_s2": 975.5,
                    "gm_source": "https://ssd.jpl.nasa.gov/astro_par.html", "time_range": period})
    print(json.dumps({"path": record["path"], "sha256": record["sha256"], "bytes": record["bytes"], "rows": len(rows)}))


if __name__ == "__main__":
    main()

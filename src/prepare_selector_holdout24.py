"""Freeze and fetch the selector-v3 whole-object holdout.

Selection is metadata-only and deliberately happens only after the parent
method freeze exists.  The downloader is serial, resumable, and writes only
the ``selector_holdout24`` namespace.  No forecast error or future target
state is consulted while choosing objects.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

from download_development_data import build_jobs
from download_jpl_pilot import download_one, write_immutable
from ng_inputs_v2 import load_ng_input
from prepare_development_sample import atomic_json, catalogue_queries, rows
from run_eda import parse_horizons


METHOD_FREEZE = "outputs/selector_v3/method_freeze.json"
SAMPLE_PATH = "data/processed/selector_holdout24/sample.json"
MANIFEST_PATH = "data/checksums/selector_holdout24_manifest.json"
RAW_DIRECTORY = "data/raw/selector_holdout24"
SEED = "selector-v3-holdout24-2026-09-09"
REQUIRED_PER_STRATUM = 4
ENCOUNTER_LEAD_DAYS = 30
CONTROL_START_DATE = "2029-01-01"
START_MIN = dt.date(2027, 1, 1)
START_MAX = dt.date(2029, 12, 31)
JUPITER_CATALOGUE = "data/raw/fresh_holdout12/catalogues/cad_jupiter_2026_2029_10au.json"
ENCOUNTER_CATALOGUE = "cad_2026_2029_02au"
CONTROL_CATALOGUES = ("inner_controls", "outer_controls")
BODY_IDS = {"Earth": "399", "Venus": "299", "Mars": "4", "Jupiter": "5"}
ENCOUNTER_STRATA = tuple(BODY_IDS)
STRATA = ENCOUNTER_STRATA + CONTROL_CATALOGUES
COORDINATE_FIELDS = (
    "Center body name: Sun (10)",
    "Output units    : AU-D",
    "Reference frame : ICRF",
    "Output type     : GEOMETRIC cartesian states",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def immutable_json(path: Path, value: object) -> None:
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False).encode() + b"\n"
    write_immutable(path, payload)


def check_hashes(root: Path, hashes: dict[str, str]) -> None:
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError("method freeze must contain non-empty hashes")
    for relative, expected in hashes.items():
        path = root / relative
        if not path.is_file() or sha(path) != str(expected):
            raise ValueError(f"Frozen method dependency changed: {relative}")


def _catalogue_paths(root: Path) -> dict[str, Path]:
    paths = {
        name: root / "data/raw/development30/catalogues" / f"{name}.json"
        for name, _, _ in catalogue_queries()
    }
    paths["cad_jupiter_2026_2029_10au"] = root / JUPITER_CATALOGUE
    return paths


def load_catalogues(root: Path) -> dict[str, dict[str, Any]]:
    documents = {}
    for name, path in _catalogue_paths(root).items():
        if not path.is_file():
            raise FileNotFoundError(f"Missing cached sampling catalogue: {path}")
        documents[name] = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(documents[name].get("fields"), list) or not isinstance(documents[name].get("data"), list):
            raise ValueError(f"Invalid sampling catalogue: {name}")
    return documents


def _as_id(value: Any) -> str | None:
    text = str(value).strip()
    return text if text.isdigit() else None


def _event_date(row: dict[str, Any]) -> dt.date:
    try:
        return dt.datetime.strptime(str(row["cd"]).split()[0], "%Y-%b-%d").date()
    except (KeyError, ValueError) as exc:
        raise ValueError(f"CAD row has invalid event date: {row!r}") from exc


def _candidate_rows(documents: dict[str, dict[str, Any]], excluded: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    blocked = {str(value) for value in excluded}
    candidates: dict[str, list[dict[str, Any]]] = {}
    for stratum in ENCOUNTER_STRATA:
        catalogue = "cad_jupiter_2026_2029_10au" if stratum == "Jupiter" else ENCOUNTER_CATALOGUE
        unique: dict[str, dict[str, Any]] = {}
        for row in rows(documents[catalogue]):
            object_id = _as_id(row.get("des"))
            if object_id is None or object_id in blocked or row.get("body", stratum) != stratum:
                continue
            start = _event_date(row) - dt.timedelta(days=ENCOUNTER_LEAD_DAYS)
            if not START_MIN <= start <= START_MAX:
                continue
            # A numbered object can occur more than once in a broad CAD query.
            # The deterministic ordering below chooses its closest occurrence.
            previous = unique.get(object_id)
            if previous is None or (float(row["dist"]), float(row["jd"]), object_id) < (
                float(previous["dist"]), float(previous["jd"]), object_id
            ):
                unique[object_id] = row
        candidates[stratum] = sorted(
            unique.values(), key=lambda row: (float(row["dist"]), float(row["jd"]), int(str(row["des"])))
        )

    for stratum in CONTROL_CATALOGUES:
        unique = {}
        for row in rows(documents[stratum]):
            object_id = _as_id(row.get("pdes"))
            if object_id is not None and object_id not in blocked:
                unique[object_id] = row
        candidates[stratum] = sorted(
            unique.values(),
            key=lambda row: (
                hashlib.sha256(f"{SEED}:selection:{stratum}:{str(row['pdes']).strip()}".encode()).hexdigest(),
                int(str(row["pdes"]).strip()),
            ),
        )
    return candidates


def _candidate_id(stratum: str, row: dict[str, Any]) -> str:
    return str(row["des"] if stratum in ENCOUNTER_STRATA else row["pdes"]).strip()


def _greedy_available(
    candidates: dict[str, list[dict[str, Any]]], *, required: int
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    """Apply one fixed cross-stratum greedy order to selection and counts."""
    selected_ids: set[str] = set()
    available_by_stratum: dict[str, list[dict[str, Any]]] = {}
    counts: dict[str, dict[str, Any]] = {}
    for stratum in STRATA:
        available = [row for row in candidates[stratum] if _candidate_id(stratum, row) not in selected_ids]
        available_by_stratum[stratum] = available
        counts[stratum] = {
            "eligible_count": len(available),
            "required_count": required,
            "sufficient": len(available) >= required,
        }
        selected_ids.update(_candidate_id(stratum, row) for row in available[:required])
    return available_by_stratum, counts


def metadata_availability_counts(
    documents: dict[str, dict[str, Any]], excluded: Iterable[str], *, required: int = REQUIRED_PER_STRATUM
) -> dict[str, dict[str, Any]]:
    """Return counts only; this result intentionally contains no object IDs."""
    if isinstance(required, bool) or not isinstance(required, int) or required <= 0:
        raise ValueError("required must be a positive integer")
    candidates = _candidate_rows(documents, excluded)
    _, counts = _greedy_available(candidates, required=required)
    return counts


def validate_availability_counts(counts: dict[str, dict[str, Any]], *, required: int = REQUIRED_PER_STRATUM) -> None:
    if any(
        stratum not in counts
        or counts[stratum].get("eligible_count", 0) < required
        or not counts[stratum].get("sufficient", False)
        for stratum in STRATA
    ):
        raise ValueError(f"Fewer than {required} eligible numbered objects in a selector stratum")


def _excluded_ids(root: Path) -> set[str]:
    development_config = json.loads((root / "configs/development30.json").read_text(encoding="utf-8"))
    development_sample = json.loads((root / "data/processed/development30/sample.json").read_text(encoding="utf-8"))
    fresh_sample = json.loads((root / "data/processed/fresh_holdout12/sample.json").read_text(encoding="utf-8"))
    pilot_config = json.loads((root / "configs/eda_pilot_6.json").read_text(encoding="utf-8"))
    force_config = json.loads((root / "configs/b3plus_pilot_6.json").read_text(encoding="utf-8"))
    excluded = {str(value) for value in development_config.get("excluded_object_ids", [])}
    excluded.update(str(obj["id"]) for obj in development_sample.get("objects", []))
    excluded.update(str(obj["id"]) for obj in fresh_sample.get("objects", []))
    excluded.update(str(value) for value in fresh_sample.get("excluded_object_ids", []))
    excluded.update(str(obj["id"]) for obj in pilot_config.get("asteroids", []))
    excluded.add("1620")
    excluded.update(str(body["id"]) for body in force_config.get("small_body_perturbers", []))
    return excluded


def select_objects(documents: dict[str, dict[str, Any]], excluded: Iterable[str]) -> list[dict[str, Any]]:
    """Select exactly four objects per stratum from cached metadata."""
    candidates = _candidate_rows(documents, excluded)
    available_by_stratum, _ = _greedy_available(candidates, required=REQUIRED_PER_STRATUM)
    selected: list[dict[str, Any]] = []
    for stratum in STRATA:
        group = available_by_stratum[stratum][:REQUIRED_PER_STRATUM]
        if len(group) < REQUIRED_PER_STRATUM:
            raise ValueError(f"Fewer than {REQUIRED_PER_STRATUM} eligible numbered objects in {stratum}")
        for row in group:
            object_id = str(row["des"] if stratum in ENCOUNTER_STRATA else row["pdes"]).strip()
            if stratum in ENCOUNTER_STRATA:
                event_day = _event_date(row)
                event = {
                    "body_id": BODY_IDS[stratum],
                    "body_name": stratum,
                    "jd": float(row["jd"]),
                    "cd": str(row["cd"]),
                    "dist_au": float(row["dist"]),
                    "catalogue_row": row,
                }
                start_date = (event_day - dt.timedelta(days=ENCOUNTER_LEAD_DAYS)).isoformat()
            else:
                event = None
                start_date = CONTROL_START_DATE
            selected.append(
                {
                    "id": object_id,
                    "name": str(row.get("full_name", object_id)).strip() or object_id,
                    "stratum": stratum,
                    "split": "selector_holdout24",
                    "start_date": start_date,
                    "event": event,
                    "source_catalogue": (
                        "cad_jupiter_2026_2029_10au" if stratum == "Jupiter" else ENCOUNTER_CATALOGUE
                    )
                    if stratum in ENCOUNTER_STRATA
                    else stratum,
                    "catalogue_row": row,
                }
            )
    if len(selected) != 24 or len({item["id"] for item in selected}) != 24:
        raise ValueError("Selector holdout must contain 24 unique objects")
    return selected


def _method(root: Path) -> tuple[dict[str, Any], str]:
    path = root / METHOD_FREEZE
    if not path.is_file():
        raise FileNotFoundError(f"Required parent method freeze is missing: {path}")
    method = json.loads(path.read_text(encoding="utf-8"))
    check_hashes(root, method.get("hashes"))
    return method, sha(path)


def freeze(root: Path) -> dict[str, Any]:
    """Require the parent method freeze, then immutably write the sample."""
    _, method_sha = _method(root)
    documents = load_catalogues(root)
    excluded = _excluded_ids(root)
    counts = metadata_availability_counts(documents, excluded)
    validate_availability_counts(counts)
    selected = select_objects(documents, excluded)
    data_config = json.loads((root / "configs/eda_pilot_6.json").read_text(encoding="utf-8"))
    sample = {
        "schema_version": 1,
        "seed": SEED,
        "scope": "selector-v3 whole-object holdout24; metadata-only selection",
        "objects": selected,
        "strata": list(STRATA),
        "objects_per_stratum": REQUIRED_PER_STRATUM,
        "availability_counts": counts,
        "excluded_object_ids": sorted(excluded, key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value)),
        "method_freeze_sha256": method_sha,
        "coordinates": data_config["coordinates"],
        "time_scale": "TDB",
        "daily_rows": 366,
        "refined_rows": 1153,
    }
    immutable_json(root / SAMPLE_PATH, sample)
    return sample


def _download_config(root: Path) -> dict[str, Any]:
    config = json.loads((root / "configs/development30.json").read_text(encoding="utf-8"))
    config = dict(config)
    config["raw_directory"] = RAW_DIRECTORY
    config["horizons_days"] = [365]
    config["reference_refinement"] = {"half_window_days": 2, "step": "5 m"}
    return config


def build_selector_jobs(root: Path, sample: dict[str, Any]) -> list[dict[str, Any]]:
    return build_jobs(root, _download_config(root), sample)


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _check_header(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    signature = document.get("signature", {})
    if signature.get("source") != "NASA/JPL Horizons API" or signature.get("version") != "1.2":
        raise ValueError(f"Unexpected Horizons API signature: {path}")
    header = str(document.get("result", "")).split("$$SOE", 1)[0]
    if any(field not in header for field in COORDINATE_FIELDS):
        raise ValueError(f"Coordinate contract mismatch: {path}")
    return document


def _parse_rows(path: Path, target_id: str, target_name: str) -> list[dict[str, Any]]:
    _check_header(path)
    return parse_horizons(path, target_id, target_name)[1]


def _expected_job_rows(job: dict[str, Any], sample_by_id: dict[str, dict[str, Any]], parsed: list[dict[str, Any]]) -> None:
    if job["kind"] != "horizons_asteroid":
        return
    object_id = str(job["target_id"])
    obj = sample_by_id[object_id]
    label = job["path"].stem.rsplit("_", 1)[-1]
    if label == "daily":
        if len(parsed) != 366 or not _regular_grid(parsed, 1.0):
            raise ValueError(f"Daily reference must contain 366 rows over 365 days: {job['path']}")
    elif label == "refined":
        if len(parsed) != 1153:
            raise ValueError(f"Refined reference must contain 1153 rows: {job['path']}")
        if not _regular_grid(parsed, 5.0 / 1440.0):
            raise ValueError(f"Refined reference is not on the 5-minute grid: {job['path']}")
    else:
        raise ValueError(f"Unexpected asteroid job label: {job['path']}")
    if obj["event"] is None and label == "refined":
        raise ValueError(f"Control object unexpectedly has a refined job: {object_id}")


def _regular_grid(parsed: list[dict[str, Any]], expected_step: float) -> bool:
    """Accept only a complete grid, allowing two endpoint ULPs at large JD."""
    if len(parsed) < 2:
        return False
    for index in range(len(parsed) - 1):
        left, right = parsed[index], parsed[index + 1]
        left_jd = float(left["epoch_jd_tdb"])
        right_jd = float(right["epoch_jd_tdb"])
        tolerance = 2.0 * max(math.ulp(left_jd), math.ulp(right_jd))
        if abs((right_jd - left_jd) - expected_step) > tolerance:
            return False
    return True


def _check_ng_pair(root: Path, obj: dict[str, Any], daily: Path, refined: Path | None, start_jd: float) -> dict[str, Any]:
    daily_ng = load_ng_input(daily)
    if daily_ng.parameters is not None and (
        daily_ng.available_from_jd_tdb is None or daily_ng.available_from_jd_tdb > start_jd
    ):
        raise ValueError(f"NG parameters are not demonstrably available before start: {obj['id']}")
    if refined is not None:
        refined_ng = load_ng_input(refined)
        if daily_ng.parameters != refined_ng.parameters:
            raise ValueError(f"Daily/refined NG parameters disagree: {obj['id']}")
    return {
        "status": daily_ng.status(start_jd),
        "source": daily_ng.source,
        "source_sha256": daily_ng.source_sha256,
        "available_from_jd_tdb": daily_ng.available_from_jd_tdb,
        "availability_basis": daily_ng.availability_basis,
    }


def validate_downloads(root: Path, sample: dict[str, Any], jobs: list[dict[str, Any]], records: list[dict[str, Any]]) -> dict[str, Any]:
    sample_by_id = {str(obj["id"]): obj for obj in sample["objects"]}
    by_object: dict[str, dict[str, Path]] = {key: {} for key in sample_by_id}
    for job, record in zip(jobs, records, strict=True):
        path = root / record["path"]
        parsed = _parse_rows(path, str(job["target_id"]), str(job["target_name"]))
        if record.get("rows") != len(parsed):
            raise ValueError(f"Manifest row count mismatch: {record['path']}")
        _expected_job_rows(job, sample_by_id, parsed)
        if job["kind"] == "horizons_asteroid":
            label = job["path"].stem.rsplit("_", 1)[-1]
            by_object[str(job["target_id"])][label] = path
    shared = 0
    ng_statuses = {}
    for object_id, obj in sample_by_id.items():
        daily = by_object[object_id].get("daily")
        if daily is None:
            raise ValueError(f"Missing daily object reference: {object_id}")
        daily_rows = _parse_rows(daily, object_id, str(obj["name"]))
        refined = by_object[object_id].get("refined")
        if obj["event"] is not None and refined is None:
            raise ValueError(f"Missing refined event reference: {object_id}")
        refined_rows = _parse_rows(refined, object_id, str(obj["name"])) if refined else None
        if refined_rows is not None:
            daily_by_epoch = {row["epoch_jd_tdb"]: row for row in daily_rows}
            common = 0
            for row in refined_rows:
                other = daily_by_epoch.get(row["epoch_jd_tdb"])
                if other is not None:
                    common += 1
                    if row["r"] != other["r"] or row["v"] != other["v"]:
                        raise ValueError(f"Daily/refined shared state mismatch: {object_id}")
            if common == 0:
                raise ValueError(f"Daily/refined grids have no shared nodes: {object_id}")
            shared += common
        ng_statuses[object_id] = _check_ng_pair(root, obj, daily, refined, daily_rows[0]["epoch_jd_tdb"])
    return {"objects": len(sample_by_id), "jobs": len(jobs), "shared_daily_refined_nodes": shared, "ng": ng_statuses}


def download(root: Path) -> dict[str, Any]:
    sample = freeze(root)
    jobs = build_selector_jobs(root, sample)
    manifest_path = root / MANIFEST_PATH
    previous = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {"files": []}
    previous_files = previous.get("files", [])
    known = {str(record["path"]): record for record in previous_files}
    if len(known) != len(previous_files):
        raise ValueError("Selector manifest contains duplicate paths")
    expected_paths = {_relative(root, job["path"]) for job in jobs}
    if set(known) - expected_paths:
        raise ValueError("Selector manifest contains paths outside the current job set")
    if previous.get("sample_sha256") is not None and previous["sample_sha256"] != sha(root / SAMPLE_PATH):
        raise ValueError("Selector manifest sample hash differs")
    if previous.get("method_freeze_sha256") is not None and previous["method_freeze_sha256"] != sample["method_freeze_sha256"]:
        raise ValueError("Selector manifest method-freeze hash differs")
    records: list[dict[str, Any]] = []
    created = previous.get("created_utc", dt.datetime.now(dt.timezone.utc).isoformat())
    for index, job in enumerate(jobs, 1):
        relative = _relative(root, job["path"])
        if relative in known:
            record = dict(known[relative])
            if (
                record.get("kind") != job["kind"]
                or str(record.get("target_id")) != str(job["target_id"])
                or record.get("source_url") != job["url"]
                or not job["path"].is_file()
                or record.get("sha256") != sha(job["path"])
                or record.get("bytes") != job["path"].stat().st_size
            ):
                raise ValueError(f"Raw provenance mismatch: {relative}")
        else:
            if job["path"].exists():
                raise ValueError(f"Orphan raw file without selector manifest: {relative}")
            record = download_one(job)
            record.pop("reused", None)
            record["path"] = relative
            record["retrieved_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
            document = _check_header(job["path"])
            parsed = parse_horizons(job["path"], str(job["target_id"]), str(job["target_name"]))[1]
            record.update(
                {
                    "signature": document["signature"],
                    "rows": len(parsed),
                    "first_jd_tdb": parsed[0]["epoch_jd_tdb"],
                    "last_jd_tdb": parsed[-1]["epoch_jd_tdb"],
                    "coordinates": sample["coordinates"],
                    "time_scale": "TDB",
                }
            )
        records.append(record)
        atomic_json(
            manifest_path,
            {
                "schema_version": 1,
                "created_utc": created,
                "sample_sha256": sha(root / SAMPLE_PATH),
                "method_freeze_sha256": sample["method_freeze_sha256"],
                "complete": False,
                "files": records,
            },
        )
        print(json.dumps({"downloaded": index, "total": len(jobs), "path": relative}, sort_keys=True), flush=True)
    validation = validate_downloads(root, sample, jobs, records)
    manifest = {
        "schema_version": 1,
        "created_utc": created,
        "sample_sha256": sha(root / SAMPLE_PATH),
        "method_freeze_sha256": sample["method_freeze_sha256"],
        "complete": True,
        "files": records,
        "validation": validation,
    }
    atomic_json(manifest_path, manifest)
    return {"files": len(records), "complete": True, "validation": validation}


def _metadata_counts(root: Path) -> dict[str, dict[str, Any]]:
    documents = load_catalogues(root)
    return metadata_availability_counts(documents, _excluded_ids(root))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--counts", action="store_true", help="Print counts only; never prints selected IDs")
    actions.add_argument("--freeze", action="store_true", help="Write the immutable sample after method freeze")
    actions.add_argument("--download", action="store_true", help="Download selector data serially")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.counts:
        result = _metadata_counts(root)
    elif args.freeze:
        result = freeze(root)
    else:
        result = download(root)
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()

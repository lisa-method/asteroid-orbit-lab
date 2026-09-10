"""Offline provenance audit for the selector-v3 raw inputs.

The audit reads every checksum manifest and the cached Horizons documents.  It
does not invoke a downloader, an integrator, or any selector code.  A result is
written only after every hash, size, path exclusion, reuse relation, and
holdout table shape has passed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from collections import Counter
from typing import Any

from ng_inputs_v2 import load_ng_input
from run_eda import parse_horizons


SELECTOR_MANIFEST = "data/checksums/selector_holdout24_manifest.json"
SELECTOR_SAMPLE = "data/processed/selector_holdout24/sample.json"
SELECTOR_RAW = "data/raw/selector_holdout24"
OUTPUT = "outputs/selector_holdout24/raw_verification.json"
VERIFIER = "src/audit_selector_raw_v3.py"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def immutable_json(path: Path, value: Any) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise RuntimeError(f"immutable audit differs from existing file: {path}")
        return
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _path(root: Path, value: object) -> Path:
    candidate = Path(str(value))
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"manifest path escapes project root: {value}") from exc
    return candidate


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _entries(document: dict[str, Any], source: Path) -> list[dict[str, Any]]:
    values = document.get("files")
    if values is None:
        values = document.get("downloads")
    if not isinstance(values, list) or not values:
        raise ValueError(f"manifest has no non-empty files/downloads list: {source}")
    if any(not isinstance(item, dict) for item in values):
        raise ValueError(f"manifest contains a malformed record: {source}")
    return values


def _git_excluded(root: Path, relative: str) -> None:
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "--", relative], cwd=root,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if tracked.returncode == 0:
        raise ValueError(f"manifest raw path is tracked: {relative}")
    ignored = subprocess.run(["git", "check-ignore", "--no-index", "--quiet", "--", relative], cwd=root,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if ignored.returncode != 0:
        raise ValueError(f"manifest raw path is not gitignored: {relative}")


def manifest_inventory(root: Path) -> dict[str, Any]:
    manifests = sorted((root / "data/checksums").glob("*manifest.json"))
    if not manifests:
        raise FileNotFoundError("no checksum manifests")
    records: list[dict[str, Any]] = []
    by_path: dict[str, tuple[str, int]] = {}
    counts: Counter[str] = Counter()
    manifest_hashes: dict[str, str] = {}
    for manifest in manifests:
        name = _relative(root, manifest)
        manifest_hashes[name] = sha(manifest)
        document = json.loads(manifest.read_text(encoding="utf-8"))
        entries = _entries(document, manifest)
        counts[manifest.name] = len(entries)
        for entry in entries:
            if not isinstance(entry.get("path"), str) or not entry["path"]:
                raise ValueError(f"manifest record lacks path: {manifest}")
            if not isinstance(entry.get("sha256"), str) or len(entry["sha256"]) != 64:
                raise ValueError(f"manifest record lacks SHA-256: {manifest}")
            try:
                size = int(entry["bytes"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"manifest record lacks byte size: {manifest}") from exc
            if size < 0:
                raise ValueError(f"negative manifest byte size: {manifest}")
            path = _path(root, entry["path"])
            relative = _relative(root, path)
            if not path.is_file() or path.stat().st_size != size or sha(path).lower() != entry["sha256"].lower():
                raise ValueError(f"manifest raw hash/size mismatch: {relative}")
            _git_excluded(root, relative)
            pair = (entry["sha256"].lower(), size)
            previous = by_path.get(relative)
            if previous is not None and previous != pair:
                raise ValueError(f"conflicting manifest records for {relative}")
            by_path[relative] = pair
            records.append({"path": relative, "sha256": pair[0], "bytes": size, "manifest": manifest.name,
                            "kind": entry.get("kind"), "source_url": entry.get("source_url")})
    if len(by_path) != 251:
        raise ValueError(f"expected 251 unique raw paths, found {len(by_path)}")
    selector_records = [row for row in records if row["manifest"] == Path(SELECTOR_MANIFEST).name]
    if len(selector_records) != 49:
        raise ValueError(f"expected 49 selector records, found {len(selector_records)}")
    return {"manifest_count": len(manifests), "manifest_record_count": len(records),
            "unique_raw_paths": len(by_path), "unique_raw_sha256_size": len(set(by_path.values())),
            "old_unique_paths": len(by_path) - len({row["path"] for row in selector_records}),
            "selector_records": len(selector_records), "manifests": dict(sorted(counts.items())),
            "manifest_sha256": dict(sorted(manifest_hashes.items()))}


def _selector_manifest(root: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    sample = json.loads((root / SELECTOR_SAMPLE).read_text(encoding="utf-8"))
    manifest_path = root / SELECTOR_MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("complete") is not True or manifest.get("sample_sha256") != sha(root / SELECTOR_SAMPLE):
        raise ValueError("selector manifest is incomplete or sample hash differs")
    if manifest.get("method_freeze_sha256") != sample.get("method_freeze_sha256"):
        raise ValueError("selector sample/manifest method hash differs")
    entries = _entries(manifest, manifest_path)
    if len(entries) != 49:
        raise ValueError("selector manifest must contain 49 records")
    if len(sample.get("objects", [])) != 24 or len({str(item.get("id")) for item in sample["objects"]}) != 24:
        raise ValueError("selector sample must contain 24 unique objects")
    return sample, manifest, entries


def selector_tables(root: Path, sample: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
    by_relative = {_relative(root, _path(root, item["path"])): item for item in entries}
    planets = [item for item in entries if item.get("kind") == "horizons_planet"]
    asteroids = [item for item in entries if item.get("kind") == "horizons_asteroid"]
    if len(planets) != 9 or len(asteroids) != 40:
        raise ValueError(f"selector kinds must be 9 planets/40 asteroid tables: {len(planets)}/{len(asteroids)}")
    old_manifest_path = root / "data/checksums/development30_data_manifest.json"
    old_document = json.loads(old_manifest_path.read_text(encoding="utf-8"))
    old_entries = {str(item.get("path")): item for item in _entries(old_document, old_manifest_path)}
    reused = []
    for item in planets:
        source = item.get("reused_from")
        if not isinstance(source, str) or source not in old_entries:
            raise ValueError(f"reused selector planet lacks development30 source: {item.get('path')}")
        old = old_entries[source]
        if item["sha256"].lower() != str(old["sha256"]).lower() or int(item["bytes"]) != int(old["bytes"]):
            raise ValueError(f"reused planet hash/size differs from original: {item['path']}")
        if item.get("source_url") != old.get("source_url"):
            raise ValueError(f"reused planet source URL differs from original: {item['path']}")
        if item.get("source_manifest_sha256") != sha(old_manifest_path):
            raise ValueError(f"reused planet source manifest hash differs: {item['path']}")
        reused.append({"path": item["path"], "reused_from": source, "sha256": item["sha256"]})
    sample_by_id = {str(obj["id"]): obj for obj in sample["objects"]}
    grouped: dict[str, dict[str, tuple[dict[str, Any], list[dict[str, Any]]]]] = {}
    shared_nodes = 0
    ng_statuses: dict[str, str] = {}
    for item in asteroids:
        path = _path(root, item["path"])
        object_id = str(item["target_id"])
        if object_id not in sample_by_id:
            raise ValueError(f"selector asteroid is absent from sample: {object_id}")
        label = "refined" if str(path).endswith("_refined.json") else "daily" if str(path).endswith("_daily.json") else "other"
        if label == "other":
            raise ValueError(f"unexpected asteroid table name: {path}")
        parsed = parse_horizons(path, object_id, str(sample_by_id[object_id]["name"]))[1]
        expected_rows = 1153 if label == "refined" else 366
        if len(parsed) != expected_rows or int(item.get("rows", -1)) != expected_rows:
            raise ValueError(f"unexpected {label} rows for {object_id}")
        grouped.setdefault(object_id, {})[label] = (item, parsed)
    if set(grouped) != set(sample_by_id) or any(set(value) != ({"daily", "refined"} if sample_by_id[key].get("event") else {"daily"}) for key, value in grouped.items()):
        raise ValueError("selector daily/refined table set differs from frozen sample")
    for object_id, tables in grouped.items():
        daily_item, daily = tables["daily"]
        ng = load_ng_input(_path(root, daily_item["path"]))
        ng_statuses[object_id] = ng.status(float(daily[0]["epoch_jd_tdb"]))
        if "refined" in tables:
            refined_item, refined = tables["refined"]
            daily_by_epoch = {float(row["epoch_jd_tdb"]): row for row in daily}
            common = 0
            for row in refined:
                other = daily_by_epoch.get(float(row["epoch_jd_tdb"]))
                if other is not None:
                    common += 1
                    if row["r"] != other["r"] or row["v"] != other["v"]:
                        raise ValueError(f"daily/refined state mismatch: {object_id}")
            if common == 0:
                raise ValueError(f"no shared daily/refined epochs: {object_id}")
            shared_nodes += common
            refined_ng = load_ng_input(_path(root, refined_item["path"]))
            if ng.parameters != refined_ng.parameters:
                raise ValueError(f"daily/refined NG parameters differ: {object_id}")
    return {"selector_planets": len(planets), "selector_asteroid_daily": sum("daily" in item for item in grouped.values()),
            "selector_asteroid_refined": sum("refined" in item for item in grouped.values()),
            "selector_asteroid_tables": len(asteroids), "shared_daily_refined_nodes": shared_nodes,
            "reused_planets": reused, "ng_statuses": dict(sorted(ng_statuses.items()))}


def audit(root: Path) -> dict[str, Any]:
    inventory = manifest_inventory(root)
    sample, manifest, entries = _selector_manifest(root)
    tables = selector_tables(root, sample, entries)
    result = {"schema_version": 1, "scope": "selector-v3 raw provenance audit", "passed": True,
              "verifier_sha256": sha(root / VERIFIER), "selector_manifest_sha256": sha(root / SELECTOR_MANIFEST),
              "selector_sample_sha256": sha(root / SELECTOR_SAMPLE), "inventory": inventory, "selector": tables,
              "runtime": {"python_version": sys.version, "python_implementation": sys.implementation.name,
                          "platform": sys.platform, "platform_release": platform.release(), "machine": platform.machine()}}
    immutable_json(root / OUTPUT, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    result = audit(args.root.resolve())
    print(json.dumps({"passed": result["passed"], "unique_raw_paths": result["inventory"]["unique_raw_paths"],
                      "selector_tables": result["selector"]["selector_asteroid_tables"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["audit", "immutable_json", "manifest_inventory", "selector_tables"]

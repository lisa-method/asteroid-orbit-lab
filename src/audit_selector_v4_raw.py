"""Offline audit of every raw manifest and the forty new target tables."""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess

from audit_selector_raw_v3 import _entries, _path
from prepare_selector_v4_holdout24 import (
    MANIFEST_PATH, SAMPLE_PATH, METHOD_FREEZE, build_selector_jobs,
    validate_downloads, check_hashes, immutable_json, sha,
)
from run_force_models_v2 import _runtime_environment


def audit(root):
    manifests = sorted((root / "data/checksums").glob("*manifest.json"))
    paths, manifest_hashes = {}, {}
    entry_count = 0
    for path in manifests:
        manifest_hashes[path.relative_to(root).as_posix()] = sha(path)
        for entry in _entries(json.loads(path.read_text()), path):
            raw = _path(root, entry["path"])
            relative = raw.relative_to(root).as_posix()
            signature = (entry["sha256"], entry["bytes"])
            if not raw.is_file() or (sha(raw), raw.stat().st_size) != signature:
                raise ValueError(f"Raw SHA/size mismatch: {relative}")
            if relative in paths and paths[relative] != signature:
                raise ValueError(f"Conflicting raw provenance: {relative}")
            paths[relative] = signature
            entry_count += 1
    ignored = subprocess.run(["git", "check-ignore", "--stdin"], cwd=root, input="\n".join(paths)+"\n",
                             text=True, capture_output=True)
    tracked = subprocess.run(["git", "ls-files"], cwd=root, text=True, capture_output=True, check=True)
    if set(ignored.stdout.splitlines()) != set(paths) or set(tracked.stdout.splitlines()) & paths.keys():
        raise ValueError("Raw data must be ignored and untracked")
    sample = json.loads((root / SAMPLE_PATH).read_text())
    manifest = json.loads((root / MANIFEST_PATH).read_text())
    method = json.loads((root / METHOD_FREEZE).read_text())
    check_hashes(root, method["hashes"])
    if method["runtime"] != _runtime_environment():
        raise ValueError("Runtime differs from frozen method")
    if manifest.get("complete") is not True or manifest["sample_sha256"] != sha(root / SAMPLE_PATH):
        raise ValueError("Incomplete sample/manifest")
    if manifest["method_freeze_sha256"] != sample["method_freeze_sha256"] or sample["method_freeze_sha256"] != sha(root / METHOD_FREEZE):
        raise ValueError("Method freeze differs")
    jobs = build_selector_jobs(root, sample)
    if len(jobs) != len(manifest["files"]) or len(jobs) != 40:
        raise ValueError("Require precisely forty new target tables")
    for job, record in zip(jobs, manifest["files"], strict=True):
        if job["path"].relative_to(root).as_posix() != record["path"] or record["source_url"] != job["url"]:
            raise ValueError("Saved raw request differs from frozen job")
    validated = validate_downloads(root, sample, jobs, manifest["files"])
    # availability_basis embeds the manifest hash seen at retrieval, so compare
    # stable availability fields rather than the self-referential hash string.
    for oid, current in validated["ng"].items():
        saved = manifest["validation"]["ng"][oid]
        for field in ("status", "source_sha256", "available_from_jd_tdb"):
            if current[field] != saved[field]:
                raise ValueError("NG availability no longer reproduces")
    if len(paths) != 291 or len(manifests) != 21 or validated["shared_daily_refined_nodes"] != 80:
        raise ValueError("Unexpected raw inventory or shared grid size")
    value = {"passed": True, "unique_raw_paths": len(paths), "manifest_count": len(manifests),
             "manifest_record_count": entry_count, "new_target_files": len(jobs),
             "shared_daily_refined_nodes": 80, "ng_status_counts": dict(Counter(v["status"] for v in validated["ng"].values())),
             "ng": validated["ng"], "manifest_sha256": manifest_hashes,
             "method_freeze_sha256": sha(root / METHOD_FREEZE), "runtime": _runtime_environment(),
             "verifier_sha256": sha(Path(__file__)), "git_ignored_and_untracked": True}
    immutable_json(root / "outputs/selector_v4_holdout24/raw_verification.json", value)
    return {k: value[k] for k in ("passed", "unique_raw_paths", "manifest_count", "new_target_files", "ng_status_counts")}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=Path("."))
    print(json.dumps(audit(p.parse_args().root.resolve()), indent=2))

"""Offline verifier for the frozen Apophis gravity-convention factorial."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from typing import Any

from orbit_baselines import State, norm, subtract
from run_eda import load_json, parse_horizons
from run_physics_baselines import errors, state_from_row
import verify_apophis_reference_time as reference_verifier
import verify_precise_propagation as propagation_verifier


SUMMARY_FIELDS = reference_verifier.SUMMARY_FIELDS
REFERENCE_NAMES = {"annual_daily", "annual_hourly", "short_daily", "short_hourly", "old_daily", "old_refined", "recent_refined", "long_repeat"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _state(value: Any) -> State:
    return reference_verifier._state(value)


def _flat(state: State) -> list[float]:
    return [*state.position, *state.velocity]


def _shift(left: list[State], right: list[State], au_km: float, day_s: float) -> dict[str, float]:
    return reference_verifier._shift(left, right, au_km, day_s)


def _write_immutable(path: Path, value: Any) -> None:
    if path.exists():
        if load_json(path) != value:
            raise RuntimeError(f"immutable artifact differs: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _record_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _check_freeze(root: Path, matrix: dict[str, Any], freeze_path: Path) -> dict[str, Any]:
    freeze = load_json(freeze_path)
    if matrix.get("fingerprint") != freeze.get("fingerprint") or matrix.get("freeze_sha256") != _sha(freeze_path):
        raise ValueError("matrix/freeze fingerprint mismatch")
    hashes = freeze.get("hashes")
    runtime = freeze.get("runtime")
    if not isinstance(hashes, dict) or not isinstance(runtime, dict):
        raise ValueError("freeze lacks hashes/runtime")
    for relative, digest in hashes.items():
        path = _record_path(root, str(relative))
        if not path.is_file() or _sha(path) != str(digest):
            raise ValueError(f"frozen source hash mismatch: {relative}")
    current_runtime = {"executable": sys.executable, "version": sys.version, "platform": platform.platform()}
    if runtime != current_runtime:
        raise ValueError("current runtime differs from frozen runtime")
    payload = {"hashes": hashes, "runtime": runtime}
    if hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest() != freeze.get("fingerprint"):
        raise ValueError("freeze fingerprint recomputation mismatch")
    return freeze


def _manifest_inventory(root: Path) -> dict[str, Any]:
    """Validate every manifest schema and every raw path, including the new header."""
    manifest_paths = sorted((root / "data/checksums").glob("*manifest.json"))
    all_records: list[tuple[str, str, int, str]] = []
    for manifest_path in manifest_paths:
        manifest = load_json(manifest_path)
        entries = manifest.get("files")
        if entries is None:
            entries = manifest.get("downloads")
        if not isinstance(entries, list):
            raise ValueError(f"manifest has no files/downloads list: {manifest_path.name}")
        for item in entries:
            if not isinstance(item, dict) or not all(key in item for key in ("path", "sha256", "bytes")):
                raise ValueError(f"incomplete manifest record: {manifest_path.name}")
            raw_name = str(item["path"])
            raw = _record_path(root, raw_name)
            expected_sha = str(item["sha256"]).lower()
            expected_bytes = int(item["bytes"])
            if not raw.is_file() or _sha(raw) != expected_sha or raw.stat().st_size != expected_bytes:
                raise ValueError(f"manifest raw mismatch: {raw_name}")
            relative = raw.relative_to(root).as_posix() if raw.is_relative_to(root) else raw_name
            if subprocess.run(["git", "ls-files", "--error-unmatch", "--", relative], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0:
                raise ValueError(f"manifest raw is tracked: {relative}")
            if subprocess.run(["git", "check-ignore", "--no-index", "--quiet", "--", relative], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode != 0:
                raise ValueError(f"manifest raw is not gitignored: {relative}")
            all_records.append((relative, expected_sha, expected_bytes, manifest_path.name))
    if not any(name == "gravity_conventions_manifest.json" for _, _, _, name in all_records):
        raise ValueError("gravity_conventions_manifest.json was not found")
    unique_paths = {path for path, _, _, _ in all_records}
    if len(unique_paths) != 138:
        raise ValueError(f"expected 138 unique raw paths, found {len(unique_paths)}")
    return {
        "manifest_count": len(manifest_paths),
        "manifest_records": len(all_records),
        "unique_raw_paths": len(unique_paths),
        "raw_sha256_size_records": len({(sha, size) for _, sha, size, _ in all_records}),
        "manifests": {name: sum(1 for _, _, _, source in all_records if source == name) for name in sorted({source for _, _, _, source in all_records})},
    }


def _load_teachers(root: Path) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, Any]]:
    previous = load_json(root / "outputs/apophis_reference_time/matrix.json")
    annual, references, data = propagation_verifier._load_teachers(root, previous)
    if len(annual) != 797 or set(references) != REFERENCE_NAMES:
        raise ValueError("teacher rows are not the frozen annual/eight-reference set")
    return annual, references, data


def _expected_setting(setting: dict[str, Any], row: dict[str, Any]) -> None:
    saved = row.get("setting")
    if row["solver"] == "dopri54":
        if not isinstance(saved, dict):
            raise ValueError(f"DP setting is not a mapping: {row['key']}")
        normalized = {key: value for key, value in saved.items() if key != "solver"}
        expected = {key: setting[key] for key in ("label", "rtol", "atol_position", "atol_velocity")}
        if normalized != expected:
            raise ValueError(f"DP setting mismatch: {row['key']}")
    else:
        if isinstance(saved, dict):
            if saved != setting:
                raise ValueError(f"RK4 setting mismatch: {row['key']}")
        elif isinstance(saved, bool) or not isinstance(saved, (int, float)) or float(saved) != float(setting["scale"]):
            raise ValueError(f"RK4 setting mismatch: {row['key']}")


def _check_record_metadata(row: dict[str, Any], arm: dict[str, Any], settings: dict[str, dict[str, Any]]) -> None:
    key = row.get("key")
    if not isinstance(key, str) or key != f"{row.get('arm')}__{key.rsplit('__', 1)[-1]}":
        raise ValueError(f"noncanonical gravity record key: {key}")
    label = key.rsplit("__", 1)[-1]
    if label not in settings:
        raise ValueError(f"unknown gravity setting: {key}")
    expected_mode = "precise_dp" if label in {"extreme", "ultra"} else "compensated"
    if row.get("arm") != arm["id"] or row.get("initial_source") != "annual_hourly" or row.get("numerical_mode") != expected_mode:
        raise ValueError(f"gravity record metadata mismatch: {key}")
    if row.get("force_specification") != arm:
        raise ValueError(f"force specification mismatch: {key}")
    expected_solver = "dopri54" if expected_mode == "precise_dp" else "rk4"
    if row.get("solver") != expected_solver or row.get("mode") != "relative_calendar_knots":
        raise ValueError(f"solver/mode mismatch: {key}")
    if row.get("accepted_time_basis") != "relative_days_since_start":
        raise ValueError(f"accepted time basis mismatch: {key}")
    _expected_setting(settings[label], row)
    stats = row.get("solver_stats")
    if not isinstance(stats, dict) or stats.get("compensated") is not True:
        raise ValueError(f"compensated numerical mode missing: {key}")
    if "endpoint_consistent" in stats and stats["endpoint_consistent"] is not True:
        raise ValueError(f"endpoint consistency flag failed: {key}")
    if row["solver"] == "rk4":
        step_count = stats.get("rk4_steps", stats.get("steps"))
        if isinstance(step_count, bool) or not isinstance(step_count, (int, float)) or int(step_count) != step_count:
            raise ValueError(f"RK4 step count missing: {key}")


def _source_row(root: Path, reused: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if set(reused) != {"matrix", "key", "sha256"}:
        raise ValueError("reused_from must contain exactly matrix/key/sha256")
    matrix_path = _record_path(root, str(reused["matrix"]))
    if not matrix_path.is_file() or _sha(matrix_path) != str(reused["sha256"]):
        raise ValueError("reused_from matrix hash mismatch")
    matrix = load_json(matrix_path)
    source = next((row for row in matrix.get("records", []) if row.get("key") == reused["key"]), None)
    if not isinstance(source, dict):
        raise ValueError(f"reused source record missing: {reused['key']}")
    return source, str(matrix_path)


def _check_reused_identity(root: Path, row: dict[str, Any], source: dict[str, Any]) -> None:
    ignored = {"key", "fingerprint", "reused_from", "force_specification"}
    current = {key: value for key, value in row.items() if key not in ignored}
    original = {key: value for key, value in source.items() if key not in ignored}
    if isinstance(current.get("setting"), dict):
        current["setting"] = {key: value for key, value in current["setting"].items() if key != "solver"}
    if isinstance(original.get("setting"), dict):
        original["setting"] = {key: value for key, value in original["setting"].items() if key != "solver"}
    elif isinstance(original.get("setting"), (int, float)) and not isinstance(original.get("setting"), bool):
        current_setting = current.get("setting")
        if not isinstance(current_setting, dict) or float(current_setting.get("scale", math.nan)) != float(original["setting"]):
            raise ValueError(f"baseline RK4 setting differs from source: {row['key']}")
        current.pop("setting", None)
        original.pop("setting", None)
    if current != original:
        raise ValueError(f"baseline reused trace differs from source: {row['key']}")


def _diagnostic_errors(row: dict[str, Any], annual: list[dict[str, Any]], references: dict[str, list[dict[str, Any]]], au_km: float, day_s: float, days: list[Any]) -> dict[str, dict[str, float]]:
    predicted = {item["epoch_tdb"]: _state(state) for item, state in zip(annual, row["requested_states"])}
    annual_origin = float(annual[0]["epoch_jd_tdb"])
    daily = references["annual_daily"]
    result: dict[str, dict[str, float]] = {}
    for requested_day in days:
        day = float(requested_day)
        matches = [item for item in daily if abs(float(item["epoch_jd_tdb"]) - annual_origin - day) <= 1.0e-8]
        if len(matches) != 1 or matches[0]["epoch_tdb"] not in predicted:
            raise ValueError(f"diagnostic day is absent from annual_daily: {row['key']} day={day}")
        position_error, velocity_error = errors(predicted[matches[0]["epoch_tdb"]], state_from_row(matches[0]), au_km, day_s)
        result[str(requested_day)] = {"position_error_km": position_error, "velocity_error_m_s": velocity_error}
    return result


def _vector_effect(arm: dict[str, Any], baseline: dict[str, Any]) -> list[State]:
    return [_state([left - right for left, right in zip(a, b)]) for a, b in zip(arm["requested_states"], baseline["requested_states"])]


def _analysis(records: list[dict[str, Any]], annual: list[dict[str, Any]], references: dict[str, list[dict[str, Any]]], au_km: float, day_s: float, diagnostic_days: list[Any], cross_solver_budget_m: float) -> dict[str, Any]:
    by_key = {row["key"]: row for row in records}
    dp_differences = []
    cross_solver = []
    effects = []
    for arm in sorted({row["arm"] for row in records}):
        extreme = by_key[f"{arm}__extreme"]
        ultra = by_key[f"{arm}__ultra"]
        rk4 = by_key[f"{arm}__rk4_fine"]
        dp_differences.append({"arm": arm, **_shift([_state(s) for s in extreme["requested_states"]], [_state(s) for s in ultra["requested_states"]], au_km, day_s)})
        cross_solver.append({"arm": arm, "dp_label": "ultra", "dp_key": ultra["key"], "rk4_key": rk4["key"], "criterion": True, **_shift([_state(s) for s in ultra["requested_states"]], [_state(s) for s in rk4["requested_states"]], au_km, day_s)})
        cross_solver.append({"arm": arm, "dp_label": "extreme", "dp_key": extreme["key"], "rk4_key": rk4["key"], "criterion": False, **_shift([_state(s) for s in extreme["requested_states"]], [_state(s) for s in rk4["requested_states"]], au_km, day_s)})
        if arm != "baseline":
            base_ultra, base_rk4 = by_key["baseline__ultra"], by_key["baseline__rk4_fine"]
            ultra_effect = _vector_effect(ultra, base_ultra)
            rk4_effect = _vector_effect(rk4, base_rk4)
            effects.append({"arm": arm, "ultra_effect_vs_baseline": _shift(ultra_effect, [_state([0.0] * 6) for _ in ultra_effect], au_km, day_s), "rk4_effect_vs_baseline": _shift(rk4_effect, [_state([0.0] * 6) for _ in rk4_effect], au_km, day_s), "ultra_vs_rk4_effect_vector": _shift(ultra_effect, rk4_effect, au_km, day_s)})
    teacher = []
    for row in records:
        diagnostics = _diagnostic_errors(row, annual, references, au_km, day_s, diagnostic_days)
        origin = diagnostics[str(diagnostic_days[0])]
        growth = {day: {"position_error_growth_km": values["position_error_km"] - origin["position_error_km"], "velocity_error_growth_m_s": values["velocity_error_m_s"] - origin["velocity_error_m_s"]} for day, values in diagnostics.items()}
        teacher.append({"key": row["key"], "arm": row["arm"], "setting": row["setting"], "matched_annual_daily": row["reference_comparisons"]["annual_daily"], "diagnostic_days": diagnostics, "selected_day_growth": growth})
    return {"schema_version": 1, "dp_setting_differences": dp_differences, "cross_solver_differences": cross_solver, "force_effect_vectors": effects, "matched_annual_daily_errors": teacher, "cross_solver_budget_m": cross_solver_budget_m, "cross_solver_criterion": [item for item in cross_solver if item["criterion"]], "empirical_only": True}


def _de_constants(text: str) -> dict[str, str]:
    blocks = re.split(r"(?m)^\s*GROUP\s+", text)
    groups: dict[str, list[str]] = {}
    for block in blocks:
        tokens = block.split()
        if tokens and tokens[0] in {"1040", "1041"}:
            groups[tokens[0]] = tokens[1:]
    if set(groups) != {"1040", "1041"} or not groups["1040"] or groups["1040"][0] != groups["1041"][0]:
        raise ValueError("malformed DE441 groups 1040/1041")
    count = int(groups["1040"][0])
    names, values = groups["1040"][1:], groups["1041"][1:]
    if count != len(names) or len(names) != len(values):
        raise ValueError("DE441 group field counts disagree")
    return dict(zip(names, values, strict=True))


def _header_inputs(root: Path, freeze: dict[str, Any], config: dict[str, Any], inputs: dict[str, Any], au_km: float) -> dict[str, Any]:
    header_path = _record_path(root, config["header"])
    if not header_path.is_file():
        raise ValueError("DE441 header is missing")
    de = _de_constants(header_path.read_text(encoding="utf-8"))
    required = ("J2SUN", "ASUN", "J2E", "RE", "GMS", "AU", "CLIGHT")
    if set(inputs.get("de_original_strings", {})) != set(required) or any(inputs["de_original_strings"][key] != de[key] for key in required):
        raise ValueError("DE original strings differ from independently parsed groups")
    expected_solar = {"j2": float(de["J2SUN"].replace("D", "E")), "radius_au": float(de["ASUN"].replace("D", "E")) / au_km}
    solar = inputs.get("solar_constants")
    if not isinstance(solar, dict) or solar.get("j2") != expected_solar["j2"] or solar.get("radius_au") != expected_solar["radius_au"] or solar.get("pole_ra_deg") != config["solar_pole_ra_deg"] or solar.get("pole_dec_deg") != config["solar_pole_dec_deg"]:
        raise ValueError("solar constants/provenance differ from DE header/config")
    expected_headers = []
    for relative in sorted(name for name in freeze["hashes"] if str(name).startswith("data/raw/") and str(name).endswith(".json")):
        path = root / relative
        document = load_json(path)
        text = str(document.get("result", "")).split("$$SOE")[0]
        if "Apophis" not in text or "99942" not in text:
            continue
        models = sorted(set(match.strip() for match in re.findall(r"EOBL_MOD\s*=\s*([^\n]+)", text)))
        limits = sorted(set(float(match) for match in re.findall(r"EOBL_LIM\s*=\s*([0-9.]+)", text)))
        if models != ["2x0 (J2 only)"] or limits != [1.0]:
            raise ValueError(f"unexpected Apophis EOBL convention: {relative}")
        expected_headers.append({"path": relative, "sha256": _sha(path), "earth_models": models, "earth_cutoffs_au": limits})
    if inputs.get("headers") != expected_headers or len(expected_headers) != 8:
        raise ValueError("inputs Apophis header/EOBL inventory mismatch")
    manifest = load_json(root / config["manifest"])
    entries = manifest.get("files", [])
    header_entries = [item for item in entries if item.get("path") == config["header"]]
    if len(header_entries) != 1 or header_entries[0]["sha256"] != _sha(header_path) or int(header_entries[0]["bytes"]) != header_path.stat().st_size:
        raise ValueError("DE header SHA/size differs from gravity manifest")
    return {"de_constants": de, "de_header_sha256": _sha(header_path), "apophis_header_count": len(expected_headers)}


def verify(root: Path) -> dict[str, Any]:
    root = root.resolve()
    config = load_json(root / "configs/apophis_gravity_conventions.json")
    output = root / config["output_directory"]
    matrix_path, freeze_path = output / "matrix.json", output / "freeze.json"
    matrix = load_json(matrix_path)
    freeze = _check_freeze(root, matrix, freeze_path)
    manifest = _manifest_inventory(root)
    inputs = load_json(output / "inputs.json")
    if inputs.get("fingerprint") != freeze["fingerprint"] or not isinstance(inputs.get("headers"), list) or not inputs.get("de_original_strings"):
        raise ValueError("gravity inputs provenance differs from freeze")
    annual, references, data = _load_teachers(root)
    au_km, day_s = float(data["constants"]["au_km"]), float(data["constants"]["day_s"])
    header_provenance = _header_inputs(root, freeze, config, inputs, au_km)
    settings = {item["label"]: item for item in config["solver_settings"]}
    arms = {item["id"]: item for item in config["arms"]}
    expected_keys = {f"{arm}__{label}" for arm in arms for label in settings}
    records = matrix.get("records")
    if not isinstance(records, list) or {row.get("key") for row in records} != expected_keys or len(records) != 12:
        raise ValueError("gravity matrix does not contain the exact 12-run specification")
    if any(row.get("fingerprint") != freeze["fingerprint"] for row in records):
        raise ValueError("record fingerprint differs from freeze")
    initial_sources = {"annual_hourly": state_from_row(references["annual_hourly"][0])}
    reused_specs = {
        "extreme": ("outputs/precise_dp/matrix.json", "precise_dp__baseline__annual_hourly__extreme"),
        "ultra": ("outputs/precise_dp/matrix.json", "precise_dp__baseline__annual_hourly__ultra"),
        "rk4_fine": ("outputs/precise_propagation/matrix.json", "compensated__baseline__annual_hourly__0.125"),
    }
    source_matrix_hashes = {path: _sha(root / path) for path, _ in reused_specs.values()}
    for row in records:
        arm = arms[row["arm"]]
        _check_record_metadata(row, arm, settings)
        # The reused RK4 record names its count ``rk4_steps`` while the new
        # runner's equivalent stats use ``steps``.  Validate the original
        # metadata above, then provide the frozen helper its legacy alias.
        helper_row = row
        if row["solver"] == "rk4" and "rk4_steps" not in row["solver_stats"]:
            helper_row = dict(row)
            helper_row["solver_stats"] = {**row["solver_stats"], "rk4_steps": row["solver_stats"]["steps"]}
        reference_verifier._verify_record(helper_row, annual, references, initial_sources, au_km, day_s)
        if row["arm"] == "baseline":
            source_path, source_key = reused_specs[row["key"].rsplit("__", 1)[-1]]
            reused = row.get("reused_from")
            if not isinstance(reused, dict) or reused.get("matrix") != source_path or reused.get("key") != source_key or reused.get("sha256") != source_matrix_hashes[source_path]:
                raise ValueError(f"baseline reuse provenance mismatch: {row['key']}")
            source, _ = _source_row(root, reused)
            _check_reused_identity(root, row, source)
        elif "reused_from" in row:
            raise ValueError(f"new gravity run unexpectedly claims reuse: {row['key']}")
    checkpoint_files = sorted((output / "checkpoints").glob("*.json"))
    if {path.stem for path in checkpoint_files} != expected_keys:
        raise ValueError("gravity checkpoint set differs from matrix")
    by_key = {row["key"]: row for row in records}
    checkpoint_hashes = {}
    for path in checkpoint_files:
        if load_json(path) != by_key[path.stem]:
            raise ValueError(f"checkpoint differs from matrix: {path.name}")
        checkpoint_hashes[path.name] = _sha(path)
    analysis = _analysis(records, annual, references, au_km, day_s, config["diagnostic_days"], float(config["cross_solver_budget_m"]))
    analysis["header_provenance"] = header_provenance
    analysis.update({"matrix_sha256": _sha(matrix_path), "verifier_sha256": _sha(root / "src/verify_apophis_gravity_conventions.py"), "reference_verifier_sha256": _sha(root / "src/verify_apophis_reference_time.py"), "precise_dp_verifier_sha256": _sha(root / "src/verify_precise_dp.py"), "precise_propagation_verifier_sha256": _sha(root / "src/verify_precise_propagation.py")})
    _write_immutable(output / "analysis.json", analysis)
    scalar_error_count = sum(2 * (len(annual) + sum(int(row["reference_comparisons"][name]["samples"]) for name in references)) for row in records)
    verification = {"schema_version": 1, "matrix": str(matrix_path.relative_to(root)), "matrix_sha256": _sha(matrix_path), "inputs_sha256": _sha(output / "inputs.json"), "verifier_sha256": _sha(root / "src/verify_apophis_gravity_conventions.py"), "reference_verifier_sha256": _sha(root / "src/verify_apophis_reference_time.py"), "precise_dp_verifier_sha256": _sha(root / "src/verify_precise_dp.py"), "precise_propagation_verifier_sha256": _sha(root / "src/verify_precise_propagation.py"), "freeze_sha256": _sha(freeze_path), "fingerprint": freeze["fingerprint"], "run_count": len(records), "unique_run_keys": len(expected_keys), "checkpoint_count": len(checkpoint_files), "checkpoint_sha256": checkpoint_hashes, "manifest": manifest, "header_provenance": header_provenance, "primary_rows": len(annual), "reference_tables": len(references), "accepted_endpoint_count": sum(len(row["accepted_endpoints"]) for row in records), "scalar_error_count": scalar_error_count, "paired_scalar_count": (len(analysis["dp_setting_differences"]) + len(analysis["cross_solver_differences"])) * len(annual) * 2, "reused_baselines_verified": 3, "new_propagations_verified": 9, "cross_solver_budget_m": config["cross_solver_budget_m"]}
    _write_immutable(output / "verification.json", verification)
    return verification


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    print(json.dumps(verify(args.root), indent=2, ensure_ascii=False))

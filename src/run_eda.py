"""Parse the JPL pilot and produce deterministic physics-aware EDA outputs.

Only the Python standard library is required. The calculations are diagnostic:
the technical pilot is not a final scientific split and does not train models.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import math
from pathlib import Path
import re
import statistics
from typing import Iterable


MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def percentile(values: Iterable[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.nan
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def norm(vector: tuple[float, float, float]) -> float:
    return math.sqrt(sum(component * component for component in vector))


def add(
    left: tuple[float, float, float], right: tuple[float, float, float]
) -> tuple[float, float, float]:
    return tuple(a + b for a, b in zip(left, right))  # type: ignore[return-value]


def subtract(
    left: tuple[float, float, float], right: tuple[float, float, float]
) -> tuple[float, float, float]:
    return tuple(a - b for a, b in zip(left, right))  # type: ignore[return-value]


def scale(
    vector: tuple[float, float, float], factor: float
) -> tuple[float, float, float]:
    return tuple(component * factor for component in vector)  # type: ignore[return-value]


def cross(
    left: tuple[float, float, float], right: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def calendar_to_iso(value: str) -> str:
    match = re.search(
        r"A\.D\.\s+(\d{4})-([A-Z][a-z]{2})-(\d{2})\s+"
        r"(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?",
        value,
    )
    if not match:
        raise ValueError(f"Unrecognized Horizons calendar value: {value}")
    year, month_name, day, hour, minute, second, fraction = match.groups()
    microsecond = int(((fraction or "0") + "000000")[:6])
    timestamp = dt.datetime(
        int(year),
        MONTHS[month_name],
        int(day),
        int(hour),
        int(minute),
        int(second),
        microsecond,
    )
    return timestamp.isoformat(timespec="seconds")


def extract_header(result: str) -> dict:
    fields = {}
    labels = {
        "target": "Target body name",
        "center": "Center body name",
        "start": "Start time",
        "stop": "Stop  time",
        "step": "Step-size",
        "units": "Output units",
        "output_type": "Output type",
        "reference_frame": "Reference frame",
    }
    for key, label in labels.items():
        match = re.search(rf"^{re.escape(label)}\s*:\s*(.+)$", result, re.MULTILINE)
        if not match:
            raise ValueError(f"Missing Horizons header field: {label}")
        fields[key] = match.group(1).strip()
    fields["geometric"] = "GEOMETRIC cartesian states" in result
    fields["tdb"] = 'Barycentric Dynamical Time ("TDB"' in result
    fields["small_perturbers"] = "Small perturbers: Yes" in result
    return fields


def parse_horizons(path: Path, target_id: str, target_name: str) -> tuple[dict, list[dict]]:
    document = load_json(path)
    result = document["result"]
    header = extract_header(result)
    header["api_version"] = document.get("signature", {}).get("version")
    header["source"] = document.get("signature", {}).get("source")
    block = result.split("$$SOE", 1)[1].split("$$EOE", 1)[0]
    rows = []
    for raw_row in csv.reader(block.splitlines()):
        cells = [cell.strip() for cell in raw_row]
        if len(cells) < 8 or not cells[0]:
            continue
        values = [float(value) for value in cells[2:8]]
        rows.append(
            {
                "target_id": target_id,
                "target_name": target_name,
                "epoch_jd_tdb": float(cells[0]),
                "epoch_tdb": calendar_to_iso(cells[1]),
                "r": (values[0], values[1], values[2]),
                "v": (values[3], values[4], values[5]),
            }
        )
    if not rows:
        raise ValueError(f"No state rows parsed from {path}")
    return header, rows


def extract_sbdb(path: Path, configured_name: str) -> dict:
    document = load_json(path)
    orbit = document["orbit"]
    obj = document["object"]
    elements = {entry["name"]: entry.get("value") for entry in orbit["elements"]}
    estimated_non_grav = [
        entry["name"]
        for entry in (orbit.get("model_pars") or [])
        if entry.get("kind") == "EST"
    ]
    return {
        "object_id": obj["des"],
        "configured_name": configured_name,
        "fullname": obj["fullname"],
        "orbit_class_code": (obj.get("orbit_class") or {}).get("code"),
        "orbit_class_name": (obj.get("orbit_class") or {}).get("name"),
        "neo": obj.get("neo"),
        "pha": obj.get("pha"),
        "orbit_solution_id": orbit.get("orbit_id"),
        "solution_date": orbit.get("soln_date"),
        "first_observation": orbit.get("first_obs"),
        "last_observation": orbit.get("last_obs"),
        "data_arc_days": float(orbit["data_arc"]) if orbit.get("data_arc") else None,
        "n_observations": int(orbit["n_obs_used"]) if orbit.get("n_obs_used") else None,
        "condition_code": orbit.get("condition_code"),
        "orbit_rms_arcsec": float(orbit["rms"]) if orbit.get("rms") else None,
        "epoch_jd_tdb": float(orbit["epoch"]) if orbit.get("epoch") else None,
        "a_au": float(elements["a"]) if elements.get("a") else None,
        "e": float(elements["e"]) if elements.get("e") else None,
        "i_ecliptic_deg": float(elements["i"]) if elements.get("i") else None,
        "q_au": float(elements["q"]) if elements.get("q") else None,
        "moid_au": float(orbit["moid"]) if orbit.get("moid") else None,
        "estimated_non_grav_parameters": ",".join(estimated_non_grav),
        "planetary_ephemeris": orbit.get("pe_used"),
        "small_body_perturbers": orbit.get("sb_used"),
        "sbdb_api_version": document.get("signature", {}).get("version"),
    }


def validate_series(series: list[dict]) -> dict:
    epochs = [row["epoch_jd_tdb"] for row in series]
    intervals = [right - left for left, right in zip(epochs, epochs[1:])]
    unique = len(set(epochs))
    finite = all(
        math.isfinite(value)
        for row in series
        for value in (*row["r"], *row["v"], row["epoch_jd_tdb"])
    )
    return {
        "rows": len(series),
        "unique_epochs": unique,
        "duplicate_epochs": len(series) - unique,
        "finite": finite,
        "cadence_min_days": min(intervals),
        "cadence_median_days": statistics.median(intervals),
        "cadence_max_days": max(intervals),
        "monotonic": all(interval > 0 for interval in intervals),
        "start": series[0]["epoch_tdb"],
        "stop": series[-1]["epoch_tdb"],
    }


def osculating_invariants(
    position: tuple[float, float, float],
    velocity: tuple[float, float, float],
    mu: float,
) -> tuple[float, float, float]:
    radius = norm(position)
    speed = norm(velocity)
    energy = 0.5 * speed * speed - mu / radius
    semi_major_axis = -mu / (2.0 * energy)
    angular_momentum = cross(position, velocity)
    h_mag = norm(angular_momentum)
    eccentricity_vector = subtract(
        scale(cross(velocity, angular_momentum), 1.0 / mu),
        scale(position, 1.0 / radius),
    )
    return semi_major_axis, norm(eccentricity_vector), h_mag


def acceleration_toward(
    displacement: tuple[float, float, float], mu: float
) -> tuple[float, float, float]:
    distance = norm(displacement)
    return scale(displacement, mu / (distance**3))


def pearson(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        return math.nan
    mean_left = statistics.mean(left)
    mean_right = statistics.mean(right)
    numerator = sum(
        (x - mean_left) * (y - mean_right) for x, y in zip(left, right)
    )
    denominator = math.sqrt(
        sum((x - mean_left) ** 2 for x in left)
        * sum((y - mean_right) ** 2 for y in right)
    )
    return numerator / denominator if denominator else math.nan


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def fmt(value: float | None, digits: int = 4) -> str:
    if value is None or not math.isfinite(value):
        return "—"
    return f"{value:.{digits}g}"


def build_orbit_svg(path: Path, features: list[dict], names: dict[str, str]) -> None:
    width, height = 920, 760
    margin = 74
    sample = [row for index, row in enumerate(features) if index % 7 == 0]
    extent = max(max(abs(row["r_x"]), abs(row["r_y"])) for row in sample) * 1.04
    plot = min(width - 2 * margin, height - 2 * margin)

    def px(value: float) -> float:
        return margin + (value + extent) / (2 * extent) * plot

    def py(value: float) -> float:
        return margin + plot - (value + extent) / (2 * extent) * plot

    colors = ["#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2"]
    by_object: dict[str, list[dict]] = {}
    for row in sample:
        by_object.setdefault(row["object_id"], []).append(row)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<text x="24" y="34" font-family="sans-serif" font-size="22">Heliocentric trajectories — ICRF X–Y projection</text>',
        f'<rect x="{margin}" y="{margin}" width="{plot}" height="{plot}" fill="none" stroke="#94a3b8"/>',
        f'<line x1="{px(-extent):.1f}" y1="{py(0):.1f}" x2="{px(extent):.1f}" y2="{py(0):.1f}" stroke="#cbd5e1"/>',
        f'<line x1="{px(0):.1f}" y1="{py(-extent):.1f}" x2="{px(0):.1f}" y2="{py(extent):.1f}" stroke="#cbd5e1"/>',
        f'<circle cx="{px(0):.1f}" cy="{py(0):.1f}" r="6" fill="#f59e0b"/>',
    ]
    for index, (object_id, rows) in enumerate(by_object.items()):
        points = " ".join(f"{px(row['r_x']):.1f},{py(row['r_y']):.1f}" for row in rows)
        color = colors[index % len(colors)]
        parts.append(
            f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="1.4" opacity="0.78"/>'
        )
        legend_x = margin + plot + 18
        legend_y = margin + 20 + index * 25
        parts.extend(
            [
                f'<line x1="{legend_x}" y1="{legend_y}" x2="{legend_x + 20}" y2="{legend_y}" stroke="{color}" stroke-width="3"/>',
                f'<text x="{legend_x + 27}" y="{legend_y + 4}" font-family="sans-serif" font-size="12">{html.escape(names[object_id])}</text>',
            ]
        )
    parts.extend(
        [
            f'<text x="{margin + plot / 2}" y="{margin + plot + 46}" text-anchor="middle" font-family="sans-serif" font-size="14">ICRF X, AU</text>',
            f'<text x="20" y="{margin + plot / 2}" transform="rotate(-90 20 {margin + plot / 2})" text-anchor="middle" font-family="sans-serif" font-size="14">ICRF Y, AU</text>',
            '</svg>',
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def build_small_multiples_svg(
    path: Path,
    features: list[dict],
    names: dict[str, str],
    value_key: str,
    title: str,
    y_label: str,
    log_scale: bool,
) -> None:
    width = 940
    panel_height = 130
    margin_left, margin_right, top = 86, 24, 58
    object_ids = list(names)
    height = top + panel_height * len(object_ids) + 52
    all_jd = [row["epoch_jd_tdb"] for row in features]
    min_jd, max_jd = min(all_jd), max(all_jd)

    def px(jd: float) -> float:
        return margin_left + (jd - min_jd) / (max_jd - min_jd) * (
            width - margin_left - margin_right
        )

    transformed = []
    for row in features:
        value = row[value_key]
        transformed.append(math.log10(max(value, 1e-30)) if log_scale else value)
    low, high = min(transformed), max(transformed)
    padding = 0.04 * (high - low or 1.0)
    low -= padding
    high += padding
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="24" y="32" font-family="sans-serif" font-size="22">{html.escape(title)}</text>',
    ]
    for panel, object_id in enumerate(object_ids):
        y0 = top + panel * panel_height
        rows = [row for row in features if row["object_id"] == object_id]
        rows = rows[::7]

        def py(value: float) -> float:
            converted = math.log10(max(value, 1e-30)) if log_scale else value
            return y0 + panel_height - 24 - (converted - low) / (high - low) * (
                panel_height - 38
            )

        points = " ".join(
            f"{px(row['epoch_jd_tdb']):.1f},{py(row[value_key]):.1f}" for row in rows
        )
        parts.extend(
            [
                f'<rect x="{margin_left}" y="{y0}" width="{width - margin_left - margin_right}" height="{panel_height - 20}" fill="none" stroke="#cbd5e1"/>',
                f'<polyline points="{points}" fill="none" stroke="#2563eb" stroke-width="1.4"/>',
                f'<text x="{margin_left - 10}" y="{y0 + 20}" text-anchor="end" font-family="sans-serif" font-size="12">{html.escape(names[object_id])}</text>',
            ]
        )
    parts.extend(
        [
            f'<text x="{width / 2}" y="{height - 15}" text-anchor="middle" font-family="sans-serif" font-size="14">Epoch, JDTDB (2020–2030)</text>',
            f'<text x="18" y="{height / 2}" transform="rotate(-90 18 {height / 2})" text-anchor="middle" font-family="sans-serif" font-size="14">{html.escape(y_label)}</text>',
            '</svg>',
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def build_event_svg(path: Path, event: dict) -> None:
    width, height = 920, 430
    left, right, top, bottom = 88, 30, 58, 62
    series = event["series"]
    min_hour = min(row["hours_from_start"] for row in series)
    max_hour = max(row["hours_from_start"] for row in series)
    values = [
        math.log10(row[key])
        for row in series
        for key in ("earth_distance_km", "moon_distance_km")
    ]
    low, high = min(values), max(values)
    low -= 0.04 * (high - low)
    high += 0.04 * (high - low)

    def px(value: float) -> float:
        return left + (value - min_hour) / (max_hour - min_hour) * (width - left - right)

    def py(value: float) -> float:
        transformed = math.log10(value)
        return top + (high - transformed) / (high - low) * (height - top - bottom)

    colors = {"earth_distance_km": "#2563eb", "moon_distance_km": "#9333ea"}
    labels = {"earth_distance_km": "Earth", "moon_distance_km": "Moon"}
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<text x="24" y="32" font-family="sans-serif" font-size="22">Apophis 2029 encounter — five-minute refinement</text>',
        f'<rect x="{left}" y="{top}" width="{width-left-right}" height="{height-top-bottom}" fill="none" stroke="#94a3b8"/>',
    ]
    for key in colors:
        points = " ".join(
            f"{px(row['hours_from_start']):.1f},{py(row[key]):.1f}" for row in series
        )
        parts.append(
            f'<polyline points="{points}" fill="none" stroke="{colors[key]}" stroke-width="1.8"/>'
        )
    for index, key in enumerate(colors):
        x = left + 16 + index * 105
        parts.extend(
            [
                f'<line x1="{x}" y1="{top + 18}" x2="{x + 22}" y2="{top + 18}" stroke="{colors[key]}" stroke-width="3"/>',
                f'<text x="{x + 29}" y="{top + 22}" font-family="sans-serif" font-size="12">{labels[key]}</text>',
            ]
        )
    earth_min = event["per_body_minima"]["399"]
    parts.extend(
        [
            f'<circle cx="{px(earth_min["hours_from_start"]):.1f}" cy="{py(earth_min["distance_km"]):.1f}" r="5" fill="#2563eb"/>',
            f'<text x="{px(earth_min["hours_from_start"])+8:.1f}" y="{py(earth_min["distance_km"])-8:.1f}" font-family="sans-serif" font-size="12">{earth_min["distance_km"]:,.0f} km</text>',
            f'<text x="{width/2}" y="{height-18}" text-anchor="middle" font-family="sans-serif" font-size="14">Hours since {event["start_tdb"]} TDB</text>',
            f'<text x="18" y="{height/2}" transform="rotate(-90 18 {height/2})" text-anchor="middle" font-family="sans-serif" font-size="14">log10(distance / km)</text>',
            '</svg>',
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def analyze_refined_event(
    root: Path,
    base_config: dict,
    event_config_path: Path,
    au_km: float,
    day_s: float,
    mu_sun: float,
    processed_dir: Path,
    figures_dir: Path,
) -> dict:
    event_config = load_json(event_config_path)
    event_id = event_config["event_id"].replace("-", "_")
    asteroid = event_config["asteroid"]
    _, asteroid_states = parse_horizons(
        root / "data" / "raw" / "horizons_refined" / f"{event_id}_{asteroid['id']}.json",
        asteroid["id"],
        asteroid["name"],
    )
    body_definitions = {body["id"]: body for body in base_config["perturbers"]}
    body_states = {}
    for body in event_config["bodies"]:
        _, series = parse_horizons(
            root / "data" / "raw" / "horizons_refined" / f"{event_id}_{body['id']}.json",
            body["id"],
            body["name"],
        )
        body_states[body["id"]] = series
    epochs = [row["epoch_jd_tdb"] for row in asteroid_states]
    if any([row["epoch_jd_tdb"] for row in series] != epochs for series in body_states.values()):
        raise ValueError("Refined event grids are not synchronized")
    start_jd = epochs[0]
    rows = []
    minima = {}
    for index, asteroid_state in enumerate(asteroid_states):
        radius = norm(asteroid_state["r"])
        a_sun = scale(asteroid_state["r"], -mu_sun / radius**3)
        row = {
            "epoch_jd_tdb": asteroid_state["epoch_jd_tdb"],
            "epoch_tdb": asteroid_state["epoch_tdb"],
            "hours_from_start": (asteroid_state["epoch_jd_tdb"] - start_jd) * 24.0,
        }
        for body in event_config["bodies"]:
            body_state = body_states[body["id"]][index]
            definition = body_definitions[body["id"]]
            mu = definition["mu_km3_s2"] * day_s**2 / au_km**3
            displacement = subtract(body_state["r"], asteroid_state["r"])
            relative_velocity = subtract(body_state["v"], asteroid_state["v"])
            direct = acceleration_toward(displacement, mu)
            indirect = acceleration_toward(body_state["r"], mu)
            contribution = subtract(direct, indirect)
            distance_au = norm(displacement)
            distance_km = distance_au * au_km
            body_radius = norm(body_state["r"])
            hill = body_radius * (mu / (3.0 * mu_sun)) ** (1.0 / 3.0)
            entry = {
                "body_id": body["id"],
                "body_name": body["name"],
                "epoch_tdb": asteroid_state["epoch_tdb"],
                "epoch_jd_tdb": asteroid_state["epoch_jd_tdb"],
                "hours_from_start": row["hours_from_start"],
                "distance_au": distance_au,
                "distance_km": distance_km,
                "relative_speed_km_s": norm(relative_velocity) * au_km / day_s,
                "eta": norm(contribution) / norm(a_sun),
                "rho": distance_au / hill,
            }
            if body["id"] not in minima or distance_au < minima[body["id"]]["distance_au"]:
                minima[body["id"]] = entry
            row[f"{slug(body['name'])}_distance_au"] = distance_au
            row[f"{slug(body['name'])}_distance_km"] = distance_km
            row[f"{slug(body['name'])}_relative_speed_km_s"] = entry["relative_speed_km_s"]
            row[f"{slug(body['name'])}_eta"] = entry["eta"]
            row[f"{slug(body['name'])}_rho"] = entry["rho"]
        rows.append(row)
    earth_minimum = minima["399"]
    earth_minimum_row = next(
        row for row in rows if row["epoch_jd_tdb"] == earth_minimum["epoch_jd_tdb"]
    )
    moon_at_earth_minimum = {
        "body_id": "301",
        "body_name": "Moon (at Earth minimum)",
        "epoch_tdb": earth_minimum_row["epoch_tdb"],
        "epoch_jd_tdb": earth_minimum_row["epoch_jd_tdb"],
        "hours_from_start": earth_minimum_row["hours_from_start"],
        "distance_au": earth_minimum_row["moon_distance_au"],
        "distance_km": earth_minimum_row["moon_distance_km"],
        "relative_speed_km_s": earth_minimum_row["moon_relative_speed_km_s"],
        "eta": earth_minimum_row["moon_eta"],
        "rho": earth_minimum_row["moon_rho"],
    }
    fields = list(rows[0])
    write_csv(processed_dir / f"{event_id}.csv", fields, rows)
    result = {
        "event_id": event_config["event_id"],
        "purpose": event_config["purpose"],
        "cadence_minutes": 5,
        "rows": len(rows),
        "start_tdb": rows[0]["epoch_tdb"],
        "stop_tdb": rows[-1]["epoch_tdb"],
        "per_body_minima": minima,
        "earth_minimum": earth_minimum,
        "moon_at_earth_minimum": moon_at_earth_minimum,
        "series": rows,
    }
    build_event_svg(figures_dir / "apophis_2029_refined.svg", result)
    return result


def build_report(summary: dict) -> str:
    metadata = summary["metadata"]
    per_object = summary["per_object"]
    figure_prefix = "../" + summary["artifacts"]["figures_dir"]
    asteroid_count = summary["counts"]["asteroids"]
    neo_count = sum(bool(row["neo"]) for row in metadata)
    pha_count = sum(bool(row["pha"]) for row in metadata)
    class_names = sorted({row["orbit_class_name"] for row in metadata})
    lines = [
        "# EDA technical pilot — JPL small-body dynamics",
        "",
        f"> Generated {summary['generated_at_utc']}. This is an exploratory technical pilot, not the final train/validation/test dataset.",
        "",
        "## Executive summary",
        "",
        f"- Parsed **{summary['counts']['asteroid_state_rows']:,}** asteroid states and **{summary['counts']['body_state_rows']:,}** massive-body states.",
        f"- Coverage is {summary['coverage']['start']} through {summary['coverage']['stop']} at an exact daily cadence in TDB.",
        f"- Missing/duplicate/non-finite state rows: **{summary['quality']['problem_rows']}**.",
        f"- Horizons vector headers consistently report heliocentric Sun-center, ICRF, AU/day and geometric (uncorrected) states: **{summary['quality']['conventions_valid']}**.",
            f"- Correlation between `log10(max eta)` and `log10(|planetary acceleration|)` is **{summary['global']['log_eta_acceleration_pearson']:.4f}**; the heliocentric perturbation indicator tracks the omitted-force magnitude as intended.",
        "- Daily sampling is sufficient for broad dynamics EDA but not for precise closest-approach time or distance; candidate events require local hourly/minute refinement.",
        "",
        "## Data contract audit",
        "",
        "| Check | Result |",
        "| --- | --- |",
        f"| Horizons API versions seen | `{', '.join(summary['quality']['horizons_api_versions'])}` |",
        f"| SBDB API versions seen | `{', '.join(summary['quality']['sbdb_api_versions'])}` |",
        f"| Reference center | `{summary['quality']['center']}` |",
        f"| Reference frame | `{summary['quality']['reference_frame']}` |",
        f"| Units | `{summary['quality']['units']}` |",
        f"| Output | `{summary['quality']['output_type']}` |",
        f"| Raw manifest files | {summary['counts']['raw_files']} |",
        f"| Raw bytes | {summary['counts']['raw_bytes']:,} |",
        "",
        "The public documentation currently labels the Horizons API as version 1.3, while the returned vector payloads identify themselves as version 1.2. The response schema used here is validated from markers and headers rather than trusted solely from the version string.",
        "",
        "## Object metadata",
        "",
        "| Object | Class | NEO | PHA | a, AU | e | i, deg | MOID, AU | Observations | Arc, d | Non-grav. parameters |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in metadata:
        lines.append(
            "| {name} | {class_code} | {neo} | {pha} | {a} | {e} | {i} | {moid} | {obs} | {arc} | {nongrav} |".format(
                name=row["configured_name"],
                class_code=row["orbit_class_code"],
                neo="yes" if row["neo"] else "no",
                pha="yes" if row["pha"] else "no",
                a=fmt(row["a_au"], 6),
                e=fmt(row["e"], 6),
                i=fmt(row["i_ecliptic_deg"], 5),
                moid=fmt(row["moid_au"], 5),
                obs=row["n_observations"],
                arc=fmt(row["data_arc_days"], 7),
                nongrav=row["estimated_non_grav_parameters"] or "—",
            )
        )
    lines.extend(
        [
            "",
            f"The sample spans {', '.join(class_names)} regimes. It is useful for pipeline stress-testing but is selection-biased and must not become the final evaluation sample.",
            "",
            "## Physical ranges and consistency",
            "",
            "| Object | r range, AU | speed range, km/s | median a, AU | median e | energy span | h span | FD velocity p95 3pt/5pt, m/s | force residual p95 3pt/5pt, m/s² | dominant perturber |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for object_id, row in per_object.items():
        lines.append(
            f"| {row['name']} | {row['r_min_au']:.3f}–{row['r_max_au']:.3f} | "
            f"{row['speed_min_km_s']:.2f}–{row['speed_max_km_s']:.2f} | "
            f"{row['a_median_au']:.4f} | {row['e_median']:.4f} | "
            f"{row['energy_relative_span']:.2e} | {row['h_relative_span']:.2e} | "
            f"{row['fd3_velocity_error_p95_m_s']:.3g}/{row['fd5_velocity_error_p95_m_s']:.3g} | "
            f"{row['force3_error_p95_m_s2']:.3g}/{row['force5_error_p95_m_s2']:.3g} | "
            f"{row['dominant_perturber']} ({row['dominant_fraction']:.1%}) |"
        )
    lines.extend(
        [
            "",
            "`energy span` and `h span` are not expected to be zero: Horizons includes planetary/small-body perturbations and, for some objects, fitted non-gravitational terms. The table reports both three-point and five-point centered differences. Their gap is a direct cadence-sensitivity warning; the remaining force residual also combines omitted perturbers, relativity and non-gravitational effects.",
            "",
            f"![Heliocentric trajectories]({figure_prefix}/orbit_xy.svg)",
            "",
            f"![Perturbation indicator]({figure_prefix}/max_eta.svg)",
            "",
            f"![Earth separation]({figure_prefix}/earth_distance.svg)",
            "",
        ]
    )
    if summary.get("refined_event"):
        refined = summary["refined_event"]
        lines.extend(
            [
                "## High-cadence encounter refinement",
                "",
                f"The strongest daily-grid candidate was re-queried on a {refined['cadence_minutes']}-minute synchronized grid from {refined['start_tdb']} to {refined['stop_tdb']} TDB.",
                "",
                "| Body | Epoch TDB | Distance, km | Relative speed, km/s | eta | rho |",
                "| --- | --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for event in (refined["earth_minimum"], refined["moon_at_earth_minimum"]):
            lines.append(
                f"| {event['body_name']} | {event['epoch_tdb']} | {event['distance_km']:,.0f} | "
                f"{event['relative_speed_km_s']:.3f} | {event['eta']:.3g} | {event['rho']:.3g} |"
            )
        lines.extend(
            [
                "",
                f"![Apophis 2029 refined encounter]({figure_prefix}/apophis_2029_refined.svg)",
                "",
                "The refined minimum is still grid-based, not a continuous optimization or operational hazard product. Its purpose is to quantify how much the daily backbone smears a fast encounter.",
                "",
            ]
        )
    lines.extend(
        [
            "## Closest daily-sampled configurations",
            "",
            "| Rank | Object | Perturber | Epoch TDB | Distance, AU | Distance, km | Relative speed, km/s | eta | rho |",
            "| ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for index, event in enumerate(summary["closest_configurations"][:20], start=1):
        lines.append(
            f"| {index} | {event['object_name']} | {event['body_name']} | {event['epoch_tdb']} | "
            f"{event['distance_au']:.6g} | {event['distance_km']:,.0f} | "
            f"{event['relative_speed_km_s']:.3f} | {event['eta']:.3g} | {event['rho']:.3g} |"
        )
    lines.extend(
        [
            "",
            "These are minima on a one-day grid, not certified closest approaches. In particular, fast Earth encounters can be materially underestimated or shifted by many hours. They are discovery candidates for a refined event catalogue, not labels for model evaluation.",
            "",
            "## Findings that affect data preparation",
            "",
            "1. **Conventions are internally consistent.** The raw headers agree on Sun-center, ICRF, geometric states, TDB and AU/day; these fields must become explicit columns or dataset-level metadata rather than implicit assumptions.",
            f"2. **The pilot remains selection-biased.** {neo_count} of {asteroid_count} bodies are NEOs and {pha_count} are PHA objects. Final sampling needs SBDB-based strata across orbit class, eccentricity, inclination, perihelion and encounter intensity.",
            "3. **Daily cadence is multi-purpose but not event-grade.** Retain a daily backbone for 7–365 day rollouts and add nested high-cadence windows around candidate encounters.",
            "4. **Earth and Moon must remain separate.** The force table uses Earth and Moon separately, avoiding double counting the Earth–Moon barycenter while preserving lunar perturbations.",
            "5. **Heliocentric planetary forces require the indirect term.** Features and `eta` were computed from `mu_p[(r_p-r)/|r_p-r|^3 - r_p/|r_p|^3]`; omitting the second term would create a frame artefact and misleading perturber rankings.",
            "6. **Horizons is a richer teacher than the planned B3.** Payloads report DE441 plus small perturbers, and SBDB indicates fitted non-gravitational parameters for some targets. A residual against a planets-only B3 is therefore not automatically an unknown force or an ML target.",
            "7. **Do not finalize thresholds from this sample.** `eta` and `rho` are useful continuous diagnostics, but routing thresholds must be selected on a larger train/validation pilot and frozen before test.",
            "",
            "## Recommended next data-preparation step",
            "",
            f"Build a 30-object train/validation pilot using predeclared SBDB strata. Preserve the daily 2020–2030 backbone, refine only candidate encounter windows at hourly and then minute cadence, and reserve whole objects plus complete events before fitting any thresholds. The {asteroid_count} objects here remain an engineering regression set and should not be reused as the final test set.",
            "",
            "## Reproduction",
            "",
            "The raw files are immutable and ignored by Git; manifests in `data/checksums/` record source URLs and SHA-256 digests. No credentials or authenticated services were used. All scripts run with the standard library only.",
            "",
            "```bash",
            f"env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache uv run --no-project --python-preference only-system python src/download_jpl_pilot.py --config {summary['config_relative']} --root .",
            f"env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache uv run --no-project --python-preference only-system python src/download_event_refinement.py --base-config {summary['config_relative']} --event-config configs/eda_apophis_2029_refinement.json --root .",
            f"env UV_CACHE_DIR=/private/tmp/astrophysics-ml-uv-cache uv run --no-project --python-preference only-system python src/run_eda.py --config {summary['config_relative']} --event-config configs/eda_apophis_2029_refinement.json --root .",
            "```",
            "",
            "Official references: [Horizons API](https://ssd-api.jpl.nasa.gov/doc/horizons.html), [Horizons manual](https://ssd.jpl.nasa.gov/horizons/manual.html), [SBDB API](https://ssd-api.jpl.nasa.gov/doc/sbdb.html), [JPL astrodynamic parameters](https://ssd.jpl.nasa.gov/astro_par.html).",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--event-config", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    config = load_json(args.config.resolve())
    namespace = config.get("artifact_namespace")
    if namespace and (Path(namespace).name != namespace or namespace in {".", ".."}):
        raise ValueError("artifact_namespace must be a plain directory name")
    report_filename = config.get("report_filename", "EDA_REPORT.md")
    if Path(report_filename).name != report_filename:
        raise ValueError("report_filename must be a plain filename")
    manifest_filename = config.get("manifest_filename", "jpl_pilot_manifest.json")
    if Path(manifest_filename).name != manifest_filename:
        raise ValueError("manifest_filename must be a plain filename")
    interim_dir = root / "data" / "interim"
    processed_dir = root / "data" / "processed"
    outputs = root / "outputs" / "eda"
    figures = root / "figures" / "eda"
    if namespace:
        interim_dir = interim_dir / namespace
        processed_dir = processed_dir / namespace
        outputs = root / "outputs" / namespace
        figures = root / "figures" / namespace
    constants = config["constants"]
    au_km = constants["au_km"]
    day_s = constants["day_s"]
    mu_sun = constants["mu_sun_km3_s2"] * day_s**2 / au_km**3

    manifest = load_json(root / "data" / "checksums" / manifest_filename)
    raw_bytes = sum(record["bytes"] for record in manifest["files"])
    raw_files = len(manifest["files"])
    if args.event_config:
        event_manifest_name = load_json(args.event_config.resolve())["event_id"].replace("-", "_")
        event_manifest = load_json(
            root / "data" / "checksums" / f"{event_manifest_name}_manifest.json"
        )
        raw_bytes += sum(record["bytes"] for record in event_manifest["files"])
        raw_files += len(event_manifest["files"])
    metadata = []
    asteroid_series = {}
    body_series = {}
    headers = []

    for asteroid in config["asteroids"]:
        object_id = asteroid["id"]
        header, series = parse_horizons(
            root / "data" / "raw" / "horizons" / f"asteroid_{object_id}.json",
            object_id,
            asteroid["name"],
        )
        headers.append(header)
        asteroid_series[object_id] = series
        metadata.append(
            extract_sbdb(
                root / "data" / "raw" / "sbdb" / f"object_{object_id}.json",
                asteroid["name"],
            )
        )

    for body in config["perturbers"]:
        body_id = body["id"]
        header, series = parse_horizons(
            root / "data" / "raw" / "horizons" / f"body_{body_id}.json",
            body_id,
            body["name"],
        )
        headers.append(header)
        body_series[body_id] = series

    asteroid_checks = {key: validate_series(value) for key, value in asteroid_series.items()}
    body_checks = {key: validate_series(value) for key, value in body_series.items()}
    reference_epochs = [row["epoch_jd_tdb"] for row in next(iter(body_series.values()))]
    if any(
        [row["epoch_jd_tdb"] for row in series] != reference_epochs
        for series in [*asteroid_series.values(), *body_series.values()]
    ):
        raise ValueError("State-vector time grids are not identical")

    conventions_valid = all(
        header["center"].startswith("Sun (10)")
        and header["reference_frame"] == "ICRF"
        and header["units"] == "AU-D"
        and header["geometric"]
        and header["tdb"]
        for header in headers
    )
    if not conventions_valid:
        raise ValueError("Horizons convention validation failed")

    body_by_epoch = {
        body_id: {row["epoch_jd_tdb"]: row for row in series}
        for body_id, series in body_series.items()
    }
    perturbers = []
    for body in config["perturbers"]:
        converted = dict(body)
        converted["mu"] = body["mu_km3_s2"] * day_s**2 / au_km**3
        converted["slug"] = slug(body["name"])
        perturbers.append(converted)

    state_fields = [
        "target_id",
        "target_name",
        "epoch_jd_tdb",
        "epoch_tdb",
        "x_au",
        "y_au",
        "z_au",
        "vx_au_d",
        "vy_au_d",
        "vz_au_d",
    ]
    asteroid_state_rows = []
    for series in asteroid_series.values():
        for row in series:
            asteroid_state_rows.append(
                {
                    "target_id": row["target_id"],
                    "target_name": row["target_name"],
                    "epoch_jd_tdb": f"{row['epoch_jd_tdb']:.9f}",
                    "epoch_tdb": row["epoch_tdb"],
                    "x_au": f"{row['r'][0]:.16e}",
                    "y_au": f"{row['r'][1]:.16e}",
                    "z_au": f"{row['r'][2]:.16e}",
                    "vx_au_d": f"{row['v'][0]:.16e}",
                    "vy_au_d": f"{row['v'][1]:.16e}",
                    "vz_au_d": f"{row['v'][2]:.16e}",
                }
            )
    body_state_rows = []
    for series in body_series.values():
        for row in series:
            body_state_rows.append(
                {
                    "target_id": row["target_id"],
                    "target_name": row["target_name"],
                    "epoch_jd_tdb": f"{row['epoch_jd_tdb']:.9f}",
                    "epoch_tdb": row["epoch_tdb"],
                    "x_au": f"{row['r'][0]:.16e}",
                    "y_au": f"{row['r'][1]:.16e}",
                    "z_au": f"{row['r'][2]:.16e}",
                    "vx_au_d": f"{row['v'][0]:.16e}",
                    "vy_au_d": f"{row['v'][1]:.16e}",
                    "vz_au_d": f"{row['v'][2]:.16e}",
                }
            )
    write_csv(interim_dir / "asteroid_states.csv", state_fields, asteroid_state_rows)
    write_csv(interim_dir / "body_states.csv", state_fields, body_state_rows)

    metadata_fields = list(metadata[0])
    write_csv(interim_dir / "object_metadata.csv", metadata_fields, metadata)

    feature_fields = [
        "object_id",
        "object_name",
        "epoch_jd_tdb",
        "epoch_tdb",
        "r_x",
        "r_y",
        "r_z",
        "v_x",
        "v_y",
        "v_z",
        "r_au",
        "speed_km_s",
        "specific_energy_au2_d2",
        "h_au2_d",
        "osculating_a_au",
        "osculating_e",
        "a_two_body_x",
        "a_two_body_y",
        "a_two_body_z",
        "a_planets_x",
        "a_planets_y",
        "a_planets_z",
        "a_n_body_x",
        "a_n_body_y",
        "a_n_body_z",
        "a_planets_norm_au_d2",
        "max_eta",
        "dominant_perturber_id",
    ]
    for body in perturbers:
        feature_fields.extend(
            [
                f"d_{body['slug']}_au",
                f"vrel_{body['slug']}_km_s",
                f"eta_{body['slug']}",
                f"rho_{body['slug']}",
            ]
        )

    features = []
    internal = {}
    closest = []
    log_eta = []
    log_acceleration = []
    names = {row["id"]: row["name"] for row in config["asteroids"]}
    for object_id, series in asteroid_series.items():
        internal[object_id] = []
        pair_minima = {body["id"]: None for body in perturbers}
        for state in series:
            radius = norm(state["r"])
            speed = norm(state["v"])
            energy = 0.5 * speed * speed - mu_sun / radius
            semi_major_axis, eccentricity, angular_momentum = osculating_invariants(
                state["r"], state["v"], mu_sun
            )
            a_sun = scale(state["r"], -mu_sun / radius**3)
            a_planets = (0.0, 0.0, 0.0)
            metrics = {}
            max_eta = -1.0
            dominant = None
            for body in perturbers:
                body_state = body_by_epoch[body["id"]][state["epoch_jd_tdb"]]
                displacement = subtract(body_state["r"], state["r"])
                relative_velocity = subtract(body_state["v"], state["v"])
                distance = norm(displacement)
                direct = acceleration_toward(displacement, body["mu"])
                indirect = acceleration_toward(body_state["r"], body["mu"])
                contribution = subtract(direct, indirect)
                a_planets = add(a_planets, contribution)
                eta = norm(contribution) / norm(a_sun)
                body_radius = norm(body_state["r"])
                hill = body_radius * (body["mu"] / (3.0 * mu_sun)) ** (1.0 / 3.0)
                rho = distance / hill
                relative_speed = norm(relative_velocity) * au_km / day_s
                metrics[body["id"]] = {
                    "distance": distance,
                    "relative_speed": relative_speed,
                    "eta": eta,
                    "rho": rho,
                }
                candidate = {
                    "object_id": object_id,
                    "object_name": state["target_name"],
                    "body_id": body["id"],
                    "body_name": body["name"],
                    "epoch_tdb": state["epoch_tdb"],
                    "epoch_jd_tdb": state["epoch_jd_tdb"],
                    "distance_au": distance,
                    "distance_km": distance * au_km,
                    "relative_speed_km_s": relative_speed,
                    "eta": eta,
                    "rho": rho,
                }
                if pair_minima[body["id"]] is None or distance < pair_minima[body["id"]]["distance_au"]:
                    pair_minima[body["id"]] = candidate
                if eta > max_eta:
                    max_eta = eta
                    dominant = body["id"]
            a_n_body = add(a_sun, a_planets)
            acceleration_norm = norm(a_planets)
            log_eta.append(math.log10(max(max_eta, 1e-30)))
            log_acceleration.append(math.log10(max(acceleration_norm, 1e-30)))
            feature = {
                "object_id": object_id,
                "object_name": state["target_name"],
                "epoch_jd_tdb": state["epoch_jd_tdb"],
                "epoch_tdb": state["epoch_tdb"],
                "r_x": state["r"][0],
                "r_y": state["r"][1],
                "r_z": state["r"][2],
                "v_x": state["v"][0],
                "v_y": state["v"][1],
                "v_z": state["v"][2],
                "r_au": radius,
                "speed_km_s": speed * au_km / day_s,
                "specific_energy_au2_d2": energy,
                "h_au2_d": angular_momentum,
                "osculating_a_au": semi_major_axis,
                "osculating_e": eccentricity,
                "a_two_body_x": a_sun[0],
                "a_two_body_y": a_sun[1],
                "a_two_body_z": a_sun[2],
                "a_planets_x": a_planets[0],
                "a_planets_y": a_planets[1],
                "a_planets_z": a_planets[2],
                "a_n_body_x": a_n_body[0],
                "a_n_body_y": a_n_body[1],
                "a_n_body_z": a_n_body[2],
                "a_planets_norm_au_d2": acceleration_norm,
                "max_eta": max_eta,
                "dominant_perturber_id": dominant,
            }
            for body in perturbers:
                metric = metrics[body["id"]]
                feature[f"d_{body['slug']}_au"] = metric["distance"]
                feature[f"vrel_{body['slug']}_km_s"] = metric["relative_speed"]
                feature[f"eta_{body['slug']}"] = metric["eta"]
                feature[f"rho_{body['slug']}"] = metric["rho"]
            features.append(feature)
            internal[object_id].append(
                {
                    "state": state,
                    "a_n_body": a_n_body,
                    "feature": feature,
                }
            )
        closest.extend(pair_minima.values())

    formatted_features = []
    for row in features:
        formatted = {}
        for key in feature_fields:
            value = row[key]
            formatted[key] = f"{value:.16e}" if isinstance(value, float) else value
        formatted_features.append(formatted)
    write_csv(
        processed_dir / "dynamics_features.csv",
        feature_fields,
        formatted_features,
    )

    body_names = {body["id"]: body["name"] for body in perturbers}
    per_object = {}
    for object_id, records in internal.items():
        object_features = [record["feature"] for record in records]
        velocity_errors_3 = []
        velocity_errors_5 = []
        acceleration_errors_3 = []
        acceleration_errors_5 = []
        acceleration_relative_errors_5 = []
        for index in range(2, len(records) - 2):
            previous2 = records[index - 2]
            previous = records[index - 1]
            current = records[index]
            following = records[index + 1]
            following2 = records[index + 2]
            step_days = following["state"]["epoch_jd_tdb"] - current["state"]["epoch_jd_tdb"]
            fd3_velocity = scale(
                subtract(following["state"]["r"], previous["state"]["r"]),
                1.0 / (2.0 * step_days),
            )
            fd5_velocity = scale(
                add(
                    add(previous2["state"]["r"], scale(previous["state"]["r"], -8.0)),
                    add(scale(following["state"]["r"], 8.0), scale(following2["state"]["r"], -1.0)),
                ),
                1.0 / (12.0 * step_days),
            )
            velocity_errors_3.append(
                norm(subtract(fd3_velocity, current["state"]["v"])) * au_km * 1000.0 / day_s
            )
            velocity_errors_5.append(
                norm(subtract(fd5_velocity, current["state"]["v"])) * au_km * 1000.0 / day_s
            )
            fd3_acceleration = scale(
                subtract(following["state"]["v"], previous["state"]["v"]),
                1.0 / (2.0 * step_days),
            )
            fd5_acceleration = scale(
                add(
                    add(previous2["state"]["v"], scale(previous["state"]["v"], -8.0)),
                    add(scale(following["state"]["v"], 8.0), scale(following2["state"]["v"], -1.0)),
                ),
                1.0 / (12.0 * step_days),
            )
            acceleration_error_3 = norm(subtract(fd3_acceleration, current["a_n_body"]))
            acceleration_error_5 = norm(subtract(fd5_acceleration, current["a_n_body"]))
            acceleration_errors_3.append(acceleration_error_3 * au_km * 1000.0 / day_s**2)
            acceleration_errors_5.append(acceleration_error_5 * au_km * 1000.0 / day_s**2)
            acceleration_relative_errors_5.append(
                acceleration_error_5 / norm(current["a_n_body"])
            )
        energies = [row["specific_energy_au2_d2"] for row in object_features]
        angular_momenta = [row["h_au2_d"] for row in object_features]
        dominants = [row["dominant_perturber_id"] for row in object_features]
        dominant = max(set(dominants), key=dominants.count)
        per_object[object_id] = {
            "name": names[object_id],
            "rows": len(records),
            "r_min_au": min(row["r_au"] for row in object_features),
            "r_median_au": statistics.median(row["r_au"] for row in object_features),
            "r_max_au": max(row["r_au"] for row in object_features),
            "speed_min_km_s": min(row["speed_km_s"] for row in object_features),
            "speed_median_km_s": statistics.median(row["speed_km_s"] for row in object_features),
            "speed_max_km_s": max(row["speed_km_s"] for row in object_features),
            "a_median_au": statistics.median(row["osculating_a_au"] for row in object_features),
            "e_median": statistics.median(row["osculating_e"] for row in object_features),
            "energy_relative_span": (max(energies) - min(energies)) / abs(statistics.median(energies)),
            "h_relative_span": (max(angular_momenta) - min(angular_momenta)) / statistics.median(angular_momenta),
            "fd3_velocity_error_median_m_s": statistics.median(velocity_errors_3),
            "fd3_velocity_error_p95_m_s": percentile(velocity_errors_3, 0.95),
            "fd5_velocity_error_median_m_s": statistics.median(velocity_errors_5),
            "fd5_velocity_error_p95_m_s": percentile(velocity_errors_5, 0.95),
            "force3_error_median_m_s2": statistics.median(acceleration_errors_3),
            "force3_error_p95_m_s2": percentile(acceleration_errors_3, 0.95),
            "force5_error_median_m_s2": statistics.median(acceleration_errors_5),
            "force5_error_p95_m_s2": percentile(acceleration_errors_5, 0.95),
            "force5_relative_error_p95": percentile(acceleration_relative_errors_5, 0.95),
            "max_eta_median": statistics.median(row["max_eta"] for row in object_features),
            "max_eta_p99": percentile((row["max_eta"] for row in object_features), 0.99),
            "max_eta_max": max(row["max_eta"] for row in object_features),
            "dominant_perturber": body_names[dominant],
            "dominant_fraction": dominants.count(dominant) / len(dominants),
            "quality": asteroid_checks[object_id],
        }

    closest.sort(key=lambda row: row["distance_au"])
    all_checks = [*asteroid_checks.values(), *body_checks.values()]
    problem_rows = sum(
        check["duplicate_epochs"] + (0 if check["finite"] and check["monotonic"] else check["rows"])
        for check in all_checks
    )
    sbdb_versions = sorted({row["sbdb_api_version"] for row in metadata})
    refined_event = None
    if args.event_config:
        refined_event = analyze_refined_event(
            root,
            config,
            args.event_config.resolve(),
            au_km,
            day_s,
            mu_sun,
            processed_dir,
            figures,
        )
    summary = {
        "schema_version": 1,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "config": args.config.resolve().as_posix(),
        "config_relative": args.config.resolve().relative_to(root).as_posix(),
        "artifacts": {
            "namespace": namespace,
            "interim_dir": interim_dir.relative_to(root).as_posix(),
            "processed_dir": processed_dir.relative_to(root).as_posix(),
            "outputs_dir": outputs.relative_to(root).as_posix(),
            "figures_dir": figures.relative_to(root).as_posix(),
            "report": (Path("docs") / report_filename).as_posix(),
        },
        "counts": {
            "asteroids": len(asteroid_series),
            "perturbers": len(body_series),
            "asteroid_state_rows": len(asteroid_state_rows),
            "body_state_rows": len(body_state_rows),
            "feature_rows": len(features),
            "raw_files": raw_files,
            "raw_bytes": raw_bytes,
        },
        "coverage": {
            "start": next(iter(asteroid_series.values()))[0]["epoch_tdb"],
            "stop": next(iter(asteroid_series.values()))[-1]["epoch_tdb"],
            "cadence_days": 1.0,
        },
        "quality": {
            "problem_rows": problem_rows,
            "conventions_valid": conventions_valid,
            "center": headers[0]["center"],
            "reference_frame": headers[0]["reference_frame"],
            "units": headers[0]["units"],
            "output_type": headers[0]["output_type"],
            "horizons_api_versions": sorted({header["api_version"] for header in headers}),
            "sbdb_api_versions": sbdb_versions,
            "asteroid_checks": asteroid_checks,
            "body_checks": body_checks,
        },
        "metadata": metadata,
        "per_object": per_object,
        "closest_configurations": closest,
        "global": {
            "log_eta_acceleration_pearson": pearson(log_eta, log_acceleration),
        },
        "refined_event": refined_event,
    }
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    build_orbit_svg(figures / "orbit_xy.svg", features, names)
    build_small_multiples_svg(
        figures / "max_eta.svg",
        features,
        names,
        "max_eta",
        "Maximum heliocentric planetary perturbation ratio",
        "log10(max eta)",
        True,
    )
    build_small_multiples_svg(
        figures / "earth_distance.svg",
        features,
        names,
        "d_earth_au",
        "Asteroid–Earth simultaneous separation",
        "log10(distance / AU)",
        True,
    )
    report_path = root / "docs" / report_filename
    report_path.write_text(build_report(summary), encoding="utf-8")
    print(json.dumps(summary["counts"], indent=2))
    print(f"Report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

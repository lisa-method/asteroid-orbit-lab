#!/usr/bin/env python3
"""Render the two factual physics-diagnostic SVG panels.

The values are copied from the linked audit reports; this script performs no
propagation and has no external dependencies.  Run from the project root:

    python3 docs/figures/physics_diagnostics/make_physics_diagnostics_svg.py
"""

from __future__ import annotations

import html
import json
import math
from pathlib import Path


HERE = Path(__file__).resolve().parent

DATA = {
    "provenance": {
        "reference": "JPL Horizons model-derived teacher",
        "units": "maximum position error, km",
        "scope": "post-hoc physics audit; production/fine is a separate step-sensitivity value",
    },
    "panels": {
        "153814_earth_j2": {
            "title": "153814 (2001 WN5): Earth J2 audit",
            "window": "annual window 2028-05-27 → 2029-05-27; 365 days",
            "source_report": "DEVELOPMENT30_OUTLIER_AUDIT_REPORT.md",
            "values": [
                {"label": "baseline", "value_km": 13.452789212, "display": "13.452789212", "color": "#637083"},
                {"label": "EarthJ2", "value_km": 0.029853293, "display": "0.029853293", "color": "#14866d"},
            ],
            "step_sensitivity_m": 0.830,
        },
        "apophis_j2_ng": {
            "title": "Апофис: J2 и nominal-NG ветви",
            "source_report": "EARTH_J2_PILOT6_REPORT.md",
            "groups": [
                {
                    "title": "local 36 h",
                    "subtitle": "merged; no-NG → J2 IAU",
                    "values": [
                        {"label": "no-NG", "value_km": 3.65057, "display": "3.65057", "color": "#637083"},
                        {"label": "+ J2 IAU", "value_km": 0.00184481, "display": "0.00184481", "color": "#14866d"},
                    ],
                },
                {
                    "title": "annual, no-NG",
                    "subtitle": "merged; no J2 → J2 IAU",
                    "values": [
                        {"label": "no J2", "value_km": 359.482, "display": "359.482", "color": "#637083"},
                        {"label": "+ J2 IAU", "value_km": 1701.89, "display": "1701.89", "color": "#b45538"},
                    ],
                },
                {
                    "title": "annual, nominal-NG",
                    "subtitle": "merged; no J2 → J2 IAU",
                    "values": [
                        {"label": "no J2", "value_km": 1814.69, "display": "1814.69", "color": "#637083"},
                        {"label": "+ J2 IAU", "value_km": 13.1481, "display": "13.1481", "color": "#14866d"},
                    ],
                },
            ],
        },
    },
}


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def text(x: float, y: float, value: str, *, size: int = 16, weight: str = "400", fill: str = "#20252b", anchor: str = "start") -> str:
    return f'<text x="{x:g}" y="{y:g}" font-size="{size}px" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{esc(value)}</text>'


def log_x(value: float, x0: float, x1: float, lo: float, hi: float) -> float:
    return x0 + (math.log10(value) - math.log10(lo)) / (math.log10(hi) - math.log10(lo)) * (x1 - x0)


def tick_label(value: float) -> str:
    if value >= 1000:
        return f"{value:g}"
    if value >= 1:
        return f"{value:g}"
    return f"{value:g}"


def axis(x0: float, x1: float, y: float, lo: float, hi: float, ticks: list[float]) -> str:
    out = [f'<line x1="{x0:g}" x2="{x1:g}" y1="{y:g}" y2="{y:g}" stroke="#45515e" stroke-width="1.5"/>']
    for value in ticks:
        x = log_x(value, x0, x1, lo, hi)
        out.append(f'<line x1="{x:g}" x2="{x:g}" y1="{y - 205:g}" y2="{y:g}" stroke="#dfe4e8" stroke-width="1"/>')
        out.append(f'<line x1="{x:g}" x2="{x:g}" y1="{y:g}" y2="{y + 6:g}" stroke="#45515e" stroke-width="1.5"/>')
        out.append(text(x, y + 28, tick_label(value), size=13, fill="#45515e", anchor="middle"))
    out.append(text((x0 + x1) / 2, y + 53, "max position error (km), logarithmic scale", size=13, fill="#45515e", anchor="middle"))
    return "\n".join(out)


def bar_rows(values: list[dict[str, object]], x0: float, x1: float, y_axis: float, lo: float, hi: float, *, label_x: float, value_x: float, value_anchor: str = "start", row_gap: float = 72, show_values: bool = True) -> str:
    out: list[str] = []
    for index, item in enumerate(values):
        y = y_axis - 164 + index * row_gap
        value = float(item["value_km"])
        x = log_x(value, x0, x1, lo, hi)
        out.append(text(label_x, y + 20, str(item["label"]), size=15, weight="600", fill=str(item["color"])))
        out.append(f'<rect x="{x0:g}" y="{y:g}" width="{max(2.0, x - x0):g}" height="28" rx="5" fill="{esc(item["color"])}"/>')
        if show_values:
            out.append(text(value_x, y + 21, f'{item.get("display", value)} km', size=15, weight="600", fill="#20252b", anchor=value_anchor))
    return "\n".join(out)


def svg_153814() -> str:
    width, height = 1000, 470
    x0, x1, y_axis = 285, 875, 350
    lo, hi = 0.01, 100.0
    ticks = [0.01, 0.1, 1, 10, 100]
    p = DATA["panels"]["153814_earth_j2"]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="auto" viewBox="0 0 {width} {height}" preserveAspectRatio="xMinYMin meet" style="display:block;width:100%;height:auto" font-family="system-ui, -apple-system, BlinkMacSystemFont, \'Segoe UI\', sans-serif" role="img" aria-labelledby="title-153814 desc-153814">',
        '<title id="title-153814">153814 Earth J2 annual maximum position error</title>',
        '<desc id="desc-153814">Log-scale comparison of baseline and Earth J2 maximum position errors from the annual post-hoc audit.</desc>',
        '<rect width="100%" height="100%" fill="#fbfcfd"/>',
        text(35, 42, p["title"], size=24, weight="700"),
        text(35, 70, p["window"], size=14, fill="#45515e"),
        text(35, 98, "JPL Horizons model-derived teacher; values are audit summaries, not error bars", size=13, fill="#45515e"),
        '<rect x="35" y="122" width="930" height="55" rx="8" fill="#eef5f2" stroke="#b8d8cd"/>',
        text(55, 146, "Separate step check", size=14, weight="700", fill="#176a57"),
        text(205, 146, "production → fine shift: 0.830 m", size=15, weight="600"),
        text(205, 168, "This is reported as a sensitivity value, not an uncertainty bar on either error.", size=12, fill="#45515e"),
        axis(x0, x1, y_axis, lo, hi, ticks),
        bar_rows(p["values"], x0, x1, y_axis, lo, hi, label_x=55, value_x=965, value_anchor="end"),
        text(35, 445, "Source: DEVELOPMENT30_OUTLIER_AUDIT_REPORT.md; annual reference daily + refined grid.", size=12, fill="#45515e"),
        '</svg>',
    ]
    return "\n".join(parts)


def svg_apophis() -> str:
    width, height = 1400, 625
    panels = DATA["panels"]["apophis_j2_ng"]["groups"]
    positions = [55, 485, 915]
    panel_width = 385
    xpad = 150
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="auto" viewBox="0 0 {width} {height}" preserveAspectRatio="xMinYMin meet" style="display:block;width:100%;height:auto" font-family="system-ui, -apple-system, BlinkMacSystemFont, \'Segoe UI\', sans-serif" role="img" aria-labelledby="title-apophis desc-apophis">',
        '<title id="title-apophis">Apophis J2 and nominal NG audit comparisons</title>',
        '<desc id="desc-apophis">Three separate logarithmic panels compare local and annual maximum position errors for Apophis.</desc>',
        '<rect width="100%" height="100%" fill="#fbfcfd"/>',
        text(35, 42, DATA["panels"]["apophis_j2_ng"]["title"], size=24, weight="700"),
        text(35, 70, "Separate windows and force branches; bars show maximum position error only", size=14, fill="#45515e"),
        text(35, 96, "JPL Horizons model-derived teacher; annual J2 changes are not monotonic across branches", size=13, fill="#45515e"),
    ]
    for idx, (group, left) in enumerate(zip(panels, positions)):
        x0, x1 = left + xpad, left + panel_width - 20
        y_axis = 440
        lo, hi = (0.001, 10.0) if idx == 0 else (1.0, 3000.0)
        ticks = [0.001, 0.01, 0.1, 1, 10] if idx == 0 else [1, 10, 100, 1000]
        parts.extend([
            f'<rect x="{left:g}" y="125" width="{panel_width:g}" height="390" rx="10" fill="#ffffff" stroke="#d5dce2"/>',
            text(left + 16, 158, str(group["title"]), size=18, weight="700"),
            text(left + 16, 181, str(group["subtitle"]), size=13, fill="#45515e"),
            axis(x0, x1, y_axis, lo, hi, ticks),
            bar_rows(group["values"], x0, x1, y_axis, lo, hi, label_x=left + 16, value_x=left + 16, row_gap=84, show_values=False),
        ])
        # Values are written beside bars inside each panel; put the two labels at the right edge.
        for row, item in enumerate(group["values"]):
            y = y_axis - 164 + row * 84
            parts.append(text(x1 - 8, y + 20, f'{item.get("display", item["value_km"])} km', size=14, weight="600", anchor="end"))
    parts.extend([
        text(35, 555, "Local: 36-hour merged Earth/Moon refinement. Annual: 2029-01-01 → 2030-01-01, merged inputs.", size=12, fill="#45515e"),
        text(35, 578, "Source: EARTH_J2_PILOT6_REPORT.md. Separate bars are not uncertainty intervals; no synthetic trajectory is plotted.", size=12, fill="#45515e"),
        '</svg>',
    ])
    return "\n".join(parts)


def main() -> None:
    (HERE / "153814-earthj2.svg").write_text(svg_153814(), encoding="utf-8")
    (HERE / "apophis-j2-ng.svg").write_text(svg_apophis(), encoding="utf-8")
    (HERE / "plot_data.json").write_text(json.dumps(DATA, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

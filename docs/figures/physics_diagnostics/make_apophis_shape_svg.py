#!/usr/bin/env python3
"""Render the supplied Apophis PDS triangular mesh as factual SVG projections.

This is a display-only reconstruction.  It reads the immutable OBJ, PDS label,
and moments sidecar, and does not propagate, rotate, or infer an attitude.
Standard-library only; deterministic output is intended for GitHub review.
"""

from __future__ import annotations

import hashlib
import html
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
OBJ_PATH = PROJECT / "data/raw/apophis_shape/apophis_v233s7.obj"
LABEL_PATH = PROJECT / "data/raw/apophis_shape/apophis_v233s7.xml"
MOMENTS_PATH = PROJECT / "data/processed/apophis_shape/moments.json"
OUTPUT_PATH = HERE / "apophis-pds-shape.svg"


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def svg_text(x: float, y: float, value: str, *, size: int = 16, weight: str = "400", fill: str = "#1f2933", anchor: str = "start") -> str:
    return (
        f'<text x="{x:g}" y="{y:g}" font-size="{size}px" font-weight="{weight}" '
        f'fill="{fill}" text-anchor="{anchor}">{esc(value)}</text>'
    )


def local_text(root: ET.Element, name: str) -> str:
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] == name and node.text:
            return node.text.strip()
    return ""


def read_sources() -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]], dict[str, object], str, str]:
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    for raw in OBJ_PATH.read_text(encoding="utf-8").splitlines():
        fields = raw.split()
        if not fields:
            continue
        if fields[0] == "v":
            if len(fields) != 4:
                raise ValueError(f"unexpected vertex row: {raw!r}")
            vertices.append(tuple(float(v) for v in fields[1:4]))
        elif fields[0] == "f":
            if len(fields) != 4:
                raise ValueError(f"unexpected facet row: {raw!r}")
            indices = tuple(int(re.split(r"/", value)[0]) - 1 for value in fields[1:4])
            if any(index < 0 or index >= len(vertices) for index in indices):
                raise ValueError(f"facet index out of range: {raw!r}")
            faces.append(indices)

    import json

    moments = json.loads(MOMENTS_PATH.read_text(encoding="utf-8"))
    label_root = ET.parse(LABEL_PATH).getroot()
    label_title = local_text(label_root, "title")
    logical_id = local_text(label_root, "logical_identifier")
    if len(vertices) != int(moments["vertices"]) or len(faces) != int(moments["facets"]):
        raise ValueError("OBJ counts disagree with moments.json")
    if label_title != "(99942) Apophis 3-D radar shape model":
        raise ValueError(f"unexpected PDS title: {label_title!r}")
    return vertices, faces, moments, label_title, logical_id


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def face_info(vertices: list[tuple[float, float, float]], face: tuple[int, int, int]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    a, b, c = (vertices[index] for index in face)
    ab = tuple(b[i] - a[i] for i in range(3))
    ac = tuple(c[i] - a[i] for i in range(3))
    normal = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    length = math.sqrt(sum(component * component for component in normal))
    if length == 0:
        raise ValueError("degenerate facet")
    unit_normal = tuple(component / length for component in normal)
    centroid = tuple((a[i] + b[i] + c[i]) / 3.0 for i in range(3))
    return unit_normal, centroid


def shade(normal: tuple[float, float, float]) -> str:
    light = (0.38, -0.46, 0.80)
    light_len = math.sqrt(sum(component * component for component in light))
    dot = sum(normal[i] * light[i] for i in range(3)) / light_len
    level = max(0.0, min(1.0, 0.18 + 0.82 * dot))
    # Navy/teal palette with a restrained highlight; all colours are local SVG.
    dark = (25, 68, 91)
    light_rgb = (174, 216, 204)
    rgb = tuple(round(dark[i] * (1.0 - level) + light_rgb[i] * level) for i in range(3))
    return "#" + "".join(f"{component:02x}" for component in rgb)


def project(point: tuple[float, float, float], axes: tuple[int, int]) -> tuple[float, float]:
    horizontal, vertical = axes
    return point[horizontal], -point[vertical]


def fmt_extent(value: float) -> str:
    return f"{value:.6f} km"


def render_panel(
    vertices: list[tuple[float, float, float]],
    faces: list[tuple[int, int, int]],
    *,
    left: float,
    top: float,
    width: float,
    height: float,
    title: str,
    subtitle: str,
    axes: tuple[int, int],
    depth_axis: int,
    extents: tuple[float, float],
    scale: float,
) -> str:
    plot_left = left + 36.0
    plot_top = top + 78.0
    plot_width = width - 72.0
    plot_height = height - 138.0
    cx = plot_left + plot_width / 2.0
    cy = plot_top + plot_height / 2.0
    projected = [project(vertex, axes) for vertex in vertices]
    span_x = extents[0] * scale
    span_y = extents[1] * scale
    dx = cx - (min(point[0] for point in projected) + max(point[0] for point in projected)) * scale / 2.0
    dy = cy - (min(point[1] for point in projected) + max(point[1] for point in projected)) * scale / 2.0
    horizontal_label = "XYZ"[axes[0]]
    vertical_label = "XYZ"[axes[1]]

    out = [
        f'<rect x="{left:g}" y="{top:g}" width="{width:g}" height="{height:g}" rx="12" fill="#ffffff" stroke="#d5dde3"/>',
        svg_text(left + 20, top + 32, title, size=21, weight="700"),
        svg_text(left + 20, top + 56, subtitle, size=13, fill="#536271"),
    ]
    origin_x, origin_y = dx, dy
    # Body-frame crosshair is behind the mesh and marks the projected origin.
    out.extend([
        f'<line x1="{origin_x - span_x / 2 - 12:g}" x2="{origin_x + span_x / 2 + 12:g}" y1="{origin_y:g}" y2="{origin_y:g}" stroke="#9aa9b5" stroke-dasharray="4 4"/>',
        f'<line x1="{origin_x:g}" x2="{origin_x:g}" y1="{origin_y - span_y / 2 - 12:g}" y2="{origin_y + span_y / 2 + 12:g}" stroke="#9aa9b5" stroke-dasharray="4 4"/>',
        svg_text(cx + span_x / 2 + 18, cy + 5, "body +" + horizontal_label, size=11, fill="#657482"),
        svg_text(cx + 5, cy - span_y / 2 - 18, "body +" + vertical_label, size=11, fill="#657482"),
    ])

    sortable: list[tuple[float, int, str]] = []
    for index, face in enumerate(faces):
        normal, centroid = face_info(vertices, face)
        points = []
        for vertex_index in face:
            x, y = projected[vertex_index]
            points.append(f"{x * scale + dx:g},{y * scale + dy:g}")
        sortable.append((centroid[depth_axis], index, f'<polygon points="{" ".join(points)}" fill="{shade(normal)}" stroke="#27495a" stroke-opacity="0.15" stroke-width="0.34"/>'))
    # Back-to-front painter ordering. Index tie-breaker keeps output deterministic.
    out.extend(item[2] for item in sorted(sortable, key=lambda item: (item[0], item[1])))

    bar_km = 0.1
    bar_px = bar_km * scale
    bar_x = left + 22.0
    bar_y = top + height - 42.0
    out.extend([
        f'<line x1="{bar_x:g}" x2="{bar_x + bar_px:g}" y1="{bar_y:g}" y2="{bar_y:g}" stroke="#1f2933" stroke-width="4"/>',
        f'<line x1="{bar_x:g}" x2="{bar_x:g}" y1="{bar_y - 6:g}" y2="{bar_y + 6:g}" stroke="#1f2933" stroke-width="2"/>',
        f'<line x1="{bar_x + bar_px:g}" x2="{bar_x + bar_px:g}" y1="{bar_y - 6:g}" y2="{bar_y + 6:g}" stroke="#1f2933" stroke-width="2"/>',
        svg_text(bar_x + bar_px + 10, bar_y + 5, "100 m", size=12, weight="600"),
        svg_text(left + width - 20, top + height - 16, f"axes: {horizontal_label}/{vertical_label}; {fmt_extent(extents[0])} × {fmt_extent(extents[1])}", size=11, fill="#536271", anchor="end"),
    ])
    return "\n".join(out)


def build_svg() -> str:
    vertices, faces, moments, label_title, logical_id = read_sources()
    obj_hash = sha256(OBJ_PATH)
    label_hash = sha256(LABEL_PATH)
    moments_hash = sha256(MOMENTS_PATH)
    if obj_hash != moments["obj_sha256"]:
        raise ValueError("OBJ SHA-256 disagrees with moments.json")
    extents = tuple(float(value) for value in moments["bbox_extents_km"])
    # One scale is shared by all three panels; the largest full span fits each panel.
    scale = 760.0
    width, height = 1500, 630
    panel_width, panel_height = 460, 430
    panel_top = 125
    panels = [
        render_panel(vertices, faces, left=30, top=panel_top, width=panel_width, height=panel_height, title="XY projection", subtitle="view from body +Z", axes=(0, 1), depth_axis=2, extents=(extents[0], extents[1]), scale=scale),
        render_panel(vertices, faces, left=520, top=panel_top, width=panel_width, height=panel_height, title="XZ projection", subtitle="view from body +Y", axes=(0, 2), depth_axis=1, extents=(extents[0], extents[2]), scale=scale),
        render_panel(vertices, faces, left=1010, top=panel_top, width=panel_width, height=panel_height, title="YZ projection", subtitle="view from body +X", axes=(1, 2), depth_axis=0, extents=(extents[1], extents[2]), scale=scale),
    ]
    metadata = (
        "source_obj=data/raw/apophis_shape/apophis_v233s7.obj\n"
        "source_label=data/raw/apophis_shape/apophis_v233s7.xml\n"
        "source_moments=data/processed/apophis_shape/moments.json\n"
        + "source_obj_sha256=" + obj_hash + "\n"
        + "source_label_sha256=" + label_hash + "\n"
        + "source_moments_sha256=" + moments_hash + "\n"
        + "vertices=2000; facets=3996; units=km; projection=orthographic; painter_order=centroid_depth\n"
        + "pds_logical_identifier=" + logical_id
    )
    return "\n".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="auto" viewBox="0 0 {width} {height}" preserveAspectRatio="xMinYMin meet" style="display:block;width:100%;height:auto" font-family="system-ui, -apple-system, BlinkMacSystemFont, \'Segoe UI\', sans-serif" role="img" aria-labelledby="title desc">',
        '<title id="title">Apophis preliminary PDS Model B: three body-frame orthographic projections</title>',
        '<desc id="desc">A deterministic SVG polygon rendering of the supplied 2000-vertex, 3996-facet Apophis shape mesh. Reconstruction, not a photograph; body frame, not predicted 2029 attitude.</desc>',
        f'<metadata>{esc(metadata)}</metadata>',
        '<rect width="100%" height="100%" fill="#f7f9fb"/>',
        svg_text(35, 42, "Apophis — preliminary PDS Model B (Brozovic et al. 2018)", size=27, weight="700"),
        svg_text(35, 70, "Reconstruction, not a photograph · body frame; not predicted 2029 attitude", size=15, fill="#435463"),
        svg_text(35, 96, f"PDS: {label_title} · 2,000 vertices · 3,996 triangular facets · all projections share one scale", size=13, fill="#536271"),
        *panels,
        svg_text(35, 574, "Shape source: NASA PDS radar model, Model B; vertices/facets in km. No rotation, trajectory or attitude prediction is applied.", size=12, fill="#536271"),
        svg_text(35, 594, "Dimensions describe this mesh; shape remains uncertain.", size=12, fill="#536271"),
        svg_text(35, 614, "Attribution DOI: 10.26033/ydyq-5756 · publication DOI: 10.1016/j.icarus.2017.08.032", size=12, fill="#536271"),
        '</svg>',
    ])


def main() -> None:
    OUTPUT_PATH.write_text(build_svg(), encoding="utf-8")
    print(f"wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()

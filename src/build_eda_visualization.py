"""Build a compact in-conversation D3 view from the EDA summary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


TEMPLATE = r'''<div id="eda-overview-20260902">
  <h2>JPL small-body EDA pilot</h2>
  <div class="viz-grid eda-stats">
    <div class="card viz-stat"><div class="text-muted">Asteroid states</div><div class="viz-stat-value tabular-nums">__STATE_COUNT__</div><div class="text-small text-muted">__OBJECT_COUNT__ objects · daily</div></div>
    <div class="card viz-stat"><div class="text-muted">Quality issues</div><div class="viz-stat-value tabular-nums">0</div><div class="text-small text-muted">missing · duplicate · non-finite</div></div>
    <div class="card viz-stat"><div class="text-muted">Apophis–Earth minimum</div><div class="viz-stat-value tabular-nums">__EARTH_MIN__ km</div><div class="text-small text-muted">5-minute grid · TDB</div></div>
  </div>
  <div class="eda-pair">
    <section><h3>Orbital regimes</h3><div id="eda-regimes"></div></section>
    <section><h3>Peak perturbation ratio</h3><div id="eda-eta"></div></section>
  </div>
  <section class="eda-event"><h3>Apophis 2029 encounter refinement</h3><div class="viz-row eda-legend" aria-label="Series"></div><div id="eda-event"></div></section>
  <div class="sr-only" id="eda-summary">__OBJECT_COUNT__-object engineering pilot with __NEO_COUNT__ near-Earth asteroids. Apophis is the extreme close-encounter stress case.</div>
</div>
<style>
  #eda-overview-20260902 { color: var(--foreground); width: 100%; }
  #eda-overview-20260902 h2 { margin: 0 0 12px; font-weight: 500; }
  #eda-overview-20260902 h3 { margin: 18px 0 6px; font-weight: 500; }
  #eda-overview-20260902 .eda-stats { margin-bottom: 4px; }
  #eda-overview-20260902 .eda-pair { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
  #eda-overview-20260902 section { min-width: 0; }
  #eda-overview-20260902 svg { display: block; width: 100%; }
  #eda-overview-20260902 .axis text,
  #eda-overview-20260902 .axis-title,
  #eda-overview-20260902 .point-label,
  #eda-overview-20260902 .value-label { fill: var(--foreground); font-size: 12px; }
  #eda-overview-20260902 .axis path,
  #eda-overview-20260902 .axis line { stroke: var(--border); }
  #eda-overview-20260902 .grid-line { stroke: var(--border); stroke-opacity: .55; }
  #eda-overview-20260902 [data-chart-frame] { fill: none; stroke: var(--border); }
  #eda-overview-20260902 .eda-legend button { background: transparent; border: 0; color: var(--foreground); padding: 4px 6px; display: inline-flex; align-items: center; gap: 6px; }
  #eda-overview-20260902 .eda-legend button[aria-pressed="false"] { opacity: .45; }
  #eda-overview-20260902 .swatch { width: 18px; height: 3px; display: inline-block; }
  #eda-overview-20260902 .tooltip { position: absolute; pointer-events: none; background: var(--popover); color: var(--popover-foreground); border: 1px solid var(--border); padding: 7px 9px; opacity: 0; z-index: 3; }
  #eda-overview-20260902 .event-wrap { position: relative; }
  @media (max-width: 720px) { #eda-overview-20260902 .eda-pair { grid-template-columns: 1fr; gap: 0; } }
</style>
<script src="https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js"></script>
<script>
(() => {
  const root = document.getElementById('eda-overview-20260902');
  const objects = __OBJECTS__;
  const eventData = __EVENT_DATA__;
  const series = [
    {key: 'earth', label: 'Earth', color: 'var(--viz-series-1)'},
    {key: 'moon', label: 'Moon', color: 'var(--viz-series-2)'}
  ];
  const visible = new Set(series.map(d => d.key));

  function frame(container, height, label) {
    const width = Math.max(320, container.getBoundingClientRect().width || 736);
    d3.select(container).selectAll('*').remove();
    const svg = d3.select(container).append('svg').attr('viewBox', `0 0 ${width} ${height}`).attr('role', 'img').attr('aria-label', label);
    svg.append('title').text(label);
    return {svg, width, height, margin: {top: 18, right: 26, bottom: 54, left: 72}};
  }

  function drawRegimes() {
    const container = root.querySelector('#eda-regimes');
    const {svg, width, height, margin} = frame(container, 310, 'Semi-major axis versus eccentricity for pilot asteroids');
    const xExtent = d3.extent(objects, d => d.a), xPad = (xExtent[1] - xExtent[0]) * .08;
    const yExtent = d3.extent(objects, d => d.e), yPad = (yExtent[1] - yExtent[0]) * .09;
    const x = d3.scaleLinear().domain([xExtent[0]-xPad, xExtent[1]+xPad]).range([margin.left, width-margin.right]);
    const y = d3.scaleLinear().domain([Math.max(0, yExtent[0]-yPad), Math.min(1, yExtent[1]+yPad)]).range([height-margin.bottom, margin.top]);
    svg.append('rect').attr('data-chart-frame','').attr('x',margin.left).attr('y',margin.top).attr('width',width-margin.left-margin.right).attr('height',height-margin.top-margin.bottom);
    svg.append('g').attr('class','axis').attr('transform',`translate(0,${height-margin.bottom})`).call(d3.axisBottom(x).ticks(width < 420 ? 4 : 6));
    svg.append('g').attr('class','axis').attr('transform',`translate(${margin.left},0)`).call(d3.axisLeft(y).ticks(5));
    svg.append('text').attr('class','axis-title').attr('data-axis','x').attr('x',(margin.left+width-margin.right)/2).attr('y',height-10).attr('text-anchor','middle').text('Semi-major axis, AU');
    svg.append('text').attr('class','axis-title').attr('data-axis','y').attr('transform',`translate(18 ${(margin.top+height-margin.bottom)/2}) rotate(-90)`).attr('text-anchor','middle').text('Eccentricity');
    svg.selectAll('circle.regime').data(objects).join('circle').attr('class','regime').attr('cx',d=>x(d.a)).attr('cy',d=>y(d.e)).attr('r',6).attr('fill',(d,i)=>`var(--viz-series-${i+1})`);
    svg.selectAll('text.point-label').data(objects).join('text').attr('class','point-label').attr('x',d=>x(d.a)+(d.name==='Seraphina'?-8:8)).attr('y',d=>y(d.e)+(d.name==='Phaethon'||d.name==='Apophis'?18:-9)).attr('text-anchor',d=>d.name==='Seraphina'?'end':'start').text(d=>`${d.name} · ${d.classCode}`);
  }

  function drawEta() {
    const container = root.querySelector('#eda-eta');
    const {svg, width, height, margin} = frame(container, 310, 'Maximum heliocentric perturbation ratio by asteroid');
    const ordered = [...objects].sort((a,b)=>a.maxEta-b.maxEta);
    const xExtent = d3.extent(ordered,d=>d.maxEta);
    const x = d3.scaleLog().domain([xExtent[0]/2, xExtent[1]*2]).range([margin.left,width-margin.right]);
    const y = d3.scalePoint().domain(ordered.map(d=>d.name)).range([height-margin.bottom,margin.top]).padding(.55);
    svg.append('rect').attr('data-chart-frame','').attr('x',margin.left).attr('y',margin.top).attr('width',width-margin.left-margin.right).attr('height',height-margin.top-margin.bottom);
    svg.append('g').attr('class','axis').attr('transform',`translate(0,${height-margin.bottom})`).call(d3.axisBottom(x).ticks(width < 420 ? 3 : 5,'.1e'));
    svg.append('g').attr('class','axis').attr('transform',`translate(${margin.left},0)`).call(d3.axisLeft(y));
    svg.append('text').attr('class','axis-title').attr('data-axis','x').attr('x',(margin.left+width-margin.right)/2).attr('y',height-10).attr('text-anchor','middle').text('max η = |aₚ,helio| / |a☉|');
    svg.selectAll('line.stem').data(ordered).join('line').attr('class','stem').attr('x1',x(x.domain()[0])).attr('x2',d=>x(d.maxEta)).attr('y1',d=>y(d.name)).attr('y2',d=>y(d.name)).attr('stroke','var(--border)');
    svg.selectAll('circle.dot').data(ordered).join('circle').attr('class','dot').attr('cx',d=>x(d.maxEta)).attr('cy',d=>y(d.name)).attr('r',6).attr('fill','var(--viz-series-1)');
    svg.selectAll('text.value-label').data(ordered).join('text').attr('class','value-label').attr('x',d=>Math.min(width-margin.right-4,x(d.maxEta)+8)).attr('y',d=>y(d.name)+4).attr('text-anchor',d=>x(d.maxEta)>width-margin.right-70?'end':'start').text(d=>d3.format('.2g')(d.maxEta));
  }

  const legend = d3.select(root.querySelector('.eda-legend'));
  legend.selectAll('button').data(series).join('button').attr('type','button').attr('aria-pressed','true').html(d=>`<span class="swatch" style="background:${d.color}"></span>${d.label}`).on('click', function(_,d){
    visible.has(d.key) ? visible.delete(d.key) : visible.add(d.key);
    d3.select(this).attr('aria-pressed', visible.has(d.key));
    drawEvent();
  });

  function drawEvent() {
    const container = root.querySelector('#eda-event');
    const {svg, width, height, margin} = frame(container, 330, 'Five-minute Apophis separation from Earth and Moon during the 2029 encounter');
    d3.select(container).classed('event-wrap',true);
    const active = series.filter(s=>visible.has(s.key));
    const x = d3.scaleLinear().domain(d3.extent(eventData,d=>d.hour)).range([margin.left,width-margin.right]);
    const allValues = eventData.flatMap(d=>active.map(s=>d[s.key]));
    const extent = d3.extent(allValues.length?allValues:[1,10]);
    const y = d3.scaleLog().domain([extent[0]*.85,extent[1]*1.15]).range([height-margin.bottom,margin.top]);
    svg.append('rect').attr('data-chart-frame','').attr('x',margin.left).attr('y',margin.top).attr('width',width-margin.left-margin.right).attr('height',height-margin.top-margin.bottom);
    svg.append('g').attr('class','axis').attr('transform',`translate(0,${height-margin.bottom})`).call(d3.axisBottom(x).ticks(width < 420 ? 4 : 6));
    svg.append('g').attr('class','axis').attr('transform',`translate(${margin.left},0)`).call(d3.axisLeft(y).ticks(5,'~s'));
    svg.append('text').attr('class','axis-title').attr('data-axis','x').attr('x',(margin.left+width-margin.right)/2).attr('y',height-10).attr('text-anchor','middle').text('Hours since 2029-04-13 00:00 TDB');
    svg.append('text').attr('class','axis-title').attr('data-axis','y').attr('transform',`translate(18 ${(margin.top+height-margin.bottom)/2}) rotate(-90)`).attr('text-anchor','middle').text('Separation, km (log scale)');
    const line = d3.line();
    active.forEach(s=>svg.append('path').datum(eventData).attr('fill','none').attr('stroke',s.color).attr('stroke-width',2).attr('d',line.x(d=>x(d.hour)).y(d=>y(d[s.key]))));
    const tooltip = d3.select(container).append('div').attr('class','tooltip').attr('role','tooltip');
    const guide = svg.append('line').attr('data-chart-hover-guide','').attr('y1',margin.top).attr('y2',height-margin.bottom).attr('stroke','var(--foreground)').attr('opacity',0);
    const markers = svg.selectAll('circle.hover').data(active,d=>d.key).join('circle').attr('class','hover').attr('data-chart-hover-marker','').attr('r',5).attr('fill',d=>d.color).attr('opacity',0);
    svg.append('rect').attr('data-chart-hit','').attr('data-chart-hover-overlay','cross-series').attr('x',margin.left).attr('y',margin.top).attr('width',width-margin.left-margin.right).attr('height',height-margin.top-margin.bottom).attr('fill','transparent').on('pointermove', function(event){
      const [mouseX] = d3.pointer(event,this); const hour=Math.max(x.domain()[0],Math.min(x.domain()[1],x.invert(mouseX))); const rightIndex=Math.min(eventData.length-1,d3.bisector(d=>d.hour).left(eventData,hour)); const leftIndex=Math.max(0,rightIndex-1); const leftRow=eventData[leftIndex], rightRow=eventData[rightIndex]; const ratio=rightRow.hour===leftRow.hour?0:(hour-leftRow.hour)/(rightRow.hour-leftRow.hour); const values=Object.fromEntries(active.map(s=>[s.key,leftRow[s.key]+ratio*(rightRow[s.key]-leftRow[s.key])]));
      guide.attr('x1',x(hour)).attr('x2',x(hour)).attr('opacity',.65);
      markers.attr('cx',x(hour)).attr('cy',s=>y(values[s.key])).attr('opacity',1);
      tooltip.style('opacity',1).style('left',`${Math.min(width-190,Math.max(8,x(hour)+10))}px`).style('top',`${margin.top+8}px`).html(`<strong>+${d3.format('.2f')(hour)} h</strong><br>${active.map(s=>`${s.label}: ${d3.format(',.0f')(values[s.key])} km`).join('<br>')}`);
    }).on('pointerleave',()=>{guide.attr('opacity',0);markers.attr('opacity',0);tooltip.style('opacity',0);});
  }

  const redraw = () => { drawRegimes(); drawEta(); drawEvent(); };
  redraw();
  new ResizeObserver(redraw).observe(root);
})();
</script>
'''


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--force-color-scheme", choices=("light", "dark"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    metadata = {row["object_id"]: row for row in summary["metadata"]}
    objects = []
    for object_id, row in summary["per_object"].items():
        objects.append(
            {
                "id": object_id,
                "name": row["name"],
                "classCode": metadata[object_id]["orbit_class_code"],
                "a": metadata[object_id]["a_au"],
                "e": metadata[object_id]["e"],
                "maxEta": row["max_eta_max"],
            }
        )
    refined = summary["refined_event"]
    event_rows = []
    for index, row in enumerate(refined["series"]):
        if index % 3 == 0 or row["epoch_tdb"] == refined["earth_minimum"]["epoch_tdb"]:
            event_rows.append(
                {
                    "hour": round(row["hours_from_start"], 6),
                    "iso": row["epoch_tdb"].replace("T", " "),
                    "earth": round(row["earth_distance_km"], 3),
                    "moon": round(row["moon_distance_km"], 3),
                }
            )
    fragment = (
        TEMPLATE.replace("__STATE_COUNT__", f"{summary['counts']['asteroid_state_rows']:,}")
        .replace("__OBJECT_COUNT__", str(summary["counts"]["asteroids"]))
        .replace("__NEO_COUNT__", str(sum(bool(row["neo"]) for row in summary["metadata"])))
        .replace("__EARTH_MIN__", f"{refined['earth_minimum']['distance_km']:,.0f}")
        .replace("__OBJECTS__", json.dumps(objects, separators=(",", ":")))
        .replace("__EVENT_DATA__", json.dumps(event_rows, separators=(",", ":")))
    )
    if args.force_color_scheme:
        fragment = fragment.replace(
            "<style>",
            f"<style>\n  :root {{ color-scheme: {args.force_color_scheme}; }}",
            1,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(fragment, encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

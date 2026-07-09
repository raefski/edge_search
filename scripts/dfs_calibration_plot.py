#!/usr/bin/env python3
"""Render data/dfs_calibration.json into a self-contained HTML calibration
dashboard: actual-vs-predicted for DK points AND ownership, pitchers and
hitters each get their own pair of scatter charts.

Run scripts/dfs_calibration.py first to (re)build the joined dataset, then
this to (re)build the page. Designed to be re-run every time new contest
data comes in -- deterministic output, safe to redeploy to the same Artifact
URL each time.

Usage: python3 scripts/dfs_calibration_plot.py [output_path.html]
       (defaults to writing next to this script if no path given)
"""
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.dfs_validate import cross_slate_summary  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data/dfs_calibration.html"


def main():
    data = json.loads((ROOT / "data/dfs_calibration.json").read_text())
    if not data:
        sys.exit("data/dfs_calibration.json is empty -- run scripts/dfs_calibration.py first")

    dates = sorted(set(r["date"] for r in data))
    pit = [r for r in data if r["is_pitcher"]]
    hit = [r for r in data if not r["is_pitcher"]]

    charts = {
        "pit_pts": {"title": "Pitchers -- DK points", "rows": pit, "pk": "pred_proj", "ak": "actual_pts",
                   "names": [r["player"] for r in pit], "axis": "DK points"},
        "pit_own": {"title": "Pitchers -- ownership %", "rows": pit, "pk": "pred_own", "ak": "actual_own",
                   "names": [r["player"] for r in pit], "axis": "% drafted"},
        "hit_pts": {"title": "Hitters -- DK points", "rows": hit, "pk": "pred_proj", "ak": "actual_pts",
                   "names": [r["player"] for r in hit], "axis": "DK points"},
        "hit_own": {"title": "Hitters -- ownership %", "rows": hit, "pk": "pred_own", "ak": "actual_own",
                   "names": [r["player"] for r in hit], "axis": "% drafted"},
    }

    chart_data = {}
    for key, c in charts.items():
        pairs = [(r[c["pk"]], r[c["ak"]], r["player"], r["date"]) for r in c["rows"]
                 if r.get(c["pk"]) is not None and r.get(c["ak"]) is not None]
        mae = round(statistics.mean(abs(a - p) for p, a, *_ in pairs), 2) if pairs else None

        # per-slate + pooled, BOTH Pearson (what most DFS write-ups report) and
        # Spearman (robust to the skew that inflates Pearson on ownership data --
        # an external review caught the pooled pitcher-ownership Pearson of 0.89
        # collapsing to 0.33 once restricted to the sub-15%-owned range that
        # actually matters for leverage decisions; Spearman doesn't hide that).
        rows_for_stats = [{"date": r["date"], "x": r[c["pk"]], "y": r[c["ak"]]} for r in c["rows"]]
        pearson_summary = cross_slate_summary(rows_for_stats, "date", "x", "y", method="pearson")
        spearman_summary = cross_slate_summary(rows_for_stats, "date", "x", "y", method="spearman")

        chart_data[key] = {
            "title": c["title"], "axis": c["axis"], "n": len(pairs), "mae": mae,
            "pearson": pearson_summary["pooled_corr"], "spearman": spearman_summary["pooled_corr"],
            "n_slates": pearson_summary["n_slates"],
            "cross_slate_se": pearson_summary.get("cross_slate_se"),
            "points": [{"x": p, "y": a, "name": nm, "date": d} for p, a, nm, d in pairs],
        }

    n_own_dates = len(set(r["date"] for r in data if r.get("pred_own") is not None))
    html = _render(chart_data, dates, n_own_dates)
    OUT.write_text(html)
    print(f"wrote {OUT}")
    for key, c in chart_data.items():
        se_str = f" (cross-slate SE {c['cross_slate_se']})" if c.get("cross_slate_se") is not None else ""
        print(f"  {c['title']:24} n={c['n']:4} n_slates={c['n_slates']}  "
              f"pearson={c['pearson']}  spearman={c['spearman']}{se_str}  mae={c['mae']}")


def _render(chart_data, dates, n_own_dates):
    data_json = json.dumps(chart_data)
    date_range = f"{dates[0]} to {dates[-1]}" if dates else "no data"
    return f"""<!doctype html>
<title>DFS Calibration: Predicted vs Actual</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
.viz-root {{
  --surface-1:      #fcfcfb;
  --page:           #f9f9f7;
  --text-primary:   #0b0b0b;
  --text-secondary: #52514e;
  --text-muted:     #898781;
  --grid:           #e1e0d9;
  --axis:           #c3c2b7;
  --series-1:       #2a78d6;
  --series-1-wash:  rgba(42,120,214,0.10);
  --ring:           #fcfcfb;
  --border:         rgba(11,11,11,0.10);
}}
@media (prefers-color-scheme: dark) {{
  .viz-root {{
    --surface-1:      #1a1a19;
    --page:           #0d0d0d;
    --text-primary:   #ffffff;
    --text-secondary: #c3c2b7;
    --text-muted:     #898781;
    --grid:           #2c2c2a;
    --axis:           #383835;
    --series-1:       #3987e5;
    --series-1-wash:  rgba(57,135,229,0.14);
    --ring:           #1a1a19;
    --border:         rgba(255,255,255,0.10);
  }}
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; padding: 24px; background: var(--page); color: var(--text-primary);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
}}
h1 {{ font-size: 20px; margin: 0 0 4px; }}
.sub {{ color: var(--text-secondary); font-size: 13px; margin: 0 0 24px; line-height: 1.5; }}
.section-title {{ font-size: 15px; font-weight: 600; margin: 32px 0 12px; color: var(--text-primary); }}
.grid2 {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 16px; }}
.card {{
  background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px;
  padding: 16px; position: relative;
}}
.card h3 {{ font-size: 13px; font-weight: 600; margin: 0 0 2px; color: var(--text-primary); }}
.stat-line {{ font-size: 12px; color: var(--text-secondary); margin: 0 0 10px; }}
.stat-line b {{ color: var(--text-primary); font-variant-numeric: tabular-nums; }}
svg {{ display: block; width: 100%; height: auto; overflow: visible; }}
.axis-label {{ fill: var(--text-muted); font-size: 10px; }}
.gridline {{ stroke: var(--grid); stroke-width: 1; }}
.refline {{ stroke: var(--axis); stroke-width: 1.5; stroke-dasharray: none; }}
.dot {{ fill: var(--series-1); stroke: var(--ring); stroke-width: 2; }}
.hit {{ fill: transparent; cursor: pointer; }}
.hit:hover + .dot, .dot.hover {{ fill: var(--series-1); r: 6; }}
.tooltip {{
  position: fixed; pointer-events: none; background: var(--text-primary); color: var(--surface-1);
  font-size: 12px; padding: 6px 9px; border-radius: 6px; opacity: 0; transition: opacity 0.1s;
  z-index: 10; white-space: nowrap; font-family: inherit;
}}
.tooltip b {{ font-variant-numeric: tabular-nums; }}
.tooltip.show {{ opacity: 1; }}
details {{ margin-top: 10px; }}
summary {{ font-size: 12px; color: var(--text-secondary); cursor: pointer; }}
table {{ width: 100%; border-collapse: collapse; font-size: 11px; margin-top: 8px; max-height: 240px; }}
.tablewrap {{ max-height: 240px; overflow-y: auto; }}
th, td {{ text-align: left; padding: 3px 6px; border-bottom: 1px solid var(--grid); }}
th {{ color: var(--text-muted); font-weight: 600; position: sticky; top: 0; background: var(--surface-1); }}
td {{ font-variant-numeric: tabular-nums; color: var(--text-secondary); }}
.empty {{ color: var(--text-muted); font-size: 13px; padding: 40px 0; text-align: center; }}
</style>
<body>
<div class="viz-root">
  <h1>DFS Calibration: Predicted vs Actual</h1>
  <p class="sub">Every dot is one player-game. Predicted = our model's proj/own at build time (data/dfs_proj_log.csv);
  actual = DraftKings' own reported FPTS/%Drafted from your contest exports. Dashed line = perfect prediction (y=x) --
  the closer the cloud hugs it, the more calibrated the model. Coverage: {date_range} ({len(dates)} slates).
  Ownership has data for only {n_own_dates} of those slates -- ownership is only computed when a full lineup
  successfully builds that day; a date whose last build was partial (early, pitcher-only) has no logged ownership.</p>

  <div id="pitcher-section"></div>
  <div id="hitter-section"></div>
</div>

<div class="tooltip" id="tooltip"></div>

<script>
const DATA = {data_json};

function niceRange(vals) {{
  if (!vals.length) return [0, 1];
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (lo === hi) {{ lo -= 1; hi += 1; }}
  const pad = (hi - lo) * 0.08;
  return [Math.min(lo - pad, 0), hi + pad];
}}

function buildChart(key, c) {{
  const W = 340, H = 280, M = {{l: 40, r: 14, t: 10, b: 30}};
  const plotW = W - M.l - M.r, plotH = H - M.t - M.b;
  if (!c.points.length) {{
    return `<div class="card"><h3>${{c.title}}</h3><div class="empty">no data yet</div></div>`;
  }}
  const xs = c.points.map(p => p.x), ys = c.points.map(p => p.y);
  const all = xs.concat(ys);
  const [lo, hi] = niceRange(all);
  const sx = v => M.l + ((v - lo) / (hi - lo)) * plotW;
  const sy = v => M.t + plotH - ((v - lo) / (hi - lo)) * plotH;

  const ticks = 5;
  let gridlines = '', axisLabels = '';
  for (let i = 0; i <= ticks; i++) {{
    const v = lo + (hi - lo) * i / ticks;
    const x = sx(v), y = sy(v);
    gridlines += `<line class="gridline" x1="${{M.l}}" y1="${{y}}" x2="${{W-M.r}}" y2="${{y}}"/>`;
    gridlines += `<line class="gridline" x1="${{x}}" y1="${{M.t}}" x2="${{x}}" y2="${{H-M.b}}"/>`;
    axisLabels += `<text class="axis-label" x="${{M.l - 6}}" y="${{y + 3}}" text-anchor="end">${{v.toFixed(0)}}</text>`;
    axisLabels += `<text class="axis-label" x="${{x}}" y="${{H - M.b + 14}}" text-anchor="middle">${{v.toFixed(0)}}</text>`;
  }}

  let dots = '';
  c.points.forEach((p, i) => {{
    const cx = sx(p.x), cy = sy(p.y);
    dots += `<g data-i="${{i}}" data-chart="${{key}}">
      <circle class="hit" cx="${{cx}}" cy="${{cy}}" r="12"/>
      <circle class="dot" cx="${{cx}}" cy="${{cy}}" r="4.5"/>
    </g>`;
  }});

  const seStr = c.cross_slate_se !== null && c.cross_slate_se !== undefined
    ? ` &plusmn;${{c.cross_slate_se}} (cross-slate SE, ${{c.n_slates}} slates)` : '';
  const statLine = c.pearson !== null
    ? `n=<b>${{c.n}}</b> &middot; Pearson=<b>${{c.pearson >= 0 ? '+' : ''}}${{c.pearson}}</b> &middot; `
      + `Spearman=<b>${{c.spearman >= 0 ? '+' : ''}}${{c.spearman}}</b>${{seStr}} &middot; MAE=<b>${{c.mae}}</b>`
    : `n=<b>${{c.n}}</b>`;

  const tableRows = c.points.slice().sort((a, b) => b.y - a.y).map(p =>
    `<tr><td>${{p.date}}</td><td>${{p.name}}</td><td>${{p.x.toFixed(1)}}</td><td>${{p.y.toFixed(1)}}</td></tr>`
  ).join('');

  return `<div class="card">
    <h3>${{c.title}}</h3>
    <p class="stat-line">${{statLine}}</p>
    <svg viewBox="0 0 ${{W}} ${{H}}">
      ${{gridlines}}
      <line class="refline" x1="${{sx(lo)}}" y1="${{sy(lo)}}" x2="${{sx(hi)}}" y2="${{sy(hi)}}"/>
      ${{dots}}
      ${{axisLabels}}
      <text class="axis-label" x="${{W/2}}" y="${{H - 2}}" text-anchor="middle">predicted ${{c.axis}}</text>
      <text class="axis-label" x="${{-H/2}}" y="12" text-anchor="middle" transform="rotate(-90)">actual ${{c.axis}}</text>
    </svg>
    <details>
      <summary>Table view (${{c.n}} rows)</summary>
      <div class="tablewrap"><table>
        <thead><tr><th>Date</th><th>Player</th><th>Predicted</th><th>Actual</th></tr></thead>
        <tbody>${{tableRows}}</tbody>
      </table></div>
    </details>
  </div>`;
}}

document.getElementById('pitcher-section').innerHTML =
  '<div class="section-title">Pitchers</div><div class="grid2">' +
  buildChart('pit_pts', DATA.pit_pts) + buildChart('pit_own', DATA.pit_own) + '</div>';
document.getElementById('hitter-section').innerHTML =
  '<div class="section-title">Hitters</div><div class="grid2">' +
  buildChart('hit_pts', DATA.hit_pts) + buildChart('hit_own', DATA.hit_own) + '</div>';

const tooltip = document.getElementById('tooltip');
document.querySelectorAll('g[data-chart]').forEach(g => {{
  const key = g.dataset.chart, i = +g.dataset.i;
  const p = DATA[key].points[i];
  const dot = g.querySelector('.dot');
  g.addEventListener('pointermove', e => {{
    dot.classList.add('hover');
    tooltip.innerHTML = `<b>${{p.y.toFixed(1)}}</b> actual vs <b>${{p.x.toFixed(1)}}</b> predicted<br>${{p.name}} &middot; ${{p.date}}`;
    tooltip.style.left = (e.clientX + 14) + 'px';
    tooltip.style.top = (e.clientY + 14) + 'px';
    tooltip.classList.add('show');
  }});
  g.addEventListener('pointerleave', () => {{
    dot.classList.remove('hover');
    tooltip.classList.remove('show');
  }});
}});
</script>
</body>
"""


if __name__ == "__main__":
    main()

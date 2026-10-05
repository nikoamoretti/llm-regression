"""Static HTML report for ``runner monitor``: inline SVG, light and dark, hover readouts.

Colors are the validated reference palette (series blue, status critical for
alarms) defined once as CSS custom properties; marks follow the house specs
(2px lines, r=4 dots with a 2px surface ring, hairline solid grids, a 10%
wash for intervals). Every value a tooltip shows is also in a table view.
"""

from __future__ import annotations

import html
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

# Two fixed layouts so text stays legible: the narrow one replaces the wide one
# on phones (CSS media query) instead of shrinking it.
LAYOUTS = {
    "wide": {"w": 720, "h": 240, "left": 44, "right": 64, "top": 16, "bottom": 30, "xticks": 5},
    "narrow": {"w": 360, "h": 220, "left": 36, "right": 52, "top": 16, "bottom": 28, "xticks": 3},
}

CSS = """
.viz-root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --ring: rgba(11,11,11,0.10);
  --series: #2a78d6; --series-wash: rgba(42,120,214,0.10); --ref-wash: rgba(11,11,11,0.035);
  --good: #0ca30c; --good-text: #006300; --critical: #d03b3b;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) .viz-root {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --ring: rgba(255,255,255,0.10);
    --series: #3987e5; --series-wash: rgba(57,135,229,0.12); --ref-wash: rgba(255,255,255,0.04);
    --good: #0ca30c; --good-text: #0ca30c; --critical: #d03b3b;
  }
}
:root[data-theme="dark"] .viz-root {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --ring: rgba(255,255,255,0.10);
  --series: #3987e5; --series-wash: rgba(57,135,229,0.12); --ref-wash: rgba(255,255,255,0.04);
  --good: #0ca30c; --good-text: #0ca30c; --critical: #d03b3b;
}
html, body { margin: 0; }
body { background: var(--page, #f9f9f7); }
.viz-root { background: var(--page); color: var(--ink); min-height: 100vh;
  font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; padding: 24px 16px 48px; box-sizing: border-box; }
.viz-root * { box-sizing: border-box; }
.wrap { max-width: 800px; margin: 0 auto; }
h1 { font-size: 22px; margin: 0 0 4px; }
.sub { color: var(--ink-2); margin: 0 0 24px; }
.card { background: var(--surface); border: 1px solid var(--ring); border-radius: 12px; padding: 16px; margin: 0 0 20px; }
.card-head { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: baseline; justify-content: space-between; }
.card h2 { font-size: 16px; margin: 0; overflow-wrap: break-word; }
.status { display: inline-flex; gap: 6px; align-items: center; font-weight: 600; color: var(--ink); white-space: nowrap; }
.status .icon { display: inline-grid; place-items: center; width: 18px; height: 18px; border-radius: 50%;
  color: #fff; font-size: 11px; line-height: 1; }
.status-alarm .icon { background: var(--critical); }
.status-no_alarm .icon { background: var(--good); }
.status-insufficient .icon { background: var(--muted); }
.message { color: var(--ink-2); margin: 8px 0 12px; }
.message.warn { color: var(--ink); }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; margin: 0 0 12px; }
.tile { border: 1px solid var(--ring); border-radius: 8px; padding: 8px 10px; }
.tile .label { color: var(--ink-2); font-size: 12px; }
.tile .value { font-size: 18px; font-weight: 600; }
.tile .note { color: var(--muted); font-size: 12px; }
figure { margin: 12px 0 0; }
figcaption { color: var(--ink-2); font-size: 13px; margin: 0 0 6px; }
.keys { display: flex; flex-wrap: wrap; gap: 4px 14px; color: var(--ink-2); font-size: 12px; margin: 0 0 4px; }
.key { display: inline-flex; align-items: center; gap: 6px; }
.chart { position: relative; outline: none; }
.chart:focus-visible { box-shadow: 0 0 0 2px var(--series); border-radius: 6px; }
.chart svg { display: block; width: 100%; height: auto; overflow: visible; }
.chart svg text { font: 11px system-ui, -apple-system, "Segoe UI", sans-serif; }
.chart.narrow { display: none; }
@media (max-width: 560px) { .chart.wide { display: none; } .chart.narrow { display: block; } }
.key svg { display: inline-block; flex: none; }
.tick { fill: var(--muted); font-variant-numeric: tabular-nums; }
.end-label { fill: var(--ink-2); font-weight: 600; }
.annot { fill: var(--ink-2); }
details { margin-top: 12px; }
summary { cursor: pointer; color: var(--ink-2); }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; margin-top: 8px; font-variant-numeric: tabular-nums; font-size: 13px; }
th, td { text-align: right; padding: 4px 8px; border-bottom: 1px solid var(--grid); white-space: nowrap; }
th:first-child, td:first-child { text-align: left; }
th { color: var(--ink-2); font-weight: 600; }
#viz-tooltip { position: fixed; z-index: 10; pointer-events: none; background: var(--surface); color: var(--ink);
  border: 1px solid var(--ring); border-radius: 8px; padding: 8px 10px; box-shadow: 0 4px 16px rgba(0,0,0,0.12);
  font-size: 12px; min-width: 160px; }
#viz-tooltip .t-head { color: var(--ink-2); margin-bottom: 4px; }
#viz-tooltip .t-row { display: flex; justify-content: space-between; gap: 12px; }
#viz-tooltip .t-row b { font-weight: 600; }
#viz-tooltip .t-row span { color: var(--ink-2); }
"""

SCRIPT = """
(() => {
  const data = JSON.parse(document.getElementById('viz-data').textContent);
  const tip = document.getElementById('viz-tooltip');
  const fill = (head, rows) => {
    tip.replaceChildren();
    const h = document.createElement('div'); h.className = 't-head'; h.textContent = head; tip.append(h);
    for (const [label, value] of rows) {
      const row = document.createElement('div'); row.className = 't-row';
      const b = document.createElement('b'); b.textContent = value;
      const s = document.createElement('span'); s.textContent = label;
      row.append(s, b); tip.append(row);
    }
  };
  for (const chart of data.charts) {
    const host = document.getElementById(chart.id);
    if (!host || !chart.points.length) continue;
    const svg = host.querySelector('svg');
    const hair = svg.querySelector('.crosshair');
    let index = chart.points.length - 1;
    const show = (i, clientX, clientY) => {
      index = Math.max(0, Math.min(chart.points.length - 1, i));
      const p = chart.points[index];
      hair.setAttribute('x1', p.x); hair.setAttribute('x2', p.x); hair.style.display = '';
      fill(p.head, p.rows); tip.hidden = false;
      const box = svg.getBoundingClientRect();
      const x = clientX ?? box.left + (p.x / chart.width) * box.width;
      const y = clientY ?? box.top + 24;
      const left = Math.min(x + 14, window.innerWidth - tip.offsetWidth - 8);
      tip.style.left = Math.max(8, left) + 'px';
      tip.style.top = Math.max(8, y - tip.offsetHeight - 12) + 'px';
    };
    const nearest = (clientX) => {
      const box = svg.getBoundingClientRect();
      const x = (clientX - box.left) / box.width * chart.width;
      let best = 0;
      chart.points.forEach((p, i) => { if (Math.abs(p.x - x) < Math.abs(chart.points[best].x - x)) best = i; });
      return best;
    };
    const hide = () => { tip.hidden = true; hair.style.display = 'none'; };
    svg.addEventListener('pointermove', (e) => show(nearest(e.clientX), e.clientX, e.clientY));
    svg.addEventListener('pointerleave', hide);
    host.addEventListener('focus', () => show(index));
    host.addEventListener('blur', hide);
    host.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowLeft') { show(index - 1); e.preventDefault(); }
      if (e.key === 'ArrowRight') { show(index + 1); e.preventDefault(); }
      if (e.key === 'Escape') hide();
    });
  }
})();
"""


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{value * 100:.0f}%"


def _day(value: str) -> str:
    when = date.fromisoformat(value[:10])
    return f"{when.strftime('%b')} {when.day}"


def _x_scale(g: dict[str, int], lo: float, hi: float):
    span = (hi - lo) or 1.0
    return lambda value: g["left"] + (value - lo) / span * (g["w"] - g["left"] - g["right"])


def _y_scale(g: dict[str, int], lo: float, hi: float):
    span = (hi - lo) or 1.0
    return lambda value: g["h"] - g["bottom"] - (value - lo) / span * (g["h"] - g["top"] - g["bottom"])


def _ordinal(value: str) -> float:
    return float(date.fromisoformat(value[:10]).toordinal())


def _x_ticks(lo: float, hi: float, count: int = 5) -> list[float]:
    if hi <= lo:
        return [lo]
    step = max(1, round((hi - lo) / (count - 1)))
    ticks, value = [], lo
    while value <= hi + 1e-9:
        ticks.append(value)
        value += step
    return ticks


def _nice_ticks(top: float, most: int = 5) -> list[float]:
    """0..top in clean steps (1, 2, 2.5 or 5 times a power of ten)."""
    raw = max(top, 1e-9) / (most - 1)
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    count = math.ceil(top / step - 1e-9)
    return [round(step * i, 10) for i in range(count + 1)]


def _frame(g: dict[str, int], y, y_ticks: list[float], fmt) -> list[str]:
    right = g["w"] - g["right"]
    parts = []
    for tick in y_ticks:
        yy = y(tick)
        parts.append(f'<line x1="{g["left"]}" x2="{right}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="var(--grid)" stroke-width="1"/>')
        parts.append(f'<text class="tick" x="{g["left"] - 8}" y="{yy + 4:.1f}" text-anchor="end">{html.escape(fmt(tick))}</text>')
    base = y(y_ticks[0])
    parts.append(f'<line x1="{g["left"]}" x2="{right}" y1="{base:.1f}" y2="{base:.1f}" stroke="var(--axis)" stroke-width="1"/>')
    return parts


def _overlay(g: dict[str, int]) -> list[str]:
    return [
        f'<line class="crosshair" x1="0" x2="0" y1="{g["top"]}" y2="{g["h"] - g["bottom"]}" stroke="var(--axis)" '
        'stroke-width="1" style="display:none"/>',
        f'<rect x="{g["left"]}" y="{g["top"]}" width="{g["w"] - g["left"] - g["right"]}" '
        f'height="{g["h"] - g["top"] - g["bottom"]}" fill="transparent"/>',
    ]


def pass_rate_chart(report: dict[str, Any], chart_id: str, g: dict[str, int]) -> tuple[str, dict[str, Any]]:
    daily, rolling = report.get("daily") or [], report.get("rolling") or []
    days = [_ordinal(item["date"]) for item in daily]
    lo, hi = (min(days), max(days)) if days else (0.0, 1.0)
    if hi == lo:
        lo, hi = lo - 1, hi + 1
    x, y = _x_scale(g, lo, hi), _y_scale(g, 0.0, 1.0)
    right, bottom = g["w"] - g["right"], g["h"] - g["bottom"]
    parts = [f'<svg viewBox="0 0 {g["w"]} {g["h"]}" role="img" aria-labelledby="{chart_id}-title">',
             f'<title id="{chart_id}-title">Share of graded attempts that passed, {html.escape(report["series"])}</title>']
    ref = report.get("reference")
    if ref:
        x0, x1 = x(max(_ordinal(ref["start"]) - 0.5, lo)), x(min(_ordinal(ref["end"]) + 0.5, hi))
        parts.append(f'<rect x="{x0:.1f}" y="{g["top"]}" width="{max(x1 - x0, 1):.1f}" height="{bottom - g["top"]}" fill="var(--ref-wash)"/>')
        parts.append(f'<text class="annot" x="{x0 + 6:.1f}" y="{g["top"] + 12}">reference</text>')
    parts += _frame(g, y, [0, 0.25, 0.5, 0.75, 1.0], lambda t: f"{t * 100:.0f}%")
    for tick in _x_ticks(lo, hi, g["xticks"]):
        parts.append(
            f'<text class="tick" x="{x(tick):.1f}" y="{g["h"] - 10}" text-anchor="middle">'
            f"{html.escape(_day(date.fromordinal(int(tick)).isoformat()))}</text>"
        )
    if ref and ref.get("rate") is not None:
        yy = y(ref["rate"])
        parts.append(f'<line x1="{g["left"]}" x2="{right}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="var(--muted)" stroke-width="1"/>')
        parts.append(f'<text class="annot" x="{right + 6}" y="{yy + 4:.1f}">ref {_pct(ref["rate"])}</text>')
    banded = [item for item in rolling if item["lo"] is not None]
    if banded:
        upper = [f"{x(_ordinal(i['date'])):.1f},{y(i['hi']):.1f}" for i in banded]
        lower = [f"{x(_ordinal(i['date'])):.1f},{y(i['lo']):.1f}" for i in reversed(banded)]
        parts.append(f'<polygon points="{" ".join(upper + lower)}" fill="var(--series-wash)"/>')
        line = " ".join(f"{x(_ordinal(i['date'])):.1f},{y(i['rate']):.1f}" for i in banded)
        parts.append(f'<polyline points="{line}" fill="none" stroke="var(--series)" stroke-width="2" '
                     'stroke-linejoin="round" stroke-linecap="round"/>')
        end_item = banded[-1]
        parts.append(f'<text class="end-label" x="{x(_ordinal(end_item["date"])) + 8:.1f}" y="{y(end_item["rate"]) - 8:.1f}">'
                     f"{_pct(end_item['rate'])}</text>")
    for item in daily:
        if item["rate"] is None:
            continue
        parts.append(f'<circle cx="{x(_ordinal(item["date"])):.1f}" cy="{y(item["rate"]):.1f}" r="4" '
                     'fill="var(--series)" stroke="var(--surface)" stroke-width="2"/>')
    parts += _overlay(g)
    parts.append("</svg>")
    by_date = {item["date"]: item for item in rolling}
    points = []
    for item in daily:
        roll = by_date.get(item["date"], {})
        points.append(
            {
                "x": round(x(_ordinal(item["date"])), 1),
                "head": _day(item["date"]),
                "rows": [
                    ["day", f"{_pct(item['rate'])} of {item['n']}"],
                    [f"{report['window_days']}-day", f"{_pct(roll.get('rate'))} ({_pct(roll.get('lo'))}–{_pct(roll.get('hi'))})"],
                ],
            }
        )
    return "".join(parts), {"id": chart_id, "width": g["w"], "points": points}


def cusum_chart(report: dict[str, Any], chart_id: str, g: dict[str, int]) -> tuple[str, dict[str, Any]]:
    cusum = report["cusum"]
    path, threshold = cusum["path"], cusum["threshold"]
    n = max(len(path), 2)
    y_ticks = _nice_ticks(max([threshold * 1.15] + [item["statistic"] for item in path]))
    x, y = _x_scale(g, 0, n - 1), _y_scale(g, 0.0, y_ticks[-1])
    right = g["w"] - g["right"]
    parts = [f'<svg viewBox="0 0 {g["w"]} {g["h"]}" role="img" aria-labelledby="{chart_id}-title">',
             f'<title id="{chart_id}-title">Risk-adjusted CUSUM, {html.escape(report["series"])}</title>']
    parts += _frame(g, y, y_ticks, lambda t: f"{t:g}")
    for index in sorted({0, (len(path) - 1) // 2, len(path) - 1}):
        if 0 <= index < len(path):
            parts.append(f'<text class="tick" x="{x(index):.1f}" y="{g["h"] - 10}" text-anchor="middle">'
                         f"{html.escape(_day(path[index]['when']))}</text>")
    yy = y(threshold)
    parts.append(f'<line x1="{g["left"]}" x2="{right}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="var(--muted)" stroke-width="1"/>')
    parts.append(f'<text class="annot" x="{right + 6}" y="{yy + 4:.1f}">h {threshold:.2f}</text>')
    if path:
        line = " ".join(f"{x(i):.1f},{y(item['statistic']):.1f}" for i, item in enumerate(path))
        parts.append(f'<polyline points="{line}" fill="none" stroke="var(--series)" stroke-width="2" '
                     'stroke-linejoin="round" stroke-linecap="round"/>')
    last_label = -1e9
    for alarm in cusum["alarms"]:
        item = path[alarm["index"]]
        cx, cy = x(alarm["index"]), y(item["statistic"])
        parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" fill="var(--critical)" stroke="var(--surface)" stroke-width="2"/>')
        # Label selectively: skip a label that would collide with the previous one.
        if cx - last_label > 40:
            parts.append(f'<text class="annot" x="{cx:.1f}" y="{cy - 10:.1f}" text-anchor="middle">alarm</text>')
            last_label = cx
    parts += _overlay(g)
    parts.append("</svg>")
    points = [
        {
            "x": round(x(i), 1),
            "head": f"{_day(item['when'])} · attempt {i + 1}",
            "rows": [
                ["task", item["task"]],
                ["outcome", "pass" if item["passed"] else "fail"],
                ["expected pass", _pct(item["expected"])],
                ["statistic", f"{item['statistic']:.2f}" + (" (alarm)" if item["alarm"] else "")],
            ],
        }
        for i, item in enumerate(path)
    ]
    return "".join(parts), {"id": chart_id, "width": g["w"], "points": points}


def _charts(kind: str, render, report: dict[str, Any], index: int, charts: list[dict[str, Any]]) -> str:
    hosts = []
    for name, layout in LAYOUTS.items():
        chart_id = f"{kind}-{index}-{name}"
        svg, meta = render(report, chart_id, layout)
        charts.append(meta)
        hosts.append(f'<div class="chart {name}" id="{chart_id}" tabindex="0">{svg}</div>')
    return "".join(hosts)


def _when(value: str) -> str:
    when = datetime.fromisoformat(value)
    return f"{_day(value)}, {when.strftime('%H:%M')} UTC"


STATUS = {
    "alarm": ("!", "Alarm"),
    "no_alarm": ("✓", "No alarm"),
    "insufficient": ("…", "Not enough data"),
}


def _tile(label: str, value: str, note: str = "") -> str:
    return (f'<div class="tile"><div class="label">{html.escape(label)}</div>'
            f'<div class="value">{html.escape(value)}</div>'
            + (f'<div class="note">{html.escape(note)}</div>' if note else "") + "</div>")


def _section(report: dict[str, Any], index: int, charts: list[dict[str, Any]]) -> str:
    icon, label = STATUS[report["status"]]
    out = [f'<section class="card" id="series-{index}"><div class="card-head">'
           f"<h2>{'/<wbr>'.join(html.escape(part) for part in report['series'].split('/'))}</h2>"
           f'<span class="status status-{report["status"]}"><span class="icon" aria-hidden="true">{icon}</span>{label}</span>'
           f'</div><p class="message">{html.escape(report.get("message", ""))}</p>']
    if report.get("warning"):
        out.append(f'<p class="message warn"><span aria-hidden="true">⚠ </span>{html.escape(report["warning"])}</p>')
    ref, cur = report.get("reference"), report.get("current")
    if ref:
        tiles = [
            _tile("Reference pass rate", _pct(ref["rate"]), f"{ref['n']} attempts, {_day(ref['start'])}–{_day(ref['end'])}"),
            _tile("Latest window", _pct(cur["rate"]) if cur else "–",
                  f"{cur['n']} attempts, {_day(cur['start'])}–{_day(cur['end'])}" if cur and cur["n"] else ""),
        ]
        paired = report.get("paired")
        if paired and paired.get("mean_diff") is not None:
            lo, hi = paired["ci"]
            tiles.append(_tile("Task-paired change", f"{paired['mean_diff'] * 100:+.1f} pts",
                               f"95% CI {lo * 100:+.1f} to {hi * 100:+.1f}, p={paired['p_value']:.2f}"))
        if report.get("cusum"):
            cusum = report["cusum"]
            detect = cusum.get("attempts_to_detect") or {}
            tiles.append(_tile("Alarm sensitivity", f"~{detect.get('20', 0):.0f} attempts",
                               f"to flag a 20-point drop (~{detect.get('10', 0):.0f} for 10); "
                               f"a false alarm every ~{cusum['arl0']:g} if nothing changed"))
        out.append('<div class="tiles">' + "".join(tiles) + "</div>")
    if report.get("daily"):
        out.append(
            "<figure><figcaption>Share of graded attempts that passed (strict)</figcaption>"
            '<div class="keys"><span class="key"><svg width="12" height="12" aria-hidden="true">'
            '<circle cx="6" cy="6" r="4" fill="var(--series)"/></svg>daily</span>'
            '<span class="key"><svg width="18" height="12" aria-hidden="true"><line x1="1" x2="17" y1="6" y2="6" '
            f'stroke="var(--series)" stroke-width="2" stroke-linecap="round"/></svg>trailing {report["window_days"]}-day, '
            "with 95% interval</span></div>"
            + _charts("rate", pass_rate_chart, report, index, charts)
            + "</figure>"
        )
    if report.get("cusum") and report["cusum"]["path"]:
        out.append(
            "<figure><figcaption>Risk-adjusted CUSUM: rises on failures the reference did not expect; "
            "an alarm when it crosses h</figcaption>"
            + _charts("cusum", cusum_chart, report, index, charts)
            + "</figure>"
        )
    paired = report.get("paired") or {}
    if paired.get("per_task"):
        rows = "".join(
            f"<tr><td>{html.escape(item['task'])}</td><td>{_pct(item['reference'])} ({item['reference_n']})</td>"
            f"<td>{_pct(item['current'])} ({item['current_n']})</td><td>{item['diff'] * 100:+.0f}</td></tr>"
            for item in paired["per_task"]
        )
        out.append("<details><summary>Per-task comparison</summary><div class=\"scroll\"><table>"
                   "<tr><th>Task</th><th>Reference</th><th>Latest window</th><th>Change (pts)</th></tr>"
                   f"{rows}</table></div></details>")
    if report.get("daily"):
        rolling = {item["date"]: item for item in report.get("rolling") or []}
        rows = "".join(
            f"<tr><td>{item['date']}</td><td>{item['n']}</td><td>{item['passes']}</td><td>{_pct(item['rate'])}</td>"
            f"<td>{_pct(rolling.get(item['date'], {}).get('rate'))}</td></tr>"
            for item in report["daily"]
        )
        out.append("<details><summary>Daily data</summary><div class=\"scroll\"><table>"
                   f"<tr><th>Date</th><th>Attempts</th><th>Passed</th><th>Day</th><th>{report['window_days']}-day</th></tr>"
                   f"{rows}</table></div></details>")
    if report.get("cusum") and report["cusum"]["alarms"]:
        items = "".join(
            f"<li>{html.escape(_when(alarm['when']))} · attempt {alarm['index'] + 1}</li>"
            for alarm in report["cusum"]["alarms"]
        )
        out.append(f"<details open><summary>Alarms</summary><ul>{items}</ul></details>")
    out.append("</section>")
    return "".join(out)


def render_html(reports: list[dict[str, Any]], generated_at: datetime | None = None) -> str:
    generated = (generated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    charts: list[dict[str, Any]] = []
    sections = "".join(_section(report, index, charts) for index, report in enumerate(reports))
    if not reports:
        sections = '<section class="card"><p class="message">No graded scientific attempts yet.</p></section>'
    payload = json.dumps({"charts": charts}).replace("</", "<\\/")
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Drift monitor</title><style>{CSS}</style></head><body>"
        '<div class="viz-root"><div class="wrap"><h1>Drift monitor</h1>'
        f'<p class="sub">Generated {generated}. Graded scientific attempts only; each series is model, effort and '
        "track. A drop here means strict task success decreased on this benchmark, not that a model changed.</p>"
        f"{sections}</div>"
        '<div id="viz-tooltip" role="status" hidden></div></div>'
        f'<script type="application/json" id="viz-data">{payload}</script>'
        f"<script>{SCRIPT}</script></body></html>"
    )


def write_report(reports: list[dict[str, Any]], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "monitor.json").write_text(json.dumps(reports, indent=2) + "\n", encoding="utf-8")
    path = out_dir / "index.html"
    path.write_text(render_html(reports), encoding="utf-8")
    return path

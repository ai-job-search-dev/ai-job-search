#!/usr/bin/env python3
"""Render the /html-report dashboard: one self-contained, offline HTML file.

Data comes from tools/report_data.py (unchanged figures); this module only
draws them. Charts are inline SVG or CSS, fonts are the system UI and
monospace stacks, and the page makes no network requests.

Usage: python3 tools/html_report.py [--out reports/application-dashboard.html] [--root .]
"""
import argparse
import datetime
import os
import re
import sys
from html import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from report_data import STATUS_ORDER, load  # noqa: E402

KEY = {"Drafted": "drafted", "Active": "active", "Interview": "interview", "Offer": "offer",
       "Hired": "hired", "Rejected/Closed": "rejected"}
STAGES = ["Application screen", "Recruiter screen", "Technical round", "Case or presentation", "Final round"]


def E(v):
    return escape(str(v), quote=True)


def parse_date(s):
    try:
        return datetime.date.fromisoformat((s or "").strip()[:10])
    except ValueError:
        return None


def stopped_at(r, archives):
    ticked = [n for t, n in archives.get(r["_a"], []) if t == "x"] if r["_a"] else []
    text = " ".join(ticked).lower()
    for stage, pat in (("Final round", "final"), ("Case or presentation", "case"),
                       ("Technical round", "technical"), ("Recruiter screen", "phone|screen")):
        if re.search(pat, text):
            return stage
    return "Application screen"


def hidden_table(caption, pairs):
    body = "".join(f"<tr><th scope='row'>{E(k)}</th><td>{E(v)}</td></tr>" for k, v in pairs)
    return f"<table class='sr-only'><caption>{E(caption)}</caption>{body}</table>"


def hbars(pairs, total=None, var="--accent"):
    """Horizontal bar chart drawn with CSS; pairs are (label, value)."""
    mx = max([v for _, v in pairs] or [1]) or 1
    label = ", ".join(f"{k} {v}" for k, v in pairs)
    rows = []
    for k, v in pairs:
        pct = f" · {v / total * 100:.0f}%" if total else ""
        width = v / mx * 100
        rows.append(f"<div class='hb-row'><span class='lbl'>{E(k)}</span>"
                    f"<span class='hb-track'><span class='hb-fill' style='width:{width:.1f}%;background:var({var})'></span></span>"
                    f"<span class='val'>{v}{pct}</span></div>")
    return f"<div class='hbars' role='img' aria-label='{E(label)}'>{''.join(rows)}</div>"


def weekly(sent, today):
    dates = [d for d in (parse_date(r["date"]) for r in sent) if d]
    if not dates:
        return "<p class='empty'>No dated applications yet.</p>", []
    start = min(dates) - datetime.timedelta(days=min(dates).weekday())
    end = today - datetime.timedelta(days=today.weekday())
    weeks = []
    w = start
    while w <= end:
        weeks.append(w)
        w += datetime.timedelta(days=7)
    weeks = weeks[-12:]
    counts = [sum(1 for d in dates if wk <= d < wk + datetime.timedelta(days=7)) for wk in weeks]
    mx = max(counts) or 1
    width, h, base, left = 520, 180, 150, 28
    col = (width - left) / len(weeks)
    bw = min(col * 0.62, 34)
    parts = [f"<svg class='chart' viewBox='0 0 {width} {h}' role='img' aria-label='Applications per week: "
             + E(", ".join(f"week of {wk:%d %b} {c}" for wk, c in zip(weeks, counts))) + "'>"]
    for g in (0, 0.5, 1):
        y = base - (base - 12) * g
        parts.append(f"<line x1='{left}' x2='{width}' y1='{y:.1f}' y2='{y:.1f}' class='grid'/>"
                     f"<text x='{left - 6}' y='{y + 4:.1f}' class='tick' text-anchor='end'>{round(mx * g)}</text>")
    for i, (wk, c) in enumerate(zip(weeks, counts)):
        x = left + i * col + (col - bw) / 2
        bh = (base - 12) * c / mx
        parts.append(f"<rect x='{x:.1f}' y='{base - bh:.1f}' width='{bw:.1f}' height='{bh:.1f}' rx='3' "
                     f"style='fill:var(--st-active)'><title>Week of {wk:%d %b}: {c}</title></rect>")
        if c:
            parts.append(f"<text x='{x + bw / 2:.1f}' y='{base - bh - 5:.1f}' class='val' text-anchor='middle'>{c}</text>")
        if i % 2 == 0 or len(weeks) <= 6:
            parts.append(f"<text x='{x + bw / 2:.1f}' y='{base + 18}' class='tick' text-anchor='middle'>{wk:%d %b}</text>")
    parts.append("</svg>")
    return "".join(parts), [(f"Week of {wk:%d %b %Y}", c) for wk, c in zip(weeks, counts)]


def status_mix(counts):
    total = sum(counts.values()) or 1
    segs, legend = [], []
    for s in STATUS_ORDER:
        v = counts.get(s, 0)
        if not v:
            continue
        pct = v / total * 100
        segs.append(f"<span class='seg' style='width:{pct:.2f}%;background:var(--st-{KEY[s]})' title='{E(s)}: {v}'></span>")
        legend.append(f"<li><span class='sw' style='background:var(--st-{KEY[s]})'></span>{E(s)}"
                      f"<span class='num'>{v}</span><span class='muted num'>{pct:.0f}%</span></li>")
    label = "Status mix: " + ", ".join(f"{counts.get(s, 0)} {s}" for s in STATUS_ORDER if counts.get(s))
    return (f"<div class='stack' role='img' aria-label='{E(label)}'>{''.join(segs)}</div>"
            f"<ul class='legend'>{''.join(legend)}</ul>")


def render(d):
    rows, sent, counts = d["rows"], d["sent"], d["counts"]
    today = datetime.date.fromisoformat(d["today"])
    fun = d["funnel"]

    # KPI strip
    kpis = [("Sent", len(sent), ""), ("Active", counts.get("Active", 0), "active"),
            ("Interview", counts.get("Interview", 0), "interview"), ("Offer", counts.get("Offer", 0), "offer"),
            ("Closed", counts.get("Rejected/Closed", 0), "rejected"),
            ("Drafted", counts.get("Drafted", 0), "drafted"),
            ("Past CV screen", d["progress_rate"], ""), ("Rejection rate", d["rejection_rate"], "")]
    kpi_html = "".join(
        f"<div class='kpi'><span class='kpi-label'>"
        + (f"<span class='sw' style='background:var(--st-{k})'></span>" if k else "")
        + f"{E(label)}</span><span class='kpi-value num'>{E(v)}</span></div>"
        for label, v, k in kpis)

    funnel = hbars(fun, total=fun[0][1] or None, var="--st-active")
    week_svg, week_pairs = weekly(sent, today)
    closed = [r for r in rows if r["_b"] == "Rejected/Closed"]
    stop_counts = [(s, sum(1 for r in closed if stopped_at(r, d["archives"]) == s)) for s in STAGES]
    stop_svg = hbars(stop_counts, total=len(closed) or None, var="--st-rejected")
    top = d["sector"].most_common()
    sector_pairs = top[:8] + ([(f"Other ({len(top) - 8} sectors)", sum(v for _, v in top[8:]))] if len(top) > 8 else [])
    channel_pairs = d["channel"].most_common()

    waiting = sorted(((r, parse_date(r["date"])) for r in rows if r["_b"] == "Active"),
                     key=lambda x: x[1] or today)
    waiting = [(r, (today - dt).days) for r, dt in waiting if dt][:8]
    wait_rows = "".join(
        f"<tr><td>{E(r['company'])}</td><td>{E(r['role'])}</td>"
        f"<td class='num right'>{days}</td></tr>" for r, days in waiting) or \
        "<tr><td colspan='3' class='empty'>Nothing is waiting on a reply.</td></tr>"

    # Applications table
    rows_sorted = sorted(rows, key=lambda r: (r["company"] or "").lower())
    rows_sorted.sort(key=lambda r: r["date"] or "", reverse=True)
    show_deadline = any((r.get("deadline") or "").strip() for r in rows)
    trs = []
    for r in rows_sorted:
        notes = (r.get("notes") or "").strip()
        short = notes[:90] + ("…" if len(notes) > 90 else "")
        src = (r.get("source") or "").strip()
        src_cell = (f"<a href='{E(src)}' rel='noopener noreferrer' target='_blank'>Open</a>"
                    if src.startswith(("http://", "https://")) else (E(src[:24]) or "<span class='muted'>None</span>"))
        fit = (r.get("fit_rating") or "").strip()
        dl = (r.get("deadline") or "").strip()
        sector, channel = (r["sector"] or "").strip(), (r.get("channel") or "").strip()
        cells = [
            f"<td class='num' data-v='{E(r['date'] or '')}'>{E(r['date'] or 'None')}</td>",
            f"<td class='num' data-v='{E(dl)}'>{E(dl) or '<span class=muted>None</span>'}</td>" if show_deadline else "",
            f"<td data-v='{E(r['company'].lower())}'><strong>{E(r['company'])}</strong></td>",
            f"<td data-v='{E(r['role'].lower())}'>{E(r['role'])}</td>",
            f"<td data-v='{E(sector.lower())}'>{E(sector) or '<span class=muted>None</span>'}</td>",
            f"<td data-v='{E(channel.lower())}'>{E(channel) or '<span class=muted>None</span>'}</td>",
            f"<td data-v='{E(r['_b'])}'><span class='badge b-{KEY[r['_b']]}'>{E(r['_b'])}</span></td>",
            f"<td class='num right' data-v='{E(fit.zfill(3) if fit.isdigit() else '')}'>{E(fit) or '<span class=muted>None</span>'}</td>",
            f"<td class='notes' title='{E(notes)}'>{E(short) or '<span class=muted>None</span>'}</td>",
            f"<td>{src_cell}</td>",
        ]
        trs.append(
            f"<tr data-status='{E(r['_b'])}' data-sector='{E(sector)}' "
            f"data-text='{E((r['company'] + ' ' + r['role'] + ' ' + sector).lower())}'>"
            + "".join(cells) + "</tr>")
    cols = [("Date", True, ""), ("Deadline", True, "") if show_deadline else None, ("Company", True, ""),
            ("Role", True, ""), ("Sector", True, ""), ("Channel", True, ""), ("Status", True, ""),
            ("Fit", True, "right"), ("Notes", False, ""), ("Source", False, "")]
    cols = [c for c in cols if c]
    thead = "".join(
        f"<th scope='col'{' class=' + repr(cls) if cls else ''}{' aria-sort=' + repr('descending') if i == 0 else ''}>"
        + (f"<button class='sort' data-col='{i}'>{name}</button>" if sortable else name) + "</th>"
        for i, (name, sortable, cls) in enumerate(cols))
    sectors = sorted({(r["sector"] or "").strip() for r in rows if (r["sector"] or "").strip()})
    sector_opts = "".join(f"<option>{E(s)}</option>" for s in sectors)
    status_opts = "".join(f"<option>{E(s)}</option>" for s in STATUS_ORDER)
    unknown = (f"<p class='muted small'>Unrecognised status values: {E(', '.join(sorted(d['unknown'])))}</p>"
               if d["unknown"] else "")

    return TEMPLATE.format(
        today=E(today.strftime("%d %b %Y")), iso=E(d["today"]), kpis=kpi_html,
        funnel=funnel, funnel_table=hidden_table("Application funnel", fun),
        mix=status_mix(counts), week=week_svg, week_table=hidden_table("Applications per week", week_pairs),
        stopped=stop_svg, stopped_table=hidden_table("Where closed applications stopped", stop_counts),
        closed=len(closed), wait_rows=wait_rows, rows="".join(trs), total=len(rows),
        sector_opts=sector_opts, status_opts=status_opts, unknown=unknown,
        sent=len(sent), interviews=fun[1][1], thead=thead,
        sector=hbars(sector_pairs, total=len(sent) or None), sector_table=hidden_table("Sent by sector", sector_pairs),
        channel=hbars(channel_pairs, total=len(sent) or None), channel_table=hidden_table("Sent by channel", channel_pairs))


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job search dashboard</title>
<link rel="icon" href="data:,">
<style>
:root {{
  --font-sans: system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  --font-mono: ui-monospace, 'SF Mono', SFMono-Regular, 'Cascadia Mono', 'Segoe UI Mono', Menlo, Consolas, 'Roboto Mono', monospace;
  --fs-xs: 12px; --fs-sm: 14px; --fs-md: 16px; --fs-lg: 20px; --fs-xl: 28px; --fs-2xl: 36px;
  --sp-1: 4px; --sp-2: 8px; --sp-3: 12px; --sp-4: 16px; --sp-5: 24px; --sp-6: 32px; --sp-7: 48px;
  --r-sm: 6px; --r-md: 10px;
  --bg: #F6F7F9; --surface: #FFFFFF; --surface-2: #F1F3F6; --border: #E2E6EC;
  --text: #16202E; --text-muted: #5B6676; --accent: #3E63DD;
  --st-drafted: #7C8594; --st-active: #3E63DD; --st-interview: #C97A1E; --st-offer: #8E4EC6;
  --st-hired: #2A9461; --st-rejected: #DC3E42;
  --tx-drafted: #60646C; --tx-active: #3A5BC7; --tx-interview: #9A5600; --tx-offer: #8145B5;
  --tx-hired: #1E7A4E; --tx-rejected: #C22D32;
  --shadow: 0 1px 2px rgba(22, 32, 46, .06);
  color-scheme: light;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #0F1217; --surface: #161A21; --surface-2: #1C212A; --border: #2A313C;
    --text: #E6E9EE; --text-muted: #98A2B3; --accent: #7B93FF;
    --st-drafted: #8B95A5; --st-active: #7B93FF; --st-interview: #F0A04B; --st-offer: #B184E8;
    --st-hired: #3DD68C; --st-rejected: #FF6369;
    --tx-drafted: #B0B4BA; --tx-active: #9EB1FF; --tx-interview: #FFCA16; --tx-offer: #D19DFF;
    --tx-hired: #3DD68C; --tx-rejected: #FF9592;
    --shadow: 0 1px 2px rgba(0, 0, 0, .4);
    color-scheme: dark;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #0F1217; --surface: #161A21; --surface-2: #1C212A; --border: #2A313C;
  --text: #E6E9EE; --text-muted: #98A2B3; --accent: #7B93FF;
  --st-drafted: #8B95A5; --st-active: #7B93FF; --st-interview: #F0A04B; --st-offer: #B184E8;
  --st-hired: #3DD68C; --st-rejected: #FF6369;
  --tx-drafted: #B0B4BA; --tx-active: #9EB1FF; --tx-interview: #FFCA16; --tx-offer: #D19DFF;
  --tx-hired: #3DD68C; --tx-rejected: #FF9592;
  --shadow: 0 1px 2px rgba(0, 0, 0, .4);
  color-scheme: dark;
}}
* {{ box-sizing: border-box; }}
html {{ scroll-behavior: smooth; scroll-padding-top: 72px; }}
@media (prefers-reduced-motion: reduce) {{ html {{ scroll-behavior: auto; }} }}
body {{ margin: 0; background: var(--bg); color: var(--text); font-family: var(--font-sans);
  font-size: var(--fs-sm); line-height: 1.5; -webkit-font-smoothing: antialiased; }}
.num, .kpi-value, table td.num, .val, .tick, .lbl {{ font-family: var(--font-mono); font-variant-numeric: tabular-nums; }}
a {{ color: var(--accent); text-underline-offset: 2px; }}
:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; border-radius: var(--r-sm); }}
.muted {{ color: var(--text-muted); }}
.small {{ font-size: var(--fs-xs); }}
.sr-only {{ position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0; }}

header {{ position: sticky; top: 0; z-index: 10; background: var(--surface); border-bottom: 1px solid var(--border); box-shadow: var(--shadow); }}
.bar {{ max-width: 1240px; margin: 0 auto; padding: var(--sp-3) var(--sp-5); display: flex; align-items: center; gap: var(--sp-5); flex-wrap: wrap; }}
.brand {{ display: flex; flex-direction: column; margin-right: auto; }}
.brand h1 {{ margin: 0; font-size: var(--fs-lg); font-weight: 600; line-height: 1.2; }}
.brand span {{ font-size: var(--fs-xs); color: var(--text-muted); font-family: var(--font-mono); }}
nav {{ display: flex; gap: var(--sp-1); }}
nav a {{ font: inherit; font-size: var(--fs-sm); color: var(--text-muted); text-decoration: none; padding: var(--sp-1) var(--sp-3); border-radius: var(--r-sm); border: 1px solid transparent; }}
nav a:hover {{ color: var(--text); background: var(--surface-2); }}
.theme {{ position: relative; display: inline-flex; align-items: center; justify-content: space-between; width: 60px; height: 30px; padding: 0 7px; border: 1px solid var(--border); border-radius: 999px; background: var(--surface-2); color: var(--text-muted); cursor: pointer; }}
.theme svg {{ position: relative; z-index: 1; width: 16px; height: 16px; transition: color .2s; }}
.theme::before {{ content: ""; position: absolute; top: 2px; left: 2px; width: 24px; height: 24px; border-radius: 50%; background: var(--surface); box-shadow: 0 1px 2px rgba(0,0,0,.2); transition: transform .2s; }}
.theme .sun {{ color: var(--st-interview); }}
.theme[aria-checked="true"]::before {{ transform: translateX(30px); }}
.theme[aria-checked="true"] .sun {{ color: var(--text-muted); }}
.theme[aria-checked="true"] .moon {{ color: var(--accent); }}
@media (prefers-reduced-motion: reduce) {{ .theme::before, .theme svg {{ transition: none; }} }}

main {{ max-width: 1240px; margin: 0 auto; padding: var(--sp-5); display: grid; gap: var(--sp-5); }}
section {{ display: grid; gap: var(--sp-4); }}
h2 {{ margin: 0; font-size: var(--fs-md); font-weight: 600; }}
.panel {{ background: var(--surface); border: 1px solid var(--border); border-radius: var(--r-md); padding: var(--sp-5); min-width: 0; }}
.panel header, .panel-head {{ position: static; box-shadow: none; border: 0; background: none; display: flex; justify-content: space-between; align-items: baseline; gap: var(--sp-3); margin-bottom: var(--sp-4); }}
.panel-head p {{ margin: 0; color: var(--text-muted); font-size: var(--fs-xs); }}
.grid-2 {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--sp-4); }}

.kpis {{ display: grid; grid-template-columns: repeat(8, minmax(0, 1fr)); background: var(--surface); border: 1px solid var(--border); border-radius: var(--r-md); }}
.kpi {{ padding: var(--sp-4) var(--sp-5); display: flex; flex-direction: column; gap: var(--sp-2); border-left: 1px solid var(--border); }}
.kpi:first-child {{ border-left: 0; }}
.kpi-label {{ font-size: var(--fs-xs); color: var(--text-muted); display: flex; align-items: center; gap: var(--sp-2); }}
.kpi-value {{ font-size: var(--fs-xl); font-weight: 600; line-height: 1.1; }}
.sw {{ width: 8px; height: 8px; border-radius: 2px; display: inline-block; flex: none; }}

.chart {{ width: 100%; height: auto; display: block; overflow: visible; }}
.chart .lbl {{ font-size: 13px; fill: var(--text); }}
.chart .val {{ font-size: 12px; fill: var(--text-muted); }}
.chart .tick {{ font-size: 11px; fill: var(--text-muted); }}
.chart .track {{ fill: var(--surface-2); }}
.chart .grid {{ stroke: var(--border); stroke-width: 1; }}

.hbars {{ display: grid; grid-template-columns: max-content minmax(0, 1fr) max-content; gap: var(--sp-3) var(--sp-4); align-items: center; }}
.hb-row {{ display: contents; }}
.hbars .lbl {{ font-size: 13px; color: var(--text); }}
.hbars .val {{ font-size: 12px; color: var(--text-muted); text-align: right; min-width: 72px; }}
.hb-track {{ display: block; height: 16px; border-radius: 4px; background: var(--surface-2); overflow: hidden; }}
.hb-fill {{ display: block; height: 100%; border-radius: 4px; min-width: 0; }}
.stack {{ display: flex; height: 16px; border-radius: 4px; overflow: hidden; gap: 2px; background: var(--surface-2); }}
.seg {{ display: block; height: 100%; min-width: 3px; }}
.legend {{ list-style: none; padding: 0; margin: var(--sp-4) 0 0; display: grid; gap: var(--sp-2); }}
.legend li {{ display: grid; grid-template-columns: 8px 1fr auto 48px; align-items: center; gap: var(--sp-3); padding: var(--sp-1) 0; border-bottom: 1px solid var(--border); }}
.legend li:last-child {{ border-bottom: 0; }}
.legend .num {{ text-align: right; }}

table.data {{ width: 100%; border-collapse: collapse; font-size: var(--fs-sm); }}
table.data th, table.data td {{ text-align: left; padding: var(--sp-2) var(--sp-3); border-bottom: 1px solid var(--border); vertical-align: top; }}
table.data thead th {{ position: sticky; top: 0; background: var(--surface); z-index: 1; font-size: var(--fs-xs); font-weight: 600; color: var(--text-muted); white-space: nowrap; }}
table.data tbody tr:hover {{ background: var(--surface-2); }}
table.data .right {{ text-align: right; }}
table.data td.num {{ white-space: nowrap; }}
td.notes {{ color: var(--text-muted); max-width: 360px; }}
.sort {{ font: inherit; color: inherit; background: none; border: 0; padding: 0; cursor: pointer; display: inline-flex; gap: var(--sp-1); align-items: center; }}
.sort::after {{ content: '↕'; opacity: .4; font-size: 11px; }}
th[aria-sort="ascending"] .sort::after {{ content: '↑'; opacity: 1; }}
th[aria-sort="descending"] .sort::after {{ content: '↓'; opacity: 1; }}
.table-wrap {{ overflow: auto; max-height: 70vh; border: 1px solid var(--border); border-radius: var(--r-sm); }}
.badge {{ display: inline-block; padding: 1px var(--sp-2); border-radius: 999px; font-size: var(--fs-xs); font-weight: 600; white-space: nowrap; border: 1px solid currentColor; }}
.b-drafted {{ color: var(--tx-drafted); background: color-mix(in srgb, var(--st-drafted) 12%, transparent); }}
.b-active {{ color: var(--tx-active); background: color-mix(in srgb, var(--st-active) 12%, transparent); }}
.b-interview {{ color: var(--tx-interview); background: color-mix(in srgb, var(--st-interview) 12%, transparent); }}
.b-offer {{ color: var(--tx-offer); background: color-mix(in srgb, var(--st-offer) 12%, transparent); }}
.b-hired {{ color: var(--tx-hired); background: color-mix(in srgb, var(--st-hired) 12%, transparent); }}
.b-rejected {{ color: var(--tx-rejected); background: color-mix(in srgb, var(--st-rejected) 12%, transparent); }}

.filters {{ display: flex; gap: var(--sp-3); flex-wrap: wrap; align-items: center; margin-bottom: var(--sp-4); }}
.filters label {{ display: flex; flex-direction: column; gap: var(--sp-1); font-size: var(--fs-xs); color: var(--text-muted); }}
.filters select, .filters input {{ font: inherit; font-size: var(--fs-sm); color: var(--text); background: var(--surface); border: 1px solid var(--border); border-radius: var(--r-sm); padding: var(--sp-2) var(--sp-3); min-width: 180px; }}
.filters input {{ min-width: 240px; }}
.count {{ margin-left: auto; font-size: var(--fs-xs); color: var(--text-muted); align-self: flex-end; }}
.empty {{ color: var(--text-muted); }}
footer {{ max-width: 1240px; margin: 0 auto; padding: 0 var(--sp-5) var(--sp-6); color: var(--text-muted); font-size: var(--fs-xs); }}

@media (max-width: 1100px) {{ .kpis {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }} .kpi:nth-child(5) {{ border-left: 0; }} .kpi:nth-child(n+5) {{ border-top: 1px solid var(--border); }} }}
@media (max-width: 900px) {{ .grid-2 {{ grid-template-columns: 1fr; }} .bar {{ padding: var(--sp-3) var(--sp-4); }} main {{ padding: var(--sp-4); }} }}
@media (max-width: 560px) {{
  .kpis {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  .kpi {{ border-left: 0; border-top: 1px solid var(--border); }} .kpi:nth-child(-n+2) {{ border-top: 0; }}
  .kpi:nth-child(even) {{ border-left: 1px solid var(--border); }}
  .panel {{ padding: var(--sp-4); }} nav {{ order: 3; width: 100%; }}
  .chart .val {{ font-size: 18px; }} .chart .tick {{ font-size: 18px; }}
  .hbars .lbl {{ font-size: 12px; }}
  .filters select, .filters input {{ min-width: 0; width: 100%; }} .filters label {{ flex: 1 1 100%; }}
}}
</style>
</head>
<body>
<header>
  <div class="bar">
    <div class="brand"><h1>Job search</h1><span>Updated {today}</span></div>
    <nav aria-label="Sections"><a href="#overview">Overview</a><a href="#pipeline">Pipeline</a><a href="#applications">Applications</a></nav>
    <button class="theme" id="theme" type="button" role="switch" aria-checked="false" aria-label="Dark theme">
      <svg class="sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
      <svg class="moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>
    </button>
  </div>
</header>
<main>
  <section id="overview" aria-labelledby="h-overview">
    <h2 id="h-overview" class="sr-only">Overview</h2>
    <div class="kpis">{kpis}</div>
    {unknown}
  </section>

  <section id="pipeline" aria-labelledby="h-pipeline">
    <h2 id="h-pipeline">Pipeline</h2>
    <div class="grid-2">
      <div class="panel">
        <div class="panel-head"><h3 class="h3">Funnel</h3><p>{interviews} of {sent} sent reached an interview stage</p></div>
        {funnel}{funnel_table}
      </div>
      <div class="panel">
        <div class="panel-head"><h3 class="h3">Status mix</h3><p>All rows, including drafts</p></div>
        {mix}
      </div>
      <div class="panel">
        <div class="panel-head"><h3 class="h3">Applications per week</h3><p>Sent, last 12 weeks</p></div>
        {week}{week_table}
      </div>
      <div class="panel">
        <div class="panel-head"><h3 class="h3">Where closed applications stopped</h3><p>{closed} closed</p></div>
        {stopped}{stopped_table}
      </div>
      <div class="panel">
        <div class="panel-head"><h3 class="h3">By sector</h3><p>Sent, top 8</p></div>
        {sector}{sector_table}
      </div>
      <div class="panel">
        <div class="panel-head"><h3 class="h3">By channel</h3><p>Sent</p></div>
        {channel}{channel_table}
      </div>
    </div>
    <div class="panel">
      <div class="panel-head"><h3 class="h3">Waiting longest for a reply</h3><p>Follow-up candidates</p></div>
      <div class="table-wrap" style="max-height:none">
        <table class="data"><thead><tr><th scope="col">Company</th><th scope="col">Role</th><th scope="col" class="right">Days waiting</th></tr></thead>
        <tbody>{wait_rows}</tbody></table>
      </div>
    </div>
  </section>

  <section id="applications" aria-labelledby="h-apps">
    <div class="panel">
      <div class="panel-head"><h2 id="h-apps">Applications</h2><p>Newest first</p></div>
      <div class="filters">
        <label>Status<select id="f-status"><option value="">All statuses</option>{status_opts}</select></label>
        <label>Sector<select id="f-sector"><option value="">All sectors</option>{sector_opts}</select></label>
        <label>Search<input id="f-q" type="search" placeholder="Company, role or sector"></label>
        <span class="count" id="count" aria-live="polite">{total} of {total}</span>
      </div>
      <div class="table-wrap">
        <table class="data" id="apps">
          <thead><tr>{thead}          </tr></thead>
          <tbody>{rows}</tbody>
        </table>
      </div>
    </div>
  </section>
</main>
<footer>Generated by Claude Code from job_search_tracker.csv on {iso}.</footer>
<script>
(function () {{
  var root = document.documentElement, btn = document.getElementById('theme');
  function isDark() {{
    var t = root.getAttribute('data-theme');
    return t ? t === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
  }}
  try {{ var saved = localStorage.getItem('dash-theme'); if (saved) root.setAttribute('data-theme', saved); }} catch (e) {{}}
  btn.setAttribute('aria-checked', String(isDark()));
  btn.addEventListener('click', function () {{
    var next = isDark() ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    btn.setAttribute('aria-checked', String(next === 'dark'));
    try {{ localStorage.setItem('dash-theme', next); }} catch (e) {{}}
  }});

  var tbody = document.querySelector('#apps tbody'), rows = Array.prototype.slice.call(tbody.rows);
  var fs = document.getElementById('f-status'), fc = document.getElementById('f-sector'), fq = document.getElementById('f-q');
  var count = document.getElementById('count');
  function filter() {{
    var q = fq.value.trim().toLowerCase(), shown = 0;
    rows.forEach(function (r) {{
      var ok = (!fs.value || r.dataset.status === fs.value) && (!fc.value || r.dataset.sector === fc.value)
        && (!q || r.dataset.text.indexOf(q) !== -1);
      r.hidden = !ok; if (ok) shown++;
    }});
    count.textContent = shown + ' of ' + rows.length;
  }}
  [fs, fc, fq].forEach(function (el) {{ el.addEventListener('input', filter); }});

  document.querySelectorAll('#apps .sort').forEach(function (b) {{
    b.addEventListener('click', function () {{
      var th = b.parentElement, col = +b.dataset.col;
      var dir = th.getAttribute('aria-sort') === 'ascending' ? 'descending' : 'ascending';
      document.querySelectorAll('#apps th').forEach(function (h) {{ h.removeAttribute('aria-sort'); }});
      th.setAttribute('aria-sort', dir);
      rows.sort(function (a, c) {{
        var x = a.cells[col].dataset.v || '', y = c.cells[col].dataset.v || '';
        return (x < y ? -1 : x > y ? 1 : 0) * (dir === 'ascending' ? 1 : -1);
      }});
      rows.forEach(function (r) {{ tbody.appendChild(r); }});
    }});
  }});
}})();
</script>
</body>
</html>
"""


def main():
    p = argparse.ArgumentParser(description="Render the offline application dashboard.")
    p.add_argument("--root", default=".", help="repo root holding job_search_tracker.csv")
    p.add_argument("--out", default=os.path.join("reports", "application-dashboard.html"))
    a = p.parse_args()
    # settings.json pre-approves this script, so --out must not reach arbitrary files
    if not a.out.lower().endswith(".html"):
        p.error("--out must end in .html")
    data = load(a.root)
    html = render(data)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(html)
    c = data["counts"]
    print(f"wrote {a.out}: sent {len(data['sent'])}, drafted {c.get('Drafted', 0)}, active {c.get('Active', 0)}, "
          f"interview {c.get('Interview', 0)}, closed {c.get('Rejected/Closed', 0)}, "
          f"past CV screen {data['progress_rate']}, rejection rate {data['rejection_rate']}")


if __name__ == "__main__":
    main()

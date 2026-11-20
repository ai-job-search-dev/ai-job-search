"""Render the /rank results as a self-contained HTML report.

Reads the ranked entries in job_scraper/seen_jobs.json (the same file
tools/rank_state.py writes) and writes

  reports/job-ranking.html   (a single page, rewritten in place on every run)

The page needs no internet: all CSS and JavaScript are inline. It shows the
shortlist (Good/Strong Fit with no location or language veto) and a separate
below-threshold table, with a search box and sortable columns. Jobs scored in
the current /rank run are marked "New" and listed before earlier runs.

It also reads job_search_tracker.csv and adds a "Your applications" table
(every tracked application, ranked by its /apply fit score), and marks any
ranked job you have already applied to with an "Applied" badge.

Usage (Python 3.9+, standard library only):
  python3 tools/make_rank_report.py --run-results <file passed to rank_state.py apply>
  python3 tools/make_rank_report.py            # no run file: "new" = latest rank_date

All posting-derived text is third-party data: it is HTML-escaped, and only
http(s) links are rendered.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE = ROOT / "job_scraper" / "seen_jobs.json"
DEFAULT_OUT = ROOT / "reports"
REPORT_NAME = "job-ranking.html"
DEFAULT_TRACKER = ROOT / "job_search_tracker.csv"
STOP = {"and", "of", "the", "a", "level", "manager", "it", "inc", "ltd"}
SHORTLIST_VERDICTS = {"Strong Fit", "Good Fit"}
VERDICT_VALUES = {"PASS", "FAIL", "FLAG"}


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def safe_url(url) -> str | None:
    if isinstance(url, str) and re.match(r"^https?://", url.strip(), re.I):
        return url.strip()
    return None


def load_ranked(state_path: Path) -> list[dict]:
    try:
        doc = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"make_rank_report: {state_path} not found - run /scrape and /rank first")
    seen = doc.get("seen", {}) if isinstance(doc, dict) else {}
    out = []
    for key, entry in seen.items():
        if isinstance(entry, dict) and entry.get("status") == "ranked":
            out.append(dict(entry, key=key))
    return out


def load_all(state_path: Path) -> list[dict]:
    """Every scraped entry, whatever its status, for the cross-reference table."""
    try:
        doc = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    seen = doc.get("seen", {}) if isinstance(doc, dict) else {}
    return [dict(v, key=k) for k, v in seen.items() if isinstance(v, dict)]


def norm(text) -> str:
    """Match/search key: NFKC, lowercase, every run of non-letter/mark/number characters to one space.

    Keeps letters, combining marks and digits of any script. The in-page search (JS) applies
    the same steps to the typed query, so queries match these keys.
    """
    text = unicodedata.normalize("NFKC", str(text or "")).lower()
    return " ".join("".join(c if unicodedata.category(c)[0] in "LMN" else " " for c in text).split())


def load_tracker(path: Path) -> list[dict]:
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            return [r for r in csv.DictReader(fh) if (r.get("company") or "").strip()]
    except FileNotFoundError:
        return []


def same_job(tracked: dict, entry: dict) -> bool:
    """Company names contain one another and the titles share two meaningful word stems."""
    tc, ec = norm(tracked.get("company")), norm(entry.get("company"))
    if not tc or not ec or not (tc in ec or ec in tc):
        return False
    words = lambda t: {w[:5] for w in norm(t).split() if w not in STOP and len(w) > 1}
    tw, ew = words(tracked.get("role")), words(entry.get("title"))
    return len(tw & ew) >= 2 or (bool(tw) and tw <= ew)


def fit_of(row: dict) -> float:
    try:
        return float(row.get("fit_rating"))
    except (TypeError, ValueError):
        return -1.0


def run_keys_from_results(path: Path) -> set[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        sys.exit(f"make_rank_report: {path} is not a JSON array of scoring results")
    return {str(x["key"]) for x in data if isinstance(x, dict) and x.get("key") and x.get("status", "scored") == "scored"}


def location_verdict(entry: dict) -> str | None:
    v = entry.get("location_verdict")
    if v in VERDICT_VALUES:
        return v
    legacy = entry.get("location")  # entries ranked before the rename carried the verdict here
    return legacy if legacy in VERDICT_VALUES else None


def place(entry: dict) -> str:
    loc = entry.get("location")
    return "" if loc in VERDICT_VALUES or loc is None else str(loc)


def score_of(entry: dict) -> float:
    try:
        return float(entry.get("rank_score"))
    except (TypeError, ValueError):
        return -1.0


def classify(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    short, below = [], []
    for e in entries:
        loc_v = location_verdict(e)
        lang_v = e.get("language_gate")
        vetoes = []
        if loc_v == "FAIL":
            vetoes.append("location FAIL")
        if lang_v == "FAIL":
            vetoes.append("language FAIL" + (f": {e['language_note']}" if e.get("language_note") else ""))
        flags = []
        if loc_v == "FLAG":
            flags.append("location FLAG")
        if lang_v == "FLAG":
            flags.append("language FLAG" + (f": {e['language_note']}" if e.get("language_note") else ""))
        if e.get("guard") and e.get("guard") != "clean":
            flags.append("posting guard: " + str(e["guard"]))
        e["_vetoes"], e["_flags"] = vetoes, flags
        (short if e.get("rank_verdict") in SHORTLIST_VERDICTS and not vetoes else below).append(e)
    order = lambda e: (0 if e["_new"] else 1, -score_of(e), str(e.get("company") or ""))
    return sorted(short, key=order), sorted(below, key=order)


def bullets(items) -> str:
    if not isinstance(items, list) or not items:
        return '<span class="muted">-</span>'
    return "<ul>" + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul>"


def row(e: dict, rank: int | None, show_reason: bool) -> str:
    url = safe_url(e.get("url"))
    link = f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">Open posting</a>' if url else '<span class="muted">no link</span>'
    badge = '<span class="badge new">New</span>' if e["_new"] else f'<span class="badge old">{esc(e.get("rank_date") or "earlier")}</span>'
    if e.get("_applied"):
        badge += f' <span class="badge applied">Applied &middot; {esc(e["_applied"])}</span>'
    notes = "".join(f'<div class="flag">&#9888; {esc(f)}</div>' for f in e["_flags"])
    reason = ""
    if show_reason:
        why = "; ".join(e["_vetoes"]) if e["_vetoes"] else (e.get("rank_verdict") or "")
        reason = f'<td data-sort="{esc(why)}">{esc(why)}</td>'
    score = score_of(e)
    search = " ".join(
        str(x) for x in [e.get("company"), e.get("title"), place(e), e.get("portal"), e.get("rank_verdict")]
        + list(e.get("strengths") or []) + list(e.get("gaps") or []) if x
    ).lower()
    rank_cell = f'<td data-sort="{rank}">{rank}</td>' if rank is not None else ""
    return (
        f'<tr data-search="{esc(search)}" data-q="{esc(norm(str(e.get("title") or "") + " " + str(e.get("company") or "")))}" data-new="{1 if e["_new"] else 0}">'
        f"{rank_cell}"
        f'<td data-sort="{0 if e["_new"] else 1}">{badge}</td>'
        f'<td data-sort="{score:.0f}" class="num">{"" if score < 0 else f"{score:.0f}"}</td>'
        f'<td data-sort="{esc(e.get("rank_verdict"))}">{esc(e.get("rank_verdict"))}</td>'
        f'<td data-sort="{esc(e.get("company"))}">{esc(e.get("company"))}</td>'
        f'<td data-sort="{esc(e.get("title"))}"><strong>{esc(e.get("title"))}</strong>{notes}</td>'
        f'<td data-sort="{esc(place(e))}">{esc(place(e))}</td>'
        f'<td data-sort="{esc(e.get("portal"))}">{esc(e.get("portal"))}</td>'
        f"{reason}"
        f'<td class="list">{bullets(e.get("strengths"))}</td>'
        f'<td class="list">{bullets(e.get("gaps"))}</td>'
        f"<td>{link}</td>"
        "</tr>"
    )


def table(title: str, rows: list[dict], numbered: bool, show_reason: bool, empty: str) -> str:
    head = []
    if numbered:
        head.append('<th data-type="num">#</th>')
    head += ['<th data-type="num">Run</th>', '<th data-type="num">Score</th>', "<th>Verdict</th>",
             "<th>Company</th>", "<th>Title</th>", "<th>Location</th>", "<th>Source portal</th>"]
    if show_reason:
        head.append("<th>Reason</th>")
    head += ['<th class="nosort">Strengths</th>', '<th class="nosort">Gaps</th>', '<th class="nosort">Link</th>']
    body = "".join(row(e, i + 1 if numbered else None, show_reason) for i, e in enumerate(rows))
    if not rows:
        body = f'<tr class="empty"><td colspan="{len(head)}">{esc(empty)}</td></tr>'
    tbl = f'<table class="sortable"><thead><tr>{"".join(head)}</tr></thead><tbody>{body}</tbody></table>'
    return section(norm(title).replace(" ", "-"), title, len(rows), tbl)


def section(sid: str, title: str, count: int, tbl: str, extra: str = "", note: str = "", open_: bool = True) -> str:
    """A collapsible section with its own title/company search and 20-per-page pagination (wired up in JS)."""
    return (f'<section class="sec" id="{esc(sid)}"><details{" open" if open_ else ""}>'
            f'<summary><h2>{esc(title)} <span class="count">({count})</span></h2></summary>'
            f'<div class="bar"><input type="search" class="sq" placeholder="Search job title or company..." '
            f'aria-label="Search {esc(title)} by job title or company">{extra}<span class="muted shown"></span></div>'
            f'<div class="wrap">{tbl}</div><nav class="pager" aria-label="{esc(title)} pages"></nav>{note}</details></section>')


def applications_table(tracker: list[dict], entries: list[dict]) -> str:
    latest = max((r.get("date") or "" for r in tracker), default="")
    rows = sorted(tracker, key=lambda r: (-fit_of(r), r.get("date") or "", r.get("company") or ""))
    out = []
    for i, r in enumerate(rows, 1):
        hits = [e for e in entries if same_job(r, e)]
        match = next((e for e in hits if score_of(e) >= 0), hits[0] if hits else None)  # prefer the ranked entry
        triage = score_of(match) if match else -1.0
        url = safe_url(r.get("source"))
        link = f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">Open posting</a>' if url else '<span class="muted">no link</span>'
        fit = fit_of(r)
        status = (r.get("status") or "").strip()
        new = bool(latest) and (r.get("date") or "") == latest
        badge = '<span class="badge new">New</span>' if new else f'<span class="badge old">{esc(r.get("date"))}</span>'
        search = " ".join(str(r.get(k) or "") for k in ("company", "role", "sector", "status")).lower()
        cls = norm(status).replace(" ", "_")
        out.append(
            f'<tr data-search="{esc(search)}" data-q="{esc(norm(str(r.get("role") or "") + " " + str(r.get("company") or "")))}">'
            f'<td data-sort="{i}">{i}</td>'
            f'<td data-sort="{esc(r.get("date"))}">{badge}</td>'
            f'<td data-sort="{fit:.0f}" class="num">{"" if fit < 0 else f"{fit:.0f}"}</td>'
            f'<td data-sort="{triage:.0f}" class="num">{"" if triage < 0 else f"{triage:.0f}"}</td>'
            f'<td data-sort="{esc(status)}"><span class="badge status s-{esc(cls)}">{esc(status or "-")}</span></td>'
            f'<td data-sort="{esc(r.get("company"))}">{esc(r.get("company"))}</td>'
            f'<td data-sort="{esc(r.get("role"))}"><strong>{esc(r.get("role"))}</strong></td>'
            f'<td data-sort="{esc(r.get("sector"))}">{esc(r.get("sector"))}</td>'
            f'<td data-sort="{esc(r.get("deadline"))}">{esc(r.get("deadline") or "-")}</td>'
            f"<td>{link}</td></tr>"
        )
    head = ['<th data-type="num">#</th>', "<th>Applied</th>", '<th data-type="num">Fit (/apply)</th>',
            '<th data-type="num">Triage (/rank)</th>', "<th>Status</th>", "<th>Company</th>", "<th>Role</th>",
            "<th>Sector</th>", "<th>Deadline</th>", '<th class="nosort">Link</th>']
    body = "".join(out) or f'<tr class="empty"><td colspan="{len(head)}">No tracked applications.</td></tr>'
    tbl = f'<table class="sortable"><thead><tr>{"".join(head)}</tr></thead><tbody>{body}</tbody></table>'
    return section("applications", "Your applications", len(rows), tbl, note=(
        '<p class="note">Ranked by the fit score /apply gave after full evaluation. Triage = the /rank score from posting text, '
        'where the job was also scraped. "New" = added on the latest tracker date.</p>'))


def scraped_table(all_entries: list[dict], tracker: list[dict]) -> str:
    """Every job in seen_jobs.json, so scraped, skipped, ranked and applied jobs can be cross-referenced."""
    rows = sorted(all_entries, key=lambda e: (str(e.get("first_seen") or ""), score_of(e)), reverse=True)  # newest first, then highest score
    statuses = sorted({str(e.get("status") or "-") for e in rows})
    out = []
    for e in rows:
        hit = next((r for r in tracker if same_job(r, e)), None)
        applied = (hit.get("status") or "tracked") if hit else ""
        status = str(e.get("status") or "-")
        score = score_of(e)
        url = safe_url(e.get("url"))
        link = f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">Open</a>' if url else '<span class="muted">-</span>'
        loc = place(e)
        badges = f'<span class="badge status st-{esc(norm(status).replace(" ", "_"))}">{esc(status)}</span>'
        if applied:
            badges += f' <span class="badge applied">Applied &middot; {esc(applied)}</span>'
        reason = e.get("last_fetch_error") or ""
        search = " ".join(str(x) for x in [e.get("company"), e.get("title"), loc, e.get("portal"), status,
                                           e.get("fit"), e.get("rank_verdict"), applied, e.get("employment_type")] if x).lower()
        out.append(
            f'<tr data-search="{esc(search)}" data-q="{esc(norm(str(e.get("title") or "") + " " + str(e.get("company") or "")))}" data-status="{esc(status)}">'
            f'<td data-sort="{esc(e.get("first_seen"))}">{esc(e.get("first_seen") or "-")}</td>'
            f'<td data-sort="{esc(e.get("posted_date"))}">{esc(e.get("posted_date") or "-")}</td>'
            f'<td data-sort="{esc(status)}">{badges}</td>'
            f'<td data-sort="{esc(e.get("fit"))}">{esc(e.get("fit") or "-")}</td>'
            f'<td data-sort="{score:.0f}" class="num">{"" if score < 0 else f"{score:.0f}"}</td>'
            f'<td data-sort="{esc(e.get("rank_verdict"))}">{esc(e.get("rank_verdict") or "-")}</td>'
            f'<td data-sort="{esc(e.get("company"))}">{esc(e.get("company"))}</td>'
            f'<td data-sort="{esc(e.get("title"))}"><strong>{esc(e.get("title"))}</strong>'
            + (f'<div class="flag">&#9888; {esc(reason)}</div>' if reason else "") + "</td>"
            f'<td data-sort="{esc(loc)}">{esc(loc or "-")}</td>'
            f'<td data-sort="{esc(e.get("employment_type"))}">{esc(e.get("employment_type") or "-")}</td>'
            f'<td data-sort="{esc(e.get("deadline"))}">{esc(e.get("deadline") or "-")}</td>'
            f'<td data-sort="{esc(e.get("portal"))}">{esc(e.get("portal") or "-")}</td>'
            f"<td>{link}</td></tr>"
        )
    head = ["<th>First seen</th>", "<th>Posted</th>", "<th>Status</th>", "<th>Scrape fit</th>",
            '<th data-type="num">Rank score</th>', "<th>Verdict</th>", "<th>Company</th>", "<th>Title</th>",
            "<th>Location</th>", "<th>Type</th>", "<th>Deadline</th>", "<th>Portal</th>", '<th class="nosort">Link</th>']
    opts = "".join(f'<option value="{esc(s)}">{esc(s)} ({sum(1 for e in rows if str(e.get("status") or "-") == s)})</option>' for s in statuses)
    body = "".join(out) or f'<tr class="empty"><td colspan="{len(head)}">No scraped jobs.</td></tr>'
    tbl = f'<table class="sortable"><thead><tr>{"".join(head)}</tr></thead><tbody>{body}</tbody></table>'
    extra = f'<label class="muted">Status <select class="sst"><option value="">all</option>{opts}</select></label>'
    return section("all-scraped", "All scraped jobs", len(rows), tbl, extra=extra, open_=False, note=(
        '<p class="note">Every job /scrape has stored, including auto-rejected (skipped) and unranked ones.</p>'))


VERDICT_BANDS = [("Strong Fit", "75+", "#15803d"), ("Good Fit", "60-74", "#22c55e"), ("Moderate Fit", "45-59", "#f59e0b"),
                 ("Weak Fit", "30-44", "#f97316"), ("Poor Fit", "<30", "#ef4444")]
STATUS_INFO = [("skipped", "Auto-rejected at scrape", "#94a3b8"), ("ranked", "Ranked", "#3b82f6"),
               ("new", "Awaiting rank", "#14b8a6"), ("unreachable", "Unreachable (retrying)", "#a855f7"),
               ("expired", "Expired / closed", "#ef4444")]


def _hbars(items: list[tuple[str, int, str]], label_w: int = 190, aria: str = "", sub: dict | None = None) -> str:
    """Horizontal bar chart as inline SVG. items = (label, value, colour); sub = optional {label: inner value}."""
    if not items:
        return '<p class="muted">No data.</p>'
    mx = max(v for _, v, _ in items) or 1
    w, h = 640, 30
    bw = w - label_w - (130 if sub else 70)
    out = [f'<svg viewBox="0 0 {w} {len(items) * h + 6}" role="img" aria-label="{esc(aria)}">']
    for i, (label, v, col) in enumerate(items):
        y = 3 + i * h
        bl = max(bw * v / mx, 2 if v else 0)
        out.append(f'<text x="{label_w - 8}" y="{y + 19}" text-anchor="end" class="lbl">{esc(label)}</text>')
        faded = ' opacity=".35"' if sub and label in sub else ""
        out.append(f'<rect x="{label_w}" y="{y + 5}" width="{bl:.1f}" height="{h - 10}" rx="4" fill="{col}"'
                   f'{faded}><title>{esc(label)}: {v}</title></rect>')
        if sub and label in sub:
            sl = max(bw * sub[label] / mx, 2 if sub[label] else 0)
            out.append(f'<rect x="{label_w}" y="{y + 5}" width="{sl:.1f}" height="{h - 10}" rx="4" fill="{col}">'
                       f'<title>{esc(label)}: {sub[label]} ranked</title></rect>')
        txt = f"{v}" + (f" ({sub[label]} ranked)" if sub and label in sub else "")
        out.append(f'<text x="{label_w + bl + 6:.1f}" y="{y + 19}" class="lbl val">{esc(txt)}</text>')
    out.append("</svg>")
    return "".join(out)


def _doughnut(items: list[tuple[str, int, str]], centre: str, aria: str) -> str:
    import math
    items = [it for it in items if it[1] > 0]
    total = sum(v for _, v, _ in items) or 1
    cx = cy = 90
    ro, ri = 82, 52
    a = -math.pi / 2
    parts = []
    for label, v, col in items:
        frac = v / total
        if frac > 0.9999:
            parts.append(f'<circle cx="{cx}" cy="{cy}" r="{(ro + ri) / 2}" fill="none" stroke="{col}" stroke-width="{ro - ri}">'
                         f'<title>{esc(label)}: {v}</title></circle>')
            continue
        a2 = a + frac * 2 * math.pi
        lg = 1 if frac > 0.5 else 0
        pt = lambda r, t: (cx + r * math.cos(t), cy + r * math.sin(t))
        (x1, y1), (x2, y2), (x3, y3), (x4, y4) = pt(ro, a), pt(ro, a2), pt(ri, a2), pt(ri, a)
        parts.append(f'<path d="M{x1:.2f},{y1:.2f} A{ro},{ro} 0 {lg} 1 {x2:.2f},{y2:.2f} L{x3:.2f},{y3:.2f} '
                     f'A{ri},{ri} 0 {lg} 0 {x4:.2f},{y4:.2f} Z" fill="{col}"><title>{esc(label)}: {v}</title></path>')
        a = a2
    legend = "".join(
        f'<rect x="196" y="{14 + i * 26}" width="14" height="14" rx="3" fill="{col}"/>'
        f'<text x="218" y="{26 + i * 26}" class="lbl">{esc(label)} &middot; {v} ({100 * v / total:.0f}%)</text>'
        for i, (label, v, col) in enumerate(items))
    return (f'<svg viewBox="0 0 460 180" role="img" aria-label="{esc(aria)}">{"".join(parts)}'
            f'<text x="{cx}" y="{cy + 2}" text-anchor="middle" class="big">{esc(centre)}</text>'
            f'<text x="{cx}" y="{cy + 20}" text-anchor="middle" class="lbl">jobs</text>{legend}</svg>')


def _histogram(entries: list[dict]) -> str:
    bins = [0] * 10
    for e in entries:
        s = score_of(e)
        if s >= 0:
            bins[min(int(s // 10), 9)] += 1
    mx = max(bins) or 1
    w, h, base, left = 640, 190, 160, 30
    bw = (w - left - 10) / 10

    def colour(lo):
        return "#15803d" if lo >= 75 else "#22c55e" if lo >= 60 else "#f59e0b" if lo >= 45 else "#f97316" if lo >= 30 else "#ef4444"
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{esc("Score distribution: " + ", ".join(f"{i * 10}-{i * 10 + 9}: {b}" for i, b in enumerate(bins)))}">']
    for i, b in enumerate(bins):
        bh = (base - 20) * b / mx
        x = left + i * bw
        out.append(f'<rect x="{x + 3:.1f}" y="{base - bh:.1f}" width="{bw - 6:.1f}" height="{bh:.1f}" rx="3" fill="{colour(i * 10 + 5)}">'
                   f'<title>{i * 10}-{i * 10 + 9}: {b}</title></rect>')
        if b:
            out.append(f'<text x="{x + bw / 2:.1f}" y="{base - bh - 5:.1f}" text-anchor="middle" class="lbl val">{b}</text>')
        out.append(f'<text x="{x + bw / 2:.1f}" y="{base + 16}" text-anchor="middle" class="lbl">{i * 10}</text>')
    for t, lab in ((60, "Good 60"), (75, "Strong 75")):
        x = left + bw * t / 10
        out.append(f'<line x1="{x:.1f}" y1="12" x2="{x:.1f}" y2="{base}" class="thr"/><text x="{x + 4:.1f}" y="22" class="lbl">{lab}</text>')
    out.append(f'<line x1="{left}" y1="{base}" x2="{w - 10}" y2="{base}" class="axis"/></svg>')
    return "".join(out)


def infographic(entries: list[dict], all_entries: list[dict], tracker: list[dict], short: list[dict]) -> str:
    total = len(all_entries)
    by_status: dict[str, int] = {}
    for e in all_entries:
        k = str(e.get("status") or "-")
        by_status[k] = by_status.get(k, 0) + 1
    ranked = len(entries)
    vetoed = sum(1 for e in entries if e["_vetoes"])
    sent = [r for r in tracker if (r.get("status") or "").strip().lower() != "drafted"]
    drafted = len(tracker) - len(sent)
    matched = sum(1 for r in sent if any(same_job(r, e) for e in all_entries))
    verdicts = {v: 0 for v, _, _ in VERDICT_BANDS}
    for e in entries:
        if e.get("rank_verdict") in verdicts:
            verdicts[e["rank_verdict"]] += 1
    skipped = by_status.get("skipped", 0)
    passed = total - skipped

    def card(n, label, col, sub=""):
        return (f'<div class="card" style="border-left-color:{col}"><div class="n">{n}</div>'
                f'<div class="cl">{esc(label)}</div>' + (f'<div class="cs">{esc(sub)}</div>' if sub else "") + "</div>")
    pct = lambda a, b: f"{100 * a / b:.0f}% of scraped" if b else ""
    cards = "".join([
        card(total, "Jobs scraped", "#0f172a", "unique postings stored"),
        card(skipped, "Auto-rejected at scrape", "#94a3b8", pct(skipped, total)),
        card(ranked, "Ranked", "#3b82f6", pct(ranked, total)),
        card(len(short), "Shortlisted", "#22c55e", "Good/Strong Fit, no veto"),
        card(vetoed, "Vetoed", "#b42318", "location or language FAIL"),
        card(by_status.get("expired", 0) + by_status.get("unreachable", 0), "Expired / unreachable", "#a855f7",
             f'{by_status.get("expired", 0)} closed, {by_status.get("unreachable", 0)} retrying'),
        card(len(sent), "Applied", "#2457c5", (f"{drafted} drafted, not sent" if drafted else "") ),
    ])
    other = [(s, n) for s, n in by_status.items() if s not in {k for k, _, _ in STATUS_INFO}]
    status_items = [(lab, by_status.get(k, 0), col) for k, lab, col in STATUS_INFO] + [(s, n, "#64748b") for s, n in other]
    funnel = [("Scraped", total, "#0f172a"), ("Passed scrape filter", passed, "#64748b"), ("Ranked", ranked, "#3b82f6"),
              ("Shortlisted", len(short), "#22c55e"), ("Applied", len(sent), "#2457c5")]
    band_items = [(f"{v} ({rng})", verdicts[v], col) for v, rng, col in VERDICT_BANDS]
    portals: dict[str, list[int]] = {}
    for e in all_entries:
        p = portals.setdefault(str(e.get("portal") or "unknown"), [0, 0])
        p[0] += 1
        p[1] += 1 if e.get("status") == "ranked" else 0
    portal_items = [(p, n[0], "#6366f1") for p, n in sorted(portals.items(), key=lambda x: -x[1][0])]
    portal_sub = {p: n[1] for p, n in portals.items()}
    return (
        '<section class="info"><h2>Pipeline at a glance</h2>'
        f'<div class="cards">{cards}</div>'
        '<div class="grid">'
        '<div class="chart"><h3>Pipeline funnel</h3>'
        + _hbars(funnel, 170, "Pipeline funnel: " + ", ".join(f"{l} {v}" for l, v, _ in funnel))
        + f'<p class="cap">{matched} of {len(sent)} applications match a scraped posting; the rest came from outside /scrape.</p></div>'
        '<div class="chart"><h3>What happened to scraped jobs</h3>'
        + _doughnut(status_items, str(total), "Scrape outcomes: " + ", ".join(f"{l} {v}" for l, v, _ in status_items))
        + '<p class="cap">Auto-rejected = judged low fit from the posting at scrape time and never sent to /rank.</p></div>'
        '<div class="chart"><h3>Threshold categories (ranked jobs)</h3>'
        + _hbars(band_items, 170, "Verdict bands: " + ", ".join(f"{l} {v}" for l, v, _ in band_items))
        + f'<p class="cap">Shortlist = Good or Strong Fit without a veto. {vetoed} ranked job(s) vetoed on location or language.</p></div>'
        '<div class="chart"><h3>Rank score distribution</h3>' + _histogram(entries)
        + '<p class="cap">Bars coloured by verdict band; dashed lines mark the Good (60) and Strong (75) thresholds.</p></div>'
        '<div class="chart wide"><h3>Jobs by source portal</h3>'
        + _hbars(portal_items, 170, "Scraped per portal: " + ", ".join(f"{p} {n}" for p, n, _ in portal_items), portal_sub)
        + '<p class="cap">Full bar = scraped; solid part = ranked.</p></div>'
        "</div></section>"
    )


CSS = """
:root{--bg:#f7f7f5;--card:#fff;--fg:#1d1d1b;--muted:#6b6b66;--line:#e2e1dc;--accent:#2457c5;--new:#1f7a3f;--newbg:#e3f3e8;--warn:#9a5b00;--warnbg:#fff3dc;--head:#efeee9}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#1f1f1d;--fg:#ecebe6;--muted:#a3a29b;--line:#34332f;--accent:#7aa2ff;--new:#7fd69d;--newbg:#1d3326;--warn:#f0b75a;--warnbg:#3a2c12;--head:#262624}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1500px;margin:0 auto;padding:24px 16px 48px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 10px}
.meta{color:var(--muted);margin:0 0 16px}.count{color:var(--muted);font-weight:400}
.bar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:12px 0}
input[type=search]{flex:1;min-width:220px;max-width:480px;padding:8px 10px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg);font:inherit}
.wrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;width:100%;min-width:1100px}th,td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left}
th{background:var(--head);position:sticky;top:0;font-weight:600;white-space:nowrap;cursor:pointer;user-select:none}th.nosort{cursor:default}
th[aria-sort=ascending]::after{content:" \\25B2"}th[aria-sort=descending]::after{content:" \\25BC"}
td.num{font-variant-numeric:tabular-nums;font-weight:600}td.list ul{margin:0;padding-left:16px}td.list{min-width:260px;max-width:380px}
.badge{display:inline-block;padding:1px 7px;border-radius:10px;font-size:12px;white-space:nowrap}.badge.new{background:var(--newbg);color:var(--new);font-weight:600}.badge.old{color:var(--muted);border:1px solid var(--line)}
.flag{margin-top:4px;font-size:12px;color:var(--warn);background:var(--warnbg);padding:2px 6px;border-radius:4px;display:inline-block}
.badge.applied{background:#e6eefc;color:#2457c5;font-weight:600}.badge.status{border:1px solid var(--line)}.s-applied{color:#2457c5}.s-drafted{color:var(--muted)}.s-interview{color:#9a5b00}.s-offer,.s-hired{color:#1f7a3f}.s-rejected,.s-no_response,.s-withdrawn{color:#b42318}
.st-ranked{color:#2457c5}.st-new{color:var(--new)}.st-skipped{color:var(--muted)}.st-expired,.st-unreachable{color:#b42318}
select{padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg);font:inherit}
.muted{color:var(--muted)}a{color:var(--accent)}tr.hidden,tr.hidden2{display:none}tr.empty td{color:var(--muted)}
.note{color:var(--muted);font-size:13px;margin-top:20px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:0 0 16px}
.card{background:var(--card);border:1px solid var(--line);border-left:5px solid;border-radius:8px;padding:12px 14px}
.card .n{font-size:28px;font-weight:700;font-variant-numeric:tabular-nums}.card .cl{font-weight:600}.card .cs{color:var(--muted);font-size:12px;margin-top:2px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.chart{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px 16px}
.chart.wide{grid-column:1/-1}.chart h3{margin:0 0 10px;font-size:15px}.cap{color:var(--muted);font-size:12px;margin:8px 0 0}
@media (max-width:900px){.grid{grid-template-columns:1fr}}
.chart svg{width:100%;height:auto;display:block}.lbl{font-size:12px;fill:var(--fg)}.lbl.val{font-weight:600}.big{font-size:26px;font-weight:700;fill:var(--fg)}
.thr{stroke:var(--muted);stroke-dasharray:4 3}.axis{stroke:var(--line)}
details>summary{list-style:none;cursor:pointer;display:flex;align-items:center;gap:8px}details>summary::-webkit-details-marker{display:none}
details>summary::before{content:"\\25B8";color:var(--muted);font-size:14px;transition:transform .15s}details[open]>summary::before{transform:rotate(90deg)}
details>summary h2{display:inline-block}details>summary:hover h2{color:var(--accent)}
.pager{display:flex;gap:6px;flex-wrap:wrap;justify-content:center;margin:12px 0 4px}
.pager button{min-width:36px;padding:6px 10px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--accent);font:inherit;cursor:pointer}
.pager button.cur{background:var(--accent);color:var(--card);border-color:var(--accent);font-weight:700;cursor:default}
.pager button:disabled{color:var(--muted);cursor:default;opacity:.6}.pager .gap{padding:6px 2px;color:var(--muted)}
.shown{margin-left:auto}
"""

JS = """
(function(){
  var PER=20;
  function sortTable(tbl,th,ths,idx){
    var asc=th.getAttribute('aria-sort')!=='ascending';
    ths.forEach(function(h){h.removeAttribute('aria-sort');});
    th.setAttribute('aria-sort',asc?'ascending':'descending');
    var num=th.getAttribute('data-type')==='num',tb=tbl.tBodies[0];
    var rows=Array.prototype.slice.call(tb.rows).filter(function(r){return !r.classList.contains('empty');});
    rows.sort(function(a,b){
      var x=a.cells[idx].getAttribute('data-sort')||'',y=b.cells[idx].getAttribute('data-sort')||'';
      var c=num?(parseFloat(x)||0)-(parseFloat(y)||0):x.localeCompare(y,undefined,{sensitivity:'base'});
      return asc?c:-c;
    });
    rows.forEach(function(r){tb.appendChild(r);});
  }
  function pageList(cur,n){
    var out=[],i;
    if(n<=9){for(i=1;i<=n;i++)out.push(i);return out;}
    var lo=Math.max(2,cur-2),hi=Math.min(n-1,cur+2);
    out.push(1);
    if(lo>2)out.push('...');
    for(i=lo;i<=hi;i++)out.push(i);
    if(hi<n-1)out.push('...');
    out.push(n);
    return out;
  }
  document.querySelectorAll('section.sec').forEach(function(sec){
    var tbl=sec.querySelector('table'),tb=tbl.tBodies[0],q=sec.querySelector('.sq'),st=sec.querySelector('.sst'),
        pager=sec.querySelector('.pager'),shown=sec.querySelector('.shown'),page=1;
    function rows(){return Array.prototype.slice.call(tb.rows).filter(function(r){return !r.classList.contains('empty');});}
    function render(){
      var t=q.value.normalize('NFKC').toLowerCase().replace(/[^\\p{L}\\p{M}\\p{N}]+/gu,' ').trim(),sv=st?st.value:'',all=rows();
      var match=all.filter(function(r){
        return (t===''||(r.getAttribute('data-q')||'').indexOf(t)>-1)&&(sv===''||r.getAttribute('data-status')===sv);
      });
      var pages=Math.max(1,Math.ceil(match.length/PER));
      if(page>pages)page=pages;
      if(page<1)page=1;
      all.forEach(function(r){r.style.display='none';});
      match.slice((page-1)*PER,page*PER).forEach(function(r){r.style.display='';});
      if(!all.length){shown.textContent='';}
      else if(!match.length){shown.textContent='No matches';}
      else{shown.textContent='Showing '+((page-1)*PER+1)+'-'+Math.min(page*PER,match.length)+' of '+match.length;}
      var h='';
      if(pages>1){
        h+='<button type="button" data-p="'+(page-1)+'"'+(page===1?' disabled':'')+'>&lsaquo; Previous</button>';
        pageList(page,pages).forEach(function(p){
          h+=p==='...'?'<span class="gap">&hellip;</span>':'<button type="button" data-p="'+p+'"'+(p===page?' class="cur" aria-current="page"':'')+'>'+p+'</button>';
        });
        h+='<button type="button" data-p="'+(page+1)+'"'+(page===pages?' disabled':'')+'>Next &rsaquo;</button>';
      }
      pager.innerHTML=h;
    }
    pager.addEventListener('click',function(ev){
      var b=ev.target.closest('button[data-p]');
      if(!b||b.disabled||b.classList.contains('cur'))return;
      page=parseInt(b.getAttribute('data-p'),10);
      render();
      sec.scrollIntoView({behavior:'smooth',block:'start'});
    });
    q.addEventListener('input',function(){page=1;render();});
    if(st)st.addEventListener('change',function(){page=1;render();});
    var ths=tbl.querySelectorAll('th');
    ths.forEach(function(th,idx){
      if(th.classList.contains('nosort'))return;
      th.addEventListener('click',function(){sortTable(tbl,th,ths,idx);page=1;render();});
    });
    render();
  });
})();
"""


def render(entries: list[dict], run_label: str, new_basis: str, generated: datetime, tracker: list[dict] | None = None,
           all_entries: list[dict] | None = None) -> str:
    tracker = tracker or []
    for e in entries:
        hit = next((r for r in tracker if same_job(r, e)), None)
        e["_applied"] = (hit.get("status") or "tracked") if hit else ""
    short, below = classify(entries)
    n_new = sum(1 for e in entries if e["_new"])
    n_app = sum(1 for r in tracker if (r.get("status") or "").strip().lower() != "drafted")
    summary = (f"{len(entries)} ranked jobs, {n_new} new this run, {len(short)} shortlisted, {len(below)} below threshold"
               + (f", {n_app} applications sent" if tracker else ""))
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta name="ranking-summary" content="{esc(summary)}">'
        f"<title>Job Ranking {esc(generated.strftime('%Y-%m-%d %H:%M'))}</title>"
        f"<style>{CSS}</style></head><body><main>"
        f"<h1>Job Ranking</h1>"
        f'<p class="meta">Run: <strong>{esc(run_label)}</strong> &middot; {esc(summary)}</p>'
        + infographic(entries, all_entries or entries, tracker, short)
        + '<p class="meta">Click a section name to hide or show it. Click a column header to sort.</p>'
        + (applications_table(tracker, all_entries or entries) if tracker else "")
        + table("Shortlist", short, True, False, "No shortlisted jobs.")
        + table("Below threshold", below, False, True, "No jobs below threshold.")
        + (scraped_table(all_entries, tracker) if all_entries else "")
        + f'<p class="note">"New" = {esc(new_basis)}. Triage scores from posting text only; /apply re-evaluates with company research. '
        'Shortlist = Good or Strong Fit with no location or language veto. This page is rewritten on every /rank run.</p>'
        f"<script>{JS}</script></main></body></html>"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--state", type=Path, default=DEFAULT_STATE, help="seen_jobs.json path")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT, help="report directory (default: reports/)")
    ap.add_argument("--tracker", type=Path, default=DEFAULT_TRACKER, help="job_search_tracker.csv path (applications table)")
    ap.add_argument("--run-results", type=Path, help="the scoring-results JSON passed to rank_state.py apply; its scored keys are marked New")
    args = ap.parse_args(argv)

    entries = load_ranked(args.state)
    if args.run_results:
        run_keys = run_keys_from_results(args.run_results)
        new_basis = "scored in this /rank run"
    else:
        dates = [str(e.get("rank_date")) for e in entries if e.get("rank_date")]
        latest = max(dates) if dates else None
        run_keys = {e["key"] for e in entries if latest and str(e.get("rank_date")) == latest}
        new_basis = f"ranked on the latest rank date ({latest}); no run results file was given" if latest else "none (no rank dates)"
    for e in entries:
        e["_new"] = e["key"] in run_keys

    now = datetime.now()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    all_entries = load_all(args.state)
    page = render(entries, now.strftime("%Y-%m-%d %H:%M"), new_basis, now, load_tracker(args.tracker), all_entries)
    path = args.out_dir / REPORT_NAME
    tmp = args.out_dir / f".{REPORT_NAME}.tmp"
    tmp.write_text(page, encoding="utf-8")
    tmp.replace(path)  # atomic: the page is never left half-written
    print(json.dumps({"report": str(path), "ranked": len(entries), "all_scraped": len(all_entries),
                      "new": sum(1 for e in entries if e["_new"])}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Local dashboard for scraped jobs.

Serves job_scraper/seen_jobs.json (plus optional notes in
job_scraper/job_notes.json, keyed by posting URL) as a filterable page.
Data is re-read on every request, so a new /scrape or /rank shows up on refresh.

Each job also shows its application status from job_search_tracker.csv, read
only. The page never writes: statuses change through /apply, /outcome and
/gmail-sync, which own the tracker.

    python3 tools/jobs_dashboard.py            # http://127.0.0.1:8765
    python3 tools/jobs_dashboard.py --port 9000
"""

import argparse
import csv
import json
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rank_state import STATE, TRACKER, norm  # noqa: E402  (same paths and matching as /rank and /scrape)

ROOT = Path(__file__).resolve().parent.parent
NOTES = ROOT / "job_scraper" / "job_notes.json"

# Tracker status vocabulary is defined in .claude/commands/outcome.md; legacy spellings read as canonical.
LEGACY_STATUS = {"no response": "no_response", "offer declined": "offer_declined"}
TRACKER_HEADER = ["date", "company", "sector", "role", "role_type", "channel", "status", "contact_person",
                  "fit_rating", "notes", "cv_file", "cover_letter_file", "source", "deadline"]


class TrackerError(Exception):
    """The tracker could not be parsed safely; the page then shows no application statuses."""


def _url_key(url) -> str:
    return str(url or "").strip().rstrip("/")


def _read_seen(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    seen = doc.get("seen", doc) if isinstance(doc, dict) else {}
    return seen if isinstance(seen, dict) else {}


def _tracker_lines(path: Path) -> tuple[list[str], list[str], list[list[str] | None]]:
    """(header, physical lines, parsed record per line or None for blank lines).

    The tracker is one record per line (outcome.md forbids line breaks inside a
    field). A line with an unbalanced quote breaks that, so the file is refused
    rather than edited at the wrong offsets.
    """
    if not path.is_file():
        return list(TRACKER_HEADER), [], []
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines(keepends=True)
    records: list[list[str] | None] = []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            records.append(None)
            continue
        if line.count('"') % 2:
            raise TrackerError(f"{path.name} line {n} has a field that spans lines")
        records.append(next(csv.reader([line])))
    header = next((r for r in records if r is not None), None) or list(TRACKER_HEADER)
    return header, lines, records


def _match(header: list[str], records: list, job: dict) -> int | None:
    """Line index of the tracker row for this job: posting URL first, then company + role."""
    col = {name: i for i, name in enumerate(header)}
    url = _url_key(job.get("url"))
    pair = (norm(job.get("company")), norm(job.get("title")))
    by_pair = None
    for i, rec in enumerate(records[1:], 1):
        if rec is None:
            continue
        get = lambda name: rec[col[name]] if name in col and col[name] < len(rec) else ""  # noqa: E731
        if url and _url_key(get("source")) == url:
            return i
        if by_pair is None and (norm(get("company")), norm(get("role"))) == pair:
            by_pair = i
    return by_pair


def _canonical(status: str) -> str:
    s = (status or "").strip().lower()
    return LEGACY_STATUS.get(s, s)


def load_jobs(state: Path = STATE, notes_path: Path = NOTES, tracker: Path = TRACKER) -> list:
    seen = _read_seen(state)
    try:
        notes = json.loads(notes_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        notes = {}
    try:
        header, _, records = _tracker_lines(tracker)
    except (OSError, TrackerError):
        header, records = list(TRACKER_HEADER), []
    col = {name: i for i, name in enumerate(header)}
    jobs = []
    for key, entry in seen.items():
        if not isinstance(entry, dict):
            continue
        job = dict(entry, key=key)
        job["note"] = notes.get(entry.get("url", ""))
        i = _match(header, records, job) if records else None
        if i is not None:
            rec = records[i]
            get = lambda name: rec[col[name]] if name in col and col[name] < len(rec) else ""  # noqa: E731
            job["application"] = {"status": _canonical(get("status")), "date": get("date")}
        jobs.append(job)
    return jobs


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job Matches</title>
<style>
:root {
  --bg: #f7f7f5; --surface: #ffffff; --text: #1d1d1b; --muted: #6b6b66; --border: #e2e1dc;
  --accent: #2f5bd3; --high: #1f7a4d; --high-bg: #e3f3ea; --med: #8a5a00; --med-bg: #fbf0d9;
  --low: #8f2f2f; --low-bg: #f7e3e3; --chip: #efeee9;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #141413; --surface: #1d1d1b; --text: #ecebe6; --muted: #9b9a93; --border: #33332f;
    --accent: #7d9cf0; --high: #6fcf9b; --high-bg: #1d3328; --med: #e6b75a; --med-bg: #3a2f17;
    --low: #ec8a8a; --low-bg: #3a1f1f; --chip: #2a2a27;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
main { max-width: 1280px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 22px; margin: 0 0 4px; }
.sub { color: var(--muted); margin: 0 0 20px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; margin-bottom: 18px; }
.tile { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; }
.tile b { display: block; font-size: 24px; font-variant-numeric: tabular-nums; }
.tile span { color: var(--muted); font-size: 12px; }
.controls { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 14px; }
input[type=search], select { background: var(--surface); color: var(--text); border: 1px solid var(--border);
  border-radius: 8px; padding: 7px 10px; font: inherit; }
input[type=search] { flex: 1 1 240px; min-width: 0; }
label.chk { display: flex; gap: 6px; align-items: center; color: var(--muted); }
.chips { display: flex; gap: 6px; }
.chip { border: 1px solid var(--border); background: var(--surface); color: var(--text); border-radius: 999px;
  padding: 5px 12px; cursor: pointer; font: inherit; }
.chip[aria-pressed=true] { background: var(--accent); border-color: var(--accent); color: #fff; }
.wrap { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { font-size: 12px; color: var(--muted); font-weight: 600; cursor: pointer; white-space: nowrap; user-select: none; }
th[data-dir=asc]::after { content: " \25B2"; } th[data-dir=desc]::after { content: " \25BC"; }
tr.row { cursor: pointer; } tr.row:hover td { background: var(--chip); }
td.num { font-variant-numeric: tabular-nums; white-space: nowrap; }
.fit { display: inline-block; border-radius: 6px; padding: 2px 8px; font-size: 12px; font-weight: 600; }
.fit.high { color: var(--high); background: var(--high-bg); }
.fit.medium { color: var(--med); background: var(--med-bg); }
.fit.low { color: var(--low); background: var(--low-bg); }
.tag { display: inline-block; background: var(--chip); border-radius: 5px; padding: 1px 6px; font-size: 12px; color: var(--muted); }
.title { font-weight: 600; } .company { color: var(--muted); }
a { color: var(--accent); }
tr.detail td { background: var(--bg); }
.detail-body { display: grid; gap: 8px; }
.detail-body ul { margin: 0; padding-left: 18px; }
.links { display: flex; flex-wrap: wrap; gap: 12px; }
.empty { padding: 32px; text-align: center; color: var(--muted); }
.app { display: inline-block; border-radius: 6px; padding: 2px 8px; font-size: 12px; font-weight: 600; white-space: nowrap;
  border: 1px solid var(--border); color: var(--muted); }
.app.st-drafted { color: var(--med); background: var(--med-bg); border-color: transparent; }
.app.st-applied, .app.st-interview { color: var(--accent); border-color: var(--accent); }
.app.st-offer, .app.st-hired { color: var(--high); background: var(--high-bg); border-color: transparent; }
.app.st-rejected, .app.st-no_response, .app.st-offer_declined, .app.st-withdrawn { color: var(--low); background: var(--low-bg); border-color: transparent; }
.app-date { font-size: 11px; color: var(--muted); margin-top: 2px; }
@media (max-width: 700px) { .hide-sm { display: none; } }
</style>
</head>
<body>
<main>
  <h1>Job Matches</h1>
  <p class="sub" id="sub">Loading...</p>
  <div class="tiles" id="tiles"></div>
  <div class="controls">
    <input type="search" id="q" placeholder="Search title, company, location, notes">
    <div class="chips" id="fitChips">
      <button class="chip" data-fit="high" aria-pressed="true">High</button>
      <button class="chip" data-fit="medium" aria-pressed="true">Medium</button>
      <button class="chip" data-fit="low" aria-pressed="false">Low</button>
    </div>
    <select id="region"><option value="">All regions</option><option>India</option><option>Germany</option><option>Luxembourg</option><option>Remote</option><option>Other</option></select>
    <select id="app"><option value="">All applications</option></select>
    <select id="status"><option value="">All scrape statuses</option></select>
    <select id="portal"><option value="">All portals</option></select>
    <label class="chk"><input type="checkbox" id="noted" checked> With notes only</label>
    <label class="chk"><input type="checkbox" id="latest"> Latest run only</label>
  </div>
  <div class="wrap">
    <table>
      <thead><tr>
        <th data-k="fit">Fit</th><th data-k="title">Role</th><th data-k="location">Location</th>
        <th data-k="posted_date" class="hide-sm">Posted</th><th data-k="deadline" class="hide-sm">Deadline</th>
        <th data-k="rank_score" class="hide-sm">Score</th><th data-k="_app">Application</th><th data-k="status" class="hide-sm">Scrape</th><th>Link</th>
      </tr></thead>
      <tbody id="rows"></tbody>
    </table>
  </div>
</main>
<script>
const FIT = { high: 0, medium: 1, low: 2 };
const IN = /india|bengaluru|bangalore|hyderabad|pune|mumbai|gurgaon|gurugram|noida|delhi|chennai|kolkata|ahmedabad|kochi|telangana|karnataka|maharashtra|haryana|tamil|kerala|indore|vadodara|jaipur|lucknow/i;
const DE = /germany|deutschland|berlin|munich|münchen|hamburg|frankfurt|köln|cologne|stuttgart|düsseldorf|bavaria|dresden|nürnberg|nuremberg/i;
let jobs = [], sortKey = "fit", sortDir = "asc", open = new Set();
// job_search_tracker.csv vocabulary (/outcome); "" = no tracker row yet.
const APP = [["", "Not applied"], ["drafted", "Drafted"], ["applied", "Applied"], ["interview", "Interviewing"],
  ["offer", "Offer"], ["hired", "Accepted (hired)"], ["rejected", "Rejected"], ["no_response", "No response"],
  ["offer_declined", "Declined offer"], ["withdrawn", "Withdrawn"]];
const APP_LABEL = Object.fromEntries(APP);
const APP_ORDER = Object.fromEntries(APP.map(([v], i) => [v, i]));
const SUBMITTED = new Set(["applied", "interview", "offer", "hired", "rejected", "no_response", "offer_declined", "withdrawn"]);
const appOf = j => (j.application && j.application.status) || "";

// Read-only: statuses change through /apply, /outcome and /gmail-sync, never from this page.
function appBadge(j) {
  const cur = appOf(j);
  const label = APP_LABEL[cur] ?? cur;
  const when = j.application && j.application.date ? `<div class="app-date">${esc(j.application.date)}</div>` : "";
  return `<span class="app st-${esc(cur || "none")}">${esc(label)}</span>${when}`;
}

function region(j) {
  const l = j.location || "";
  if (IN.test(l)) return "India";
  if (/luxembourg/i.test(l)) return "Luxembourg";
  if (DE.test(l)) return "Germany";
  if (/remote|anywhere|worldwide/i.test(l)) return "Remote";
  return "Other";
}
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = u => /^https?:\/\//i.test(u || "") ? u : "#";
const li = kw => "https://www.linkedin.com/search/results/people/?keywords=" + encodeURIComponent(kw) + "&origin=GLOBAL_SEARCH_HEADER";
const shortTitle = t => (t || "").replace(/\(.*?\)|\[.*?\]/g, "").split(/[-–|,]/)[0].trim();

function filtered() {
  const q = document.getElementById("q").value.toLowerCase();
  const fits = [...document.querySelectorAll("#fitChips .chip")].filter(b => b.getAttribute("aria-pressed") === "true").map(b => b.dataset.fit);
  const reg = document.getElementById("region").value, st = document.getElementById("status").value, po = document.getElementById("portal").value;
  const ap = document.getElementById("app").value;
  const latest = document.getElementById("latest").checked, noted = document.getElementById("noted").checked;
  const lastRun = jobs.reduce((m, j) => (j.first_seen || "") > m ? j.first_seen : m, "");
  const appOk = j => !ap || (ap === "none" ? !appOf(j) : ap === "submitted" ? SUBMITTED.has(appOf(j)) : appOf(j) === ap);
  return jobs.filter(j =>
    fits.includes(j.fit) && (!reg || j._region === reg) && (!st || j.status === st) && (!po || j.portal === po) && appOk(j) &&
    (!latest || j.first_seen === lastRun) && (!noted || j.note) &&
    (!q || [j.title, j.company, j.location, j.note].join(" ").toLowerCase().includes(q)));
}

function cmp(a, b) {
  let x = a[sortKey], y = b[sortKey];
  if (sortKey === "fit") { x = FIT[x] ?? 9; y = FIT[y] ?? 9; }
  if (x == null || x === "") return 1; if (y == null || y === "") return -1;
  const r = typeof x === "number" ? x - y : String(x).localeCompare(String(y));
  return sortDir === "asc" ? r : -r;
}

function render() {
  const list = filtered().sort((a, b) => cmp(a, b) || (FIT[a.fit] - FIT[b.fit]) || String(b.posted_date).localeCompare(String(a.posted_date)));
  const count = f => list.filter(j => j.fit === f).length;
  const apps = s => list.filter(j => s.includes(appOf(j))).length;
  document.getElementById("tiles").innerHTML = [
    ["Showing", list.length], ["High", count("high")], ["Medium", count("medium")], ["Low", count("low")],
    ["Drafted", apps(["drafted"])], ["Applied", list.filter(j => SUBMITTED.has(appOf(j))).length],
    ["Interviewing", apps(["interview"])], ["Offer / accepted", apps(["offer", "hired"])],
    ["Rejected / closed", apps(["rejected", "no_response", "offer_declined", "withdrawn"])],
    ["Visa sponsor / relocation noted", list.filter(j => /sponsor|relocation/i.test(j.note || "")).length],
    ["With deadline", list.filter(j => j.deadline).length],
  ].map(([k, v]) => `<div class="tile"><b>${v}</b><span>${k}</span></div>`).join("");
  document.querySelectorAll("th[data-k]").forEach(th => th.dataset.dir = th.dataset.k === sortKey ? sortDir : "");
  const tb = document.getElementById("rows");
  if (!list.length) { tb.innerHTML = `<tr><td colspan="9" class="empty">No jobs match these filters.</td></tr>`; return; }
  tb.innerHTML = list.map(j => {
    const row = `<tr class="row" data-key="${esc(j.key)}">
      <td><span class="fit ${esc(j.fit)}">${esc(j.fit)}</span></td>
      <td><div class="title">${esc(j.title)}</div><div class="company">${esc(j.company)}</div></td>
      <td>${esc(j.location)} <span class="tag">${j._region}</span></td>
      <td class="num hide-sm">${esc(j.posted_date || "–")}</td>
      <td class="num hide-sm">${esc(j.deadline || "–")}</td>
      <td class="num hide-sm">${j.rank_score ?? "–"}</td>
      <td>${appBadge(j)}</td>
      <td class="hide-sm"><span class="tag">${esc(j.status)}</span></td>
      <td><a href="${esc(safeUrl(j.url))}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">Open</a></td></tr>`;
    if (!open.has(j.key)) return row;
    const bullets = (label, arr) => Array.isArray(arr) && arr.length ? `<div><b>${label}</b><ul>${arr.map(s => `<li>${esc(s)}</li>`).join("")}</ul></div>` : "";
    return row + `<tr class="detail"><td colspan="9"><div class="detail-body">
      ${j.note ? `<div>${esc(j.note)}</div>` : `<div class="company">No notes for this job.</div>`}
      ${j.rank_verdict ? `<div><b>Rank verdict:</b> ${esc(j.rank_verdict)}</div>` : ""}
      ${bullets("Strengths", j.strengths)}${bullets("Gaps", j.gaps)}
      <div class="company">Portal: ${esc(j.portal || "–")} · Source: ${esc(j.source || "–")} · First seen: ${esc(j.first_seen || "–")} · Type: ${esc(j.employment_type || "not stated")}</div>
      <div class="links">
        <a href="${li(j.company + " recruiter")}" target="_blank" rel="noopener noreferrer">LinkedIn: recruiters</a>
        <a href="${li(j.company + " " + shortTitle(j.title))}" target="_blank" rel="noopener noreferrer">LinkedIn: team peers</a>
      </div></div></td></tr>`;
  }).join("");
}

document.getElementById("rows").addEventListener("click", e => {
  const tr = e.target.closest("tr.row"); if (!tr) return;
  const k = tr.dataset.key; open.has(k) ? open.delete(k) : open.add(k); render();
});
document.querySelectorAll("th[data-k]").forEach(th => th.addEventListener("click", () => {
  if (sortKey === th.dataset.k) sortDir = sortDir === "asc" ? "desc" : "asc";
  else { sortKey = th.dataset.k; sortDir = ["posted_date", "rank_score", "_app"].includes(sortKey) ? "desc" : "asc"; }
  render();
}));
document.querySelectorAll("#fitChips .chip").forEach(b => b.addEventListener("click", () => {
  b.setAttribute("aria-pressed", b.getAttribute("aria-pressed") === "true" ? "false" : "true"); render();
}));
["q", "region", "status", "portal", "app", "latest", "noted"].forEach(id => document.getElementById(id).addEventListener("input", render));
document.getElementById("app").insertAdjacentHTML("beforeend",
  `<option value="none">Not applied</option><option value="submitted">Any submitted</option>` +
  APP.filter(([v]) => v).map(([v, l]) => `<option value="${v}">${l}</option>`).join(""));

fetch("/api/jobs").then(r => r.json()).then(data => {
  // _app sorts by pipeline stage; jobs with no tracker row sort last.
  jobs = data.map(j => ({ ...j, _region: region(j), _app: appOf(j) ? (APP_ORDER[appOf(j)] ?? 99) : null }));
  const fill = (id, vals) => document.getElementById(id).insertAdjacentHTML("beforeend", [...new Set(vals)].filter(Boolean).sort().map(v => `<option>${esc(v)}</option>`).join(""));
  fill("status", jobs.map(j => j.status)); fill("portal", jobs.map(j => j.portal));
  const last = jobs.reduce((m, j) => (j.first_seen || "") > m ? j.first_seen : m, "");
  document.getElementById("sub").textContent = `${jobs.length} jobs in history · latest run ${last || "unknown"} · click a row for notes and referral links · application status from job_search_tracker.csv (update it with /outcome)`;
  render();
}).catch(() => { document.getElementById("sub").textContent = "Could not load /api/jobs."; });
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/jobs":
            self._send(json.dumps(load_jobs()).encode("utf-8"), "application/json")
        else:
            self._send(b"not found", "text/plain", 404)

    def log_message(self, *args) -> None:
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Job dashboard at {url} (Ctrl+C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

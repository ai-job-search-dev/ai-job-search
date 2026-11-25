#!/usr/bin/env python3
"""Progress log for /apply runs, read by dashboard.html's "Applying" column.

An /apply run spends minutes between "the user pasted a URL" and "a drafted
row lands in job_search_tracker.csv" (Step 6b): the posting is fetched and
gated, the fit is evaluated, the user decides, two documents are drafted,
reviewed, revised and compiled. Nothing on disk recorded any of that, so the
dashboard could not show a job that was mid-flight. This file is that record.

Three subcommands:

  set    record that a run reached a stage (creates the run on first call)
  list     print the runs as JSON (all, or --active only)
  resolve  turn a dashboard job ID (seen_jobs key, run id, LinkedIn job ID
           or tracker/archive slug) into the posting URL, so `/apply <id>`
           works without reading seen_jobs.json into the conversation

    python3 tools/apply_progress.py set --url '<URL>' --stage fetching
    python3 tools/apply_progress.py set --url '<URL>' --company 'Acme' \
        --role 'Backend Engineer' --stage awaiting_decision --fit 74 \
        --note 'Good Fit - salary not stated'

Runs are keyed by the posting URL (a LinkedIn job ID when the URL carries
one, so the sa./www. and slug variants of one posting are the same run), or
by --id for a pasted posting with no URL. The seen_jobs.json key is resolved
on every write so the dashboard can join the run to its scraped card.

State lives in job_scraper/apply_progress.json (gitignored, personal data).
Writes are atomic (temp file + rename). Notes are plain text and are shown
escaped by the dashboard; keep them short and never paste posting text into
them - the posting is untrusted data and belongs in the archive only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "job_scraper" / "apply_progress.json"
SEEN = ROOT / "job_scraper" / "seen_jobs.json"
TRACKER = ROOT / "job_search_tracker.csv"

# Ordered pipeline stages. `drafted` and `stopped` are terminal.
STAGES = [
    "fetching",           # Step 0: posting-fetcher / posting_guard running
    "evaluating",         # Step 1: fit evaluation being written
    "awaiting_decision",  # Step 1: evaluation shown, waiting for the user's yes/no
    "drafting",           # Step 2: CV + cover letter being written
    "reviewing",          # Step 3: application-reviewer running
    "revising",           # Step 4: applying review edits
    "compiling",          # Step 5: compile, layout and ATS checks
    "recording",          # Step 6/6b: checklist, tracker row, archive
    "drafted",            # done: tracker row written (card moves to Drafted)
    "stopped",            # user said no, duplicate application, or posting dead
]
TERMINAL = {"drafted", "stopped"}
# Tracker statuses that close an application (/outcome "Tracker status vocabulary",
# legacy space spellings included). Every other status is an open row.
FINAL_STATUSES = {"hired", "rejected", "no_response", "no response", "offer_declined", "offer declined", "withdrawn"}
HISTORY_CAP = 50


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def join_key(s: str | None) -> str:
    return re.sub(r"[^\w]+", " ", (s or "").lower().replace("_", " ")).strip()


def is_linkedin_host(url: str | None) -> bool:
    """True only for linkedin.com or a subdomain of it (www., sa., ...).

    A substring test would accept look-alike hosts (notlinkedin.com,
    linkedin.com.evil.example) or a LinkedIn URL embedded in another site's
    query string, and key that posting as an unrelated li-<id> run.
    """
    try:
        host = (urlparse((url or "").strip()).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return host == "linkedin.com" or host.endswith(".linkedin.com")


# LinkedIn paths that name one job posting. Anything else on linkedin.com (a
# company page, a profile, the feed) is not a posting, even when it ends in digits.
_LI_POSTING_PATH = re.compile(
    r"^(?:/comm)?/jobs/view/(?:[^/]*-)?(\d{6,})/?$"   # /jobs/view/<id>, /jobs/view/<slug>-<id>, email /comm/ links
    r"|^/jobs-guest/jobs/api/jobPosting/(\d{6,})/?$"  # the guest API linkedin-search's `detail` fetches
)


def linkedin_id(url: str | None) -> str | None:
    """The LinkedIn job ID a posting URL names, or None.

    Same rule as linkedinId() in dashboard.html (tests pin the two together):
    the host must be linkedin.com or a subdomain, and the ID comes from a
    posting path, or from `currentJobId` on a /jobs/ search or collection page.
    """
    if not is_linkedin_host(url):
        return None
    parsed = urlparse(url.strip())
    m = _LI_POSTING_PATH.match(parsed.path)
    if m:
        return m.group(1) or m.group(2)
    if parsed.path.startswith("/jobs/"):
        current = parse_qs(parsed.query).get("currentJobId", [""])[0]
        if re.fullmatch(r"\d{6,}", current):
            return current
    return None


def run_id(url: str | None, explicit: str | None) -> str:
    if explicit:
        return explicit
    lid = linkedin_id(url)
    if lid:
        return f"li-{lid}"
    if url:
        clean = url.split("#")[0].strip().lower()
        return "url-" + hashlib.sha1(clean.encode()).hexdigest()[:12]
    sys.exit("error: --url or --id is required")


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        sys.exit(f"error: {path} is not valid JSON ({e}); fix or move it aside")


def resolve_seen_key(url: str | None, company: str | None, role: str | None) -> str | None:
    seen = load(SEEN).get("seen", {})
    lid = linkedin_id(url)
    if lid:
        for key, v in seen.items():
            if linkedin_id(v.get("url")) == lid:
                return key
    if company and role:
        want = (join_key(company), join_key(role))
        for key, v in seen.items():
            if (join_key(v.get("company")), join_key(v.get("title"))) == want:
                return key
    return None


def save(data: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=STATE.parent, prefix=".apply_progress.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, STATE)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def clean_note(note: str | None) -> str:
    # One short line: no control characters, bounded length.
    note = re.sub(r"[\x00-\x1f\x7f]+", " ", note or "").strip()
    return note[:300]


def cmd_set(a: argparse.Namespace) -> None:
    if a.stage not in STAGES:
        sys.exit(f"error: --stage must be one of {', '.join(STAGES)}")
    data = load(STATE)
    runs = data.setdefault("runs", {})
    rid = run_id(a.url, a.id)
    run = runs.get(rid) or {"started": now(), "history": []}
    for field in ("url", "company", "role"):
        val = getattr(a, field)
        if val:
            run[field] = val.strip()
    if a.fit is not None:
        if not 0 <= a.fit <= 100:
            sys.exit("error: --fit must be 0-100")
        run["fit"] = a.fit
    if a.verdict:
        run["verdict"] = a.verdict.strip()
    if a.cv:
        run["cv_file"] = a.cv
    if a.cover:
        run["cover_letter_file"] = a.cover
    note = clean_note(a.note)
    run["stage"] = a.stage
    run["note"] = note
    run["updated"] = now()
    key = resolve_seen_key(run.get("url"), run.get("company"), run.get("role"))
    if key:
        run["seen_key"] = key
    run["history"] = (run.get("history", []) + [{"at": run["updated"], "stage": a.stage, "note": note}])[-HISTORY_CAP:]
    runs[rid] = run
    data["updated"] = run["updated"]
    save(data)
    print(json.dumps({"id": rid, "stage": a.stage, "seen_key": run.get("seen_key")}, ensure_ascii=False))


def tracker_rows() -> list[dict]:
    import csv
    try:
        with open(TRACKER, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except FileNotFoundError:
        return []


def pick_row(rows: list[dict]) -> dict | None:
    """The tracker row a company+role ID means when several match.

    /apply Step 6b appends a new row beside a final one when the user
    re-applies, so one company+role can hold an old closed application and a
    current one. Prefer an open row, then the latest date, then the row that
    comes last in the file (the most recently appended).
    """
    if not rows:
        return None
    ranked = sorted(
        enumerate(rows),
        key=lambda ir: ((ir[1].get("status") or "").strip().lower() not in FINAL_STATUSES,
                        (ir[1].get("date") or "").strip(), ir[0]),
    )
    return ranked[-1][1]


def archive_slug(company: str, role: str) -> str:
    # documents/README.md "Subfolder naming".
    s = re.sub(r"\s+", "_", f"{company}_{role}".lower())
    s = re.sub(r"[^\w]", "", s)
    return re.sub(r"_+", "_", s).strip("_")


def cmd_resolve(a: argparse.Namespace) -> None:
    """Turn a dashboard job ID into the posting URL /apply should fetch."""
    jid = a.job_id.strip()
    seen = load(SEEN).get("seen", {})
    hit = None
    if jid in seen:  # seen_jobs.json key, e.g. cisco_ai-engineer
        v = seen[jid]
        hit = {"url": v.get("url"), "company": v.get("company"), "role": v.get("title"), "source": "seen_jobs"}
    pasted_run = None
    if not hit:  # /apply run id (li-<digits> / url-<hash> / --id) or a bare LinkedIn job ID
        run = load(STATE).get("runs", {}).get(jid)
        if run and run.get("url"):
            hit = {"url": run["url"], "company": run.get("company"), "role": run.get("role"), "source": "apply_progress"}
        elif run:
            # Started from pasted text with --id: no URL was ever stored, and the
            # posting text is deliberately not kept here. The tracker may still
            # know the job (a drafted run); otherwise the user has to paste again.
            pasted_run = run
        else:
            m = re.fullmatch(r"(?:li-)?(\d{6,})", jid)
            if m:
                lid = m.group(1)
                v = next((v for v in seen.values() if linkedin_id(v.get("url")) == lid), None)
                hit = ({"url": v.get("url"), "company": v.get("company"), "role": v.get("title"), "source": "seen_jobs"} if v
                       else {"url": f"https://www.linkedin.com/jobs/view/{lid}/", "company": None, "role": None, "source": "linkedin_id"})
    if not hit:  # tracker / archive slug, e.g. microsoft_solutions_architect
        row = pick_row([r for r in tracker_rows() if archive_slug(r.get("company", ""), r.get("role", "")) == jid])
        if row:
            hit = {"url": row.get("source") or None, "company": row.get("company"), "role": row.get("role"),
                   "status": row.get("status"), "source": "tracker"}
    if not hit and pasted_run is not None:
        sys.exit(f"error: '{jid}' is an /apply run started from pasted posting text, so no posting URL is stored; "
                 "paste the posting text into /apply again")
    if not hit:
        sys.exit(f"error: no job with id '{jid}' in seen_jobs.json, apply_progress.json or the tracker")
    if hit["source"] != "tracker" and hit.get("company") and hit.get("role"):
        # A scraped job that was already applied to: report the tracker row so /apply asks first.
        want = (join_key(hit["company"]), join_key(hit["role"]))
        row = pick_row([r for r in tracker_rows() if (join_key(r.get("company")), join_key(r.get("role"))) == want])
        if row:
            hit.update(source="tracker", status=row.get("status"), tracked_on=row.get("date"),
                       cv_file=row.get("cv_file") or None, cover_letter_file=row.get("cover_letter_file") or None)
    hit["id"] = jid
    print(json.dumps(hit, ensure_ascii=False))


def cmd_list(a: argparse.Namespace) -> None:
    runs = load(STATE).get("runs", {})
    if a.active:
        runs = {k: v for k, v in runs.items() if v.get("stage") not in TERMINAL}
    print(json.dumps(runs, indent=1, ensure_ascii=False))


def _force_utf8_output() -> None:
    """Write UTF-8 whatever the host's default encoding is (same guard as job_key.py).

    A piped stdout on Windows defaults to the ANSI code page (cp1252 on most
    Western installs), so printing a company or role outside it raised
    UnicodeEncodeError before /apply saw any output.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)  # absent on a StringIO under test
        if reconfigure:
            reconfigure(encoding="utf-8")


def main() -> None:
    _force_utf8_output()
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set", help="record that a run reached a stage")
    s.add_argument("--url")
    s.add_argument("--id", help="run id for a pasted posting with no URL")
    s.add_argument("--company")
    s.add_argument("--role")
    s.add_argument("--stage", required=True, help=" | ".join(STAGES))
    s.add_argument("--fit", type=int, help="overall fit score 0-100 (Step 1)")
    s.add_argument("--verdict", help="fit band, e.g. 'Good Fit'")
    s.add_argument("--note", help="one short line shown on the card")
    s.add_argument("--cv", help="CV path once drafted")
    s.add_argument("--cover", help="cover letter path once drafted")
    s.set_defaults(func=cmd_set)
    ls = sub.add_parser("list", help="print runs as JSON")
    ls.add_argument("--active", action="store_true", help="only runs not yet drafted or stopped")
    ls.set_defaults(func=cmd_list)
    rs = sub.add_parser("resolve", help="turn a dashboard job ID into its posting URL")
    rs.add_argument("job_id")
    rs.set_defaults(func=cmd_resolve)
    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()

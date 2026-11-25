#!/usr/bin/env python3
"""Data layer for /html-report.

Tracker read, status buckets, outcome.md stage matching, counts, funnel and
rejection rate, wrapped in load() so the view (tools/html_report.py) can
render it. The view must not recompute these figures differently.
"""
import collections
import csv
import datetime
import glob
import os
import re
import sys
import unicodedata

BUCKET = {"drafted": "Drafted", "applied": "Active", "interview": "Interview", "offer": "Offer", "hired": "Hired"}
CLOSED = {"rejected", "no_response", "no response", "offer_declined", "offer declined", "withdrawn"}
FIELDS = ["date", "company", "sector", "role", "role_type", "channel", "status", "contact_person",
          "fit_rating", "notes", "cv_file", "cover_letter_file", "source", "deadline"]
# The one stage rule. A row's furthest stage is the later of what its current status implies
# and the furthest outcome.md checkbox ticked; the funnel and the "where closed applications
# stopped" chart both read it from r["_stage"], an index into STAGES.
STAGES = ["Application screen", "Recruiter screen", "Technical round", "Case or presentation",
          "Final round", "Offer", "Hired"]
STATUS_STAGE = {"interview": 1, "offer": 5, "offer_declined": 5, "offer declined": 5, "hired": 6}
TICK_STAGE = [(5, r"offer"), (4, r"final"), (3, r"case|assessment|presentation"), (2, r"technical"),
              (1, r"phone|screen|recruiter|interview")]  # first match wins: "Technical interview" is 2
INTERVIEW, OFFER, HIRED = 1, 5, 6
STATUS_ORDER = ["Drafted", "Active", "Interview", "Offer", "Hired", "Rejected/Closed"]


def folder_name(company, role):
    """The Subfolder naming rule in documents/README.md: <company>_<role>, lowercase, spaces to
    underscores, every character but a letter, digit or underscore dropped, runs of underscores
    collapsed, ends trimmed. NFC first, so a name typed with combining accents matches its folder."""
    name = unicodedata.normalize("NFC", f"{company} {role}").lower().replace(" ", "_")
    name = "".join(ch for ch in name if ch.isalnum() or ch == "_")
    return re.sub(r"_+", "_", name).strip("_")


def when(s, today=None):
    """(start of the period, precision) for a YYYY, YYYY-MM or YYYY-MM-DD date (- or / between the
    parts); None if unparseable, before 1970 or over a year ahead, so a typo year cannot stretch
    the chart over centuries."""
    m = re.fullmatch(r"(\d{4})(?:[-/](\d{1,2})(?:[-/](\d{1,2}))?)?", (s or "").strip())
    if not m:
        return None
    y, mo, d = m.groups()
    try:
        day = datetime.date(int(y), int(mo or 1), int(d or 1))
    except ValueError:
        return None
    today = today or datetime.date.today()
    if not datetime.date(1970, 1, 1) <= day <= today + datetime.timedelta(days=366):
        return None
    return day, "day" if d else "month" if mo else "year"


def stages_section(txt):
    """The '## Interview stages reached' checklist of an outcome.md, so a checklist under Notes
    cannot advance a stage. A file without that heading (hand-written) is read whole."""
    m = re.search(r"^## Interview stages reached[^\n]*\n(.*?)(?=^## |\Z)", txt, re.M | re.S)
    return m.group(1) if m else txt


def timeline(sent, today):
    """Sent applications per week while first application to today spans under 12 weeks of full dates, else per
    month, or per year when some rows record only the year. Months and years end at the last
    application, so a finished search is not a run of empty bars."""
    w = [x for x in (when(r["date"], today) for r in sent) if x]
    if not w:
        return "week", []
    days = [d for d, _ in w]
    first, last = min(days), max(days)
    if all(p == "day" for _, p in w) and (max(last, today) - first).days < 12 * 7:
        start = first - datetime.timedelta(days=first.weekday())
        weeks = [start + datetime.timedelta(days=7 * i) for i in range((max(last, today) - start).days // 7 + 1)]
        return "week", [(wk, sum(wk <= d < wk + datetime.timedelta(days=7) for d in days)) for wk in weeks]
    if all(p != "year" for _, p in w):
        months = [datetime.date(m // 12, m % 12 + 1, 1)
                  for m in range(first.year * 12 + first.month - 1, last.year * 12 + last.month)]
        return "month", [(m, sum((d.year, d.month) == (m.year, m.month) for d in days)) for m in months]
    return "year", [(datetime.date(y, 1, 1), sum(d.year == y for d in days)) for y in range(first.year, last.year + 1)]


def load(root="."):
    path = os.path.join(root, "job_search_tracker.csv")
    if not os.path.isfile(path):
        sys.exit(f"{path} not found - /apply creates it with the first application")
    try:
        # utf-8-sig: Excel's "CSV UTF-8" save starts with a BOM that would land in the first header
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            # read the header while the file is open (an empty file has none), case-insensitively
            header = [h.strip().lower() for h in (reader.fieldnames or [])]
            reader.fieldnames = header
            rows = list(reader)
    except UnicodeDecodeError:
        sys.exit(f"{path} is not UTF-8 text - re-save it as CSV UTF-8")
    # Excel in a Danish or German locale saves "CSV UTF-8" with semicolons: one unreadable
    # column would otherwise render a valid-looking page of zeros
    if not {"company", "role", "status"} <= set(header):
        sys.exit(f"{path} has no company, role and status columns - re-save it as comma-separated CSV UTF-8")
    for r in rows:  # a short row (13-field legacy, or truncated) reads its missing fields as empty
        for h in FIELDS:
            r[h] = r.get(h) or ""
    rows = [r for r in rows if any(r[h].strip() for h in FIELDS)]  # a row of bare commas is not an application
    today = datetime.date.today().isoformat()
    unknown = set()

    def bucket(s):
        s = (s or "").strip().lower()
        if s in BUCKET:
            return BUCKET[s]
        if s not in CLOSED:
            unknown.add(s)
        return "Rejected/Closed"

    # archives: a row matches only the folder named from its company + role by the Subfolder
    # naming rule. A folder without outcome.md still counts, with no ticked stages.
    # ponytail: a re-application to the same company + role shares the earlier one's folder (and
    # ticks); the naming rule gives them one name, so it cannot be told apart here.
    arch = {}
    for d in sorted(glob.glob(os.path.join(root, "documents/applications/*/"))):
        f = os.path.join(d, "outcome.md")
        try:
            txt = open(f, encoding="utf-8").read() if os.path.exists(f) else ""
        except UnicodeDecodeError:
            sys.exit(f"{f} is not UTF-8 text - re-save it as UTF-8")
        # the folder name goes through the same rule as the row key, so archives named under the
        # older rule (hyphens, dots kept) or by hand, and names with vowel signs, still match.
        # Folders that share a key (an old-rule archive next to the one /outcome writes now) merge
        # their ticks, so none is lost and the result does not depend on sort order.
        arch.setdefault(folder_name(os.path.basename(os.path.normpath(d)), ""), (d, []))[1].extend(
            (t.lower(), n) for t, n in re.findall(r"- \[( |x|X)\] (.+)", stages_section(txt)))

    def find_arch(r):
        key = folder_name(r["company"], r["role"])
        return key if key in arch else None

    for r in rows:
        b = bucket(r["status"]); r["_b"] = b
        a = find_arch(r); r["_a"] = a
        ticked = [n for t, n in arch[a][1] if t == "x"] if a else []
        tick = max((next((i for i, pat in TICK_STAGE if re.search(pat, n, re.I)), 0) for n in ticked), default=0)
        r["_stage"] = max(STATUS_STAGE.get(r["status"].strip().lower(), 0), tick)

    sent = [r for r in rows if r["_b"] != "Drafted"]
    cnt = collections.Counter(r["_b"] for r in rows)
    sector = collections.Counter(r["sector"].strip() or "Unspecified" for r in sent)
    channel = collections.Counter(r["channel"].strip() or "other" for r in sent)
    fun = [("Applied", len(sent)), ("Interview", sum(1 for r in sent if r["_stage"] >= INTERVIEW)),
           ("Offer", sum(1 for r in sent if r["_stage"] >= OFFER)), ("Hired", sum(1 for r in sent if r["_stage"] >= HIRED))]
    st = lambda r: r["status"].strip().lower()
    rej = sum(1 for r in rows if st(r) in ("rejected", "no_response", "no response"))
    final = sum(1 for r in rows if st(r) in CLOSED | {"hired"})
    rate = f"{rej/final*100:.0f}%" if final else "n/a"
    prog = f"{fun[1][1]/fun[0][1]*100:.0f}%" if fun[0][1] else "n/a"
    return {
        "today": today, "rows": rows, "sent": sent, "counts": cnt, "sector": sector, "channel": channel,
        "timeline": timeline(sent, datetime.date.fromisoformat(today)), "funnel": fun, "rejected": rej, "final": final, "rejection_rate": rate,
        "progress_rate": prog, "unknown": unknown, "archives": arch,
    }

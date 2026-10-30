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

BUCKET = {"drafted": "Drafted", "applied": "Active", "interview": "Interview", "offer": "Offer", "hired": "Hired"}
CLOSED = {"rejected", "no_response", "no response", "offer_declined", "offer declined", "withdrawn"}
STATUS_ORDER = ["Drafted", "Active", "Interview", "Offer", "Hired", "Rejected/Closed"]


def norm(x):
    return re.sub(r"[^a-z0-9]", "", (x or "").lower())


def load(root="."):
    rows = list(csv.DictReader(open(os.path.join(root, "job_search_tracker.csv"), newline="", encoding="utf-8")))
    today = datetime.date.today().isoformat()
    unknown = set()

    def bucket(s):
        s = (s or "").strip().lower()
        if s in BUCKET:
            return BUCKET[s]
        if s not in CLOSED:
            unknown.add(s)
        return "Rejected/Closed"

    # archives
    # every archive folder counts, so a new application's own folder (no outcome.md yet)
    # is matched instead of falling back to an older same-company archive
    arch = {}
    for d in glob.glob(os.path.join(root, "documents/applications/*/")):
        f = os.path.join(d, "outcome.md")
        txt = open(f, encoding="utf-8").read() if os.path.exists(f) else ""
        arch[os.path.basename(os.path.normpath(d))] = re.findall(r"- \[( |x)\] (.+)", txt)

    def find_arch(r):
        key = norm(r["company"] + r["role"]); c = norm(r["company"])
        best = None
        for d in arch:
            dn = norm(d)
            if dn == key or (len(dn) > 6 and (dn in key or key in dn)):
                return d
            if dn.startswith(c) and c and best is None:
                rw = [w for w in re.findall(r"[a-z]+", r["role"].lower()) if len(w) > 3]
                if any(w in d for w in rw):
                    best = d
        return best

    for r in rows:
        b = bucket(r["status"]); r["_b"] = b
        a = find_arch(r); r["_a"] = a
        lvl = 0
        if b != "Drafted":
            lvl = 1
        ticked = [n for t, n in arch.get(a, []) if t == "x"] if a else []
        if b in ("Interview", "Offer", "Hired") or any(re.search(r"phone|screen|technical|interview|final|case|assessment", n, re.I) and not re.search(r"offer", n, re.I) for n in ticked):
            lvl = max(lvl, 2)
        if b in ("Offer", "Hired") or any(re.search(r"offer", n, re.I) for n in ticked):
            lvl = max(lvl, 3)
        if b == "Hired":
            lvl = 4
        r["_lvl"] = lvl

    sent = [r for r in rows if r["_b"] != "Drafted"]
    cnt = collections.Counter(r["_b"] for r in rows)
    sector = collections.Counter((r["sector"] or "Unspecified").strip() for r in sent)
    channel = collections.Counter((r["channel"] or "other").strip() for r in sent)
    month = collections.Counter((r["date"] or "")[:7] or "unknown" for r in sent)
    fun = [("Applied", sum(1 for r in sent)), ("Interview", sum(1 for r in sent if r["_lvl"] >= 2)), ("Offer", sum(1 for r in sent if r["_lvl"] >= 3)), ("Hired", sum(1 for r in sent if r["_lvl"] >= 4))]
    st = lambda r: r["status"].strip().lower()
    rej = sum(1 for r in rows if st(r) in ("rejected", "no_response", "no response"))
    final = sum(1 for r in rows if st(r) in CLOSED | {"hired"})
    rate = f"{rej/final*100:.0f}%" if final else "n/a"
    prog = f"{fun[1][1]/fun[0][1]*100:.0f}%" if fun[0][1] else "n/a"
    return {
        "today": today, "rows": rows, "sent": sent, "counts": cnt, "sector": sector, "channel": channel,
        "month": month, "funnel": fun, "rejected": rej, "final": final, "rejection_rate": rate,
        "progress_rate": prog, "unknown": unknown, "archives": arch,
    }

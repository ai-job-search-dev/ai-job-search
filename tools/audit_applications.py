#!/usr/bin/env python3
"""Audit submitted applications: what was sent, checked against the job and the profile.

    python3 tools/audit_applications.py --job-id 916 --job-id 1761
    python3 tools/audit_applications.py --since "2026-11-21 00:00"

For each application it reads the saved record (form_values.json, the CV and cover letter PDFs) and flags:
work-authorization and sponsorship answers that disagree with the job's region, residence answers that are not
Argentina, free-text answers with names or claims the profile does not support, a letter that names another
company or skips the current job, a CV headline from a different field than the job title, and fields left empty.
Writes reports/application-audit-<time>.md and prints a one-line verdict per application.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.answer_bank import unsupported, unsupported_claims, vocabulary
from tools.db import get_db
from tools.preferences import regions_in
from tools.profile_manager import load_profile
from tools.verify_pdf import extract_text_layer

VISA_REGIONS = {"US", "UK", "CA", "AU", "NZ"}


def audit_one(conn: Any, profile: Any, job_id: int) -> Dict[str, Any]:
    job = conn.execute(
        "SELECT j.id, j.title, j.location, j.status, c.name AS company, COALESCE(NULLIF(j.cleaned_description, ''), j.raw_description, '') AS d, "
        "a.cv_path, a.applied_at FROM jobs j JOIN companies c ON c.id = j.company_id "
        "LEFT JOIN applications a ON a.id = (SELECT MAX(id) FROM applications WHERE job_id = j.id) WHERE j.id = ?", (job_id,)).fetchone()
    issues: List[str] = []
    if job is None or not job["cv_path"]:
        return {"job_id": job_id, "company": "?", "title": "?", "issues": ["no application record"], "sent": {}}
    folder = Path(job["cv_path"]).parent
    record_path = folder / "form_values.json"
    record = json.loads(record_path.read_text()) if record_path.is_file() else {"filled": [], "left_for_you": []}
    sent = {f["field"]: str(f["value"]) for f in record["filled"]}
    regions = regions_in(job["location"] or "")
    needs_visa = bool(regions & VISA_REGIONS) and not (regions - VISA_REGIONS)
    allowed = vocabulary(profile, f"{job['d']} {job['company']} {job['title']}")
    candidate = vocabulary(profile)
    places = [part.strip().casefold() for part in profile.identity.location.split(",") if part.strip()]
    for field, value in sent.items():
        q = field.casefold()
        v = value.casefold()
        q_regions = regions_in(field) or regions
        q_visa = bool(q_regions & VISA_REGIONS) and not (q_regions - VISA_REGIONS)
        if re.search(r"authori[sz]ed to work|right to work|eligible to work", q):
            if q_visa and v.startswith(("yes", "sí")):
                issues.append(f"says authorized to work for a {'/'.join(sorted(q_regions))} role: {field[:60]}")
            if not q_visa and q_regions and v.startswith("no"):
                issues.append(f"says not authorized for a non-visa region: {field[:60]}")
        if re.search(r"sponsor", q):
            if q_visa and v.startswith("no"):
                issues.append(f"says no sponsorship needed for a {'/'.join(sorted(q_regions))} role: {field[:60]}")
            if not q_visa and v.startswith("yes"):
                issues.append(f"says sponsorship needed for a role open to the candidate: {field[:60]}")
        if re.search(r"country (?:of residence|are you based|do you live)|where are you based|current country|^location", q) \
                and not re.search(r"eligible|authori[sz]ed|permitted|legally", q):
            if v in ("yes", "no") or not any(place in v for place in places):
                issues.append(f"residence answer is '{value[:30]}': {field[:50]}")
        if len(value.split()) >= 12:
            names = unsupported(value, allowed)
            claims = unsupported_claims(value, candidate)
            if names or claims:
                issues.append(f"free text has {', '.join(names + claims)} not backed by the profile: {field[:50]}")
    letter = extract_text_layer(folder / "cover_letter.pdf")[0] if (folder / "cover_letter.pdf").is_file() else ""
    current = next((e.company for e in profile.experience if e.is_current), "")
    if letter:
        if job["company"].casefold() not in letter.casefold():
            issues.append("letter does not name the company")
        if current and current.casefold() not in letter.casefold():
            issues.append(f"letter does not mention the current job at {current}")
    cv = extract_text_layer(folder / "cv.pdf")[0] if (folder / "cv.pdf").is_file() else ""
    headline = cv.splitlines()[1].strip() if cv.count("\n") > 1 else ""
    title = job["title"].casefold()
    if "quantum" in headline.casefold() and "quantum" not in title and "quantum" not in job["company"].casefold():
        issues.append(f"CV headline '{headline}' is quantum for a non-quantum role")
    if "machine learning" in headline.casefold() and not re.search(r"machine learning|\bml\b|data scien", title):
        issues.append(f"CV headline '{headline}' is ML for a non-ML role")
    required_left = [l for l in record.get("left_for_you", []) if l not in ("file",)]
    return {"job_id": job_id, "company": job["company"], "title": job["title"], "location": job["location"], "status": job["status"],
            "applied_at": job["applied_at"], "headline": headline, "issues": issues, "sent": sent, "left": required_left}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit submitted applications")
    parser.add_argument("--job-id", type=int, action="append", default=None)
    parser.add_argument("--since", default=None, help="Applied at or after this UTC time, e.g. '2026-11-21 00:00'")
    args = parser.parse_args()
    profile = load_profile()
    with get_db() as conn:
        ids = args.job_id or [r[0] for r in conn.execute(
            "SELECT job_id FROM applications WHERE stage = 'SUBMITTED' AND applied_at >= ? ORDER BY applied_at",
            (args.since or "0000",)).fetchall()]
        results = [audit_one(conn, profile, j) for j in ids]
    lines = ["# Application audit", ""]
    for r in results:
        verdict = "OK" if not r["issues"] else f"{len(r['issues'])} issue(s)"
        print(f"  [{verdict}] #{r['job_id']} {r['company']} - {r['title'][:50]} ({r.get('location') or ''})")
        for i in r["issues"]:
            print(f"      ! {i}")
        lines += [f"## [{verdict}] #{r['job_id']} {r['company']} - {r['title']}", "",
                  f"- Location: {r.get('location')}; status {r.get('status')}; applied {r.get('applied_at')}", f"- CV headline: {r.get('headline')}"]
        lines += [f"- Issue: {i}" for i in r["issues"]]
        lines += ["", "| Field | Sent |", "|---|---|"] + [f"| {k[:70]} | {v[:120].replace('|', '/')} |" for k, v in r["sent"].items()]
        lines += ["", f"Left empty: {', '.join(r.get('left') or []) or 'nothing'}", ""]
    out = ROOT_DIR / "reports" / f"application-audit-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

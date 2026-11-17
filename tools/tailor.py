#!/usr/bin/env python3
"""Document Tailor: builds a job-specific CV and cover letter from the master profile.

Selects SHORTLISTED jobs (score >= 7.5), reorders verified profile content around the
skills the ranker matched, renders Typst sources, compiles PDFs, runs the ATS and layout
verifiers, and moves the job to TAILORED or READY_TO_APPLY. Content is deterministic and
only ever drawn from the master profile, so nothing can be invented.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.db import (
    get_db,
    get_shortlisted_for_tailoring,
    init_db,
    record_tailored_documents,
)
from tools.logger import METRICS, get_logger
from tools.profile_manager import DEFAULT_PROFILE_FILE, load_profile
from tools.profile_schema import MasterProfile

logger = get_logger("tailor")

TEMPLATES_DIR = ROOT_DIR / "templates" / "typst"
APPLICATIONS_DIR = ROOT_DIR / "documents" / "applications"
SCORE_THRESHOLD = 7.5
MAX_CV_PAGES = 2
MAX_PROJECTS = 3

# (font pt, margin cm, projects). One-page options first, then roomier two-page options that fill page 2.
FIT_LADDER = (
    (10.0, 1.8, MAX_PROJECTS), (9.5, 1.5, MAX_PROJECTS), (9.0, 1.3, 2), (9.0, 1.2, 1),
    (10.0, 2.0, MAX_PROJECTS), (10.5, 2.0, MAX_PROJECTS), (11.0, 2.0, MAX_PROJECTS),
    (11.0, 2.2, MAX_PROJECTS), (10.5, 2.4, MAX_PROJECTS),
)


def page_setup(font_pt: float = 10.0, margin_cm: float = 1.8) -> str:
    return (
        f'#set page(paper: "a4", margin: (x: {margin_cm}cm, top: {margin_cm}cm, bottom: {margin_cm}cm))\n'
        f'#set text(font: ("Helvetica", "Arial", "Liberation Sans"), size: {font_pt}pt, fill: rgb("#222222"), lang: "en")\n'
        '#import "cv_lib.typ": *\n'
    )


PAGE_SETUP = page_setup()


@dataclass
class TailoredCV:
    name: str
    title: str
    email: str
    phone: str
    location: str
    linkedin: str
    github: str
    summary: str
    skill_groups: List[Tuple[str, List[str]]]
    experience: List[Dict[str, Any]]
    projects: List[Dict[str, Any]]
    education: List[Dict[str, Any]]
    extras: List[str] = field(default_factory=list)
    matched: List[str] = field(default_factory=list)
    unverified: List[str] = field(default_factory=list)


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    return slug[:max_len].strip("-") or "x"


def typst_str(text: str) -> str:
    """Render text as a Typst string literal; only backslash and quote need escaping."""
    flat = re.sub(r"\s+", " ", str(text)).strip()
    return '"' + flat.replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_json_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [str(v) for v in data] if isinstance(data, list) else []


def verified_skill_index(profile: MasterProfile) -> Dict[str, str]:
    """Casefolded name -> canonical name for every skill the profile can back up."""
    index: Dict[str, str] = {}
    names = [s.name for s in profile.skills]
    names += [s for exp in profile.experience for s in exp.skills_used]
    names += [t for proj in profile.projects for t in proj.technologies]
    for name in names:
        index.setdefault(name.casefold(), name)
    return index


def split_verified(profile: MasterProfile, job_skills: Sequence[str]) -> Tuple[List[str], List[str]]:
    index = verified_skill_index(profile)
    verified: List[str] = []
    unverified: List[str] = []
    for skill in job_skills:
        canonical = index.get(skill.strip().casefold())
        if canonical:
            if canonical not in verified:
                verified.append(canonical)
        elif skill not in unverified:
            unverified.append(skill)
    return verified, unverified


def relevance(text: str, keywords: Sequence[str]) -> int:
    lowered = text.casefold()
    return sum(
        1 for kw in keywords if re.search(r"(?<!\w)" + re.escape(kw.casefold()) + r"(?!\w)", lowered)
    )


def build_cv(
    profile: MasterProfile, matched_skills: Sequence[str], max_projects: int = MAX_PROJECTS
) -> TailoredCV:
    matched, unverified = split_verified(profile, matched_skills)
    matched_keys = {m.casefold() for m in matched}

    groups: Dict[str, List[str]] = {}
    for skill in profile.skills:
        groups.setdefault(skill.category, []).append(skill.name)
    ordered_groups: List[Tuple[str, List[str]]] = []
    for category, names in groups.items():
        names = sorted(names, key=lambda n: n.casefold() not in matched_keys)
        ordered_groups.append((category, names))
    ordered_groups.sort(key=lambda g: -sum(1 for n in g[1] if n.casefold() in matched_keys))

    experience = []
    for exp in profile.experience:
        bullets = sorted(exp.achievements, key=lambda a: -relevance(a, matched))
        end = exp.end_date or ("Present" if exp.is_current else "")
        experience.append(
            {
                "role": exp.role,
                "company": exp.company,
                "location": exp.location,
                "period": f"{exp.start_date} - {end}".strip(" -"),
                "bullets": bullets,
            }
        )

    def project_score(p: Any) -> int:
        blob = " ".join([p.name, p.description, *p.technologies, *p.impact_highlights])
        return relevance(blob, matched)

    projects = [
        {
            "name": p.name,
            "description": p.description,
            "technologies": p.technologies,
            "highlights": p.impact_highlights,
            "repo_url": p.repo_url or "",
        }
        for p in sorted(profile.projects, key=lambda p: -project_score(p))[:max_projects]
    ]

    education = [
        {"degree": e.degree, "institution": e.institution, "period": e.period, "topics": e.key_topics}
        for e in profile.education
    ]
    ident = profile.identity
    return TailoredCV(
        name=ident.name,
        title=ident.title,
        email=ident.email,
        phone=ident.phone or "",
        location=ident.location,
        linkedin=ident.linkedin or "",
        github=ident.github or "",
        summary=profile.summary,
        skill_groups=ordered_groups,
        experience=experience,
        projects=projects,
        education=education,
        extras=[*profile.certifications, *profile.publications],
        matched=matched,
        unverified=unverified,
    )


def render_cv(cv: TailoredCV, font_pt: float = 10.0, margin_cm: float = 1.8) -> str:
    out = [page_setup(font_pt, margin_cm)]
    out.append(
        "#cv_header(name: {n}, title: {t}, email: {e}, phone: {p}, location: {l}, linkedin: {li}, github: {g})\n".format(
            n=typst_str(cv.name), t=typst_str(cv.title), e=typst_str(cv.email), p=typst_str(cv.phone),
            l=typst_str(cv.location), li=typst_str(cv.linkedin), g=typst_str(cv.github),
        )
    )
    out.append('#cv_section("Professional Summary")')
    out.append(f"#{typst_str(cv.summary)}\n")

    out.append('#cv_section("Technical Skills")')
    for label, names in cv.skill_groups:
        out.append(f"- *{label}:* #{typst_str(', '.join(names))}")
    out.append("")

    if cv.experience:
        out.append('#cv_section("Experience")')
        for exp in cv.experience:
            bullets = ", ".join(typst_str(b) for b in exp["bullets"])
            desc = f"[#list({bullets})]" if bullets else "[]"
            out.append(
                f"#cv_entry(title: {typst_str(exp['role'])}, subtitle: {typst_str(exp['company'])}, "
                f"location: {typst_str(exp['location'])}, date: {typst_str(exp['period'])}, description: {desc})"
            )
        out.append("")

    if cv.projects:
        out.append('#cv_section("Projects")')
        for proj in cv.projects:
            items = [proj["description"], *proj["highlights"]]
            if proj["technologies"]:
                items.append("Technologies: " + ", ".join(proj["technologies"]))
            bullets = ", ".join(typst_str(i) for i in items)
            out.append(
                f"#cv_entry(title: {typst_str(proj['name'])}, subtitle: {typst_str(proj['repo_url'])}, "
                f"description: [#list({bullets})])"
            )
        out.append("")

    if cv.education:
        out.append('#cv_section("Education")')
        for edu in cv.education:
            topics = f"[#list({typst_str('Focus: ' + ', '.join(edu['topics']))})]" if edu["topics"] else "[]"
            out.append(
                f"#cv_entry(title: {typst_str(edu['degree'])}, subtitle: {typst_str(edu['institution'])}, "
                f"date: {typst_str(edu['period'])}, description: {topics})"
            )
        out.append("")
    if cv.extras:
        out.append('#cv_section("Certifications & Publications")')
        out.append(f"#list({', '.join(typst_str(x) for x in cv.extras)})")
    return "\n".join(out) + "\n"


def build_cover_letter(
    profile: MasterProfile,
    company: str,
    job_title: str,
    matched: Sequence[str],
    missing: Sequence[str],
    cv: TailoredCV,
) -> List[str]:
    skills = ", ".join(matched[:5]) if matched else "Python and software engineering"
    paragraphs = [
        f"Dear {company} hiring team,",
        f"I am applying for the {job_title} role at {company}. {profile.summary}",
        f"The strongest overlap with this role is {skills}. "
        + (
            f"In my role as {cv.experience[0]['role']}, a representative result: {cv.experience[0]['bullets'][0].rstrip('.')}."
            if cv.experience and cv.experience[0]["bullets"]
            else ""
        ),
    ]
    if cv.projects:
        top = cv.projects[0]
        paragraphs.append(
            f"Outside of work I built {top['name']}: {top['description'].rstrip('.')}."
        )
    verified_missing = [m for m in missing if m.casefold() not in verified_skill_index(profile)]
    if verified_missing:
        paragraphs.append(
            f"I am keen to deepen my experience with {', '.join(verified_missing[:3])}, which this role calls for."
        )
    paragraphs.append(
        "I would welcome the chance to discuss how I can contribute to your team. Thank you for your time and consideration."
    )
    return [p.strip() for p in paragraphs if p.strip()]


def render_letter(profile: MasterProfile, paragraphs: Sequence[str]) -> str:
    ident = profile.identity
    contact = " | ".join(x for x in [ident.location, ident.email, ident.phone or "", ident.linkedin or ""] if x)
    out = [PAGE_SETUP, "#set par(leading: 0.7em, spacing: 1.1em)"]
    out.append(f"#letter_header(name: {typst_str(ident.name)}, contact: {typst_str(contact)})")
    out.append(f"#{typst_str(date.today().isoformat())}\n")
    for para in paragraphs:
        out.append(f"#{typst_str(para)}\n")
    out.append(f"#{typst_str('Sincerely,')}\n")
    out.append(f"#{typst_str(ident.name)}")
    return "\n".join(out) + "\n"


def compile_typst(source: Path, output: Path) -> None:
    exe = shutil.which("typst")
    if not exe:
        raise RuntimeError("typst is not installed (brew install typst)")
    result = subprocess.run(
        [exe, "compile", "--root", str(source.parent), str(source), str(output)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0 or not output.exists():
        raise RuntimeError(f"typst compile failed for {source.name}: {result.stderr.strip()[:500]}")


def compile_cv_to_fit(
    profile: MasterProfile, matched_skills: Sequence[str], source: Path, output: Path
) -> TailoredCV:
    """Try the layout ladder and keep the first setting with at most MAX_CV_PAGES and no layout problems."""
    import contextlib
    import io

    from tools.verify_layout import parse_pdf
    from tools.verify_layout import report as layout_report
    from tools.verify_pdf import extract_text_layer

    best: Optional[Tuple[int, Tuple[float, float, int]]] = None
    for setting in FIT_LADDER:
        font_pt, margin_cm, max_projects = setting
        cv = build_cv(profile, matched_skills, max_projects=max_projects)
        source.write_text(render_cv(cv, font_pt, margin_cm), encoding="utf-8")
        compile_typst(source, output)
        if extract_text_layer(output)[1] > MAX_CV_PAGES:
            continue
        with contextlib.redirect_stdout(io.StringIO()):
            problems = len(layout_report(output, parse_pdf(output)))
        if best is None or problems < best[0]:
            best = (problems, setting)
        if problems == 0:
            break
    if best is None:
        best = (0, FIT_LADDER[-1])
    font_pt, margin_cm, max_projects = best[1]
    cv = build_cv(profile, matched_skills, max_projects=max_projects)
    source.write_text(render_cv(cv, font_pt, margin_cm), encoding="utf-8")
    compile_typst(source, output)
    return cv


def verify_documents(
    cv_pdf: Path, letter_pdf: Path, profile: MasterProfile
) -> Tuple[bool, bool, List[str]]:
    """Run the ATS text-layer and layout checks. Returns (ats_ok, layout_ok, messages)."""
    from tools.verify_layout import parse_pdf
    from tools.verify_layout import report as layout_report
    from tools.verify_pdf import VerificationError, verify_pdf

    messages: List[str] = []
    ats_ok = True
    required = [profile.identity.name, profile.identity.email]
    try:
        _, _, pages = verify_pdf(cv_pdf, min_chars=200, required_text=required, ascii_dates=True)
        if pages > MAX_CV_PAGES:
            ats_ok = False
            messages.append(f"CV has {pages} pages, maximum is {MAX_CV_PAGES}")
        verify_pdf(letter_pdf, expected_pages=1, min_chars=200, required_text=[profile.identity.name])
    except VerificationError as exc:
        ats_ok = False
        messages.append(f"ATS check failed: {exc}")

    layout_ok = True
    for pdf in (cv_pdf, letter_pdf):
        try:
            problems = layout_report(pdf, parse_pdf(pdf))
        except RuntimeError as exc:
            layout_ok = False
            messages.append(f"Layout check skipped for {pdf.name}: {exc}")
            continue
        if problems:
            layout_ok = False
            messages.extend(f"{pdf.name}: {p}" for p in problems)
    return ats_ok, layout_ok, messages


def tailor_job(
    conn: Any,
    job: Any,
    profile: MasterProfile,
    out_root: Path = APPLICATIONS_DIR,
    dry_run: bool = False,
) -> Dict[str, Any]:
    job_id = int(job["id"])
    company = job["company_name"]
    matched_in = parse_json_list(job["matched_skills"])
    missing = parse_json_list(job["missing_keywords"])

    cv = build_cv(profile, matched_in)
    if cv.unverified:
        logger.warning(f"Skills requested by ranker but not in profile were left out: {cv.unverified}", job_id=job_id)

    folder = out_root / f"{slugify(company)}_{job_id}_{slugify(job['title'])}"
    result: Dict[str, Any] = {
        "job_id": job_id, "company": company, "title": job["title"], "folder": str(folder),
        "status": "DRY_RUN" if dry_run else "TAILORED", "messages": [],
    }
    if dry_run:
        return result

    folder.mkdir(parents=True, exist_ok=True)
    for lib in ("cv_lib.typ",):
        shutil.copy(TEMPLATES_DIR / lib, folder / lib)
    cv_typ, letter_typ = folder / "cv.typ", folder / "cover_letter.typ"
    letter_typ.write_text(
        render_letter(profile, build_cover_letter(profile, company, job["title"], cv.matched, missing, cv)),
        encoding="utf-8",
    )
    cv_pdf, letter_pdf = folder / "cv.pdf", folder / "cover_letter.pdf"
    cv = compile_cv_to_fit(profile, matched_in, cv_typ, cv_pdf)
    compile_typst(letter_typ, letter_pdf)

    ats_ok, layout_ok, messages = verify_documents(cv_pdf, letter_pdf, profile)
    record_tailored_documents(
        conn, job_id, str(cv_pdf), str(letter_pdf), ats_verified=ats_ok, layout_verified=layout_ok,
        notes="; ".join(messages) or None,
    )
    METRICS.increment("tailored_total")
    result["status"] = "READY_TO_APPLY" if ats_ok and layout_ok else "TAILORED"
    result["messages"] = messages
    return result


def run_tailor(
    limit: Optional[int] = None,
    job_id: Optional[int] = None,
    min_score: Optional[float] = None,
    dry_run: bool = False,
    db_path: Optional[Path] = None,
    profile_path: Optional[Path] = None,
    out_root: Path = APPLICATIONS_DIR,
) -> List[Dict[str, Any]]:
    profile = load_profile(profile_path or DEFAULT_PROFILE_FILE)
    if min_score is None:
        min_score = profile.preferences.shortlist_threshold
    report = profile.audit_consistency()
    if not report.is_valid:
        raise ValueError(f"Master profile failed audit: {report.errors}")
    for warning in report.warnings:
        logger.warning(warning)

    init_db(db_path)
    results: List[Dict[str, Any]] = []
    with get_db(db_path) as conn:
        jobs = get_shortlisted_for_tailoring(conn, min_score=min_score, limit=None if job_id else limit)
        if job_id:
            jobs = [j for j in jobs if int(j["id"]) == job_id]
        if not jobs:
            logger.info("No SHORTLISTED jobs awaiting tailoring.")
            return []
        for job in jobs:
            try:
                res = tailor_job(conn, job, profile, out_root=out_root, dry_run=dry_run)
            except Exception as exc:
                logger.error(f"Tailoring failed for job {job['id']}: {exc}")
                res = {"job_id": int(job["id"]), "company": job["company_name"], "title": job["title"],
                       "status": "ERROR", "messages": [str(exc)], "folder": ""}
            logger.info(f"{res['status']}: {res['company']} - {res['title']}", folder=res["folder"])
            results.append(res)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate tailored CV and cover letter for SHORTLISTED jobs")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--job-id", type=int, default=None)
    parser.add_argument("--min-score", type=float, default=None, help="Defaults to the profile shortlist_threshold")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--profile", type=Path, default=None)
    args = parser.parse_args()
    try:
        outcome = run_tailor(
            limit=args.limit, job_id=args.job_id, min_score=args.min_score,
            dry_run=args.dry_run, profile_path=args.profile,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    METRICS.print_summary()
    sys.exit(1 if any(r["status"] == "ERROR" for r in outcome) else 0)

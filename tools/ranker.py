#!/usr/bin/env python3
"""Semantic Job Evaluation Agent (Ranker) with Instructor & Pydantic.

Audits pending jobs in job_search.db against candidate's Quantum SWE profile,
applying strict typed validation, hardware/lab vetoes, and the anti-PhD boilerplate heuristic.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

PROMPTS_PATH = ROOT_DIR / "config" / "prompts.yaml"
PROFILE_PATH = ROOT_DIR / "config" / "master_profile.yaml"

from tools.db import (
    get_db,
    init_db,
    get_pending_jobs,
    record_evaluation,
    STATUS_NEW,
    STATUS_SHORTLISTED,
    STATUS_IGNORED,
)
from tools.logger import get_logger, METRICS
from tools.ranker_schema import QuantumJobEvaluation

logger = get_logger("ranker")

# Load environment if .env exists
try:
    dotenv_path = ROOT_DIR / ".env"
    if dotenv_path.exists():
        with open(dotenv_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
except Exception:
    pass


def load_config() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Load evaluation prompt templates and candidate profile."""
    prompts = {}
    if PROMPTS_PATH.exists():
        with open(PROMPTS_PATH, "r", encoding="utf-8") as f:
            prompts = yaml.safe_load(f).get("ranker", {})

    profile = {}
    if PROFILE_PATH.exists():
        with open(PROFILE_PATH, "r", encoding="utf-8") as f:
            profile = yaml.safe_load(f).get("candidate", {})

    return prompts, profile


HARDWARE_VETO_REASON = "VETO: Experimental hardware/wet-lab role (cryogenics/laser/cleanroom fabrication)."
CITIZENSHIP_VETO_REASON = "VETO: Requires US citizenship or a security clearance."
PHD_VETO_REASON = "VETO: Pure academic theoretical physics research without software focus."
RE_EVALUABLE_STATUSES = {STATUS_NEW, "DISCOVERED", STATUS_SHORTLISTED, STATUS_IGNORED}


def normalize_evaluation(ev: QuantumJobEvaluation) -> QuantumJobEvaluation:
    """Enforce the business rules on an LLM answer so the model cannot contradict them."""
    updates: Dict[str, Any] = {}
    if ev.is_experimental_hardware and not ev.should_veto:
        updates.update(should_veto=True, veto_reason=ev.veto_reason or HARDWARE_VETO_REASON)
    elif (
        ev.should_veto
        and not ev.is_experimental_hardware
        and ev.is_software_role
        and ev.phd_requirement_analysis == "generic_hr_boilerplate"
        and re.search(r"ph\.?d|doctorate", ev.veto_reason or "", re.IGNORECASE)
    ):
        updates.update(should_veto=False, veto_reason=None)
    elif (
        not ev.should_veto
        and ev.phd_requirement_analysis == "strict_scientific_research"
        and not ev.is_software_role
    ):
        updates.update(should_veto=True, veto_reason=ev.veto_reason or PHD_VETO_REASON)
    return ev.model_copy(update=updates) if updates else ev


NON_ENGINEERING_TITLE = re.compile(
    r"customer success|success (?:manager|engineer)|\bsales\b|account (?:executive|manager)|recruit|talent|"
    r"marketing|\bfinance\b|accountant|\blegal\b|counsel|\bpeople\b|human resources|technical support|"
    r"support engineer|field (?:engineer|application)|program manager|product manager|office manager|"
    r"administrat|facilities|technician|business development|\bpartnerships?\b|\bprocurement\b",
    re.IGNORECASE,
)
MANAGEMENT_TITLE = re.compile(r"\bmanager\b|\bdirector\b|\bhead of\b|\bvp\b|vice president|\bchief\b", re.IGNORECASE)
STAFF_TITLE = re.compile(r"\bstaff\b|\bprincipal\b|\bdistinguished\b|\bfellow\b", re.IGNORECASE)
SENIOR_TITLE = re.compile(r"\bsenior\b|\bsr\.?\b|\blead\b", re.IGNORECASE)
CITIZENSHIP_REQUIRED = re.compile(
    r"u\.?s\.? citizen(?:ship)?s?\b(?! and)|security clearance|clearance (?:is )?required|"
    r"(?:secret|ts/sci|top secret) (?:access|clearance)|eligib\w+ for (?:a )?(?:secret|security clearance)",
    re.IGNORECASE,
)
RESEARCH_TITLE = re.compile(r"scientist|researcher|research (?:fellow|associate)|postdoc", re.IGNORECASE)
ENGINEER_TITLE = re.compile(r"engineer|developer|programmer", re.IGNORECASE)
SOFTWARE_TITLE = re.compile(r"software|developer|compiler|\bsdk\b|algorithm|\bcloud\b|backend|full[- ]?stack|\bml\b|machine learning", re.IGNORECASE)
HARDWARE_TRIGGERS = (
    "dilution refrigerator", "cryostat", "cryogenic", "cleanroom", "clean room", "laser optics",
    "optical table", "fpga hardware design", "wafer fabrication", "wire bonding", "microfabrication",
    "microwave cavity", "pcb layout",
)
SOFTWARE_KEYWORDS = (
    "python", "c++", "rust", "typescript", "sdk", "api", "apis", "compiler", "algorithms", "qiskit",
    "pennylane", "cirq", "git", "ci/cd", "testing", "cloud", "backend", "pytorch", "tensorflow",
    "machine learning", "docker", "kubernetes", "angular",
)
QUANTUM_KEYWORDS = (
    "qiskit", "pennylane", "cirq", "quantum", "qir", "vqe", "qaoa", "openqasm", "qubit", "qubits",
    "error correction", "error mitigation", "hamiltonian",
)


SKILL_DISPLAY = {
    "sdk": "SDK", "api": "API", "apis": "APIs", "ci/cd": "CI/CD", "pytorch": "PyTorch",
    "tensorflow": "TensorFlow", "typescript": "TypeScript", "pennylane": "PennyLane", "c++": "C++",
}


def saturate(hits: float, scale: float) -> float:
    """Map a keyword hit count to 0-10 with diminishing returns."""
    return round(10.0 * (1.0 - math.exp(-hits / scale)), 1)


def keyword_in(text: str, keyword: str) -> bool:
    return re.search(r"(?<![a-z0-9+#])" + re.escape(keyword) + r"(?![a-z0-9+#])", text) is not None


def heuristic_quantum_eval(
    title: str, company: str, description: str, location: str, is_remote: bool
) -> QuantumJobEvaluation:
    """Deterministic fallback evaluator used when no LLM API key is configured."""
    desc_lower = description.lower().replace("ph.d", "phd")
    title_lower = title.lower()

    is_hardware = any(hw in desc_lower for hw in HARDWARE_TRIGGERS) and not SOFTWARE_TITLE.search(title)
    matched_skills = [kw for kw in SOFTWARE_KEYWORDS if keyword_in(desc_lower, kw)]
    non_engineering = bool(NON_ENGINEERING_TITLE.search(title))
    management = bool(MANAGEMENT_TITLE.search(title))
    research_role = bool(RESEARCH_TITLE.search(title)) and not ENGINEER_TITLE.search(title)
    is_software = not non_engineering and not research_role and (
        bool(SOFTWARE_TITLE.search(title)) or (len(matched_skills) >= 3 and "engineer" in title_lower)
    )

    phd_in_title = keyword_in(title_lower, "phd")
    phd_mentions = phd_in_title or keyword_in(desc_lower, "phd") or "doctorate" in desc_lower
    if not phd_mentions:
        phd_analysis = "not_required"
    elif phd_in_title or not (is_software and len(matched_skills) >= 3):
        phd_analysis = "strict_scientific_research"
    else:
        phd_analysis = "generic_hr_boilerplate"

    veto_reason = None
    if is_hardware:
        veto_reason = HARDWARE_VETO_REASON
    elif non_engineering:
        veto_reason = "VETO: Not a software engineering role (sales, support, recruiting or operations)."
    elif CITIZENSHIP_REQUIRED.search(description):
        veto_reason = CITIZENSHIP_VETO_REASON
    elif management:
        veto_reason = "VETO: Management role, not an individual-contributor engineering position."
    elif phd_analysis == "strict_scientific_research" and (phd_in_title or not is_software):
        veto_reason = PHD_VETO_REASON
    is_vetoed = veto_reason is not None

    software_score = saturate(len(matched_skills) + (1.5 if SOFTWARE_TITLE.search(title) else 0.0), 4.0)
    quantum_hits = [kw for kw in QUANTUM_KEYWORDS if keyword_in(desc_lower, kw)]
    quantum_score = saturate(len(quantum_hits) + (1.5 if "quantum" in title_lower else 0.0), 3.0)

    if is_vetoed:
        overall = 2.0
    else:
        base = software_score * 0.5 + quantum_score * 0.5
        if phd_analysis == "generic_hr_boilerplate":
            base += 0.5
        if is_remote:
            base += 0.5
        if STAFF_TITLE.search(title):
            base -= 2.0
        elif SENIOR_TITLE.search(title):
            base -= 1.0
        if "intern" in title_lower:
            base -= 2.0
        overall = max(0.0, min(10.0, round(base, 1)))

    missing = [k for k in ("Qiskit", "C++", "PennyLane", "Quantum Error Correction") if k.lower() not in desc_lower]

    return QuantumJobEvaluation(
        overall_score=overall,
        software_fit_score=software_score,
        quantum_fit_score=quantum_score,
        is_software_role=is_software,
        is_experimental_hardware=is_hardware,
        phd_requirement_analysis=phd_analysis,
        should_veto=is_vetoed,
        veto_reason=veto_reason,
        matched_skills=[SKILL_DISPLAY.get(kw, kw.title()) for kw in matched_skills],
        missing_keywords=missing[:4],
        tailoring_recommendations=[
            "Lead with the experience that matches the posting's core stack",
            "Detail hands-on algorithm, simulation or SDK work relevant to the role",
        ],
        reasoning=(
            f"Keyword analysis: {len(matched_skills)} software and {len(quantum_hits)} quantum terms matched. "
            f"PhD requirement classified as '{phd_analysis}'. Veto: {veto_reason or 'none'}."
        ),
    )


def evaluate_with_instructor(
    title: str, company: str, description: str, location: str, is_remote: bool,
    system_prompt: str, user_template: str, profile: Dict[str, Any]
) -> QuantumJobEvaluation:
    """Evaluate posting using Instructor-wrapped LLM client."""
    api_key = os.environ.get("OPENAI_API_KEY")
    model_name = os.environ.get("AI_JOB_SEARCH_MODEL", "gpt-4o-mini")

    if not api_key:
        logger.info("OPENAI_API_KEY not set. Using built-in deterministic Quantum heuristic engine.")
        return heuristic_quantum_eval(title, company, description, location, is_remote)

    try:
        import instructor
        from openai import OpenAI

        client = instructor.from_openai(OpenAI(api_key=api_key))

        prompt_body = user_template.replace("{{ company }}", company)
        prompt_body = prompt_body.replace("{{ title }}", title)
        prompt_body = prompt_body.replace("{{ location }}", location)
        prompt_body = prompt_body.replace("{{ is_remote }}", str(is_remote))
        prompt_body = prompt_body.replace("{{ portal }}", "direct_ats_or_aggregator")
        prompt_body = prompt_body.replace("{{ description }}", description[:4500])

        evaluation: QuantumJobEvaluation = client.chat.completions.create(
            model=model_name,
            response_model=QuantumJobEvaluation,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt_body},
            ],
            temperature=0.1,
        )
        return normalize_evaluation(evaluation)
    except Exception as e:
        logger.warning(f"LLM evaluation failed ({e}). Falling back to heuristic engine.")
        return heuristic_quantum_eval(title, company, description, location, is_remote)


def run_ranker(
    limit: Optional[int] = None,
    specific_job_id: Optional[int] = None,
    dry_run: bool = False,
    db_path: Optional[Path] = None,
    reevaluate: bool = False,
) -> List[Dict[str, Any]]:
    """Evaluate pending jobs in job_search.db and transition states."""
    prompts, profile = load_config()
    system_prompt = prompts.get("system_prompt", "Evaluate job posting for Quantum SWE.")
    user_template = prompts.get("user_prompt_template", "Job: {{ company }} - {{ title }}\n{{ description }}")

    init_db(db_path)
    evaluated = []

    with get_db(db_path) as conn:
        if specific_job_id:
            jobs = conn.execute("SELECT * FROM jobs WHERE id = ?", (specific_job_id,)).fetchall()
        elif reevaluate:
            marks = ",".join("?" for _ in RE_EVALUABLE_STATUSES)
            sql = f"SELECT * FROM jobs WHERE status IN ({marks}) ORDER BY id ASC"
            params: List[Any] = sorted(RE_EVALUABLE_STATUSES)
            if limit:
                sql += " LIMIT ?"
                params.append(int(limit))
            jobs = conn.execute(sql, params).fetchall()
        else:
            jobs = get_pending_jobs(conn, limit=limit)

        if not jobs:
            logger.info("No pending jobs found awaiting evaluation (status 'NEW').")
            return []

        logger.info(f"Starting Ranker evaluation for {len(jobs)} pending jobs...")

        for job in jobs:
            job_id = int(job["id"])
            title = job["title"]
            company = job["company_id"]
            comp_row = conn.execute("SELECT name FROM companies WHERE id = ?", (company,)).fetchone()
            company_name = comp_row["name"] if comp_row else "Unknown"

            location = job["location"] or ""
            is_remote = bool(job["is_remote"])
            desc = job["cleaned_description"] or job["raw_description"] or ""

            eval_res = evaluate_with_instructor(
                title=title,
                company=company_name,
                description=desc,
                location=location,
                is_remote=is_remote,
                system_prompt=system_prompt,
                user_template=user_template,
                profile=profile,
            )

            METRICS.increment("evaluated_total")

            # Funnel metrics recording
            if eval_res.should_veto:
                if eval_res.is_experimental_hardware:
                    METRICS.increment("vetoed_hardware")
                elif eval_res.phd_requirement_analysis == "strict_scientific_research":
                    METRICS.increment("vetoed_phd_strict")
                elif eval_res.veto_reason == CITIZENSHIP_VETO_REASON:
                    METRICS.increment("vetoed_location")
                else:
                    METRICS.increment("vetoed_role")
                METRICS.increment("ignored_total")
            elif eval_res.overall_score >= 7.5:
                METRICS.increment("shortlisted_total")
                if eval_res.phd_requirement_analysis == "generic_hr_boilerplate":
                    METRICS.increment("phd_boilerplate_tolerated")
            else:
                METRICS.increment("ignored_total")

            logger.info(
                f"[{eval_res.overall_score:>4.1f}/10] {company_name} - {title}",
                veto=eval_res.should_veto,
                phd_type=eval_res.phd_requirement_analysis,
                matched=eval_res.matched_skills[:3],
            )

            if not dry_run:
                record_evaluation(
                    conn=conn,
                    job_id=job_id,
                    overall_score=eval_res.overall_score,
                    software_fit_score=eval_res.software_fit_score,
                    quantum_fit_score=eval_res.quantum_fit_score,
                    phd_requirement_analysis=eval_res.phd_requirement_analysis,
                    is_experimental_hardware=eval_res.is_experimental_hardware,
                    veto_triggered=eval_res.should_veto,
                    veto_reason=eval_res.veto_reason,
                    matched_skills=eval_res.matched_skills,
                    missing_keywords=eval_res.missing_keywords,
                    tailoring_recommendations=eval_res.tailoring_recommendations,
                    raw_eval=eval_res.model_dump(),
                    auto_update_status=job["status"] in RE_EVALUABLE_STATUSES,
                )

            evaluated.append({
                "job_id": job_id,
                "company": company_name,
                "title": title,
                "score": eval_res.overall_score,
                "status": STATUS_SHORTLISTED if (eval_res.overall_score >= 7.5 and not eval_res.should_veto) else STATUS_IGNORED,
                "veto_reason": eval_res.veto_reason,
                "matched": eval_res.matched_skills,
            })

    # Display shortlisted opportunities
    shortlisted = [e for e in evaluated if e["status"] == STATUS_SHORTLISTED]
    if shortlisted:
        print("\n" + "★" * 65)
        print("     NEWLY SHORTLISTED OPPORTUNITIES FOR TAILORING (Score >= 7.5)")
        print("★" * 65)
        for s in shortlisted:
            print(f"  • [{s['score']}/10] {s['company']} — {s['title']}")
            print(f"    Matched: {', '.join(s['matched'][:5])}")
        print("★" * 65 + "\n")

    return evaluated


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate pending jobs using Pydantic + Instructor Ranker")
    parser.add_argument("--limit", type=int, default=None, help="Max pending jobs to evaluate")
    parser.add_argument("--job-id", type=int, default=None, help="Evaluate a specific job ID")
    parser.add_argument("--dry-run", action="store_true", help="Run evaluation without updating DB status")
    parser.add_argument("--reevaluate", action="store_true", help="Also re-score NEW, SHORTLISTED and IGNORED jobs (never tailored or applied ones)")
    args = parser.parse_args()

    run_ranker(limit=args.limit, specific_job_id=args.job_id, dry_run=args.dry_run, reevaluate=args.reevaluate)
    METRICS.print_summary()

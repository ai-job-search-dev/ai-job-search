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

from tools.db import (
    get_db,
    init_db,
    backup_db,
    get_pending_jobs,
    record_evaluation,
    STATUS_NEW,
    STATUS_SHORTLISTED,
    STATUS_IGNORED,
)
from tools.llm import Budget, BudgetExceeded, LLMGateway, llm_configured, preferences_hash
from tools.logger import METRICS, get_logger, safe_print
from tools.preferences import (
    RULE_TEXT,
    assess_work_authorization,
    describe_preferences,
    detect_employment_types,
    is_unpaid,
    load_candidate_context,
    load_preferences,
    reason_matches,
)
from tools.profile_schema import CareerPreferences
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


def load_config(profile_path: Optional[Path] = None) -> Tuple[Dict[str, Any], CareerPreferences, str]:
    """Prompt templates, the user's screening preferences and a short candidate description."""
    prompts: Dict[str, Any] = {}
    if PROMPTS_PATH.exists():
        with open(PROMPTS_PATH, "r", encoding="utf-8") as f:
            prompts = (yaml.safe_load(f) or {}).get("ranker", {})
    return prompts, load_preferences(profile_path), load_candidate_context(profile_path)


HARDWARE_VETO_REASON = "VETO: Experimental hardware/wet-lab role (cryogenics/laser/cleanroom fabrication)."
CITIZENSHIP_VETO_REASON = "VETO: Requires US citizenship or a security clearance."
PHD_VETO_REASON = "VETO: Pure academic theoretical physics research without software focus."
NON_ENGINEERING_VETO_REASON = "VETO: Not a software engineering role (sales, support, recruiting or operations)."
MANAGEMENT_VETO_REASON = "VETO: Management role, not an individual-contributor engineering position."
UNPAID_VETO_REASON = "VETO: Unpaid or volunteer position."
NON_PHD_VETO_CAUSE = re.compile(r"on-?site|in-?person|relocat|location|hybrid|office|work[- ]?permit", re.IGNORECASE)


def _phd_is_sole_veto_cause(reason: Optional[str]) -> bool:
    """True when a veto reason names no cause other than the PhD boilerplate, so lifting it cannot hide another veto."""
    if NON_PHD_VETO_CAUSE.search(reason or ""):
        return False
    return not any(reason_matches(name, reason) for name in RULE_TEXT if name != "research_phd")


RE_EVALUABLE_STATUSES = {STATUS_NEW, "DISCOVERED", STATUS_SHORTLISTED, STATUS_IGNORED}


def deterministic_screen(
    prefs: CareerPreferences, title: str, location: str, description: str
) -> Tuple[Optional[str], float, List[str]]:
    """Rules that need no judgement, so the LLM cannot override them: citizenship, unpaid, type, authorization.

    Returns (veto_reason, penalty_points, notes).
    """
    veto: Optional[str] = None
    penalty = 0.0
    notes: List[str] = []
    employment = detect_employment_types(title, description)

    holds_us = "US" in {c.upper() for c in prefs.work_authorization.citizenships}
    citizenship = citizenship_requirement(description) if not holds_us else None
    if citizenship:
        setting = prefs.rules.citizenship_clearance
        if setting.mode == "veto" and citizenship == "hard":
            veto = veto or CITIZENSHIP_VETO_REASON
        elif setting.mode in ("veto", "penalty"):
            penalty += setting.penalty
            notes.append(
                "citizenship or clearance requirement" if citizenship == "hard"
                else "export-control or preferred clearance wording"
            )

    if prefs.employment.paid_only and is_unpaid(title, description):
        setting = prefs.rules.unpaid
        if setting.mode == "veto":
            veto = veto or UNPAID_VETO_REASON
        elif setting.mode == "penalty":
            penalty += setting.penalty
            notes.append("unpaid position")

    if not employment & set(prefs.employment.accepted_types):
        veto = veto or f"VETO: Employment type {sorted(employment)} is not accepted."
    else:
        kind_penalty = max((prefs.employment.penalties.get(kind, 0.0) for kind in employment), default=0.0)
        if kind_penalty:
            penalty += kind_penalty
            notes.append(f"employment type {sorted(employment)}")

    auth = assess_work_authorization(prefs, location, description)
    if auth.penalty > 0:
        mode = prefs.rules.work_authorization.mode
        if mode == "veto":
            veto = veto or f"VETO: Work authorization ({auth.note})."
        elif mode == "penalty":
            penalty += auth.penalty
            notes.append(f"work authorization: {auth.note}")
    return veto, penalty, notes


def normalize_evaluation(ev: QuantumJobEvaluation, prefs: Optional[CareerPreferences] = None) -> QuantumJobEvaluation:
    """Make an LLM answer respect the user's rules: enforce the ones set to veto, lift vetoes for rules switched off."""
    prefs = prefs or CareerPreferences()
    rules = prefs.rules
    vetoed, reason, score = ev.should_veto, ev.veto_reason, ev.overall_score

    if ev.is_experimental_hardware and not vetoed and rules.hardware_lab.mode == "veto":
        vetoed, reason = True, reason or HARDWARE_VETO_REASON
    elif (
        vetoed
        and not ev.is_experimental_hardware
        and ev.is_software_role
        and ev.phd_requirement_analysis == "generic_hr_boilerplate"
        and re.search(r"ph\.?d|doctorate", reason or "", re.IGNORECASE)
        and _phd_is_sole_veto_cause(reason)
    ):
        vetoed, reason = False, None
    elif (
        not vetoed
        and ev.phd_requirement_analysis == "strict_scientific_research"
        and not ev.is_software_role
        and rules.research_phd.mode == "veto"
    ):
        vetoed, reason = True, reason or PHD_VETO_REASON

    if vetoed:
        matching = [name for name in RULE_TEXT if reason_matches(name, reason)]
        if ev.is_experimental_hardware:
            matching = [n for n in matching if n == "hardware_lab"] or ["hardware_lab"]
        if matching and all(getattr(rules, n).mode != "veto" for n in matching):
            score -= max((getattr(rules, n).penalty for n in matching if getattr(rules, n).mode == "penalty"), default=0.0)
            vetoed, reason = False, None

    updates: Dict[str, Any] = {}
    if (vetoed, reason) != (ev.should_veto, ev.veto_reason):
        updates.update(should_veto=vetoed, veto_reason=reason)
    if score != ev.overall_score:
        updates["overall_score"] = max(0.0, round(score, 1))
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
CLEARANCE_NEGATED_BEFORE = re.compile(r"(?:\bno|\bnot|\bwithout|n't|\bnever)\b[^.;]{0,30}$", re.IGNORECASE)
CLEARANCE_NEGATED_AFTER = re.compile(r"^[^.;]{0,25}\b(?:not (?:required|needed|necessary)|is not a requirement)", re.IGNORECASE)
CLEARANCE_OPTIONAL = re.compile(r"preferred|a plus|desirable|nice to have|bonus|an advantage|is helpful|ideal", re.IGNORECASE)
EXPORT_CONTROL_BOILERPLATE = re.compile(r"license exception|any necessary license|export licen[sc]e|export[- ]control", re.IGNORECASE)


def citizenship_requirement(text: str) -> Optional[str]:
    """'hard' for a stated citizenship or clearance requirement, 'soft' for export-control boilerplate or a preference, else None."""
    level: Optional[str] = None
    for match in CITIZENSHIP_REQUIRED.finditer(text or ""):
        before = text[max(0, match.start() - 60):match.start()]
        after = text[match.end():match.end() + 60]
        if CLEARANCE_NEGATED_BEFORE.search(before) or CLEARANCE_NEGATED_AFTER.search(after):
            continue
        window = text[max(0, match.start() - 250):match.end() + 350]
        if CLEARANCE_OPTIONAL.search(before[-40:] + match.group(0) + after[:40]) or EXPORT_CONTROL_BOILERPLATE.search(window):
            level = level or "soft"
        else:
            return "hard"
    return level


MIN_DESCRIPTION_CHARS = 100
MAX_DESCRIPTION_CHARS = 4500  # characters of a posting sent to the model
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
    title: str, company: str, description: str, location: str, is_remote: bool,
    prefs: Optional[CareerPreferences] = None,
) -> QuantumJobEvaluation:
    """Deterministic fallback evaluator used when no LLM API key is configured."""
    prefs = prefs or CareerPreferences()
    desc_lower = description.lower().replace("ph.d", "phd")
    title_lower = title.lower()

    is_hardware = any(hw in desc_lower for hw in HARDWARE_TRIGGERS) and not SOFTWARE_TITLE.search(title)
    skill_terms = [k.lower() for k in (prefs.scoring.skill_keywords or SOFTWARE_KEYWORDS)]
    domain_terms = [k.lower() for k in (prefs.scoring.domain_keywords or QUANTUM_KEYWORDS)]
    matched_skills = [kw for kw in skill_terms if keyword_in(desc_lower, kw)]
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

    veto_reason: Optional[str] = None
    penalties: List[float] = []
    notes: List[str] = []

    def apply(rule: str, triggered: bool, reason: str) -> None:
        nonlocal veto_reason
        if not triggered:
            return
        setting = getattr(prefs.rules, rule)
        if setting.mode == "veto":
            veto_reason = veto_reason or reason
        elif setting.mode == "penalty":
            penalties.append(setting.penalty)
            notes.append(rule)

    apply("hardware_lab", is_hardware, HARDWARE_VETO_REASON)
    apply("non_engineering", non_engineering, NON_ENGINEERING_VETO_REASON)
    screen_veto, screen_penalty, screen_notes = deterministic_screen(prefs, title, location, description)
    veto_reason = veto_reason or screen_veto
    penalties.append(screen_penalty)
    notes.extend(screen_notes)
    apply("management", management, MANAGEMENT_VETO_REASON)
    apply("research_phd", phd_analysis == "strict_scientific_research" and (phd_in_title or not is_software), PHD_VETO_REASON)
    is_vetoed = veto_reason is not None

    software_score = saturate(len(matched_skills) + (1.5 if SOFTWARE_TITLE.search(title) else 0.0), 4.0)
    quantum_hits = [kw for kw in domain_terms if keyword_in(desc_lower, kw)]
    domain_in_title = any(keyword_in(title_lower, kw) for kw in domain_terms)
    quantum_score = saturate(len(quantum_hits) + (1.5 if domain_in_title else 0.0), 3.0)

    if is_vetoed:
        overall = 2.0
    else:
        weight = prefs.scoring.domain_weight
        base = software_score * (1.0 - weight) + quantum_score * weight
        if phd_analysis == "generic_hr_boilerplate":
            base += 0.5
        if is_remote:
            base += prefs.scoring.remote_bonus
        if STAFF_TITLE.search(title):
            base -= prefs.seniority.staff_penalty
        elif SENIOR_TITLE.search(title):
            base -= prefs.seniority.senior_penalty
        base -= sum(penalties)
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
            + (f" Penalties: {', '.join(notes)}." if notes and not is_vetoed else "")
        ),
    )


def evaluate_with_instructor(
    title: str, company: str, description: str, location: str, is_remote: bool,
    system_prompt: str, user_template: str, prefs: Optional[CareerPreferences] = None,
    candidate: str = "", use_llm: bool = True, gateway: Optional[LLMGateway] = None,
    job_id: Optional[int] = None, info: Optional[Dict[str, Any]] = None,
) -> QuantumJobEvaluation:
    """Score a posting with the LLM through `gateway`, or with the keyword heuristic when that is not possible.

    `info`, when given, is filled with how the answer was produced: source, model and cache_hit.
    """
    prefs = prefs or CareerPreferences()

    def heuristic(reason: str) -> QuantumJobEvaluation:
        if info is not None:
            info.update(source="heuristic", model="", cache_hit=False, reason=reason)
        return heuristic_quantum_eval(title, company, description, location, is_remote, prefs)

    if not use_llm:
        return heuristic("quarantined")
    if gateway is None:
        return heuristic("no_llm_configured")

    system = system_prompt.replace("{{ candidate }}", candidate).replace("{{ preferences }}", describe_preferences(prefs))
    body = user_template.replace("{{ company }}", company)
    body = body.replace("{{ title }}", title).replace("{{ location }}", location)
    body = body.replace("{{ is_remote }}", str(is_remote)).replace("{{ portal }}", "direct_ats_or_aggregator")
    body = body.replace("{{ description }}", description[:MAX_DESCRIPTION_CHARS].replace('"' * 3, "'" * 3))
    messages = [{"role": "system", "content": system}, {"role": "user", "content": body}]
    try:
        evaluation, call = gateway.evaluate(messages, QuantumJobEvaluation, job_id=job_id)
    except BudgetExceeded as exc:
        if "budget" not in gateway.summary.notes:
            logger.warning(f"LLM budget reached ({exc}). The rest of this run uses the keyword heuristic.")
            gateway.summary.notes.append("budget")
        return heuristic("budget")
    except Exception as exc:
        gateway.summary.errors += 1
        logger.warning(f"LLM evaluation failed ({exc}). Falling back to heuristic engine.")
        return heuristic("llm_error")

    evaluation = normalize_evaluation(evaluation, prefs)
    veto, penalty, _ = deterministic_screen(prefs, title, location, description)
    updates: Dict[str, Any] = {}
    if veto and not evaluation.should_veto:
        updates.update(should_veto=True, veto_reason=veto)
    if penalty:
        updates["overall_score"] = max(0.0, round(evaluation.overall_score - penalty, 1))
    if info is not None:
        info.update(source="llm", model=call.model, cache_hit=call.cache_hit)
    return evaluation.model_copy(update=updates) if updates else evaluation


def veto_metric(ev: QuantumJobEvaluation) -> str:
    """Funnel counter for a vetoed evaluation, chosen from the veto reason."""
    reason = ev.veto_reason or ""
    if reason == HARDWARE_VETO_REASON or (ev.is_experimental_hardware and not reason):
        return "vetoed_hardware"
    if reason == PHD_VETO_REASON:
        return "vetoed_phd_strict"
    if reason == CITIZENSHIP_VETO_REASON or "work authorization" in reason.lower():
        return "vetoed_location"
    return "vetoed_role"


def run_ranker(
    limit: Optional[int] = None,
    specific_job_id: Optional[int] = None,
    dry_run: bool = False,
    db_path: Optional[Path] = None,
    reevaluate: bool = False,
    profile_path: Optional[Path] = None,
    prefs: Optional[CareerPreferences] = None,
    llm_client: Any = None,
    max_llm_calls: Optional[int] = None,
    max_cost_usd: Optional[float] = None,
    use_cache: bool = True,
    stale_only: bool = False,
    usage_out: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Evaluate pending jobs in job_search.db and transition states."""
    prompts, loaded_prefs, candidate = load_config(profile_path)
    prefs = prefs or loaded_prefs
    threshold = prefs.shortlist_threshold
    phash = preferences_hash(prefs)
    system_prompt = prompts.get("system_prompt", "Evaluate job posting for Quantum SWE.")
    user_template = prompts.get("user_prompt_template", "Job: {{ company }} - {{ title }}\n{{ description }}")

    init_db(db_path)
    if reevaluate and not dry_run:
        backup_db(db_path, min_interval_minutes=10)
    evaluated = []
    with get_db(db_path) as conn:
        gateway: Optional[LLMGateway] = None
        if llm_client is not None or llm_configured():
            gateway = LLMGateway(conn, client=llm_client, budget=Budget(max_llm_calls, max_cost_usd), use_cache=use_cache, prefs_hash=phash)
        if specific_job_id:
            jobs = conn.execute("SELECT * FROM jobs WHERE id = ?", (specific_job_id,)).fetchall()
        elif stale_only:
            marks = ",".join("?" for _ in RE_EVALUABLE_STATUSES)
            sql = (
                "SELECT j.* FROM jobs j LEFT JOIN evaluations e ON e.id = (SELECT MAX(id) FROM evaluations WHERE job_id = j.id) "
                f"WHERE j.status IN ({marks}) AND (e.id IS NULL OR e.prefs_hash IS NULL OR e.prefs_hash != ?) "
                f"AND LENGTH(TRIM(COALESCE(NULLIF(j.cleaned_description, ''), j.raw_description, ''))) >= {MIN_DESCRIPTION_CHARS} ORDER BY j.id ASC"
            )
            params = [*sorted(RE_EVALUABLE_STATUSES), phash]
            if limit:
                sql += " LIMIT ?"
                params.append(int(limit))
            jobs = conn.execute(sql, params).fetchall()
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
            if len(desc.strip()) < MIN_DESCRIPTION_CHARS and not specific_job_id:
                logger.warning(f"No usable description, left as NEW: {company_name} - {title}")
                METRICS.increment("skipped_no_description")
                continue

            info: Dict[str, Any] = {}
            eval_res = evaluate_with_instructor(
                title=title,
                company=company_name,
                description=desc,
                location=location,
                is_remote=is_remote,
                system_prompt=system_prompt,
                user_template=user_template,
                prefs=prefs,
                candidate=candidate,
                use_llm=not job["guard_flagged"],
                gateway=gateway,
                job_id=job_id,
                info=info,
            )

            METRICS.increment("evaluated_total")

            # Funnel metrics recording
            if eval_res.should_veto:
                METRICS.increment(veto_metric(eval_res))
                METRICS.increment("ignored_total")
            elif eval_res.overall_score >= threshold:
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
                    threshold=threshold,
                    eval_source=info.get("source", "heuristic"),
                    eval_model=info.get("model") or None,
                    prefs_hash=phash,
                )

            evaluated.append({
                "job_id": job_id,
                "company": company_name,
                "title": title,
                "score": eval_res.overall_score,
                "status": STATUS_SHORTLISTED if (eval_res.overall_score >= threshold and not eval_res.should_veto) else STATUS_IGNORED,
                "veto_reason": eval_res.veto_reason,
                "matched": eval_res.matched_skills,
            })

    if gateway is not None:
        usage = gateway.summary
        METRICS.increment("llm_calls", usage.calls)
        METRICS.increment("llm_cache_hits", usage.cache_hits)
        METRICS.increment("llm_prompt_tokens", usage.prompt_tokens)
        METRICS.increment("llm_completion_tokens", usage.completion_tokens)
        METRICS.increment("llm_cost_microusd", int(usage.cost_usd * 1_000_000))
        METRICS.increment("llm_errors", usage.errors)
        if usage_out is not None:
            usage_out.update(usage.to_dict())
        logger.info("LLM usage", **usage.to_dict())
    shortlisted = [e for e in evaluated if e["status"] == STATUS_SHORTLISTED]
    if shortlisted:
        rule = "*" * 65
        lines = ["", rule, f"     NEWLY SHORTLISTED OPPORTUNITIES FOR TAILORING (Score >= {threshold})", rule]
        for item in shortlisted:
            lines.append(f"  - [{item['score']}/10] {item['company']} - {item['title']}")
            lines.append(f"    Matched: {', '.join(item['matched'][:5])}")
        lines.append(rule)
        safe_print("\n".join(lines))

    return evaluated


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate pending jobs using Pydantic + Instructor Ranker")
    parser.add_argument("--limit", type=int, default=None, help="Max pending jobs to evaluate")
    parser.add_argument("--job-id", type=int, default=None, help="Evaluate a specific job ID")
    parser.add_argument("--dry-run", action="store_true", help="Run evaluation without updating DB status")
    parser.add_argument("--reevaluate", action="store_true", help="Also re-score NEW, SHORTLISTED and IGNORED jobs (never tailored or applied ones)")
    parser.add_argument("--stale-only", action="store_true", help="Re-score only jobs whose evaluation predates the current preferences")
    parser.add_argument("--max-llm-calls", type=int, default=None, help="Stop calling the LLM after this many paid calls")
    parser.add_argument("--max-cost", type=float, default=None, help="Stop calling the LLM once this many USD are spent")
    parser.add_argument("--no-cache", action="store_true", help="Ignore cached LLM answers")
    args = parser.parse_args()

    run_ranker(
        limit=args.limit, specific_job_id=args.job_id, dry_run=args.dry_run, reevaluate=args.reevaluate,
        stale_only=args.stale_only, max_llm_calls=args.max_llm_calls, max_cost_usd=args.max_cost, use_cache=not args.no_cache,
    )
    METRICS.print_summary()

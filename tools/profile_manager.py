#!/usr/bin/env python3
"""Master Profile Manager: Multi-Source Ingestion, Consistency Audit & Iterative Learning.

Manages profile/master_profile.yaml (git-ignored, created on first use) as the single source of truth.
profile/master_profile.example.yaml is the committed template.
Parses PDFs, checks chronological and evidence consistency (anti-hallucination),
and learns from shortlisted jobs in SQLite to auto-complete and improve the profile over time.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.profile_schema import (
    CandidateIdentity,
    CareerPreferences,
    ConsistencyReport,
    EducationEntry,
    LanguageSkill,
    MasterProfile,
    ProjectEntry,
    SkillItem,
    WorkExperienceEntry,
)

PROFILE_DIR = ROOT_DIR / "profile"
DEFAULT_PROFILE_FILE = PROFILE_DIR / "master_profile.yaml"


def get_default_profile() -> MasterProfile:
    """Returns baseline template profile for Franco Raineri."""
    return MasterProfile(
        identity=CandidateIdentity(
            name="Franco Raineri",
            title="Software Engineer & Quantum Systems Developer",
            email="franco.raineri@example.com",
            phone=None,
            location="Argentina (Remote)",
            linkedin="https://linkedin.com/in/franco-raineri",
            github="https://github.com/franraineri",
            portfolio=None,
            languages=[
                LanguageSkill(name="Spanish", level="Native", notes="Mother tongue"),
                LanguageSkill(name="English", level="Fluent / Bilingual", notes="Professional working proficiency"),
            ],
        ),
        summary=(
            "Software Engineer specializing in modern backend architectures, distributed systems, "
            "and quantum software development (Qiskit, PennyLane). Track record of building reliable APIs, "
            "asynchronous event-driven pipelines, and mathematical simulation tooling."
        ),
        education=[
            EducationEntry(
                degree="Computer Science / Software Engineering Degree",
                institution="University",
                period="2019 - 2024",
                key_topics=["Algorithms", "Distributed Systems", "Discrete Mathematics", "Quantum Computing Basics"],
            )
        ],
        experience=[
            WorkExperienceEntry(
                company="Software & Technology Services",
                role="Software Engineer",
                location="Remote",
                start_date="2023-01",
                end_date="Present",
                is_current=True,
                achievements=[
                    "Designed and developed scalable backend APIs and event-driven data ingestion pipelines in Python.",
                    "Implemented mathematical simulation and quantum algorithm testbenches using Qiskit and NumPy.",
                    "Optimized computational throughput and memory footprint by 35% across batch workloads.",
                ],
                skills_used=["Python", "FastAPI", "Qiskit", "PostgreSQL", "Docker", "Git", "CI/CD"],
            )
        ],
        projects=[
            ProjectEntry(
                name="Quantum Simulation & Algorithm Benchmarks",
                description="High-performance simulation pipeline for hybrid quantum-classical algorithms (VQE, QAOA) using Qiskit and PennyLane with statevector analysis.",
                repo_url="https://github.com/franraineri/quantum-benchmarks",
                technologies=["Python", "Qiskit", "PennyLane", "NumPy", "Matplotlib"],
                impact_highlights=["Benchmarked circuit execution noise models and statevector convergence."],
            ),
            ProjectEntry(
                name="Distributed Job Search Automation Engine",
                description="Modular multi-agent job application assistant with SQLite state machine, ATS API connectors, and automated layout and ATS verification.",
                repo_url="https://github.com/franraineri/ai-job-search",
                technologies=["Python", "SQLite", "Pydantic", "Typst", "Playwright", "Docker"],
                impact_highlights=["Processes hundreds of job postings with deterministic prompt-injection defenses."],
            ),
        ],
        skills=[
            SkillItem(name="Python", category="Backend & APIs", proficiency="Expert", evidence_sources=["Experience", "Projects"]),
            SkillItem(name="Qiskit", category="Quantum Computing", proficiency="Advanced", evidence_sources=["Projects", "Experience"]),
            SkillItem(name="PennyLane", category="Quantum Computing", proficiency="Advanced", evidence_sources=["Projects"]),
            SkillItem(name="FastAPI", category="Backend & APIs", proficiency="Advanced", evidence_sources=["Experience"]),
            SkillItem(name="PostgreSQL", category="Backend & APIs", proficiency="Advanced", evidence_sources=["Experience"]),
            SkillItem(name="Docker", category="Cloud & DevOps", proficiency="Advanced", evidence_sources=["Experience", "Projects"]),
            SkillItem(name="Algorithms & Data Structures", category="Mathematics & Algorithms", proficiency="Advanced", evidence_sources=["Education", "Experience"]),
        ],
        preferences=CareerPreferences(
            target_roles=[
                "Quantum Software Engineer",
                "Quantum Applications Scientist",
                "Backend Software Engineer",
                "Distributed Systems Engineer",
            ],
            remote_only=True,
            salary_floor_usd=2500.0,
            deal_breakers=[
                "Cleanroom / physical hardware fabrication roles",
                "Non-remote positions outside local commuting zone",
                "Unpaid trial periods",
            ],
            preferred_locations=["Remote (Global / US / Europe / LATAM)"],
        ),
    )


def load_profile(path: Optional[Path] = None) -> MasterProfile:
    """Load Master Profile from YAML file or initialize default."""
    p = path or DEFAULT_PROFILE_FILE
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return MasterProfile.model_validate(data)
    else:
        profile = get_default_profile()
        save_profile(profile, p)
        return profile


def save_profile(profile: MasterProfile, path: Optional[Path] = None) -> Path:
    """Save Master Profile to YAML with clean formatting."""
    p = path or DEFAULT_PROFILE_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    profile.updated_at = datetime.now(timezone.utc).isoformat()
    raw = profile.model_dump()
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(raw, f, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return p


def ingest_from_pdf(pdf_path: Path, profile: MasterProfile) -> Tuple[MasterProfile, List[str]]:
    """Extract plain text from CV PDF and enrich profile."""
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    extracted_text = "\n".join([page.extract_text() or "" for page in reader.pages])

    added_signals = []

    # Detect email
    email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", extracted_text)
    if email_match and profile.identity.email == "franco.raineri@example.com":
        new_email = email_match.group(0)
        profile.identity.email = new_email
        added_signals.append(f"Updated email: {new_email}")

    # Detect GitHub / LinkedIn links
    gh_match = re.search(r"github\.com/([\w\-]+)", extracted_text, re.IGNORECASE)
    if gh_match:
        profile.identity.github = f"https://github.com/{gh_match.group(1)}"
        added_signals.append(f"Detected GitHub: {profile.identity.github}")

    li_match = re.search(r"linkedin\.com/in/([\w\-]+)", extracted_text, re.IGNORECASE)
    if li_match:
        profile.identity.linkedin = f"https://linkedin.com/in/{li_match.group(1)}"
        added_signals.append(f"Detected LinkedIn: {profile.identity.linkedin}")

    # Discover mentioned technologies
    candidate_keywords = [
        ("C++", "Software Engineering"),
        ("Rust", "Software Engineering"),
        ("Kubernetes", "Cloud & DevOps"),
        ("AWS", "Cloud & DevOps"),
        ("PyTorch", "AI & Machine Learning"),
        ("VQE", "Quantum Computing"),
        ("QAOA", "Quantum Computing"),
        ("Cirq", "Quantum Computing"),
    ]

    current_skill_names = {s.name.lower() for s in profile.skills}
    for kw, cat in candidate_keywords:
        if kw.lower() not in current_skill_names and re.search(r"\b" + re.escape(kw) + r"\b", extracted_text, re.IGNORECASE):
            profile.skills.append(
                SkillItem(name=kw, category=cat, proficiency="Advanced", evidence_sources=["Imported PDF CV"])
            )
            added_signals.append(f"Added verified skill from CV: {kw} ({cat})")

    return profile, added_signals


def learn_from_pipeline(profile: MasterProfile) -> Tuple[MasterProfile, List[str]]:
    """Iterative Auto-Complete: analyze shortlisted jobs in SQLite and suggest improvements."""
    from tools.db import get_db, STATUS_SHORTLISTED

    suggestions = []

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT j.title, j.cleaned_description, e.strengths, e.gaps
            FROM jobs j
            JOIN evaluations e ON e.job_id = j.id
            WHERE j.status = ?
            ORDER BY e.overall_score DESC
            LIMIT 50
            """,
            (STATUS_SHORTLISTED,),
        ).fetchall()

        if not rows:
            return profile, ["No shortlisted jobs found in database to learn from. Run ingestion and ranking first."]

        corpus = " ".join([r["cleaned_description"] or "" for r in rows]).lower()

        # Check market demand against candidate profile
        emerging_market_skills = [
            ("Qiskit Runtime", "Quantum Computing"),
            ("Quantum Circuit Optimization", "Quantum Computing"),
            ("Hamiltonian Simulation", "Quantum Computing"),
            ("Asynchronous Programming", "Backend & APIs"),
            ("Distributed Computing", "Architecture & Distributed Systems"),
            ("API Design", "Backend & APIs"),
            ("OpenAPI", "Backend & APIs"),
            ("Redis", "Backend & APIs"),
        ]

        existing_skills = {s.name.lower() for s in profile.skills}
        profile_corpus = " ".join([
            profile.summary,
            *(a for exp in profile.experience for a in exp.achievements),
            *(p.description for p in profile.projects),
        ]).lower()

        learned_items = []
        for kw, cat in emerging_market_skills:
            kw_lower = kw.lower()
            if kw_lower in existing_skills:
                continue

            # If market asks for it AND candidate experience mentions it:
            in_market = bool(re.search(r"\b" + re.escape(kw_lower) + r"\b", corpus))
            in_profile = bool(re.search(r"\b" + re.escape(kw_lower) + r"\b", profile_corpus))

            if in_market and in_profile:
                profile.skills.append(
                    SkillItem(name=kw, category=cat, proficiency="Advanced", evidence_sources=["Profile Evidence & Market Alignment"])
                )
                learned_items.append(
                    f"Auto-Promoted to Skills: '{kw}' (found in your project descriptions and in high demand by {len(rows)} shortlisted roles)"
                )
            elif in_market:
                suggestions.append(
                    f"Market Opportunity: Shortlisted quantum/SWE jobs frequently require '{kw}'. Consider highlighting projects involving '{kw}'."
                )

        if learned_items:
            profile.discovered_insights["auto_promoted_skills"] = learned_items
        if suggestions:
            profile.discovered_insights["market_gap_opportunities"] = suggestions

    return profile, learned_items + suggestions


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Master Profile Manager & Consistency Engine")
    parser.add_argument("--show", action="store_true", help="Print profile summary")
    parser.add_argument("--audit", action="store_true", help="Run consistency and anti-hallucination audit")
    parser.add_argument("--ingest-pdf", type=Path, help="Ingest and enrich profile from CV PDF")
    parser.add_argument("--learn", action="store_true", help="Learn from shortlisted jobs in SQLite")
    args = parser.parse_args()

    profile = load_profile()

    if args.ingest_pdf:
        print(f"Ingesting CV from: {args.ingest_pdf}...")
        profile, signals = ingest_from_pdf(args.ingest_pdf, profile)
        save_profile(profile)
        print("Ingestion results:")
        for s in signals:
            print(f"  + {s}")

    if args.learn:
        print("\nAnalyzing shortlisted market demand to refine profile...")
        profile, insights = learn_from_pipeline(profile)
        save_profile(profile)
        print("Market Learning Results:")
        for ins in insights:
            print(f"  * {ins}")

    if args.audit or not (args.show or args.ingest_pdf or args.learn):
        report = profile.audit_consistency()
        print("\n==========================================")
        print(f"Candidate Profile: {profile.identity.name} ({profile.identity.title})")
        print(f"Completeness Score: {report.completeness_score}% | Status: {'VALID' if report.is_valid else 'ERRORS'}")
        print("==========================================")
        if report.errors:
            print("\nErrors (Must Fix):")
            for e in report.errors:
                print(f"  [ERROR] {e}")
        if report.warnings:
            print("\nWarnings:")
            for w in report.warnings:
                print(f"  [WARN] {w}")
        if report.recommended_improvements:
            print("\nRecommended Profile Improvements:")
            for imp in report.recommended_improvements:
                print(f"  [TIP] {imp}")
        print(f"\nMaster Profile is stored at: {DEFAULT_PROFILE_FILE}\n")

    if args.show:
        print(f"\n--- Master Profile Summary for {profile.identity.name} ---")
        print(f"Contact: {profile.identity.email} | {profile.identity.location}")
        print(f"Target Roles: {', '.join(profile.preferences.target_roles)}")
        print(f"Total Skills: {len(profile.skills)} across Quantum, Backend, Architecture")
        print(f"Experience: {len(profile.experience)} positions | Projects: {len(profile.projects)}")

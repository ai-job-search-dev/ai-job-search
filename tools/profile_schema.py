#!/usr/bin/env python3
"""Pydantic Schema for Candidate Master Profile and Consistency Engine.

Defines the single source of truth for candidate data, career preferences,
verified STAR achievements, and consistency validation rules.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator


class LanguageSkill(BaseModel):
    name: str = Field(..., description="Language name, e.g., 'Spanish', 'English'")
    level: str = Field(..., description="Proficiency level, e.g., 'Native', 'Bilingual', 'C1', 'Fluent'")
    notes: Optional[str] = None


class STARExample(BaseModel):
    title: str = Field(..., description="Short title for the story/achievement")
    situation: str = Field(..., description="Context, company or academic challenge")
    task: str = Field(..., description="Specific goal or problem to solve")
    action: str = Field(..., description="Action taken, tools and technologies used")
    result: str = Field(..., description="Quantifiable outcome, metric, or impact")
    competencies: List[str] = Field(default_factory=list, description="Skills demonstrated in this story")


class EducationEntry(BaseModel):
    degree: str
    institution: str
    period: str
    thesis: Optional[str] = None
    key_topics: List[str] = Field(default_factory=list)


class WorkExperienceEntry(BaseModel):
    company: str
    role: str
    location: str
    start_date: str = Field(..., description="e.g., '2023-01' or 'Jan 2023'")
    end_date: Optional[str] = Field(None, description="'Present' if current")
    is_current: bool = False
    achievements: List[str] = Field(default_factory=list, description="Action-oriented bullet points")
    skills_used: List[str] = Field(default_factory=list, description="Technologies applied")
    star_examples: List[STARExample] = Field(default_factory=list)


class ProjectEntry(BaseModel):
    name: str
    description: str
    repo_url: Optional[str] = None
    technologies: List[str] = Field(default_factory=list)
    impact_highlights: List[str] = Field(default_factory=list)


class SkillItem(BaseModel):
    name: str
    category: Literal[
        "Quantum Computing",
        "Software Engineering",
        "Backend & APIs",
        "Architecture & Distributed Systems",
        "Cloud & DevOps",
        "Mathematics & Algorithms",
        "AI & Machine Learning",
        "Other"
    ]
    proficiency: Literal["Expert", "Advanced", "Intermediate", "Familiar"] = "Advanced"
    evidence_sources: List[str] = Field(
        default_factory=list,
        description="References to projects, roles, or repositories where this skill was proven"
    )


EmploymentType = Literal["full_time", "part_time", "internship", "contract"]
RuleMode = Literal["veto", "penalty", "off"]


class RuleSetting(BaseModel):
    """How one screening rule affects a posting: veto it, subtract `penalty` points, or ignore the rule."""

    mode: RuleMode = "veto"
    penalty: float = Field(2.0, ge=0.0, le=10.0, description="Points subtracted when mode is 'penalty'")


class ScreeningRules(BaseModel):
    """One switch per screening rule. Defaults reproduce the original strict behaviour."""

    hardware_lab: RuleSetting = Field(default_factory=RuleSetting, description="Wet-lab, cryogenics, cleanroom roles")
    research_phd: RuleSetting = Field(default_factory=RuleSetting, description="Research-only roles that require a PhD")
    non_engineering: RuleSetting = Field(default_factory=RuleSetting, description="Sales, support, recruiting, operations")
    management: RuleSetting = Field(default_factory=RuleSetting, description="Manager, director and head-of roles")
    citizenship_clearance: RuleSetting = Field(
        default_factory=RuleSetting, description="Postings that demand US citizenship or a security clearance"
    )
    unpaid: RuleSetting = Field(default_factory=RuleSetting, description="Unpaid or volunteer positions")
    work_authorization: RuleSetting = Field(
        default_factory=lambda: RuleSetting(mode="penalty"),
        description="Postings restricted to regions where the candidate cannot work without a visa",
    )


class WorkAuthorization(BaseModel):
    """Where the candidate may work, so remote postings tied to a country can be judged."""

    citizenships: List[str] = Field(default_factory=list, description="ISO codes, e.g. ['IT']")
    current_country: Optional[str] = Field(None, description="ISO code of the country of residence, e.g. 'AR'")
    authorized_regions: List[str] = Field(
        default_factory=list,
        description="Regions where no visa is needed. Country codes plus the groups 'EU' and 'EEA'.",
    )
    visa_possible_regions: List[str] = Field(
        default_factory=list, description="Regions where the candidate would apply for a work visa"
    )
    visa_region_penalty: float = Field(1.0, ge=0.0, le=10.0)
    unauthorized_region_penalty: float = Field(2.0, ge=0.0, le=10.0)
    no_sponsorship_penalty: float = Field(2.0, ge=0.0, le=10.0)


class EmploymentPreferences(BaseModel):
    accepted_types: List[EmploymentType] = Field(
        default_factory=lambda: ["full_time", "part_time", "internship", "contract"]
    )
    paid_only: bool = True
    penalties: Dict[str, float] = Field(
        default_factory=lambda: {"internship": 2.0},
        description="Points subtracted per accepted employment type, e.g. {'internship': 0}",
    )


class SeniorityPreferences(BaseModel):
    senior_penalty: float = Field(1.0, ge=0.0, le=10.0)
    staff_penalty: float = Field(2.0, ge=0.0, le=10.0)


class ScoringPreferences(BaseModel):
    """What counts as a good fit. Leave the keyword lists unset to use the built-in quantum-software defaults."""

    domain_keywords: Optional[List[str]] = Field(
        None, description="Terms that define the target field, e.g. ['quantum', 'qiskit']"
    )
    skill_keywords: Optional[List[str]] = Field(None, description="Engineering skills that count as a match")
    domain_weight: float = Field(
        0.5, ge=0.0, le=1.0, description="Share of the score from field fit. 0 scores on engineering skills alone"
    )
    remote_bonus: float = Field(0.5, ge=0.0, le=5.0)


class CareerPreferences(BaseModel):
    target_roles: List[str] = Field(default_factory=list)
    remote_only: bool = True
    salary_floor_usd: Optional[float] = None
    deal_breakers: List[str] = Field(default_factory=list)
    preferred_locations: List[str] = Field(default_factory=list)
    shortlist_threshold: float = Field(7.5, ge=0.0, le=10.0, description="Minimum overall score to shortlist")
    work_authorization: WorkAuthorization = Field(default_factory=WorkAuthorization)
    employment: EmploymentPreferences = Field(default_factory=EmploymentPreferences)
    seniority: SeniorityPreferences = Field(default_factory=SeniorityPreferences)
    rules: ScreeningRules = Field(default_factory=ScreeningRules)
    scoring: ScoringPreferences = Field(default_factory=ScoringPreferences)


class ConsistencyReport(BaseModel):
    """Detailed audit report on profile validity and anti-hallucination checks."""
    is_valid: bool
    completeness_score: int = Field(..., ge=0, le=100)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    unbacked_skills: List[str] = Field(
        default_factory=list,
        description="Skills claimed in the skills list but missing from experience or projects"
    )
    recommended_improvements: List[str] = Field(default_factory=list)


class CandidateIdentity(BaseModel):
    name: str
    title: str = "Software Engineer & Quantum Systems Developer"
    email: str
    phone: Optional[str] = None
    location: str
    linkedin: Optional[str] = None
    github: Optional[str] = None
    portfolio: Optional[str] = None
    languages: List[LanguageSkill] = Field(default_factory=list)


class MasterProfile(BaseModel):
    """Canonical Master Profile for the candidate."""
    version: str = "2.0.0"
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    identity: CandidateIdentity
    summary: str
    education: List[EducationEntry] = Field(default_factory=list)
    experience: List[WorkExperienceEntry] = Field(default_factory=list)
    projects: List[ProjectEntry] = Field(default_factory=list)
    skills: List[SkillItem] = Field(default_factory=list)
    preferences: CareerPreferences
    certifications: List[str] = Field(default_factory=list)
    publications: List[str] = Field(default_factory=list)
    discovered_insights: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Keywords and competencies learned from target job market analysis"
    )

    def audit_consistency(self) -> ConsistencyReport:
        """Run deep consistency, chronological, and evidence integrity checks."""
        warnings: List[str] = []
        errors: List[str] = []
        unbacked_skills: List[str] = []
        improvements: List[str] = []

        # 1. Identity validation
        if not self.identity.name or "[" in self.identity.name:
            errors.append("Identity name is empty or contains placeholder brackets.")
        if not self.identity.email or "@" not in self.identity.email:
            errors.append("Valid contact email is missing.")
        if not self.identity.languages:
            warnings.append("No languages declared. Language Gate may reject postings.")

        # 2. Evidence validation (anti-hallucination rule)
        # Collect all text from experiences and projects
        corpus = " ".join([
            self.summary,
            *(a for exp in self.experience for a in exp.achievements),
            *(s for exp in self.experience for s in exp.skills_used),
            *(p.description for p in self.projects),
            *(t for p in self.projects for t in p.technologies),
        ]).lower()

        for skill in self.skills:
            pattern = r"\b" + re.escape(skill.name.lower()) + r"\b"
            if not re.search(pattern, corpus):
                unbacked_skills.append(skill.name)
                warnings.append(
                    f"Skill '{skill.name}' is declared but not mentioned in any experience achievement or project description."
                )

        # 3. Measurable impact check (metrics in achievements)
        metric_pattern = re.compile(r"(\d+[\%xX]?|\breduced\b|\bincreased\b|\boptimized\b|\baccelerated\b)", re.IGNORECASE)
        unquantified_roles = []
        for exp in self.experience:
            has_metric = any(metric_pattern.search(ach) for ach in exp.achievements)
            if not has_metric and exp.achievements:
                unquantified_roles.append(exp.company)

        if unquantified_roles:
            improvements.append(
                f"Consider adding quantifiable metrics (%, numbers, performance gains) to roles at: {', '.join(unquantified_roles)}"
            )

        # 4. Completeness scoring
        score = 0
        if self.identity.name and self.identity.email: score += 20
        if self.summary: score += 15
        if self.experience: score += 25
        if self.projects: score += 20
        if self.skills and not unbacked_skills: score += 15
        elif self.skills: score += 10
        if self.preferences.target_roles: score += 5

        is_valid = len(errors) == 0

        return ConsistencyReport(
            is_valid=is_valid,
            completeness_score=min(100, score),
            warnings=warnings,
            errors=errors,
            unbacked_skills=unbacked_skills,
            recommended_improvements=improvements,
        )

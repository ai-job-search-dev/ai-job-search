#!/usr/bin/env python3
"""Pydantic Schemas for Structured LLM Job Evaluation.

Enforces strict typed output via Instructor for scoring, vetoing,
and resolving the 'generic HR PhD boilerplate' in quantum software roles.
"""

from __future__ import annotations

from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class QuantumJobEvaluation(BaseModel):
    """Structured evaluation of a job posting against Quantum SWE profile."""

    overall_score: float = Field(
        ...,
        ge=0.0,
        le=10.0,
        description="Overall compatibility score from 0.0 to 10.0. Scores at or above the user's shortlist threshold are shortlisted for tailoring.",
    )
    software_fit_score: float = Field(
        ...,
        ge=0.0,
        le=10.0,
        description="Software engineering stack & best practices fit (Python, C++, testing, architectures).",
    )
    quantum_fit_score: float = Field(
        ...,
        ge=0.0,
        le=10.0,
        description="Quantum computing fit (SDKs, Qiskit, PennyLane, compilers, hybrid algorithms).",
    )
    is_software_role: bool = Field(
        ...,
        description="True if the primary day-to-day duties involve writing code, building SDKs, software testing, or cloud APIs.",
    )
    is_experimental_hardware: bool = Field(
        ...,
        description="True if the role requires wet-lab work, cryogenics, lasers, dilution refrigerators, or physical cleanroom chip fab.",
    )
    phd_requirement_analysis: Literal[
        "not_required", "generic_hr_boilerplate", "strict_scientific_research"
    ] = Field(
        ...,
        description=(
            "Analysis of PhD requirement: 'not_required' if no PhD requested; "
            "'generic_hr_boilerplate' if listed as default text for a software role (DO NOT VETO); "
            "'strict_scientific_research' if the role demands frontier novel theoretical physics research (VETO)."
        ),
    )
    should_veto: bool = Field(
        ...,
        description="True if the job violates deal-breakers (experimental hardware, non-viable location, or strict academic research).",
    )
    veto_reason: Optional[str] = Field(
        None,
        description="Concise reason for veto if should_veto is True, else null.",
    )
    matched_skills: List[str] = Field(
        default_factory=list,
        description="Key technical skills and tools matched between posting and profile (e.g. ['Python', 'Qiskit', 'CI/CD']).",
    )
    missing_keywords: List[str] = Field(
        default_factory=list,
        description="Important keywords demanded by posting that are missing from candidate profile.",
    )
    tailoring_recommendations: List[str] = Field(
        default_factory=list,
        description="Concrete, honest recommendations for adapting CV bullet points and cover letter.",
    )
    reasoning: str = Field(
        ...,
        description="Analytical summary explaining the scores, veto determination, and PhD classification.",
    )

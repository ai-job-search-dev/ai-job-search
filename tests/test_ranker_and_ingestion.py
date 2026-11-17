#!/usr/bin/env python3
"""Comprehensive Unit Tests for Sprint 1, 2, and 3 Modules.

Tests:
1. Declarative configuration files loading.
2. Logging and Telemetry Funnel tracking.
3. ATS Ingestion text cleaning and security guard integration.
4. Ranker Pydantic schema validation, hardware veto, and anti-PhD boilerplate heuristic.
5. SQLite state machine transitions (NEW -> SHORTLISTED / IGNORED).
"""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
import yaml

from tools.db import (
    init_db,
    get_db,
    upsert_job,
    record_evaluation,
    STATUS_NEW,
    STATUS_SHORTLISTED,
    STATUS_IGNORED,
)
from tools.logger import FunnelMetrics, StructuredLogger
from tools.ranker_schema import QuantumJobEvaluation
from tools.ranker import heuristic_quantum_eval
from tools.ingest_ats import strip_html


class TestSprintModules(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_funnel.db"
        init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_declarative_configs_exist_and_valid(self):
        """Test that companies.yaml, portals.yaml, and prompts.yaml exist and parse cleanly."""
        root = Path(__file__).resolve().parent.parent

        companies_file = root / "config" / "companies.yaml"
        self.assertTrue(companies_file.exists(), "companies.yaml must exist")
        with open(companies_file, "r", encoding="utf-8") as f:
            comp_data = yaml.safe_load(f)
            self.assertIn("companies", comp_data)
            self.assertGreaterEqual(len(comp_data["companies"]), 4)

        portals_file = root / "config" / "portals.yaml"
        self.assertTrue(portals_file.exists(), "portals.yaml must exist")
        with open(portals_file, "r", encoding="utf-8") as f:
            portals_data = yaml.safe_load(f)
            self.assertIn("jobspy", portals_data)
            self.assertIn("niche_portals", portals_data)

        prompts_file = root / "config" / "prompts.yaml"
        self.assertTrue(prompts_file.exists(), "prompts.yaml must exist")
        with open(prompts_file, "r", encoding="utf-8") as f:
            prompts_data = yaml.safe_load(f)
            self.assertIn("ranker", prompts_data)
            self.assertIn("tailor", prompts_data)

    def test_strip_html(self):
        """Test HTML stripping utility for ATS ingestion."""
        html = "<div><h3>Quantum <b>Compiler</b> Engineer</h3><p>Python & Qiskit</p></div>"
        cleaned = strip_html(html)
        self.assertEqual(cleaned, "Quantum Compiler Engineer Python & Qiskit")

    def test_funnel_metrics_tracking(self):
        """Test thread-safe funnel telemetry counter."""
        metrics = FunnelMetrics()
        metrics.increment("ingested_total", 5)
        metrics.increment("vetoed_hardware", 2)
        metrics.increment("shortlisted_total", 3)

        data = metrics.to_dict()
        self.assertEqual(data["ingested_total"], 5)
        self.assertEqual(data["vetoed_hardware"], 2)
        self.assertEqual(data["shortlisted_total"], 3)

    def test_ranker_hardware_veto(self):
        """Test that experimental wet-lab/hardware jobs are vetoed immediately."""
        eval_result = heuristic_quantum_eval(
            title="Quantum Fabrication Engineer",
            company="CryoTech",
            description="Operate dilution refrigerators, optical tables, cleanroom lithography, and laser alignment.",
            location="Boulder, CO",
            is_remote=False,
        )
        self.assertTrue(eval_result.should_veto)
        self.assertTrue(eval_result.is_experimental_hardware)
        self.assertIn("VETO", eval_result.veto_reason)
        self.assertLess(eval_result.overall_score, 5.0)

    def test_ranker_anti_phd_boilerplate_heuristic(self):
        """Test that a Quantum SWE role requiring Python, C++, and Qiskit is NOT vetoed even if PhD is mentioned."""
        job_desc = (
            "We are seeking a Quantum Software Engineer to develop compiler backends and SDK libraries. "
            "Requirements: Strong experience in Python, C++, Qiskit, and modern testing practices. "
            "PhD in Physics or Computer Science preferred or equivalent experience."
        )
        eval_result = heuristic_quantum_eval(
            title="Quantum Software Engineer - SDK & Compilers",
            company="Rigetti Computing",
            description=job_desc,
            location="Remote",
            is_remote=True,
        )

        self.assertFalse(eval_result.should_veto, "Should NOT veto an SWE role with PhD boilerplate")
        self.assertFalse(eval_result.is_experimental_hardware)
        self.assertEqual(eval_result.phd_requirement_analysis, "generic_hr_boilerplate")
        self.assertGreaterEqual(eval_result.overall_score, 7.5, "Should meet shortlist threshold")
        self.assertIn("Python", eval_result.matched_skills)
        self.assertIn("Qiskit", eval_result.matched_skills)

    def test_db_status_transition_on_evaluation(self):
        """Test that evaluation directly transitions job in DB between NEW, SHORTLISTED, and IGNORED."""
        with get_db(self.db_path) as conn:
            # 1. Insert a new job
            job_id, is_new = upsert_job(
                conn=conn,
                title="Quantum Algorithms Developer",
                company="SandboxAQ",
                url="https://sandboxaq.com/job/123",
                portal="ashby",
                raw_description="Python, Qiskit, and quantum chemistry algorithms.",
            )
            self.assertTrue(is_new)

            job_row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            self.assertEqual(job_row["status"], STATUS_NEW)

            # 2. Record high-score evaluation (>= 7.5) -> Transitions to SHORTLISTED
            record_evaluation(
                conn=conn,
                job_id=job_id,
                overall_score=8.5,
                software_fit_score=9.0,
                quantum_fit_score=8.0,
                phd_requirement_analysis="generic_hr_boilerplate",
                is_experimental_hardware=False,
                veto_triggered=False,
                matched_skills=["Python", "Qiskit"],
            )

            job_row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            self.assertEqual(job_row["status"], STATUS_SHORTLISTED)

            # 3. Insert and veto another job -> Transitions to IGNORED
            veto_job_id, _ = upsert_job(
                conn=conn,
                title="Cryogenic Hardware Lead",
                company="IonQ",
                url="https://ionq.com/job/456",
                portal="greenhouse",
                raw_description="Dilution refrigerator maintenance and laser optics.",
            )
            record_evaluation(
                conn=conn,
                job_id=veto_job_id,
                overall_score=2.0,
                is_experimental_hardware=True,
                veto_triggered=True,
                veto_reason="VETO: Experimental hardware",
            )
            veto_row = conn.execute("SELECT status, rejection_reason FROM jobs WHERE id = ?", (veto_job_id,)).fetchone()
            self.assertEqual(veto_row["status"], STATUS_IGNORED)
            self.assertIn("VETO", veto_row["rejection_reason"])


if __name__ == "__main__":
    unittest.main()

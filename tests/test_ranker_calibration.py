#!/usr/bin/env python3
"""Calibration tests for the deterministic ranker plus config handling in the ingesters."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from tools import ingest_ats, ingest_crawl, ranker
from tools.db import STATUS_APPLIED, STATUS_IGNORED, STATUS_NEW, get_db, init_db, record_evaluation, upsert_job
from tools.ranker import (
    CITIZENSHIP_VETO_REASON,
    HARDWARE_VETO_REASON,
    heuristic_quantum_eval,
    keyword_in,
)

GOOD_DESC = (
    "Build the quantum SDK in Python and C++. Compiler passes, testing and CI/CD. "
    "Work with Qiskit on qubits and error mitigation."
)


def evaluate(title, description=GOOD_DESC, remote=True):
    return heuristic_quantum_eval(title, "Acme", description, "Remote", remote)


class HeuristicCalibrationTests(unittest.TestCase):
    def test_strong_software_fit_is_shortlisted(self):
        ev = evaluate("Quantum Software Engineer")
        self.assertFalse(ev.should_veto)
        self.assertGreaterEqual(ev.overall_score, 7.5)

    def test_seniority_lowers_score(self):
        base = evaluate("Quantum Software Engineer").overall_score
        self.assertEqual(round(base - evaluate("Senior Quantum Software Engineer").overall_score, 1), 1.0)
        self.assertEqual(round(base - evaluate("Staff Quantum Software Engineer").overall_score, 1), 2.0)

    def test_non_engineering_and_management_roles_are_vetoed(self):
        for title in ("Senior Customer Success Manager, Quantum", "Sales Engineer", "Quantum Field Engineer",
                      "Engineering Manager, Quantum Software", "Director of Software"):
            ev = evaluate(title)
            self.assertTrue(ev.should_veto, title)
            self.assertLess(ev.overall_score, 7.5, title)

    def test_negated_or_preferred_clearance_is_not_a_veto(self):
        for text in ("No security clearance required.", "A security clearance is not required.",
                     "We do not require a security clearance."):
            ev = evaluate("Quantum Software Engineer", GOOD_DESC + " " + text)
            self.assertFalse(ev.should_veto, text)
        preferred = evaluate("Quantum Software Engineer", GOOD_DESC + " Security clearance preferred.")
        self.assertFalse(preferred.should_veto)
        self.assertLess(preferred.overall_score, evaluate("Quantum Software Engineer").overall_score)

    def test_export_control_us_person_boilerplate_is_a_penalty_not_a_veto(self):
        boilerplate = (" Employment is contingent on verifying U.S. Person (e.g., U.S. citizen, U.S. national) status "
                       "for export controls, obtaining any necessary license, or a license exception.")
        ev = evaluate("Quantum Software Engineer", GOOD_DESC + boilerplate)
        self.assertFalse(ev.should_veto)
        self.assertLess(ev.overall_score, evaluate("Quantum Software Engineer").overall_score)

    def test_hard_citizenship_requirement_still_vetoes(self):
        self.assertTrue(evaluate("Quantum Software Engineer", GOOD_DESC + " Must be a U.S. citizen.").should_veto)
        self.assertTrue(evaluate("Quantum Software Engineer", GOOD_DESC + " Active TS/SCI clearance required.").should_veto)

    def test_citizenship_or_clearance_is_vetoed(self):
        ev = evaluate("Quantum Software Engineer", GOOD_DESC + " Only U.S. citizens are eligible to apply.")
        self.assertTrue(ev.should_veto)
        self.assertEqual(ev.veto_reason, CITIZENSHIP_VETO_REASON)
        ev = evaluate("Quantum Software Engineer", GOOD_DESC + " Must hold an active security clearance.")
        self.assertTrue(ev.should_veto)

    def test_phd_with_dots_and_research_titles(self):
        ev = evaluate("Quantum Algorithms Research Scientist", GOOD_DESC + " A Ph.D. in physics is required.")
        self.assertTrue(ev.should_veto)
        self.assertEqual(ev.phd_requirement_analysis, "strict_scientific_research")

    def test_phd_boilerplate_on_software_role_is_tolerated(self):
        ev = evaluate("Quantum Software Engineer", GOOD_DESC + " PhD in physics or related field preferred.")
        self.assertFalse(ev.should_veto)
        self.assertEqual(ev.phd_requirement_analysis, "generic_hr_boilerplate")

    def test_hardware_role_is_vetoed(self):
        ev = evaluate("Cryogenic Test Engineer", "Operate a dilution refrigerator and cryostat for qubit tests.")
        self.assertTrue(ev.should_veto)
        self.assertEqual(ev.veto_reason, HARDWARE_VETO_REASON)

    def test_keywords_match_whole_words_only(self):
        self.assertFalse(keyword_in("digital capital rapid", "git"))
        self.assertFalse(keyword_in("digital capital rapid", "api"))
        self.assertTrue(keyword_in("we use git, python.", "git"))
        self.assertTrue(keyword_in("c++ and rust", "c++"))

    def test_work_authorization_restriction_is_penalised_not_vetoed(self):
        base = evaluate("Quantum Software Engineer")
        for text in ("We will not sponsor visas.", "Candidates must be legally authorized to work in the United States.",
                     "You must reside in Canada."):
            ev = evaluate("Quantum Software Engineer", GOOD_DESC + " " + text)
            self.assertFalse(ev.should_veto, text)
            self.assertEqual(round(base.overall_score - ev.overall_score, 1), 2.0, text)

    def test_internship_is_penalised(self):
        self.assertLess(evaluate("Quantum Software Intern").overall_score,
                        evaluate("Quantum Software Engineer").overall_score)


class RerankTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        init_db(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, **kw):
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
            return ranker.run_ranker(db_path=self.db, **kw)

    def test_reevaluate_rescores_ignored_but_not_applied(self):
        with get_db(self.db) as conn:
            ignored, _ = upsert_job(conn, "Quantum Software Engineer", "Acme", "https://x/1", "ashby:a",
                                    "Remote", True, GOOD_DESC, GOOD_DESC)
            applied, _ = upsert_job(conn, "Quantum Software Engineer II", "Beta", "https://x/2", "ashby:b",
                                    "Remote", True, GOOD_DESC, GOOD_DESC)
            record_evaluation(conn, ignored, 1.0)
            record_evaluation(conn, applied, 9.0)
            conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (STATUS_APPLIED, applied))
        self.assertEqual(self._run(), [])
        results = self._run(reevaluate=True)
        self.assertEqual([r["job_id"] for r in results], [ignored])
        with get_db(self.db) as conn:
            statuses = {r["id"]: r["status"] for r in conn.execute("SELECT id, status FROM jobs")}
        self.assertEqual(statuses[applied], STATUS_APPLIED)
        self.assertNotEqual(statuses[ignored], STATUS_NEW)
        self.assertEqual(results[0]["status"], "SHORTLISTED")
        self.assertNotEqual(statuses[ignored], STATUS_IGNORED)


class MissingDescriptionTests(unittest.TestCase):
    def test_jobs_without_a_usable_description_are_left_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.db"
            init_db(db)
            with get_db(db) as conn:
                empty, _ = upsert_job(conn, "Quantum Software Engineer", "Acme", "https://x/1", "jobspy:linkedin",
                                      "Remote", True, "", "")
                full, _ = upsert_job(conn, "Quantum Software Engineer", "Beta", "https://x/2", "jobspy:linkedin",
                                     "Remote", True, GOOD_DESC, GOOD_DESC)
            with mock.patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
                results = ranker.run_ranker(db_path=db)
            self.assertEqual([r["job_id"] for r in results], [full])
            with get_db(db) as conn:
                status = conn.execute("SELECT status FROM jobs WHERE id = ?", (empty,)).fetchone()[0]
            self.assertEqual(status, STATUS_NEW)


class ConfigHandlingTests(unittest.TestCase):
    def test_disabled_company_is_not_queried(self):
        cfg = {"companies": [
            {"name": "Off", "ats_provider": "greenhouse", "slug": "off", "enabled": False},
            {"name": "On", "ats_provider": "greenhouse", "slug": "on"},
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.yaml"
            path.write_text(yaml.safe_dump(cfg))
            with mock.patch.object(ingest_ats, "fetch_greenhouse_jobs", return_value=[]) as fetch, \
                    mock.patch.object(ingest_ats.time, "sleep"):
                ingest_ats.run_ats_ingestion(path, db_path=Path(tmp) / "t.db")
        fetch.assert_called_once_with("on")

    def test_repo_company_config_is_valid(self):
        root = Path(__file__).resolve().parent.parent
        companies = yaml.safe_load((root / "config" / "companies.yaml").read_text())["companies"]
        enabled = [c for c in companies if c.get("enabled", True)]
        self.assertGreaterEqual(len(enabled), 4)
        for c in enabled:
            self.assertIn(c["ats_provider"], {"greenhouse", "lever", "ashby"})
            self.assertTrue(c["slug"])

    def test_unknown_crawl_parser_is_skipped_not_stored_as_a_job(self):
        cfg = {"niche_portals": [{"name": "X", "url": "https://example.com", "parser": "nope", "enabled": True}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.yaml"
            path.write_text(yaml.safe_dump(cfg))
            with mock.patch.object(ingest_crawl, "get_with_retry") as get:
                stats = ingest_crawl.run_niche_crawling(path, db_path=Path(tmp) / "t.db")
        get.assert_not_called()
        self.assertEqual(stats["total"], 0)


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path
import tempfile
import sqlite3

from tools.db import (
    init_db,
    get_db,
    upsert_job,
    record_evaluation,
    record_application,
    check_company_cooldown,
    get_pipeline_summary,
    STATUS_DISCOVERED,
    STATUS_SHORTLISTED,
    STATUS_IGNORED,
    STATUS_APPLIED,
)


class DatabaseStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = Path(self.tmp.name)
        init_db(self.db_path)

    def tearDown(self):
        try:
            self.db_path.unlink()
        except Exception:
            pass

    def test_upsert_deduplication(self):
        with get_db(self.db_path) as conn:
            job_id_1, is_new_1 = upsert_job(
                conn,
                title="Quantum Software Engineer",
                company="Xanadu",
                url="https://jobs.lever.co/xanadu/123",
                portal="lever",
                location="Remote",
                is_remote=True,
            )
            self.assertTrue(is_new_1)

            # Re-insert identical company and title
            job_id_2, is_new_2 = upsert_job(
                conn,
                title="Quantum Software Engineer",
                company="Xanadu",
                url="https://jobs.lever.co/xanadu/123",
                portal="lever",
                location="Toronto, ON",
                is_remote=True,
            )
            self.assertFalse(is_new_2)
            self.assertEqual(job_id_1, job_id_2)

    def test_evaluation_and_status_update(self):
        with get_db(self.db_path) as conn:
            job_id, _ = upsert_job(
                conn,
                title="Senior Quantum SDK Developer",
                company="Rigetti",
                url="https://boards.greenhouse.io/rigetti/456",
                portal="greenhouse",
            )
            # Shortlisted score >= 70
            record_evaluation(
                conn,
                job_id=job_id,
                overall_score=88.5,
                quantum_fit_score=95.0,
                software_fit_score=85.0,
                veto_triggered=False,
                strengths=["Qiskit", "Python", "Algorithms"],
            )

            job = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            self.assertEqual(job["status"], STATUS_SHORTLISTED)

            # Veto case
            job_id_veto, _ = upsert_job(
                conn,
                title="Cryogenic Hardware Technician",
                company="Rigetti",
                url="https://boards.greenhouse.io/rigetti/789",
                portal="greenhouse",
            )
            record_evaluation(
                conn,
                job_id=job_id_veto,
                overall_score=40.0,
                veto_triggered=True,
                veto_reason="Experimental hardware role",
            )
            veto_job = conn.execute("SELECT status, rejection_reason FROM jobs WHERE id = ?", (job_id_veto,)).fetchone()
            self.assertEqual(veto_job["status"], STATUS_IGNORED)
            self.assertIn("Experimental hardware", veto_job["rejection_reason"])

    def test_company_cooldown(self):
        with get_db(self.db_path) as conn:
            job_id, _ = upsert_job(
                conn,
                title="Backend Engineer",
                company="SandboxAQ",
                url="https://jobs.ashbyhq.com/sandboxaq/1",
                portal="ashby",
            )
            in_cooldown, reason = check_company_cooldown(conn, "SandboxAQ")
            self.assertFalse(in_cooldown)

            record_application(
                conn,
                job_id=job_id,
                cv_path="cv/main_sandboxaq.pdf",
                cooldown_days=30,
            )

            in_cooldown_after, reason_after = check_company_cooldown(conn, "SandboxAQ")
            self.assertTrue(in_cooldown_after)
            self.assertIn("SandboxAQ", reason_after)
            self.assertIn("cooldown", reason_after)


if __name__ == "__main__":
    unittest.main()

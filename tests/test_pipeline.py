#!/usr/bin/env python3
"""Tests for the single-command pipeline: lock, step isolation, run history, digest, dry run and status."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml

from tools import pipeline
from tools.db import get_db, init_db, upsert_job
from tools.pipeline import LockHeld, Options, RunLock, build_digest, plan_run, render_digest, render_plan, render_status, run_pipeline
from tools.ranker_schema import QuantumJobEvaluation

DESC = (
    "Build the quantum SDK in Python and C++. Compiler passes, testing and CI/CD. "
    "Work with Qiskit on qubits and error mitigation for our platform."
)


class FakeClient:
    def __init__(self, score=9.0):
        self.calls = 0
        self.score = score
        self.chat = SimpleNamespace(completions=SimpleNamespace(create_with_completion=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        ev = QuantumJobEvaluation(
            overall_score=self.score, software_fit_score=9.0, quantum_fit_score=9.0, is_software_role=True,
            is_experimental_hardware=False, phd_requirement_analysis="not_required", should_veto=False,
            veto_reason=None, matched_skills=["Python"], reasoning="strong fit")
        return ev, SimpleNamespace(usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=200))


def adder(*companies, description=DESC, title="Quantum Software Engineer"):
    """An ingester that stores one posting per company."""
    def ingest(db):
        with get_db(db) as conn:
            for name in companies:
                upsert_job(conn, title, name, f"https://x.io/{name}", "ashby:x", "Remote", True, description, description)
        return {"total": len(companies), "new": len(companies)}
    return ingest


def boom(db):
    raise RuntimeError("network down")


class PipelineCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "t.db"
        self.reports = self.dir / "reports"
        self.profile = self.dir / "profile.yaml"
        self.profile.write_text(yaml.safe_dump({"summary": "Engineer.", "preferences": {}}))
        self.env = mock.patch.dict("os.environ", {"OPENAI_API_KEY": "", "AI_JOB_SEARCH_MODEL": "gpt-4o-mini"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def run_it(self, opts=None, ingesters=None, client=None):
        return run_pipeline(opts or Options(), db_path=self.db, profile_path=self.profile, llm_client=client,
                            ingesters={"ats": adder("Acme"), "jobspy": adder("Beta"), "crawl": adder("Gamma"), **(ingesters or {})},
                            reports_dir=self.reports)

    def rows(self, sql, *args):
        with get_db(self.db) as conn:
            return conn.execute(sql, args).fetchall()


class LockTests(PipelineCase):
    def test_second_run_is_refused_while_the_first_holds_the_lock(self):
        init_db(self.db)
        with RunLock(self.db):
            with self.assertRaises(LockHeld):
                RunLock(self.db).acquire()
        RunLock(self.db).acquire()

    def test_lock_is_released_even_when_the_body_raises(self):
        init_db(self.db)
        with self.assertRaises(ValueError):
            with RunLock(self.db):
                raise ValueError("x")
        self.assertFalse((self.dir / ".pipeline.lock").exists())

    def test_old_corrupt_and_dead_process_locks_are_taken_over(self):
        init_db(self.db)
        lock = self.dir / ".pipeline.lock"
        lock.write_text("not json")
        RunLock(self.db).acquire()
        lock.write_text(json.dumps({"pid": os.getpid(), "started": time.time() - pipeline.LOCK_STALE_SECONDS - 5}))
        RunLock(self.db).acquire()
        if os.name != "nt":
            dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
            lock.write_text(json.dumps({"pid": int(dead.stdout), "started": time.time()}))
            RunLock(self.db).acquire()

    def test_a_live_lock_is_respected(self):
        init_db(self.db)
        (self.dir / ".pipeline.lock").write_text(json.dumps({"pid": os.getpid(), "started": time.time()}))
        with self.assertRaises(LockHeld):
            RunLock(self.db).acquire()

    def test_release_does_not_delete_a_lock_it_does_not_own(self):
        init_db(self.db)
        lock = RunLock(self.db)
        lock.release()
        (self.dir / ".pipeline.lock").write_text("{}")
        lock.release()
        self.assertTrue((self.dir / ".pipeline.lock").exists())


class RunTests(PipelineCase):
    def test_a_run_ingests_ranks_records_itself_and_writes_a_digest(self):
        report = self.run_it(client=FakeClient(9.1))
        self.assertEqual((report.status, report.exit_code), ("ok", 0))
        steps = {s.name: s.status for s in report.steps}
        self.assertEqual(steps, {"backup": "ok", "ingest_ats": "ok", "ingest_jobspy": "ok", "ingest_crawl": "skipped",
                                 "close_stale": "skipped", "rank": "ok"})
        self.assertEqual(len(report.digest), 2)
        self.assertTrue(report.digest_path.exists())
        text = report.digest_path.read_text(encoding="utf-8")
        self.assertIn("tailor.py --job-id", text)
        self.assertIn("Quantum Software Engineer", text)
        run = self.rows("SELECT * FROM pipeline_runs")[0]
        self.assertEqual((run["status"], run["id"]), ("ok", report.run_id))
        self.assertIsNotNone(run["finished_at"])
        self.assertGreater(run["llm_cost_usd"], 0)
        self.assertEqual(len(json.loads(run["steps"])), 6)

    def test_the_lock_is_free_after_a_run(self):
        self.run_it()
        self.assertFalse((self.dir / ".pipeline.lock").exists())

    def test_a_failing_source_does_not_stop_the_other_steps(self):
        report = self.run_it(ingesters={"ats": boom}, client=FakeClient())
        steps = {s.name: s for s in report.steps}
        self.assertEqual(steps["ingest_ats"].status, "failed")
        self.assertIn("network down", steps["ingest_ats"].error)
        self.assertEqual(steps["ingest_jobspy"].status, "ok")
        self.assertEqual(steps["rank"].status, "ok")
        self.assertEqual((report.status, report.exit_code), ("failed", 1))
        self.assertEqual(len(report.digest), 1)
        self.assertIn("FAILED step `ingest_ats`", report.digest_path.read_text(encoding="utf-8"))
        self.assertEqual(self.rows("SELECT status FROM pipeline_runs")[0][0], "failed")

    def test_source_level_failures_are_warnings_not_failures(self):
        report = self.run_it(ingesters={"ats": lambda db: {"total": 0, "companies_failed": 2}})
        self.assertEqual((report.status, report.exit_code), ("partial", 0))
        self.assertTrue(any("2 ATS source" in w for w in report.warnings))

    def test_skip_ingest_only_ranks_what_is_stored(self):
        init_db(self.db)
        adder("Existing")(self.db)
        report = self.run_it(Options(skip_ingest=True))
        self.assertEqual([s.status for s in report.steps if s.name.startswith("ingest")], ["skipped"] * 3)
        self.assertEqual(len(self.rows("SELECT id FROM jobs")), 1)
        self.assertEqual(len(report.digest), 1)

    def test_selected_sources_are_the_only_ones_run(self):
        report = self.run_it(Options(sources=["crawl"]))
        ran = [s.name for s in report.steps if s.name.startswith("ingest") and s.status == "ok"]
        self.assertEqual(ran, ["ingest_crawl"])

    def test_llm_budget_is_reported_as_a_warning_and_summarised(self):
        client = FakeClient()
        report = self.run_it(Options(max_llm_calls=1), client=client)
        self.assertEqual(client.calls, 1)
        self.assertEqual(report.status, "partial")
        self.assertTrue(any("budget" in w.lower() for w in report.warnings))
        self.assertEqual(report.usage["calls"], 1)

    def test_a_second_run_costs_nothing_because_answers_are_cached(self):
        client = FakeClient()
        self.run_it(client=client)
        second = self.run_it(Options(refresh_stale=True), client=client)
        self.assertEqual(client.calls, 2)
        self.assertEqual(second.usage.get("calls", 0), 0)

    def test_refresh_stale_rescoring_follows_a_preferences_change(self):
        client = FakeClient()
        self.run_it(client=client)
        self.assertEqual(self.run_it(Options(refresh_stale=True), client=client).steps[-1].detail["evaluated"], 0)
        self.profile.write_text(yaml.safe_dump({"summary": "Engineer.", "preferences": {"shortlist_threshold": 6.0}}))
        report = self.run_it(Options(refresh_stale=True), client=client)
        self.assertEqual(report.steps[-1].detail["evaluated"], 2)

    def test_close_stale_closes_vanished_postings(self):
        init_db(self.db)
        adder("Old")(self.db)
        with get_db(self.db) as conn:
            conn.execute("UPDATE jobs SET last_seen = datetime('now', '-40 days')")
        report = self.run_it(Options(skip_ingest=True, close_stale_days=21))
        self.assertEqual({s.name: s for s in report.steps}["close_stale"].detail, {"closed": 1})
        self.assertEqual(self.rows("SELECT status FROM jobs")[0][0], "CLOSED")

    def test_a_held_lock_blocks_a_run_and_leaves_no_run_record(self):
        init_db(self.db)
        with RunLock(self.db):
            with self.assertRaises(LockHeld):
                self.run_it()
        self.assertEqual(self.rows("SELECT COUNT(*) FROM pipeline_runs")[0][0], 0)

    def test_metrics_in_the_report_are_per_run(self):
        report = self.run_it(client=FakeClient())
        self.assertEqual(report.metrics.get("evaluated_total"), 2)


class DigestTests(PipelineCase):
    def test_digest_only_lists_jobs_evaluated_since_the_given_time(self):
        self.run_it(client=FakeClient())
        with get_db(self.db) as conn:
            self.assertEqual(len(build_digest(conn, "2000-01-01 00:00:00")), 2)
            self.assertEqual(build_digest(conn, "2999-01-01 00:00:00"), [])

    def test_empty_digest_says_so(self):
        self.assertIn("No new shortlisted", render_digest([]))


class PlanAndStatusTests(PipelineCase):
    def test_dry_run_reads_but_never_creates_or_writes_the_database(self):
        plan = plan_run(Options(), db_path=self.db, profile_path=self.profile)
        self.assertFalse(plan["database_exists"])
        self.assertFalse(self.db.exists())
        self.assertIn("will be created", render_plan(plan))

    def test_dry_run_counts_pending_jobs_and_estimates_cost(self):
        init_db(self.db)
        adder("A", "B", "C")(self.db)
        before = self.db.stat().st_mtime_ns
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            plan = plan_run(Options(max_llm_calls=2), db_path=self.db, profile_path=self.profile)
        self.assertEqual(plan["to_rank"]["pending_new"], 3)
        self.assertGreater(plan["llm"]["estimated_cost_usd"], 0)
        text = render_plan(plan)
        self.assertIn("max 2 calls", text)
        self.assertEqual(self.db.stat().st_mtime_ns, before)

    def test_dry_run_without_a_key_says_the_heuristic_will_run(self):
        self.assertIn("keyword heuristic", render_plan(plan_run(Options(), db_path=self.db, profile_path=self.profile)))

    def test_unpriced_model_has_no_estimate(self):
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test", "AI_JOB_SEARCH_MODEL": "my-agent"}):
            text = render_plan(plan_run(Options(), db_path=self.db, profile_path=self.profile))
        self.assertIn("no price for this model", text)

    def test_status_shows_runs_spend_and_backups(self):
        self.run_it(client=FakeClient())
        text = render_status(self.db)
        for expected in ("schema v3", "SHORTLISTED 2", "LLM:", "Recent runs (UTC):", "Backups:  1", "Lock:     free"):
            self.assertIn(expected, text)

    def test_status_without_a_database(self):
        self.assertIn("No database", render_status(self.db))


class CliTests(PipelineCase):
    def run_main(self, *argv):
        out = io.StringIO()
        with mock.patch.dict("os.environ", {"AI_JOB_SEARCH_DB": str(self.db)}), contextlib.redirect_stdout(out):
            code = pipeline.main(list(argv))
        return code, out.getvalue()

    def test_dry_run_and_status_return_zero(self):
        code, out = self.run_main("run", "--dry-run", "--sources", "ats")
        self.assertEqual(code, 0)
        self.assertIn("DRY RUN", out)
        code, out = self.run_main("status")
        self.assertEqual(code, 0)

    def test_a_locked_run_exits_with_code_2(self):
        init_db(self.db)
        with RunLock(self.db):
            with mock.patch.dict("os.environ", {"AI_JOB_SEARCH_DB": str(self.db)}), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(pipeline.main(["run", "--skip-ingest"]), 2)

    def test_unknown_source_is_rejected_by_the_parser(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            pipeline.main(["run", "--sources", "myspace"])


if __name__ == "__main__":
    unittest.main()

"""The scrape helper filters before detail fetches and never rewrites old entries."""

import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import scrape_state  # noqa: E402


class ScrapeStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "job_scraper" / "seen_jobs.json"
        self.tracker = self.root / "tracker.csv"
        self.results = self.root / "results.json"

    def run_tool(self, command, jobs):
        self.results.write_text(json.dumps(jobs), encoding="utf-8")
        args = argparse.Namespace(command=command, results=self.results, state=self.state, tracker=self.tracker)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(scrape_state.run(args), 0)
        return json.loads(output.getvalue())

    def test_candidates_excludes_history_tracker_and_repeated_hits_before_detail(self):
        old = {f"old_{i}": {"title": f"Old {i}", "company": "Archive", "url": f"https://jobs.test/{i}"} for i in range(1000)}
        old["legacy_key"] = {"title": "Engineer", "company": "Acme", "url": "https://jobs.test/old"}
        self.state.parent.mkdir()
        self.state.write_text(json.dumps({"seen": old}), encoding="utf-8")
        self.tracker.write_text("date,company,role\n2026-10-21,Tracked,Analyst\n", encoding="utf-8")
        jobs = [
            {"title": "Engineer", "company": "Acme", "url": "https://jobs.test/old"},
            {"title": "Analyst", "company": "Tracked", "url": "https://jobs.test/tracked"},
            {"title": "Builder", "company": "Fresh", "url": "https://jobs.test/fresh"},
            {"title": "Builder", "company": "Fresh", "url": "https://jobs.test/fresh"},
        ]
        out = self.run_tool("candidates", jobs)
        self.assertEqual(out["selected"], [jobs[2]])
        self.assertEqual((out["already_seen"], out["already_applied"], out["repeated_in_batch"]), (1, 1, 1))
        self.assertNotIn("Old 999", json.dumps(out))

    def test_one_malformed_portal_hit_does_not_stop_other_candidates(self):
        valid = {"title": "Builder", "company": "Fresh", "url": "https://jobs.test/fresh"}
        out = self.run_tool("candidates", [valid, {"title": "Unknown", "company": None, "url": "https://jobs.test/bad"}, None])
        self.assertEqual(out["selected"], [valid])
        self.assertEqual([item["index"] for item in out["invalid"]], [1, 2])

    def test_store_adds_only_new_jobs_and_preserves_ranked_history(self):
        old = {"legacy_key": {"title": "Engineer", "company": "Acme", "url": "https://jobs.test/old", "status": "ranked", "rank_score": 91}}
        self.state.parent.mkdir()
        self.state.write_text(json.dumps({"seen": old}), encoding="utf-8")
        fresh = {
            "title": "Builder", "company": "Fresh", "url": "https://jobs.test/fresh",
            "first_seen": "2026-10-21", "posted_date": None, "deadline": None,
            "fit": "high", "status": "new", "portal": "example-search", "source": "cli",
            "location": "Remote",
        }
        out = self.run_tool("store", [fresh, {**fresh, "url": "https://jobs.test/another"}])
        self.assertEqual(out["added"], ["fresh_builder"])
        stored = json.loads(self.state.read_text(encoding="utf-8"))["seen"]
        self.assertEqual(stored["legacy_key"], old["legacy_key"])
        self.assertEqual(stored["fresh_builder"], fresh)

    def test_store_creates_missing_history_and_rejects_partial_entries_without_writing(self):
        good = {
            "title": "Builder", "company": "Fresh", "url": "https://jobs.test/fresh",
            "first_seen": "2026-10-21", "posted_date": None, "deadline": None,
            "fit": "high", "status": "new", "portal": "example-search", "source": "cli",
        }
        with self.assertRaisesRegex(ValueError, "invalid portal or source"):
            self.run_tool("store", [good, {**good, "title": "Analyst", "url": "https://jobs.test/other", "source": None}])
        self.assertFalse(self.state.exists())
        self.assertEqual(self.run_tool("store", [good])["added"], ["fresh_builder"])
        self.assertIn("fresh_builder", json.loads(self.state.read_text(encoding="utf-8"))["seen"])

    def test_history_counts_portal_and_legacy_host_without_emitting_entries(self):
        self.state.parent.mkdir()
        self.state.write_text(json.dumps({"seen": {
            "one": {"portal": "example-search", "url": "https://jobs.test/1"},
            "two": {"url": "https://careers.jobs.test/2"},
            "three": {"url": "https://other.test/3"},
        }}), encoding="utf-8")
        args = argparse.Namespace(command="history", state=self.state, portal="example-search", domain="jobs.test")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(scrape_state.run(args), 0)
        self.assertEqual(json.loads(output.getvalue()), {"portal": "example-search", "prior_entries": 2})


if __name__ == "__main__":
    unittest.main()

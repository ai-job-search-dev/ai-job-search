#!/usr/bin/env python3
"""Tests for the JobSpy ingester: region fan-out, remote-only filtering and config loading."""
import tempfile
import unittest
from pathlib import Path

import yaml

try:
    import jobspy  # noqa: F401
    import pandas as pd
except ImportError:
    raise unittest.SkipTest("python-jobspy and pandas are not installed")

from tools import ingest_jobspy
from tools.db import get_db, init_db

CONFIG = {
    "jobspy": {
        "sites": ["linkedin", "indeed"],
        "search_terms": ["Quantum Software Engineer", "Quantum SDK Developer"],
        "regions": [
            {"name": "Australia", "location": "Australia", "country_indeed": "australia"},
            {"name": "Canada", "location": "Canada", "country_indeed": "canada"},
        ],
        "results_wanted": 5,
        "hours_old": 120,
        "remote_only": True,
        "delay_seconds": 0,
    }
}


def frame(*rows):
    return pd.DataFrame(list(rows))


def job(title, url, location="Sydney, Australia", flagged=False, description="Python and Qiskit work.", site="indeed"):
    return {
        "title": title, "company": "Acme Quantum", "job_url": url, "location": location,
        "is_remote": flagged, "description": description, "site": site,
        "min_amount": float("nan"), "max_amount": float("nan"), "currency": float("nan"),
    }


class RecordingScraper:
    def __init__(self, frames=None):
        self.calls = []
        self.frames = frames or []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.frames[len(self.calls) - 1] if len(self.calls) <= len(self.frames) else pd.DataFrame()


class JobspyIngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = self.root / "portals.yaml"
        self.cfg.write_text(yaml.safe_dump(CONFIG))
        self.db = self.root / "t.db"

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, scraper, **kw):
        return ingest_jobspy.run_jobspy_ingestion(
            db_path=self.db, config_path=self.cfg, scraper=scraper, sleep=lambda _: None, **kw)

    def test_every_term_runs_in_every_region_with_its_country(self):
        scraper = RecordingScraper()
        stats = self._run(scraper)
        self.assertEqual(stats["queries"], 4)
        self.assertEqual(len(scraper.calls), 8)
        native = [c for c in scraper.calls if c["site_name"] == ["indeed"]]
        self.assertEqual({(c["location"], c["country_indeed"]) for c in native},
                         {("Australia", "australia"), ("Canada", "canada")})
        for c in scraper.calls:
            self.assertEqual(c["hours_old"], 120)
            self.assertTrue(c["fetch_description"])

    def test_remote_is_native_for_indeed_and_appended_for_linkedin(self):
        scraper = RecordingScraper()
        self._run(scraper, max_queries=1)
        indeed = next(c for c in scraper.calls if c["site_name"] == ["indeed"])
        linkedin = next(c for c in scraper.calls if c["site_name"] == ["linkedin"])
        self.assertTrue(indeed["is_remote"])
        self.assertEqual(indeed["search_term"], "Quantum Software Engineer")
        self.assertFalse(linkedin["is_remote"])
        self.assertEqual(linkedin["search_term"], "Quantum Software Engineer remote")

    def test_non_remote_postings_are_dropped_and_remote_ones_stored(self):
        frames = [
            frame(job("Quantum Engineer", "https://x/1", flagged=True),
                  job("Quantum Engineer (Hybrid)", "https://x/2", flagged=True),
                  job("Quantum Engineer", "https://x/3", location="Sydney, NSW", flagged=False)),
            frame(job("Remote Quantum Developer", "https://x/4", location="Australia", site="linkedin")),
        ]
        stats = self._run(RecordingScraper(frames), max_queries=1)
        self.assertEqual(stats["new"], 2)
        self.assertEqual(stats["not_remote_skipped"], 2)
        with get_db(self.db) as conn:
            urls = {r["url"] for r in conn.execute("SELECT url FROM jobs")}
            self.assertEqual(urls, {"https://x/1", "https://x/4"})
            self.assertTrue(all(r["is_remote"] for r in conn.execute("SELECT is_remote FROM jobs")))

    def test_include_onsite_keeps_everything(self):
        frames = [frame(job("Quantum Engineer", "https://x/3", location="Sydney, NSW", flagged=False))]
        stats = self._run(RecordingScraper(frames), max_queries=1, is_remote=False)
        self.assertEqual(stats["new"], 1)

    def test_raised_errors_are_counted_retried_and_do_not_stop_the_run(self):
        calls = []

        def flaky(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RuntimeError("blocked")
            return pd.DataFrame()

        stats = self._run(flaky, max_queries=1)
        self.assertEqual(stats["site_errors"], 1)
        self.assertEqual(len(calls), 3)

    def test_logged_site_failure_triggers_one_retry_that_recovers_the_jobs(self):
        import logging

        calls = []

        def swallowed(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                logging.getLogger("jobspy.indeed").error("Max retries exceeded")
                return pd.DataFrame()
            if kwargs["site_name"] == ["indeed"]:
                return frame(job("Quantum Engineer", "https://x/9", flagged=True))
            return pd.DataFrame()

        stats = self._run(swallowed, max_queries=1)
        self.assertEqual(stats["site_errors"], 1)
        self.assertEqual(stats["new"], 1)

    def test_irrelevant_postings_are_skipped_when_keywords_are_configured(self):
        cfg = {"jobspy": {**CONFIG["jobspy"], "require_keywords": ["quantum", "qiskit"]}}
        self.cfg.write_text(yaml.safe_dump(cfg))
        frames = [frame(
            job("Account Manager", "https://x/1", flagged=True, description="Sell enterprise software."),
            job("Backend Engineer", "https://x/2", flagged=True, description="Work on Qiskit tooling."),
            job("Quantum Developer", "https://x/3", flagged=True, description=""),
        )]
        stats = self._run(RecordingScraper(frames), max_queries=1)
        self.assertEqual(stats["irrelevant_skipped"], 1)
        self.assertEqual(stats["new"], 2)

    def test_location_and_region_filters(self):
        config = CONFIG["jobspy"]
        only = ingest_jobspy.build_queries(config, region_names=["australia"])
        self.assertEqual({q["region"]["name"] for q in only}, {"Australia"})
        adhoc = ingest_jobspy.build_queries(config, term="X", location="Chile")
        self.assertEqual(adhoc[0]["region"]["location"], "Chile")
        self.assertEqual(len(ingest_jobspy.build_queries({}, None)), 1)


class RemoteDetectionTests(unittest.TestCase):
    def test_signals(self):
        f = ingest_jobspy.is_remote_posting
        self.assertTrue(f("Engineer", "Remote - EU", "", False))
        self.assertTrue(f("Engineer", "Berlin", "", True))
        self.assertTrue(f("Engineer", "Berlin", "This is a fully remote role.", False))
        self.assertFalse(f("Engineer", "Berlin", "Office based.", False))
        self.assertFalse(f("Engineer (Hybrid)", "Remote", "", True))
        self.assertFalse(f("Engineer", "Berlin (On-site)", "", True))


class RepoConfigTests(unittest.TestCase):
    def test_repo_config_regions_are_complete_and_valid_for_jobspy(self):
        from jobspy.model import Country

        cfg = ingest_jobspy.load_config()
        regions = cfg["regions"]
        names = {r["name"] for r in regions}
        self.assertIn("Australia", names)
        self.assertGreaterEqual(len(regions), 5)
        self.assertNotIn("google", cfg["sites"])
        self.assertIn("quantum", cfg["require_keywords"])
        for r in regions:
            Country(r["country_indeed"])
        self.assertEqual(cfg["hours_old"], 120)
        self.assertNotIn("remote_only", cfg)


if __name__ == "__main__":
    unittest.main()

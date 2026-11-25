"""Tests for tools/apply_progress.py - the /apply progress log the dashboard reads.

The tool keys each /apply run (a LinkedIn job ID when the URL carries one,
else a URL hash, else the --id of a pasted posting) and turns a dashboard job
ID back into a posting URL for `/apply <id>`. These pin the parts a silent
regression would break: which URLs count as LinkedIn, that a run keeps one id
across URL variants, which record `resolve` answers from (including the
re-application and pasted-text cases), and UTF-8 output on a non-UTF-8 host.

Every test works on a throwaway workspace: the CLI tests copy the script into
a temp repo root (the tool resolves its state files from its own location),
so nothing here reads or writes the real job_scraper/ state.
"""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "apply_progress.py"
sys.path.insert(0, str(REPO / "tools"))
import apply_progress  # noqa: E402

# Every URL shape the LinkedIn rule must decide, with the answer both implementations must give.
LINKEDIN_CASES = [
    ("https://www.linkedin.com/jobs/view/4312345678/", "4312345678"),
    ("https://sa.linkedin.com/jobs/view/backend-engineer-at-acme-4312345678", "4312345678"),
    ("https://www.linkedin.com/jobs/view/4312345678?refId=abc&trk=x", "4312345678"),
    ("https://linkedin.com/jobs/view/4312345678/#apply", "4312345678"),
    ("  https://www.linkedin.com/jobs/view/4312345678  ", "4312345678"),
    ("https://www.linkedin.com/comm/jobs/view/4312345678/", "4312345678"),
    ("https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4345841651", "4345841651"),
    ("https://www.linkedin.com/jobs/search/?currentJobId=4312345678", "4312345678"),
    # The old end-of-string rule read the geoId here, not the job.
    ("https://www.linkedin.com/jobs/search/?currentJobId=4312345678&geoId=103644278", "4312345678"),
    ("https://www.linkedin.com/jobs/collections/recommended/?currentJobId=4312345678", "4312345678"),
    # LinkedIn, but not a posting: numeric company pages, query-only digits, profiles.
    ("https://www.linkedin.com/company/1234567/", None),
    ("https://www.linkedin.com/company/acme/", None),
    ("https://www.linkedin.com/feed/?x=1234567", None),
    ("https://www.linkedin.com/jobs/search/?geoId=103644278", None),
    ("https://www.linkedin.com/in/someone-1234567/", None),
    ("https://www.linkedin.com/feed/?currentJobId=4312345678", None),
    # Not LinkedIn at all.
    ("https://notlinkedin.com/jobs/view/4312345678/", None),
    ("https://linkedin.com.evil.example/jobs/view/4312345678/", None),
    ("https://careers.example.com/redirect?to=linkedin.com/jobs/view/4312345678", None),
    ("https://example.com/linkedin.com/jobs/view/4312345678", None),
    ("", None),
]

TRACKER_FIELDS = ["date", "company", "sector", "role", "role_type", "channel", "status", "contact_person",
                  "fit_rating", "notes", "cv_file", "cover_letter_file", "source", "deadline"]


class Workspace:
    """A temp repo root holding a copy of the tool and its state files."""

    def __init__(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "tools").mkdir()
        (self.dir / "job_scraper").mkdir()
        shutil.copy(TOOL, self.dir / "tools" / "apply_progress.py")

    def seen(self, entries: dict):
        (self.dir / "job_scraper" / "seen_jobs.json").write_text(json.dumps({"seen": entries}), encoding="utf-8")

    def tracker(self, rows: list[dict]):
        with open(self.dir / "job_search_tracker.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=TRACKER_FIELDS)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in TRACKER_FIELDS})

    def run(self, *args, env_extra=None):
        env = {**os.environ, **(env_extra or {})}
        return subprocess.run([sys.executable, str(self.dir / "tools" / "apply_progress.py"), *args],
                              capture_output=True, env=env)

    def state(self) -> dict:
        return json.loads((self.dir / "job_scraper" / "apply_progress.json").read_text(encoding="utf-8"))

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class LinkedinId(unittest.TestCase):
    def test_url_variants_of_one_posting_share_an_id(self):
        for url in (
            "https://www.linkedin.com/jobs/view/4312345678/",
            "https://sa.linkedin.com/jobs/view/backend-engineer-at-acme-4312345678",
            "https://www.linkedin.com/jobs/view/4312345678?refId=abc&trk=x",
            "https://linkedin.com/jobs/view/4312345678/#apply",
            "  https://www.linkedin.com/jobs/view/4312345678  ",
        ):
            with self.subTest(url=url):
                self.assertEqual(apply_progress.linkedin_id(url), "4312345678")

    def test_look_alike_and_embedded_hosts_are_not_linkedin(self):
        for url in (
            "https://notlinkedin.com/jobs/view/4312345678/",
            "https://linkedin.com.evil.example/jobs/view/4312345678/",
            "https://careers.example.com/redirect?to=linkedin.com/jobs/view/4312345678",
            "https://example.com/linkedin.com/jobs/view/4312345678",
        ):
            with self.subTest(url=url):
                self.assertIsNone(apply_progress.linkedin_id(url))

    def test_no_url_or_no_job_id(self):
        self.assertIsNone(apply_progress.linkedin_id(None))
        self.assertIsNone(apply_progress.linkedin_id(""))
        self.assertIsNone(apply_progress.linkedin_id("https://www.linkedin.com/company/acme/"))

    def test_numeric_non_posting_pages_are_not_postings(self):
        """A company page or a query value that ends in digits is not a job:
        keying it as li-<digits> would merge it with an unrelated /apply run."""
        for url in ("https://www.linkedin.com/company/1234567/",
                    "https://www.linkedin.com/feed/?x=1234567",
                    "https://www.linkedin.com/in/someone-1234567/"):
            with self.subTest(url=url):
                self.assertIsNone(apply_progress.linkedin_id(url))

    def test_current_job_id_wins_over_other_query_digits(self):
        self.assertEqual(apply_progress.linkedin_id(
            "https://www.linkedin.com/jobs/search/?currentJobId=4312345678&geoId=103644278"), "4312345678")

    def test_every_case(self):
        for url, want in LINKEDIN_CASES:
            with self.subTest(url=url):
                self.assertEqual(apply_progress.linkedin_id(url), want)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class DashboardParity(unittest.TestCase):
    """dashboard.html joins /apply runs to cards with its own linkedinId(); if it
    disagrees with linkedin_id() here, a run keyed by the tool lands on the wrong
    card (or none). Run the page's actual function in Node over every case."""

    def test_dashboard_linkedin_id_matches_the_tool(self):
        html = (REPO / "dashboard.html").read_text(encoding="utf-8")
        start = html.index("const LI_POSTING_PATH")
        end = html.index("\n};", html.index("const linkedinId")) + 3
        script = (html[start:end] + "\nconst cases = JSON.parse(process.argv[1]);"
                  "\nprocess.stdout.write(JSON.stringify(cases.map(linkedinId)));")
        urls = [u for u, _ in LINKEDIN_CASES]
        out = subprocess.run(["node", "-e", script, json.dumps(urls)], capture_output=True, text=True, check=True)
        got = json.loads(out.stdout)
        for (url, want), js in zip(LINKEDIN_CASES, got):
            with self.subTest(url=url):
                self.assertEqual(js, apply_progress.linkedin_id(url))
                self.assertEqual(js, want)


class RunId(unittest.TestCase):
    def test_linkedin_variants_are_one_run(self):
        a = apply_progress.run_id("https://www.linkedin.com/jobs/view/4312345678/", None)
        b = apply_progress.run_id("https://sa.linkedin.com/jobs/view/backend-at-acme-4312345678?trk=x", None)
        self.assertEqual(a, "li-4312345678")
        self.assertEqual(a, b)

    def test_other_urls_hash_stably_ignoring_case_and_fragment(self):
        a = apply_progress.run_id("https://careers.example.com/jobs/123", None)
        b = apply_progress.run_id("https://Careers.Example.com/jobs/123#apply", None)
        self.assertTrue(a.startswith("url-"))
        self.assertEqual(a, b)
        self.assertEqual(a, apply_progress.run_id("https://careers.example.com/jobs/123", None))

    def test_look_alike_host_is_hashed_not_keyed_as_linkedin(self):
        rid = apply_progress.run_id("https://notlinkedin.com/jobs/view/4312345678/", None)
        self.assertTrue(rid.startswith("url-"))

    def test_explicit_id_wins(self):
        self.assertEqual(apply_progress.run_id("https://www.linkedin.com/jobs/view/4312345678/", "acme_backend"),
                         "acme_backend")

    def test_no_url_and_no_id_exits(self):
        with self.assertRaises(SystemExit):
            apply_progress.run_id(None, None)


class PickRow(unittest.TestCase):
    def test_open_row_beats_a_newer_closed_one(self):
        rows = [{"date": "2026-01-10", "status": "applied", "source": "new"},
                {"date": "2026-03-01", "status": "rejected", "source": "old"}]
        self.assertEqual(apply_progress.pick_row(rows)["source"], "new")

    def test_latest_date_among_closed_rows(self):
        rows = [{"date": "2026-03-01", "status": "rejected", "source": "later"},
                {"date": "2025-11-01", "status": "no response", "source": "earlier"}]
        self.assertEqual(apply_progress.pick_row(rows)["source"], "later")

    def test_last_appended_breaks_a_date_tie(self):
        rows = [{"date": "2026-03-01", "status": "drafted", "source": "first"},
                {"date": "2026-03-01", "status": "drafted", "source": "second"}]
        self.assertEqual(apply_progress.pick_row(rows)["source"], "second")

    def test_empty(self):
        self.assertIsNone(apply_progress.pick_row([]))


class Cli(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)

    def resolve(self, jid):
        p = self.ws.run("resolve", jid)
        return p.returncode, (json.loads(p.stdout) if p.returncode == 0 else None), p.stderr.decode("utf-8", "replace")

    def test_set_creates_one_run_across_url_variants(self):
        self.ws.seen({})
        self.ws.run("set", "--url", "https://www.linkedin.com/jobs/view/4312345678/", "--stage", "fetching")
        self.ws.run("set", "--url", "https://sa.linkedin.com/jobs/view/x-4312345678", "--stage", "evaluating",
                    "--company", "Acme", "--role", "Backend Engineer")
        runs = self.ws.state()["runs"]
        self.assertEqual(list(runs), ["li-4312345678"])
        self.assertEqual([h["stage"] for h in runs["li-4312345678"]["history"]], ["fetching", "evaluating"])

    def test_resolve_seen_jobs_key(self):
        self.ws.seen({"acme_backend-engineer": {"url": "https://careers.example.com/1", "company": "Acme",
                                                 "title": "Backend Engineer"}})
        code, hit, _ = self.resolve("acme_backend-engineer")
        self.assertEqual(code, 0)
        self.assertEqual((hit["source"], hit["url"]), ("seen_jobs", "https://careers.example.com/1"))

    def test_resolve_run_id(self):
        self.ws.seen({})
        self.ws.run("set", "--url", "https://careers.example.com/jobs/9", "--stage", "fetching")
        rid = next(iter(self.ws.state()["runs"]))
        code, hit, _ = self.resolve(rid)
        self.assertEqual(code, 0)
        self.assertEqual((hit["source"], hit["url"]), ("apply_progress", "https://careers.example.com/jobs/9"))

    def test_resolve_bare_linkedin_id(self):
        self.ws.seen({})
        code, hit, _ = self.resolve("4312345678")
        self.assertEqual(code, 0)
        self.assertEqual(hit["url"], "https://www.linkedin.com/jobs/view/4312345678/")

    def test_resolve_tracker_slug_prefers_the_open_reapplication(self):
        self.ws.seen({})
        self.ws.tracker([
            {"date": "2026-01-05", "company": "Acme", "role": "Backend Engineer", "status": "rejected",
             "source": "https://careers.example.com/old"},
            {"date": "2026-06-01", "company": "Acme", "role": "Backend Engineer", "status": "applied",
             "source": "https://careers.example.com/new"},
        ])
        code, hit, _ = self.resolve("acme_backend_engineer")
        self.assertEqual(code, 0)
        self.assertEqual((hit["source"], hit["status"], hit["url"]),
                         ("tracker", "applied", "https://careers.example.com/new"))

    def test_scraped_job_already_tracked_reports_the_current_row(self):
        self.ws.seen({"acme_backend-engineer": {"url": "https://careers.example.com/new", "company": "Acme",
                                                 "title": "Backend Engineer"}})
        self.ws.tracker([
            {"date": "2026-06-01", "company": "Acme", "role": "Backend Engineer", "status": "interview"},
            {"date": "2026-01-05", "company": "Acme", "role": "Backend Engineer", "status": "rejected"},
        ])
        code, hit, _ = self.resolve("acme_backend-engineer")
        self.assertEqual(code, 0)
        self.assertEqual((hit["source"], hit["status"], hit["tracked_on"]), ("tracker", "interview", "2026-06-01"))

    def test_resolve_pasted_run_without_url_explains_itself(self):
        self.ws.seen({})
        self.ws.run("set", "--id", "acme_backend", "--stage", "evaluating", "--company", "Acme", "--role", "Backend")
        code, _, err = self.resolve("acme_backend")
        self.assertEqual(code, 1)
        self.assertIn("pasted posting text", err)

    def test_resolve_pasted_run_falls_back_to_its_tracker_row(self):
        self.ws.seen({})
        self.ws.run("set", "--id", "acme_backend", "--stage", "drafted", "--company", "Acme", "--role", "Backend")
        self.ws.tracker([{"date": "2026-06-01", "company": "Acme", "role": "Backend", "status": "drafted"}])
        code, hit, _ = self.resolve("acme_backend")
        self.assertEqual(code, 0)
        self.assertEqual((hit["source"], hit["status"]), ("tracker", "drafted"))

    def test_resolve_unknown_id(self):
        self.ws.seen({})
        code, _, err = self.resolve("nobody_nothing")
        self.assertEqual(code, 1)
        self.assertIn("no job with id", err)

    def test_output_is_utf8_on_a_non_utf8_host(self):
        """A piped stdout on Windows defaults to cp1252; without the guard a
        company name outside it raised UnicodeEncodeError."""
        self.ws.seen({"x": {"url": "https://careers.example.com/1", "company": "شركة المثال", "title": "مهندس"}})
        p = self.ws.run("resolve", "x", env_extra={"PYTHONIOENCODING": "cp1252"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout.decode("utf-8"))["company"], "شركة المثال")
        p = self.ws.run("set", "--url", "https://careers.example.com/1", "--stage", "fetching",
                        "--company", "شركة المثال", "--role", "مهندس", env_extra={"PYTHONIOENCODING": "cp1252"})
        self.assertEqual(p.returncode, 0, p.stderr)
        p = self.ws.run("list", env_extra={"PYTHONIOENCODING": "cp1252"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("شركة المثال", p.stdout.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()

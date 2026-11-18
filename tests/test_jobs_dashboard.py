"""tools/jobs_dashboard.py shows application status from the tracker, read only.

job_search_tracker.csv belongs to /apply, /outcome and /gmail-sync. The dashboard
matches each job to its row the way /scrape does (posting URL, then company +
role) and must never write: there is no write endpoint, and serving the page
leaves the tracker byte-identical.
"""

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import jobs_dashboard as dash  # noqa: E402

HEADER = "date,company,sector,role,role_type,channel,status,contact_person,fit_rating,notes,cv_file,cover_letter_file,source,deadline\n"
RIPPLING = ('2026-11-12,Rippling,HR/payroll fintech,Staff Software Engineer - Payments,Staff Engineer (IC),online,applied,,77,'
            '"Tax Payment team, Bengaluru; applied 2026-11-12",cv/main_rippling.tex,cover_letters/cover_rippling.tex,'
            'https://ats.rippling.com/rippling/jobs/1,\n')
DRAFT = ("2026-11-10,Flipkart,,Senior Engineering Manager - Payments,,online,drafted,,80,drafted 2026-11-10,"
         "cv/main_flipkart.tex,cover_letters/cover_flipkart.tex,https://www.instahyre.com/job-462828/,2026-12-01\n")
REFOLD = "2026-11-12,Refold AI,,Head of Engineering,,portal,no response,,80,applied 2026-11-12,,,,\n"


class DashboardStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.state, self.tracker, self.notes = d / "seen_jobs.json", d / "job_search_tracker.csv", d / "job_notes.json"
        self.state.write_text(json.dumps({"seen": {
            "flipkart": {"title": "Senior Engineering Manager - Payments", "company": "Flipkart",
                         "url": "https://www.instahyre.com/job-462828", "status": "new", "fit": "high"},
            "rippling": {"title": "Staff Software Engineer - Payments", "company": "Rippling",
                         "url": "https://other-board.example/rippling-1", "status": "ranked", "fit": "high"},
            "refold": {"title": "Head of Engineering", "company": "Refold AI", "url": "https://x.example/refold", "fit": "high"},
            "cashfree": {"title": "Associate Director Engineering", "company": "Cashfree",
                         "url": "https://www.linkedin.com/jobs/view/1", "status": "new", "fit": "high"},
        }}), encoding="utf-8")
        self.tracker.write_text(HEADER + RIPPLING + DRAFT + REFOLD, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def jobs(self):
        return {j["key"]: j for j in dash.load_jobs(self.state, self.notes, self.tracker)}

    def test_matches_by_url_then_company_and_role(self):
        jobs = self.jobs()
        self.assertEqual(jobs["flipkart"]["application"], {"status": "drafted", "date": "2026-11-10"})  # URL, trailing slash ignored
        self.assertEqual(jobs["rippling"]["application"]["status"], "applied")  # company + role
        self.assertNotIn("application", jobs["cashfree"])

    def test_legacy_spelling_reads_as_canonical(self):
        self.assertEqual(self.jobs()["refold"]["application"]["status"], "no_response")

    def test_loading_never_writes_the_tracker(self):
        before = self.tracker.read_bytes()
        self.jobs()
        self.assertEqual(self.tracker.read_bytes(), before)

    def test_unparseable_tracker_shows_no_statuses_instead_of_failing(self):
        self.tracker.write_text(HEADER + '2026-11-01,Acme,,EM,,,applied,,,"line one\nline two",,,,\n', encoding="utf-8")
        jobs = self.jobs()
        self.assertEqual(len(jobs), 4)
        self.assertFalse(any("application" in j for j in jobs.values()))

    def test_missing_tracker_shows_no_statuses(self):
        self.tracker.unlink()
        self.assertFalse(any("application" in j for j in self.jobs().values()))

    def test_no_write_function_exists(self):
        self.assertFalse(hasattr(dash, "set_status"))


class DashboardServerTests(unittest.TestCase):
    """The server only serves GET; any write method is refused."""

    @classmethod
    def setUpClass(cls):
        cls.server = dash.ThreadingHTTPServer(("127.0.0.1", 0), dash.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def request(self, method, path="/api/status"):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=b'{"key":"x","status":"applied"}',
                                     headers={"Content-Type": "application/json", "X-Dashboard": "1"}, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status
        except urllib.error.HTTPError as e:
            e.close()
            return e.code

    def test_write_methods_are_not_supported(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                self.assertEqual(self.request(method), 501)

    def test_page_has_no_status_editor(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/") as r:
            page = r.read().decode("utf-8")
        self.assertNotIn("/api/status", page)
        self.assertNotIn("<select class=\"app", page)


if __name__ == "__main__":
    unittest.main()

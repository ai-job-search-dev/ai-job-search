"""tools/jobs_dashboard.py: application status read from and written to the tracker.

The tracker CSV is the one application record /outcome, /rank and /scrape read,
so the dashboard must follow /outcome's rules: canonical statuses, touch only the
matching row, leave every other line byte-identical, and move a row off
`drafted` with the submission date.
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
            "cashfree": {"title": "Associate Director Engineering", "company": "Cashfree",
                         "url": "https://www.linkedin.com/jobs/view/1", "status": "new", "fit": "high",
                         "rank_score": 81, "deadline": "2026-12-15"},
        }}), encoding="utf-8")
        self.tracker.write_text(HEADER + RIPPLING + DRAFT, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def set(self, key, status, today="2026-11-18"):
        return dash.set_status(key, status, state=self.state, tracker=self.tracker, today=today)

    def lines(self):
        return self.tracker.read_text(encoding="utf-8").splitlines(keepends=True)

    def test_load_matches_by_url_then_company_and_role(self):
        jobs = {j["key"]: j for j in dash.load_jobs(self.state, self.notes, self.tracker)}
        self.assertEqual(jobs["flipkart"]["application"], {"status": "drafted", "date": "2026-11-10"})  # URL, trailing slash ignored
        self.assertEqual(jobs["rippling"]["application"]["status"], "applied")  # company + role
        self.assertNotIn("application", jobs["cashfree"])

    def test_update_touches_only_the_matching_line(self):
        before = self.lines()
        result = self.set("rippling", "interview")
        after = self.lines()
        self.assertEqual(result, {"status": "interview", "date": "2026-11-12", "created": False})
        self.assertEqual(after[0], before[0])
        self.assertEqual(after[2], before[2])  # the Flipkart line is byte-identical
        self.assertIn(",interview,", after[1])
        self.assertIn('"Tax Payment team, Bengaluru; applied 2026-11-12; applied -> interview via dashboard 2026-11-18"', after[1])
        self.assertIn("2026-11-12,Rippling", after[1])  # date unchanged when not leaving drafted

    def test_leaving_drafted_sets_the_submission_date(self):
        self.set("flipkart", "applied")
        row = self.lines()[2]
        self.assertTrue(row.startswith("2026-11-18,Flipkart,"))
        self.assertIn(",applied,", row)
        self.assertTrue(row.rstrip("\n").endswith(",2026-12-01"))  # deadline preserved

    def test_no_row_appends_one_from_the_job(self):
        result = self.set("cashfree", "applied")
        self.assertTrue(result["created"])
        new = self.lines()[-1]
        self.assertEqual(new, "2026-11-18,Cashfree,,Associate Director Engineering,,,applied,,81,"
                              "applied via dashboard 2026-11-18,,,https://www.linkedin.com/jobs/view/1,2026-12-15\n")
        self.assertEqual(len(self.lines()), 4)

    def test_accepted_rejected_and_legacy_spellings(self):
        self.set("rippling", "hired")
        self.assertIn(",hired,", self.lines()[1])
        self.set("rippling", "No Response")
        self.assertIn(",no_response,", self.lines()[1])

    def test_unknown_status_and_unknown_job_are_refused(self):
        before = self.tracker.read_bytes()
        with self.assertRaises(dash.TrackerError):
            self.set("rippling", "accepted")
        with self.assertRaises(dash.TrackerError) as ctx:
            self.set("nope", "applied")
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(self.tracker.read_bytes(), before)

    def test_multiline_field_refuses_to_write(self):
        self.tracker.write_text(HEADER + '2026-11-01,Acme,,EM,,,applied,,,"line one\nline two",,,,\n', encoding="utf-8")
        before = self.tracker.read_bytes()
        with self.assertRaises(dash.TrackerError) as ctx:
            self.set("cashfree", "applied")
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(self.tracker.read_bytes(), before)

    def test_missing_tracker_is_created_with_the_header(self):
        self.tracker.unlink()
        self.set("cashfree", "drafted")
        lines = self.lines()
        self.assertEqual(lines[0], HEADER)
        self.assertIn(",drafted,", lines[1])

    def test_same_status_is_a_no_op(self):
        before = self.tracker.read_bytes()
        self.assertFalse(self.set("rippling", "applied")["created"])
        self.assertEqual(self.tracker.read_bytes(), before)


class DashboardServerTests(unittest.TestCase):
    """POST /api/status accepts only same-origin JSON from the page."""

    @classmethod
    def setUpClass(cls):
        cls.server = dash.ThreadingHTTPServer(("127.0.0.1", 0), dash.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def post(self, headers):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/status", data=b'{"key":"x","status":"applied"}',
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status
        except urllib.error.HTTPError as e:
            e.close()
            return e.code

    def test_cross_site_and_simple_requests_are_forbidden(self):
        self.assertEqual(self.post({"Content-Type": "text/plain"}), 403)
        self.assertEqual(self.post({"Content-Type": "application/json", "X-Dashboard": "1", "Origin": "https://evil.example"}), 403)
        self.assertEqual(self.post({"Content-Type": "application/json"}), 403)

    def test_same_origin_reaches_validation(self):
        original = dash.set_status
        dash.set_status = lambda key, status: (_ for _ in ()).throw(dash.TrackerError("that job is not in seen_jobs.json", 404))
        try:
            code = self.post({"Content-Type": "application/json", "X-Dashboard": "1", "Origin": f"http://127.0.0.1:{self.port}"})
        finally:
            dash.set_status = original
        self.assertEqual(code, 404)


if __name__ == "__main__":
    unittest.main()

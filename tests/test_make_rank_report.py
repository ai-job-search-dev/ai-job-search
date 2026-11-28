"""Tests for tools/make_rank_report.py - /rank's HTML report.

/rank persists scores, verdicts, vetoes, strengths and gaps into
job_scraper/seen_jobs.json (Step 4), but the only place a user could read them
was the Step 5 terminal output of a single run. The report renders the whole
ranked backlog as one offline page. These tests drive the documented CLI
invocation against a temporary state file and tracker and pin:

- which section a job lands in (shortlist vs below threshold, with the
  location/language vetoes /rank Step 3 defines);
- that jobs scored in this run are marked New, from the results file passed
  to rank_state.py apply (whose `status` defaults to "scored");
- that untrusted posting text is escaped and only http(s) links are rendered;
- the tracker cross-reference ("Applied" badge, applications table);
- that the tool writes the report and nothing else.
"""
import csv
import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "make_rank_report.py"
TRACKER_HEADER = ["date", "company", "sector", "role", "role_type", "channel", "status", "contact_person",
                  "fit_rating", "notes", "cv_file", "cover_letter_file", "source", "deadline"]


def ranked(**over):
    base = {
        "title": "Platform Engineer",
        "company": "Acme",
        "url": "https://example.com/jobs/1",
        "location": "Remote",
        "first_seen": "2026-09-01",
        "status": "ranked",
        "portal": "linkedin-search",
        "rank_score": 80,
        "rank_verdict": "Strong Fit",
        "rank_date": "2026-09-02",
        "location_verdict": "PASS",
        "language_gate": "PASS",
        "strengths": ["Direct match on core stack"],
        "gaps": ["No domain experience"],
    }
    base.update(over)
    return base


def section(page: str, sid: str) -> str:
    start = page.index(f'<section class="sec" id="{sid}">')
    end = page.find("<section", start + 1)
    return page[start:end if end != -1 else len(page)]


class MakeRankReport(unittest.TestCase):
    def run_tool(self, seen: dict, tracker_rows=None, results=None):
        tmp = self.tmp
        state = tmp / "seen_jobs.json"
        state.write_text(json.dumps({"seen": seen}), encoding="utf-8")
        args = [sys.executable, str(TOOL), "--state", str(state), "--out-dir", str(tmp / "reports"),
                "--tracker", str(tmp / "job_search_tracker.csv")]
        if tracker_rows is not None:
            with open(tmp / "job_search_tracker.csv", "w", encoding="utf-8", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=TRACKER_HEADER)
                w.writeheader()
                for row in tracker_rows:
                    w.writerow({k: row.get(k, "") for k in TRACKER_HEADER})
        if results is not None:
            (tmp / "results.json").write_text(json.dumps(results), encoding="utf-8")
            args += ["--run-results", str(tmp / "results.json")]
        proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        summary = json.loads(proc.stdout)
        page = Path(summary["report"]).read_text(encoding="utf-8")
        return summary, page

    def setUp(self):
        self._dir = TemporaryDirectory()
        self.tmp = Path(self._dir.name)

    def tearDown(self):
        self._dir.cleanup()

    def test_shortlist_requires_good_or_strong_fit_without_a_veto(self):
        seen = {
            "strong": ranked(title="Strong Role"),
            "good-loc-fail": ranked(title="Relocation Role", rank_verdict="Good Fit", location_verdict="FAIL"),
            "lang-fail": ranked(title="Polish Role", language_gate="FAIL", language_note="fluent Polish required"),
            "moderate": ranked(title="Moderate Role", rank_score=50, rank_verdict="Moderate Fit"),
            "skipped": ranked(title="Never Ranked", status="skipped"),
        }
        summary, page = self.run_tool(seen)
        short, below = section(page, "shortlist"), section(page, "below-threshold")
        self.assertIn("Strong Role", short)
        for title in ("Relocation Role", "Polish Role", "Moderate Role"):
            self.assertNotIn(title, short)
            self.assertIn(title, below)
        self.assertIn("location FAIL", below)
        self.assertIn("language FAIL: fluent Polish required", below)
        # Unranked entries never reach the ranked tables, only the all-scraped table.
        self.assertNotIn("Never Ranked", short + below)
        self.assertIn("Never Ranked", section(page, "all-scraped"))
        self.assertEqual(summary["ranked"], 4)
        self.assertEqual(summary["all_scraped"], 5)

    def test_flags_are_shown_but_never_veto(self):
        seen = {"flagged": ranked(title="Flagged Role", location_verdict="FLAG", language_gate="FLAG",
                                  language_note="business French preferred", guard="hidden block removed")}
        _, page = self.run_tool(seen)
        short = section(page, "shortlist")
        self.assertIn("Flagged Role", short)
        self.assertIn("location FLAG", short)
        self.assertIn("language FLAG: business French preferred", short)
        self.assertIn("posting guard: hidden block removed", short)

    def test_legacy_location_verdict_is_read_and_never_shown_as_a_place(self):
        legacy = ranked(title="Legacy Role", location="FAIL")
        del legacy["location_verdict"]
        _, page = self.run_tool({"legacy": legacy})
        below = section(page, "below-threshold")
        self.assertIn("Legacy Role", below)
        self.assertIn("location FAIL", below)
        self.assertNotIn('<td data-sort="FAIL">FAIL</td>', below)

    def test_new_marks_come_from_the_run_results_file(self):
        seen = {"a": ranked(title="Scored Now"), "b": ranked(title="Scored Earlier", company="Beta")}
        # rank_state.py treats a result without `status` as scored; expired ones are not New.
        results = [{"key": "a", "score": 80}, {"key": "b", "status": "expired"}]
        summary, page = self.run_tool(seen, results=results)
        self.assertEqual(summary["new"], 1)
        short = section(page, "shortlist")
        now_row = short[short.index("Scored Now") - 600: short.index("Scored Now")]
        self.assertIn('badge new', now_row)
        self.assertLess(short.index("Scored Now"), short.index("Scored Earlier"), "new jobs sort first")

    def test_without_results_file_the_latest_rank_date_is_new(self):
        seen = {"old": ranked(title="Old Run", rank_date="2026-08-01"),
                "new": ranked(title="Latest Run", company="Beta", rank_date="2026-09-02")}
        summary, _ = self.run_tool(seen)
        self.assertEqual(summary["new"], 1)

    def test_posting_text_is_escaped_and_only_http_links_render(self):
        seen = {
            "xss": ranked(title='<script>alert(1)</script>', company='"Evil" & Co',
                          strengths=['<img src=x onerror=alert(1)>'], url="javascript:alert(1)"),
            "ok": ranked(title="Safe Role", company="Beta", url="https://example.com/jobs/2"),
        }
        _, page = self.run_tool(seen)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertNotIn("<img src=x", page)
        self.assertNotIn('href="javascript:', page)
        self.assertIn('href="https://example.com/jobs/2"', page)

    def test_applied_badge_and_applications_table_from_the_tracker(self):
        seen = {"acme": ranked(title="Senior Platform Engineer", company="Acme Corp")}
        tracker = [{"date": "2026-09-03", "company": "Acme Corp", "role": "Senior Platform Engineer",
                    "status": "applied", "fit_rating": "77", "source": "https://example.com/jobs/1"}]
        _, page = self.run_tool(seen, tracker_rows=tracker)
        self.assertIn("Applied &middot; applied", section(page, "shortlist"))
        apps = section(page, "applications")
        self.assertIn("Senior Platform Engineer", apps)
        self.assertIn('data-sort="77"', apps)

    def test_no_tracker_means_no_applications_table(self):
        _, page = self.run_tool({"a": ranked()})
        self.assertNotIn('id="applications"', page)

    def test_writes_only_the_single_report_page(self):
        state_before = json.dumps({"seen": {"a": ranked()}})
        self.run_tool({"a": ranked()})
        self.run_tool({"a": ranked()})  # rewritten in place, never a second file
        self.assertEqual(sorted(p.name for p in (self.tmp / "reports").iterdir()), ["job-ranking.html"])
        self.assertEqual((self.tmp / "seen_jobs.json").read_text(encoding="utf-8"), state_before)

    def test_missing_state_file_exits_with_a_hint(self):
        proc = subprocess.run([sys.executable, str(TOOL), "--state", str(self.tmp / "absent.json"),
                               "--out-dir", str(self.tmp / "reports")],
                              capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("run /scrape and /rank first", proc.stderr)


    def test_non_latin_company_and_title_still_match_the_tracker(self):
        """The normaliser behind tracker matching and search must keep Unicode
        letters: an ASCII-only one reduces these names to nothing."""
        seen = {"jp": ranked(title="インフラ エンジニア リーダー", company="株式会社サンプル")}
        tracker = [{"date": "2026-09-03", "company": "株式会社サンプル", "role": "インフラ エンジニア リーダー",
                    "status": "applied", "fit_rating": "70"}]
        _, page = self.run_tool(seen, tracker_rows=tracker)
        self.assertIn("Applied &middot; applied", section(page, "shortlist"))
        self.assertIn('data-q="インフラ エンジニア リーダー 株式会社サンプル"', page)
        self.assertIn(r"q.value.normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{M}\p{N}]+/gu", page,
                      "the in-page search must normalise the same way as the Python keys")

    def test_combining_marks_keep_different_names_apart(self):
        """Stripping combining marks would turn कला and कल into the same key and
        mark a different company's job as applied."""
        seen = {"kala": ranked(title="Platform Engineer Lead", company="कला Labs")}
        tracker = [{"company": "कल Labs", "role": "Platform Engineer Lead", "status": "applied"}]
        _, page = self.run_tool(seen, tracker_rows=tracker)
        self.assertNotIn("Applied &middot;", section(page, "shortlist"))

    def test_keys_lowercase_like_the_in_page_search(self):
        """casefold() would turn ß into ss while JS toLowerCase() keeps ß, so a
        query typed with the original spelling would stop matching."""
        _, page = self.run_tool({"de": ranked(title="Leiter IT", company="Großhandel GmbH")})
        self.assertIn('data-q="leiter it großhandel gmbh"', page)

    def test_all_scraped_lists_newest_first_then_highest_score(self):
        seen = {
            "low": ranked(title="Low Same Day", first_seen="2026-09-05", rank_score=40),
            "high": ranked(title="High Same Day", company="Beta", first_seen="2026-09-05", rank_score=90),
            "older": ranked(title="Older Day", company="Gamma", first_seen="2026-09-01", rank_score=99),
        }
        _, page = self.run_tool(seen)
        table = section(page, "all-scraped")
        order = [table.index(t) for t in ("High Same Day", "Low Same Day", "Older Day")]
        self.assertEqual(order, sorted(order))

    def test_summary_counts_drafted_rows_case_insensitively(self):
        tracker = [{"company": "Acme", "role": "Platform Engineer", "status": " Drafted "},
                   {"company": "Beta", "role": "Data Engineer", "status": "applied"}]
        _, page = self.run_tool({"a": ranked()}, tracker_rows=tracker)
        self.assertIn("1 applications sent", page)


class RankCommandRunsTheReport(unittest.TestCase):
    def setUp(self):
        self.text = (REPO / ".claude" / "commands" / "rank.md").read_text(encoding="utf-8")

    def test_step5_runs_the_report_with_this_runs_results(self):
        step5 = self.text[self.text.index("## Step 5: Present the Shortlist"):]
        self.assertIn("tools/make_rank_report.py --run-results", step5)
        self.assertIn("reports/job-ranking.html", step5)

    def test_sweep_only_path_runs_the_report_without_run_results_before_stopping(self):
        line = next(l for l in self.text.splitlines() if 'say "Nothing new to rank"' in l)
        tail = line[line.index("Then "):]
        self.assertIn("HTML report step without `--run-results`", tail)
        self.assertLess(tail.index("HTML report"), tail.index("stop"), "the report must run before the path stops")


if __name__ == "__main__":
    unittest.main()

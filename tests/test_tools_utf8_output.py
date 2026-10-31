"""The Python tools must write UTF-8 whatever the host's default encoding is.

On Windows a piped stdout (which is how Claude Code runs every tool) defaults
to the ANSI code page, cp1252 on most Western installs. A tool that prints a
posting title, company, CV line or file name outside that code page then dies
with UnicodeEncodeError before the workflow sees any output: `/rank` could not
list a single Cyrillic, CJK, Devanagari, Polish or Turkish posting.

Each case runs the real CLI in a child process with a legacy stdout forced via
PYTHONIOENCODING, so the Linux CI job reproduces what Windows users hit. The
child must exit cleanly and its bytes must decode as UTF-8.
"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    import openpyxl
except ImportError:
    openpyxl = None

REPO = Path(__file__).resolve().parent.parent
TOOLS = REPO / "tools"

# One name per script family that cp1252 cannot encode.
CYRILLIC = "Яндекс"
CJK = "腾讯"
POLISH = "Żabka Łódź"


def run_legacy_stdout(argv, cwd=None):
    """Run a tool the way a Western-locale Windows host pipes it."""
    env = dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONUTF8="0")
    return subprocess.run([sys.executable, *map(str, argv)], cwd=cwd, env=env, capture_output=True)


class ToolsWriteUtf8(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)

    def assert_clean_utf8(self, proc, expect_code, *needles, stream="stdout"):
        err = proc.stderr.decode("utf-8", "replace")
        self.assertNotIn("UnicodeEncodeError", err)
        self.assertEqual(proc.returncode, expect_code, err)
        text = getattr(proc, stream).decode("utf-8")
        for needle in needles:
            self.assertIn(needle, text)
        return text

    def write_state(self, seen):
        state = self.tmp / "seen_jobs.json"
        state.write_text(json.dumps({"version": 1, "seen": seen}, ensure_ascii=False), encoding="utf-8")
        return state

    @staticmethod
    def entry(company, title):
        return {
            "title": title, "company": company, "url": "https://example.com/jobs/123456",
            "portal": "example", "status": "new", "first_seen": "2026-09-20",
        }

    def test_rank_candidates_lists_non_latin_postings(self):
        state = self.write_state({"a": self.entry(CYRILLIC, "Инженер"), "b": self.entry(CJK, "工程师")})
        proc = run_legacy_stdout([TOOLS / "rank_state.py", "candidates", "--state", state, "--today", "2026-09-21"])
        out = json.loads(self.assert_clean_utf8(proc, 0, CYRILLIC, CJK))
        self.assertEqual(out["eligible"], 2)

    def test_rank_apply_prints_the_ranking_for_non_latin_postings(self):
        state = self.write_state({"a": self.entry(CJK, "工程师")})
        results = self.tmp / "results.json"
        results.write_text(json.dumps([{
            "key": "a", "status": "scored",
            "scores": {"technical": 80, "experience": 80, "behavioral": 80, "career": 80},
        }]), encoding="utf-8")
        proc = run_legacy_stdout([
            TOOLS / "rank_state.py", "apply", "--results", results, "--state", state, "--today", "2026-09-21",
        ])
        out = json.loads(self.assert_clean_utf8(proc, 0, CJK))
        self.assertEqual([row["key"] for row in out["ranked"]], ["a"])

    def test_job_key_audit_reports_non_latin_entries(self):
        state = self.write_state({f"{CYRILLIC}_Инженер": self.entry(CYRILLIC, "Инженер")})
        proc = run_legacy_stdout([TOOLS / "job_key.py", "--audit", state])
        self.assertNotIn(b"UnicodeEncodeError", proc.stderr)
        self.assertIn(CYRILLIC, proc.stdout.decode("utf-8"))

    def test_salary_lookup_prints_a_company_outside_cp1252(self):
        shutil.copy(REPO / "salary_lookup.py", self.tmp / "salary_lookup.py")
        (self.tmp / "salary_data.json").write_text(json.dumps({
            "metadata": {"source": "fixture", "index_baseline": 100, "index_label": "Index",
                         "baseline_description": "Index 100 = baseline"},
            "companies": [{"company": POLISH, "city": "Łódź",
                           "categories": {"all_employees": {"count": 50, "index": 104.0}}}],
        }, ensure_ascii=False), encoding="utf-8")
        proc = run_legacy_stdout([self.tmp / "salary_lookup.py", "Żabka"], cwd=self.tmp)
        self.assert_clean_utf8(proc, 0, POLISH)

    def test_verify_pdf_names_a_non_latin_file_in_its_error(self):
        missing = self.tmp / f"main_{CJK}_engineer.pdf"
        proc = run_legacy_stdout([TOOLS / "verify_pdf.py", missing])
        self.assert_clean_utf8(proc, 1, CJK, stream="stderr")

    def test_verify_layout_reports_a_non_latin_cv_line(self):
        # Poppler is not installed in the Python CI job, so feed main() synthetic
        # page geometry (as test_verify_layout.py does) but still in a child
        # process, because the encoding under test belongs to the process.
        driver = self.tmp / "drive_verify_layout.py"
        driver.write_text(
            "import sys\n"
            "from unittest.mock import patch\n"
            f"sys.path.insert(0, {str(REPO)!r})\n"
            "from tools import verify_layout as v\n"
            "A4 = 842.0\n"
            "def line(text, top, left=70.0, height=10.0):\n"
            "    return v.Line(top=top, bottom=top + height, left=left, height=height, text=text)\n"
            "pages = [\n"
            f"    v.Page(height=A4, lines=[line('Profil', 100.0), line({POLISH!r}, 700.0, left=60.0)]),\n"
            "    v.Page(height=A4, lines=[line('kontynuacja punktu', 80.0, left=90.0), line('dalej', 700.0, left=90.0)]),\n"
            "]\n"
            "with patch.object(v, 'parse_pdf', return_value=pages):\n"
            "    sys.argv = ['verify_layout.py', __file__]\n"
            "    sys.exit(v.main())\n",
            encoding="utf-8",
        )
        proc = run_legacy_stdout([driver])
        self.assertNotIn(b"UnicodeEncodeError", proc.stderr)
        self.assertIn(POLISH, proc.stdout.decode("utf-8"))

    @unittest.skipUnless(openpyxl, "requires optional openpyxl (installed in CI)")
    def test_salary_converter_names_a_non_latin_worksheet(self):
        workbook = self.tmp / "salary.xlsx"
        book = openpyxl.Workbook()
        try:
            book.active.title = CYRILLIC
            book.active.append(["Company", "City", "Count", "Index"])
            book.active.append([CYRILLIC, "Москва", 40, 101.5])
            book.save(workbook)
        finally:
            book.close()
        proc = run_legacy_stdout([
            TOOLS / "convert_salary_excel.py", workbook, "--output", self.tmp / "out.json",
        ])
        self.assert_clean_utf8(proc, 0, CYRILLIC)

    def test_upstream_triage_handles_non_latin_commit_subjects(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        tools_dir = repo / "tools"
        tools_dir.mkdir()
        shutil.copy(TOOLS / "upstream_triage.py", tools_dir / "upstream_triage.py")
        subprocess.run(["git", "init", "-b", "master"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "remote", "add", "upstream", "https://github.com/ai-job-search-dev/ai-job-search.git"], cwd=repo, check=True, capture_output=True)
        (repo / "file.txt").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
        (repo / "file.txt").write_text("change\n", encoding="utf-8")
        subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True, capture_output=True)
        msg = f"feat: add support for {CYRILLIC}"
        subprocess.run(["git", "commit", "-m", msg], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "update-ref", "refs/remotes/upstream/master", "HEAD"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "reset", "--hard", "HEAD~1"], cwd=repo, check=True, capture_output=True)
        proc = run_legacy_stdout([repo / "tools" / "upstream_triage.py"], cwd=repo)
        self.assert_clean_utf8(proc, 0, CYRILLIC)

    def test_fetch_page_prints_non_latin_text(self):
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path == "/robots.txt":
                    self.send_response(404)
                    self.end_headers()
                    return
                body = f"<html><body><h1>{POLISH}</h1><p>{CYRILLIC}</p></body></html>".encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            out = self.tmp / "saved.html"
            proc = run_legacy_stdout([
                TOOLS / "fetch_page.py", f"http://127.0.0.1:{port}/posting", "--text", "--out", out,
            ])
            self.assert_clean_utf8(proc, 0, POLISH, CYRILLIC)
        finally:
            server.shutdown()
            server.server_close()

    def test_robots_check_runs_cleanly(self):
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(404)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            proc = run_legacy_stdout([
                TOOLS / "robots_check.py", f"http://127.0.0.1:{port}/jobs/{POLISH}",
            ])
            self.assert_clean_utf8(proc, 0, "ALLOWED")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()

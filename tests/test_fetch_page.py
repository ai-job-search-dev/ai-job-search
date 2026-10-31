"""tools/fetch_page.py is the browser-header retry from 09-web-research.md.

Why this test exists: the retry used to be a documented `curl` invocation plus
an inline `python3 -c` tag-stripper, which meant the workflow needed a
`Bash(curl:*)` grant - and `curl` with a free tail is an upload channel. The
script replaces both with one GET-only command whose argv is fixed, that
enforces the robots.txt gate itself, and that refuses to write inside the
repo. These tests pin each of those properties against a real local server.
"""
import contextlib
import http.server
import io
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
import fetch_page  # noqa: E402

PAGE = (
    "<!doctype html><html><head><title>Posting</title>"
    "<style>.x{color:red}</style>"
    "<script>alert('tracking')</script></head>"
    "<body><!-- hidden comment -->"
    "<h1>Visible&nbsp;title</h1><p>Visible text &amp; more.</p>"
    "<noscript>noscript text</noscript><svg><text>svg text</text></svg>"
    "</body></html>"
)
KOREAN_PAGE = (
    '<html><head><meta http-equiv="Content-Type" content="text/html; charset=euc-kr">'
    "<title>채용공고</title></head><body><h1>백엔드 개발자 채용</h1></body></html>"
)
CONTENT_TYPES = {"/euc-kr.html": "text/html; charset=euc-kr"}


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (http.server naming)
        self.server.paths.append(self.path)
        if self.path in ("/ok.html", "/ok.html?q=a%20b", "/ok.html?t=%C3%A6%C3%B8%C3%A5") or (
            self.path.startswith("/unicode/") and self.path.endswith("?page=2")
        ):
            body = PAGE.encode("utf-8")
            self.send_response(200)
        elif self.path.startswith("/unicode/"):
            self.send_response(302)
            self.send_header("Location", "?page=2")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        elif self.path == "/redirect-utf8":
            # http.server writes header values as latin-1; preserve raw UTF-8 bytes.
            self.send_response(302)
            self.send_header("Location", "/ok.html?t=" + "æøå".encode("utf-8").decode("latin-1"))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        elif self.path == "/euc-kr.html":
            body = KOREAN_PAGE.encode("cp949")
            self.send_response(200)
        elif self.path == "/robots.txt":
            body = b"User-agent: *\nAllow: /redirect\nDisallow: /blocked\n"
            self.send_response(200)
        elif self.path in ("/redirect", "/redirect-allowed"):
            self.send_response(302)
            self.send_header("Location", "/blocked" if self.path == "/redirect" else "/ok.html")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        elif self.path == "/redirect-space":
            self.send_response(302)
            self.send_header("Location", "/ok.html?q=a b")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        elif self.path == "/redirect-other":
            self.send_response(302)
            self.send_header("Location", self.server.redirect_to)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        elif self.path == "/blocked":
            body = b"<html><body>blocked posting</body></html>"
            self.send_response(200)
        elif self.path == "/forbidden":
            body = b"<html><body>denied</body></html>"
            self.send_response(403)
        elif self.path in ("/loop1", "/loop2"):
            self.send_response(302)
            self.send_header("Location", "/loop2" if self.path == "/loop1" else "/loop1")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        elif self.path == "/gzip-big":
            import gzip as _gzip
            body = _gzip.compress(b"<p>" + b"x" * 5000 + b"</p>")
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
        else:
            body = b"nope"
            self.send_response(404)
        self.send_header("Content-Type", CONTENT_TYPES.get(self.path, "text/html; charset=utf-8"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep test output pristine
        pass


class FetchPageFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.server.paths = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.out = self.tmp / "page.html"
        self.gate_calls = []
        self._orig_gate = fetch_page.gate
        fetch_page.gate = self.allow_gate
        self.addCleanup(setattr, fetch_page, "gate", self._orig_gate)

    def allow_gate(self, url):
        self.gate_calls.append(url)
        return 0, "ALLOWED - robots.txt permits this path"

    def run_main(self, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = fetch_page.main(list(argv))
        return code, stdout.getvalue(), stderr.getvalue()


class SchemeAndPathGuards(FetchPageFixture):
    def test_rejects_non_http_scheme(self):
        code, out, err = self.run_main("file:///etc/passwd", "--out", str(self.out))
        self.assertEqual(code, 2)
        self.assertIn("http(s)", err)
        self.assertFalse(self.out.exists())
        self.assertEqual(self.gate_calls, [])

    def test_refuses_out_inside_repo_before_any_network(self):
        fetch_page.gate = lambda url: self.fail("gate must not run for a refused --out")
        inside = REPO_ROOT / "cv" / "fetched.html"
        code, out, err = self.run_main(f"{self.base}/ok.html", "--out", str(inside))
        self.assertEqual(code, 2)
        self.assertIn("inside the repo", err)
        self.assertFalse(inside.exists())


class RobotsGate(FetchPageFixture):
    def test_refuses_when_robots_gate_denies(self):
        fetch_page.gate = lambda url: (1, "DISALLOWED for * - do not retry, go to step 3")
        code, out, err = self.run_main(f"{self.base}/ok.html", "--out", str(self.out))
        self.assertEqual(code, 1)
        self.assertIn("DISALLOWED", out)
        self.assertIn("escalation step 4", out)
        self.assertFalse(self.out.exists())

    def test_gate_sees_the_requested_url(self):
        self.run_main(f"{self.base}/ok.html", "--out", str(self.out))
        self.assertEqual(self.gate_calls, [f"{self.base}/ok.html"])

    def test_redirect_target_is_checked_before_fetching(self):
        # The documented retry follows redirects. An allowed posting URL can
        # redirect to a path this site's robots.txt explicitly disallows.
        fetch_page.gate = self._orig_gate
        for path in ("curl", "urllib"):
            with self.subTest(path=path):
                if path == "urllib":
                    original_which = fetch_page.shutil.which
                    fetch_page.shutil.which = lambda name: None
                    self.addCleanup(setattr, fetch_page.shutil, "which", original_which)
                self.server.paths.clear()
                code, out, err = self.run_main(f"{self.base}/redirect", "--out", str(self.out))
                self.assertEqual(code, 1, out + err)
                self.assertIn("DISALLOWED", out)
                self.assertNotIn("/blocked", self.server.paths)
                self.assertFalse(self.out.exists())

    def test_allowed_redirect_still_fetches_the_posting(self):
        fetch_page.gate = self._orig_gate
        for path in ("curl", "urllib"):
            with self.subTest(path=path):
                if path == "urllib":
                    original_which = fetch_page.shutil.which
                    fetch_page.shutil.which = lambda name: None
                    self.addCleanup(setattr, fetch_page.shutil, "which", original_which)
                self.server.paths.clear()
                code, out, err = self.run_main(f"{self.base}/redirect-allowed", "--out", str(self.out))
                self.assertEqual(code, 0, out + err)
                self.assertEqual(self.out.read_text(encoding="utf-8"), PAGE)
                self.assertIn("/ok.html", self.server.paths)
                self.assertGreaterEqual(self.server.paths.count("/robots.txt"), 2)
                self.out.unlink()

    def test_raw_space_in_location_is_encoded_on_both_paths(self):
        for path in ("curl", "urllib"):
            with self.subTest(path=path):
                if path == "urllib":
                    original_which = fetch_page.shutil.which
                    fetch_page.shutil.which = lambda name: None
                    self.addCleanup(setattr, fetch_page.shutil, "which", original_which)
                self.server.paths.clear()
                code, out, err = self.run_main(f"{self.base}/redirect-space", "--out", str(self.out))
                self.assertEqual(code, 0, out + err)
                self.assertIn("/ok.html?q=a%20b", self.server.paths)
                self.assertEqual(self.out.read_text(encoding="utf-8"), PAGE)
                self.out.unlink()

    def test_raw_utf8_in_location_keeps_its_bytes_on_both_paths(self):
        for path in ("curl", "urllib"):
            with self.subTest(path=path):
                if path == "urllib":
                    original_which = fetch_page.shutil.which
                    fetch_page.shutil.which = lambda name: None
                    self.addCleanup(setattr, fetch_page.shutil, "which", original_which)
                self.server.paths.clear()
                code, out, err = self.run_main(f"{self.base}/redirect-utf8", "--out", str(self.out))
                self.assertEqual(code, 0, out + err)
                self.assertIn("/ok.html?t=%C3%A6%C3%B8%C3%A5", self.server.paths)
                self.out.unlink()

    @unittest.skipUnless(shutil.which("curl"), "urllib cannot send a URL with raw non-ASCII characters")
    def test_relative_redirect_from_a_unicode_url(self):
        self.server.paths.clear()
        code, out, err = self.run_main(f"{self.base}/unicode/日本", "--out", str(self.out))
        self.assertEqual(code, 0, out + err)
        self.assertTrue(any(p.endswith("?page=2") for p in self.server.paths), self.server.paths)

    def test_cross_origin_redirect_uses_destination_policy(self):
        destination = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        destination.paths = []
        thread = threading.Thread(target=destination.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(destination.server_close)
        self.addCleanup(destination.shutdown)
        self.server.redirect_to = f"http://127.0.0.1:{destination.server_address[1]}/blocked"
        fetch_page.gate = self._orig_gate

        code, out, err = self.run_main(f"{self.base}/redirect-other", "--out", str(self.out))

        self.assertEqual(code, 1, out + err)
        self.assertIn("/robots.txt", destination.paths)
        self.assertNotIn("/blocked", destination.paths)
        self.assertFalse(self.out.exists())


class Fetching(FetchPageFixture):
    def test_fetches_and_reports_status(self):
        code, out, err = self.run_main(f"{self.base}/ok.html", "--out", str(self.out))
        self.assertEqual(code, 0, err)
        self.assertIn("HTTP 200 size=", out)
        self.assertIn(f"saved={self.out}", out)
        self.assertEqual(self.out.read_text(encoding="utf-8"), PAGE)

    def test_non_200_is_reported_not_fatal(self):
        # The status is information for the escalation order, not a failure:
        # a 403 body still tells the agent what the site answered.
        code, out, err = self.run_main(f"{self.base}/forbidden", "--out", str(self.out))
        self.assertEqual(code, 0, err)
        self.assertIn("HTTP 403", out)
        self.assertIn("denied", self.out.read_text(encoding="utf-8"))

    def test_text_strips_script_style_comments_and_unescapes(self):
        code, out, err = self.run_main(f"{self.base}/ok.html", "--out", str(self.out), "--text")
        self.assertEqual(code, 0, err)
        text = out.split("--- text ---", 1)[1]
        self.assertIn("Visible title", text)
        self.assertIn("Visible text & more.", text)
        for leaked in ("alert(", "color:red", "hidden comment", "noscript text", "svg text", "<p>"):
            self.assertNotIn(leaked, text)

    def test_text_decodes_a_page_by_its_declared_charset(self):
        code, out, err = self.run_main(f"{self.base}/euc-kr.html", "--out", str(self.out), "--text")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.out.read_bytes(), KOREAN_PAGE.encode("cp949"))
        text = out.split("--- text ---", 1)[1]
        self.assertIn("백엔드 개발자 채용", text)
        self.assertNotIn("\ufffd", text)

    def test_max_chars_caps_text(self):
        code, out, err = self.run_main(
            f"{self.base}/ok.html", "--out", str(self.out), "--text", "--max-chars", "7"
        )
        self.assertEqual(code, 0, err)
        text = out.split("--- text ---", 1)[1].strip()
        self.assertLessEqual(len(text), 7)
        self.assertTrue(text)

    def test_default_out_lands_in_temp_not_repo(self):
        code, out, err = self.run_main(f"{self.base}/ok.html")
        self.assertEqual(code, 0, err)
        saved = Path(out.split("saved=", 1)[1].strip())
        self.addCleanup(saved.unlink)
        self.assertTrue(saved.exists())
        with self.assertRaises(ValueError):
            saved.resolve().relative_to(REPO_ROOT)

    def _without_curl(self):
        orig_which = fetch_page.shutil.which
        fetch_page.shutil.which = lambda name: None
        self.addCleanup(setattr, fetch_page.shutil, "which", orig_which)

    def test_redirect_loop_is_a_transport_error_on_both_paths(self):
        # The docstring promises "5 redirects"; a loop must end in exit 2 with
        # nothing claimed as fetched, with or without curl installed.
        for label in ("curl", "urllib"):
            with self.subTest(path=label):
                if label == "urllib":
                    self._without_curl()
                code, out, err = self.run_main(f"{self.base}/loop1", "--out", str(self.out))
                self.assertEqual(code, 2, out + err)
                self.assertNotIn("saved=", out)

    def test_decompressed_size_is_bounded_on_urllib_path(self):
        # The 10 MB cap must apply after Content-Encoding is undone, or a small
        # gzip body can expand past it. MAX_BYTES is lowered to make it testable.
        self._without_curl()
        orig_max = fetch_page.MAX_BYTES
        fetch_page.MAX_BYTES = 1000
        self.addCleanup(setattr, fetch_page, "MAX_BYTES", orig_max)
        code, out, err = self.run_main(f"{self.base}/gzip-big", "--out", str(self.out))
        self.assertEqual(code, 2, out + err)
        self.assertIn("exceeds", err)
        self.assertFalse(self.out.exists())

    def test_urllib_fallback_when_curl_missing(self):
        orig_which = fetch_page.shutil.which
        fetch_page.shutil.which = lambda name: None
        self.addCleanup(setattr, fetch_page.shutil, "which", orig_which)
        code, out, err = self.run_main(f"{self.base}/ok.html", "--out", str(self.out), "--text")
        self.assertEqual(code, 0, err)
        self.assertIn("HTTP 200 size=", out)
        self.assertEqual(self.out.read_text(encoding="utf-8"), PAGE)
        self.assertIn("Visible title", out)


class CurlArgvShape(unittest.TestCase):
    """The grant is `Bash(python tools/fetch_page.py:*)`, so what curl is handed
    is the whole security property: GET only, no body, no upload, and the URL
    can never be parsed as an option."""

    def test_curl_argv_is_get_only_and_terminates_options(self):
        url = "-https://example.com/x"  # a URL that would read as a flag
        argv = fetch_page.build_curl_argv(url, Path("out.html"), Path("headers.txt"), 45)
        self.assertEqual(argv[0], "curl")
        self.assertEqual(argv[-2:], ["--", url])
        for forbidden in ("-d", "--data", "--data-binary", "--data-raw", "-F", "--form",
                          "-T", "--upload-file", "-X", "--request", "-u", "--user", "-K"):
            self.assertNotIn(forbidden, argv[:-1])
        self.assertIn("--max-filesize", argv)
        self.assertIn("--max-redirs", argv)
        self.assertEqual(argv[argv.index("--max-redirs") + 1], "0")
        self.assertNotIn("-L", argv)
        self.assertIn("-A", argv)
        self.assertEqual(argv[argv.index("-A") + 1], fetch_page.BROWSER)

    def test_user_agent_is_the_one_robots_check_uses(self):
        import robots_check
        self.assertEqual(fetch_page.BROWSER, robots_check.BROWSER)


if __name__ == "__main__":
    unittest.main()

"""Actual HTTP responses through the CLI entry point and urllib backend."""
import http.server
import contextlib
import io
from pathlib import Path
import sys
from unittest.mock import patch
from tempfile import TemporaryDirectory
import threading
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import fetch_page  # noqa: E402
PAGE = b"<html><body><h1>Software Engineer</h1><p>Posting details.</p></body></html>"


class DeflateBackend(unittest.TestCase):
    def test_standard_and_legacy_deflate_reach_saved_html_and_text(self):
        requests = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/robots.txt":
                    body = b"User-agent: *\nAllow: /\n"
                    encoding = None
                else:
                    requests.append((self.path, self.headers.get("Accept-Encoding")))
                    encoder = zlib.compressobj(wbits=-zlib.MAX_WBITS if self.path == "/raw" else zlib.MAX_WBITS)
                    body = encoder.compress(PAGE) + encoder.flush()
                    encoding = "deflate"
                self.send_response(200)
                if encoding:
                    self.send_header("Content-Encoding", encoding)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with TemporaryDirectory() as directory:
                root = Path(directory)
                for path in ("/standard", "/raw"):
                    with self.subTest(encoding=path):
                        output = root / (path[1:] + ".html")
                        stdout, stderr = io.StringIO(), io.StringIO()
                        # Select the existing urllib backend; robots.txt still
                        # goes through its real curl transport and parser.
                        with patch.object(fetch_page.shutil, "which", return_value=None), \
                             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                            code = fetch_page.main([
                                f"http://127.0.0.1:{server.server_port}{path}",
                                "--out", str(output), "--text",
                            ])
                        self.assertEqual(code, 0, stdout.getvalue() + stderr.getvalue())
                        self.assertEqual(output.read_bytes(), PAGE)
                        self.assertIn("Software Engineer", stdout.getvalue())
                        self.assertIn(f"HTTP 200 size={len(PAGE)}", stdout.getvalue())
                self.assertEqual(requests, [("/standard", "gzip, deflate"), ("/raw", "gzip, deflate")])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()

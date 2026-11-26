#!/usr/bin/env python3
"""GET a page with browser headers: the 403 retry from 09-web-research.md.

WebFetch sends a bot-identifying user agent and many corporate, bank and
recruiter sites answer it with 403 while serving the same page to a browser.
This script is the retry. It exists as a script, rather than as a documented
curl command, so that the workflow's pre-approved grant can name
`python tools/fetch_page.py` instead of `curl`: a curl grant with a free tail
is also an upload channel, this is not.

Properties, each pinned by tests/test_fetch_page.py:

  * GET only. The curl argv is fixed; there is no code path that sends a
    request body, a form, or a file, and the URL comes after `--` so it can
    never be parsed as an option. There is no fallback without curl: gate()
    needs curl too and reports UNCONFIRMED without it.
  * http(s) only. Any other scheme exits 2 before anything else happens.
  * Public hosts only, with limits. gate() refuses a host that resolves to
    any loopback, private, link-local or unique-local address, on every hop,
    before a request is made to it; curl's URL globbing is off. The page GET
    takes robots_check.route() too: direct, it is re-checked and pinned to the
    checked addresses; behind a proxy that resolves names itself, it goes
    through exactly the proxy gate() used and the host is only pre-screened
    locally, since the proxy resolves the name again. Non-ASCII host names
    must be given in punycode (xn--) form.
  * robots.txt is enforced, not advised. robots_check.gate() runs before each
    GET, including redirect targets, and a non-zero gate exits 1 with its
    message; no flag skips it. The retry is for firewalls on sites whose
    published policy allows access, never for sites that said no.
  * Fetched content never lands in the repo or outside the temp directory.
    --out defaults to the system temp directory; a path that, or whose
    directory, resolves inside the repo root or outside the system temp
    directory and /tmp (not on Windows) is refused, as is one that cannot be
    resolved.
  * Bounded: 45 s, 5 redirects, 10 MB, and the size cap is applied after
    Content-Encoding is undone. Local DNS lookups have no time limit of their
    own, and the 45 s does not bound them.

Usage:
  python3 tools/fetch_page.py <url> [--out PATH] [--text] [--max-chars N]

Prints `HTTP <code> size=<bytes> saved=<path>` and exits 0 whatever the HTTP
status - the status is information for the escalation order, not a failure.
With --text, also prints the tag-stripped page text (script/style/noscript/svg/
template and comments removed, including when the closer is missing, entities
unescaped, whitespace folded), capped at
--max-chars (default 6000), after a `--- text ---` line. A saved body that is
not valid UTF-8 is decoded by the charset the page declares (page_decode.py).

Exit codes: 0 fetched; 1 refused by robots policy (go to escalation step 4);
2 usage or transport error.
"""

import argparse
from functools import lru_cache
import hashlib
import html
import os
import re
import string
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from page_decode import decode_page  # noqa: E402
from robots_check import BROWSER, gate, route  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
ACCEPT_LANGUAGE = "en-GB,en;q=0.9"
TIMEOUT_SECONDS = 45
MAX_REDIRECTS = 5
MAX_BYTES = 10 * 1024 * 1024


@lru_cache(maxsize=1)
def _curl_supports_compressed() -> bool:
    """Return whether the bare curl used for fetching can decode content."""
    try:
        proc = subprocess.run(
            ["curl", "--version"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and bool(
        re.search(r"^Features:.*\b(?:libz|brotli|zstd)\b", proc.stdout, re.IGNORECASE | re.MULTILINE)
    )


def build_curl_argv(url: str, out: Path, headers: Path, timeout: float, opts=()) -> list:
    """One GET without automatic redirects, so the next hop can be gated.
    opts are robots_check.route()'s, which keep it on the host gate() checked."""
    compressed = ["--compressed"] if _curl_supports_compressed() else []
    return [
        "curl", "-q", "-sS", "--globoff",
        "--max-redirs", "0",
        "--max-time", str(timeout),
        "--max-filesize", str(MAX_BYTES),
        *compressed,
        *opts,
        "-A", BROWSER,
        "-H", f"Accept: {ACCEPT}",
        "-H", f"Accept-Language: {ACCEPT_LANGUAGE}",
        "-D", str(headers),
        "-o", str(out),
        "-w", "%{http_code}",
        "--", url,
    ]


def _location(header_text: str) -> str | None:
    """Read Location from the final response (a proxy may add an earlier one)."""
    for block in reversed(re.split(r"\r?\n\r?\n", header_text)):
        if not block.startswith("HTTP/"):
            continue
        for line in block.splitlines():
            if line.lower().startswith("location:"):
                return line.partition(":")[2].strip()
        break
    return None


def fetch_with_curl(url: str, out: Path, timeout: float) -> tuple[int, str | None]:
    fd, header_name = tempfile.mkstemp(prefix=".fetch_page_headers.")
    os.close(fd)  # curl must be able to open it on Windows too
    headers = Path(header_name)
    try:
        opts, env = route(urlsplit(url))
        proc = subprocess.run(
            build_curl_argv(url, out, headers, timeout, opts),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=timeout + 15,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"curl exit {proc.returncode}")
        code = int(proc.stdout.strip())
        location = _location(headers.read_text(encoding="iso-8859-1"))
    finally:
        headers.unlink(missing_ok=True)
    if out.stat().st_size > MAX_BYTES:
        raise RuntimeError(f"response exceeds {MAX_BYTES} bytes after decompression")
    return code, location


def strip_tags(markup: str, limit: int) -> str:
    """Plain text from HTML: drop script/style/noscript/svg/template and comments, strip
    tags, unescape entities, fold whitespace. Modern sites embed copy inside
    JSON blobs, so stray attribute fragments around real text are normal.

    A missing closer hides through the end of the input, as a browser does and
    as posting_guard.py does. Otherwise a page cut off inside <script>, or a
    <template> block, is printed into the agent's context by --text.
    """
    text = re.sub(r"(?s)<!--.*?(?:-->|\Z)", " ", markup)
    text = re.sub(
        r"(?is)<(script|style|noscript|svg|template)\b[^>]*>.*?(?:</\1\s*>|\Z)",
        " ",
        text,
    )
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", text))
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text[:limit]


def _default_out(url: str) -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return Path(tempfile.gettempdir()) / f"fetch_page_{digest}.html"


def _inside_temp(path: Path) -> bool:
    resolved = path.resolve()
    roots = {Path(tempfile.gettempdir()).resolve()}
    if os.name != "nt":
        roots.add(Path("/tmp").resolve())
    return any(resolved.is_relative_to(root) for root in roots)


def _inside_repo(path: Path) -> bool:
    # Compare by identity, not by spelling: on a case-insensitive filesystem
    # (macOS and Windows defaults) `/users/x/repo/notes.md` resolves to a
    # string that is not under ROOT yet lands in the repo. Walk up to the
    # nearest existing ancestor - the target itself is usually not there yet -
    # and ask the OS whether any ancestor is the repo root.
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    probe = candidate
    while not probe.exists():
        parent = probe.parent
        if parent == probe:
            return False
        probe = parent
    for ancestor in (probe, *probe.parents):
        try:
            if os.path.samefile(ancestor, ROOT):
                return True
        except OSError:
            return False
    return False


def _force_utf8_output() -> None:
    """Write UTF-8 whatever the host's default encoding is.

    A piped stdout on Windows defaults to the ANSI code page (cp1252 on most
    Western installs), so printing a page with characters outside it raised
    UnicodeEncodeError before the workflow saw any output.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)  # absent on a StringIO under test
        if reconfigure:
            reconfigure(encoding="utf-8")


def main(argv=None) -> int:
    _force_utf8_output()
    parser = argparse.ArgumentParser(
        prog="fetch_page.py",
        description="GET a page with browser headers (robots.txt enforced); see module docstring.",
    )
    parser.add_argument("url")
    parser.add_argument("--out", type=Path, default=None,
                        help="where to save the body: the file and its directory must be in the system temp dir or /tmp (not on Windows), never inside the repo (default: system temp dir)")
    parser.add_argument("--text", action="store_true", help="also print the tag-stripped text")
    parser.add_argument("--max-chars", type=int, default=6000, help="cap for --text (default 6000)")
    args = parser.parse_args(argv)

    scheme = urlsplit(args.url).scheme.lower()
    if scheme not in ("http", "https"):
        print(f"fetch_page: only http(s) URLs are fetched, got scheme {scheme!r}", file=sys.stderr)
        return 2

    out = args.out if args.out is not None else _default_out(args.url)
    try:
        out = out.expanduser()
        in_repo = _inside_repo(out.resolve()) or _inside_repo(out.parent.resolve())
        in_temp = _inside_temp(out) and _inside_temp(out.parent)
    except (OSError, RuntimeError) as exc:
        print(f"fetch_page: cannot resolve the --out path ({out}): {exc}", file=sys.stderr)
        return 2
    if in_repo:
        print(
            f"fetch_page: refusing to write inside the repo ({out}); fetched content is "
            "untrusted and belongs in the session scratchpad or the temp directory",
            file=sys.stderr,
        )
        return 2
    if not in_temp:
        print(
            f"fetch_page: refusing to write outside the temp directory ({out}); fetched content "
            "is untrusted and belongs in the session scratchpad or the temp directory",
            file=sys.stderr,
        )
        return 2

    try:
        current = args.url
        deadline = None
        for redirects in range(MAX_REDIRECTS + 1):
            scheme = urlsplit(current).scheme.lower()
            if scheme not in ("http", "https"):
                raise RuntimeError("redirect target is not http(s)")
            rc, message = gate(current)
            print(message)
            if rc != 0:
                print(
                    "fetch_page: refused - robots.txt does not permit this path, so the "
                    "browser-header retry does not run. Go to escalation step 4: search for "
                    "the employer's own posting."
                )
                return 1
            if deadline is None:
                deadline = time.monotonic() + TIMEOUT_SECONDS
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("fetch exceeded the time limit")
            out.parent.mkdir(parents=True, exist_ok=True)
            fd, body_name = tempfile.mkstemp(dir=out.parent, prefix=".fetch_page.", suffix=".tmp")
            os.close(fd)  # curl may reopen the path on Windows
            body = Path(body_name)
            try:
                code, location = fetch_with_curl(current, body, remaining)
                if code in (301, 302, 303, 307, 308) and location:
                    if redirects == MAX_REDIRECTS:
                        raise RuntimeError(f"more than {MAX_REDIRECTS} redirects")
                    # Encode the header's raw bytes before resolving; preserve Unicode in the base URL.
                    current = urljoin(current, quote(location, encoding="iso-8859-1", safe=string.punctuation))
                    continue
                os.replace(body, out)
                status = f"HTTP {code} size={out.stat().st_size}"
                break
            finally:
                body.unlink(missing_ok=True)
    except Exception as exc:  # transport failures of any shape: report, exit 2
        # Only RuntimeError text is ours; other messages can quote a site-controlled URL.
        detail = f": {exc}" if type(exc) is RuntimeError else ""
        print(f"fetch_page: {type(exc).__name__}{detail}", file=sys.stderr)
        return 2

    print(f"{status} saved={out}")
    if args.text:
        print("--- text ---")
        print(strip_tags(decode_page(out.read_bytes()), args.max_chars))
    return 0


if __name__ == "__main__":
    sys.exit(main())

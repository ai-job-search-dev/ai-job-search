#!/usr/bin/env python3
"""Human-in-the-loop application assistant (Playwright).

Opens a READY_TO_APPLY job in a dedicated, headed Chrome profile, pre-fills the plain
identity fields and uploads the tailored CV, then STOPS. The candidate reviews the form
and clicks Submit personally; only then does typing `applied` at the prompt record the
application and start the 30-day company cooldown.

This module never clicks anything, never presses keys, never solves CAPTCHAs, and never
answers free-text, dropdown, radio or checkbox questions (visa, sponsorship, EEO).
"""

from __future__ import annotations

import argparse
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.db import (
    STATUS_READY_TO_APPLY,
    check_company_cooldown,
    confirm_application,
    get_db,
    get_ready_application,
    init_db,
)
from tools.logger import METRICS, get_logger
from tools.profile_manager import DEFAULT_PROFILE_FILE, load_profile
from tools.profile_schema import MasterProfile

logger = get_logger("browser_apply")

# First match wins, so the more specific patterns come first.
FIELD_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("cover_letter_file", re.compile(r"cover[\s_-]*letter", re.I)),
    ("resume_file", re.compile(r"resume|r[eé]sum[eé]|\bcv\b|curriculum", re.I)),
    ("email", re.compile(r"e[\s_-]?mail", re.I)),
    ("phone", re.compile(r"phone|mobile|\btel(?:ephone)?\b|\bcell\b", re.I)),
    ("linkedin", re.compile(r"linkedin", re.I)),
    ("github", re.compile(r"github", re.I)),
    ("portfolio", re.compile(r"portfolio|personal[\s_-]*(?:site|website)|website", re.I)),
    ("first_name", re.compile(r"first[\s_-]*name|given[\s_-]*name|\bfname\b", re.I)),
    ("last_name", re.compile(r"last[\s_-]*name|family[\s_-]*name|surname|\blname\b", re.I)),
    ("full_name", re.compile(r"full[\s_-]*name|^\s*name\b|\byour[\s_-]*name", re.I)),
    ("location", re.compile(r"\bcity\b|\blocation\b|where.*(?:based|located)|current[\s_-]*location", re.I)),
]

ID_NOISE = re.compile(
    r"(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])|https?://\S+|\bquestion_\d+\b", re.IGNORECASE
)
MAX_LABEL_CHARS = 90  # longer text is a custom question (visa, authorization...), never a profile field
SKIP_INPUT_TYPES = {"hidden", "password", "checkbox", "radio", "submit", "button", "image", "reset"}
CAPTCHA_MARKERS = ("captcha", "recaptcha", "hcaptcha", "turnstile")
APPROVED_APPLY_HOSTS = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com", "workday.com", "smartrecruiters.com", "workable.com",
)


def host_allowed(url: str, extra_hosts: Tuple[str, ...] = ()) -> bool:
    """True for an https URL whose host is, or is a subdomain of, an approved ATS apex or an explicitly allowed host."""
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        return False
    return any(host == h or host.endswith("." + h) for h in APPROVED_APPLY_HOSTS + tuple(h.lower() for h in extra_hosts))

COLLECT_FIELDS_JS = """
() => {
  const out = [];
  const labelFor = (el) => {
    const parts = [];
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l) parts.push(l.innerText);
    }
    const wrap = el.closest('label');
    if (wrap) parts.push(wrap.innerText);
    const by = el.getAttribute('aria-labelledby');
    if (by) by.split(/\\s+/).forEach(id => { const n = document.getElementById(id); if (n) parts.push(n.innerText); });
    ['aria-label', 'placeholder', 'name', 'id', 'autocomplete'].forEach(a => { const v = el.getAttribute(a); if (v) parts.push(v); });
    return parts.join(' ').replace(/\\s+/g, ' ').trim();
  };
  document.querySelectorAll('input, textarea, select').forEach((el, i) => {
    const style = window.getComputedStyle(el);
    const isFile = el.type === 'file';
    if (!isFile && (el.offsetParent === null || style.visibility === 'hidden')) return;
    el.setAttribute('data-aja-idx', String(i));
    out.push({
      index: i,
      tag: el.tagName.toLowerCase(),
      type: (el.type || '').toLowerCase(),
      descriptor: labelFor(el),
      value: el.value || '',
    });
  });
  return out;
}
"""


def application_url(job_url: str, portal: str = "") -> str:
    """The page that holds the application form, for ATS hosts where it is not the posting page.

    Lever keeps the form under /apply and Ashby under /application. Greenhouse postings often live on the
    company's own site with the form in an iframe, so the embedded form URL is built from the board slug
    (`portal` is "greenhouse:<slug>") and the job id. Anything else is returned unchanged.
    """
    parsed = urlparse(job_url)
    host, path = parsed.netloc.lower(), parsed.path.rstrip("/")
    if host.endswith("jobs.lever.co") and path and not path.endswith("/apply"):
        return parsed._replace(path=path + "/apply", query="", fragment="").geturl()
    if host.endswith("jobs.ashbyhq.com") and path and not path.endswith("/application"):
        return parsed._replace(path=path + "/application", query="", fragment="").geturl()
    if portal.lower().startswith("greenhouse:"):
        slug = portal.split(":", 1)[1].strip()
        job_id = (parse_qs(parsed.query).get("gh_jid") or [""])[0] or (re.search(r"/jobs/(\d+)", path) or [None, ""])[1]
        if slug and job_id.isdigit():
            return f"https://boards.greenhouse.io/embed/job_app?for={slug}&token={job_id}"
    return job_url


def split_name(full_name: str) -> Tuple[str, str]:
    parts = full_name.split()
    if len(parts) <= 1:
        return full_name, ""
    return parts[0], " ".join(parts[1:])


def build_answers(profile: MasterProfile) -> Dict[str, str]:
    ident = profile.identity
    first, last = split_name(ident.name)
    answers = {
        "full_name": ident.name,
        "first_name": first,
        "last_name": last,
        "email": ident.email,
        "phone": ident.phone or "",
        "linkedin": ident.linkedin or "",
        "github": ident.github or "",
        "portfolio": ident.portfolio or ident.github or "",
        "location": ident.location,
    }
    return {k: v for k, v in answers.items() if v}


def classify_field(descriptor: str, tag: str = "input", input_type: str = "text") -> Optional[str]:
    """Map a form field to a profile key, or None when it needs a human."""
    if tag == "select" or input_type in SKIP_INPUT_TYPES:
        return None
    if tag == "textarea":
        return None
    descriptor = re.sub(r"\s+", " ", ID_NOISE.sub(" ", descriptor)).strip()
    if "?" in descriptor or len(descriptor) > MAX_LABEL_CHARS:
        return None
    if input_type == "file":
        for key, pattern in FIELD_PATTERNS:
            if key in ("cover_letter_file", "resume_file") and pattern.search(descriptor):
                return key
        return None
    if input_type == "email":
        return "email"
    if input_type == "tel":
        return "phone"
    for key, pattern in FIELD_PATTERNS:
        if key.endswith("_file"):
            continue
        if pattern.search(descriptor):
            return key
    return None


def plan_fills(
    fields: List[Dict[str, Any]],
    answers: Dict[str, str],
    cv_path: Optional[str],
    cover_letter_path: Optional[str],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Decide what to fill. Returns (fills, labels left for the human)."""
    fills: List[Dict[str, Any]] = []
    left: List[str] = []
    used: set = set()
    files = {"resume_file": cv_path, "cover_letter_file": cover_letter_path}
    for f in fields:
        label = (f.get("descriptor") or f.get("type") or f.get("tag") or "field")[:80]
        key = classify_field(f.get("descriptor", ""), f.get("tag", "input"), f.get("type", "text"))
        if f.get("type") == "file":
            path = files.get(key or "")
            if key and path and key not in used:
                used.add(key)
                fills.append({"index": f["index"], "kind": "file", "key": key, "value": path})
            else:
                left.append(label)
            continue
        if f.get("tag") == "select":
            left.append(label)
            continue
        if f.get("value"):
            continue
        if key and key in answers and key not in used:
            used.add(key)
            fills.append({"index": f["index"], "kind": "text", "key": key, "value": answers[key]})
        else:
            left.append(label)
    return fills, left


def detect_captcha(page_html: str) -> bool:
    lowered = page_html.lower()
    return any(marker in lowered for marker in CAPTCHA_MARKERS)


def form_recognised(fields: List[Dict[str, Any]]) -> bool:
    """True when the page has at least an email, a name or a resume field, i.e. it is an application form."""
    keys = {classify_field(f.get("descriptor", ""), f.get("tag", "input"), f.get("type", "text")) for f in fields}
    return bool(keys & {"email", "first_name", "full_name", "resume_file"})


def apply_fills(page: Any, fills: List[Dict[str, Any]]) -> int:
    done = 0
    for fill in fills:
        selector = f'[data-aja-idx="{fill["index"]}"]'
        try:
            if fill["kind"] == "file":
                page.set_input_files(selector, fill["value"])
            else:
                page.fill(selector, fill["value"])
            done += 1
        except Exception as exc:
            logger.warning(f"Could not fill {fill['key']}: {exc}")
    return done


def human_checkpoint(
    summary: str,
    input_fn: Callable[[str], str] = input,
    bell: Callable[[], None] = lambda: print("\a", end="", flush=True),
) -> bool:
    """Block until the human reports the outcome. Returns True only for an explicit `applied`."""
    print("\n" + "=" * 65)
    print("  CHECKPOINT: review the form in the browser window")
    print("  Submit is YOURS to click. This tool will never do it.")
    print("=" * 65)
    print(summary)
    bell()
    try:
        answer = input_fn("\nType 'applied' once you have submitted, or 'skip' to leave it pending: ")
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() == "applied"


def _profile_dir() -> Path:
    import os

    from tools.browser_profile import CHROME_PROFILE_DIR

    override = os.environ.get("AI_JOB_SEARCH_CHROME_PROFILE")
    return Path(override).expanduser() if override else CHROME_PROFILE_DIR


@contextmanager
def open_browser_page(url: str, cdp_url: Optional[str] = None) -> Iterator[Any]:
    """Yield a Playwright page in a headed, dedicated Chrome profile (never the daily profile)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        if cdp_url:
            browser = pw.chromium.connect_over_cdp(cdp_url)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded")
            try:
                yield page
            finally:
                browser.close()
        else:
            profile_dir = _profile_dir()
            profile_dir.mkdir(parents=True, exist_ok=True)
            context = pw.chromium.launch_persistent_context(
                str(profile_dir), channel="chrome", headless=False
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded")
            try:
                yield page
            finally:
                context.close()


def run_apply(
    job_id: int,
    db_path: Optional[Path] = None,
    profile_path: Optional[Path] = None,
    cdp_url: Optional[str] = None,
    input_fn: Callable[[str], str] = input,
    page_factory: Callable[..., Any] = open_browser_page,
    extra_hosts: Tuple[str, ...] = (),
) -> str:
    """Returns 'applied', 'pending' or 'blocked'. Nothing is filled unless the final page is on an approved https host."""
    profile = load_profile(profile_path or DEFAULT_PROFILE_FILE)
    init_db(db_path)
    with get_db(db_path) as conn:
        app = get_ready_application(conn, job_id)
        if app is None or app["job_status"] != STATUS_READY_TO_APPLY:
            logger.error(f"Job {job_id} is not READY_TO_APPLY. Run tools/tailor.py first.")
            return "blocked"
        in_cooldown, reason = check_company_cooldown(conn, app["company_name"])
        if in_cooldown:
            logger.error(f"Blocked: {reason}")
            return "blocked"
        missing = [p for p in (app["cv_path"], app["cover_letter_path"]) if p and not Path(p).is_file()]
        if missing:
            logger.error(f"Tailored documents are missing: {missing}")
            return "blocked"
        if not re.match(r"^https?://", app["url"] or ""):
            logger.error(f"Refusing to open non-http(s) URL: {app['url']!r}")
            return "blocked"

        answers = build_answers(profile)
        target_url = application_url(app["url"], app["portal"] or "")
        if target_url != app["url"]:
            logger.info(f"Opening the application form directly: {target_url}")
        with page_factory(target_url, cdp_url) as page:
            page.wait_for_timeout(1500)
            if not host_allowed(getattr(page, "url", ""), extra_hosts):
                logger.error(f"Blocked: the page ended up on {getattr(page, 'url', None)!r}, which is not an approved host. Nothing was filled.")
                return "blocked"
            fields = page.evaluate(COLLECT_FIELDS_JS)
            fills, left = plan_fills(fields, answers, app["cv_path"], app["cover_letter_path"])
            filled = apply_fills(page, fills)
            summary = [
                f"  Job:      {app['company_name']} - {app['title']}",
                f"  Filled:   {filled} field(s) ({', '.join(f['key'] for f in fills) or 'none'})",
                f"  For you:  {len(left)} field(s) left untouched"
                + (f": {'; '.join(left[:8])}" if left else ""),
            ]
            if not form_recognised(fields):
                summary.append("  WARNING:  No application form was recognised on this page (login wall, iframe or a different site).")
                summary.append("            Open the real application page yourself; nothing useful was filled.")
            if detect_captcha(page.content()):
                summary.append("  NOTE:     A CAPTCHA widget is on the page. If it asks, solve it yourself; this tool will not.")
            if not human_checkpoint("\n".join(summary), input_fn=input_fn):
                logger.info("No confirmation received. Job stays READY_TO_APPLY.")
                return "pending"

        confirm_application(conn, job_id, notes=f"Filled {filled} field(s); submitted by candidate")
        METRICS.increment("applied_total")
        logger.info(f"Recorded APPLIED for job {job_id}; cooldown started for {app['company_name']}.")
        return "applied"


def list_ready(db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    init_db(db_path)
    with get_db(db_path) as conn:
        rows = conn.execute(
            """
            SELECT j.id, j.title, c.name AS company, j.url FROM jobs j
            JOIN companies c ON c.id = j.company_id WHERE j.status = ? ORDER BY j.id
            """,
            (STATUS_READY_TO_APPLY,),
        ).fetchall()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pre-fill an application and stop before Submit (HITL)")
    parser.add_argument("--job-id", type=int, help="READY_TO_APPLY job to open")
    parser.add_argument("--list", action="store_true", help="List READY_TO_APPLY jobs")
    parser.add_argument("--cdp-url", default=None, help="Attach to a Chrome started by tools/browser_profile.py")
    parser.add_argument("--allow-host", action="append", default=[], help="Extra host (and its subdomains) to trust for this run")
    args = parser.parse_args()
    if args.list or args.job_id is None:
        for r in list_ready():
            print(f"[{r['id']}] {r['company']} - {r['title']}\n     {r['url']}")
        sys.exit(0)
    outcome = run_apply(args.job_id, cdp_url=args.cdp_url, extra_hosts=tuple(args.allow_host))
    sys.exit(0 if outcome in ("applied", "pending") else 1)

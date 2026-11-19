#!/usr/bin/env python3
"""Human-in-the-loop application assistant (Playwright).

Opens a READY_TO_APPLY job in a dedicated, headed Chrome profile, pre-fills the plain
identity fields and uploads the tailored CV, then STOPS. The candidate reviews the form
and clicks Submit personally; only then does typing `applied` at the prompt record the
application and start the 30-day company cooldown.

It also fills country, city, school, degree, discipline and graduation date from the profile, including
dropdowns: it types into the dropdown and clicks an option only when exactly one option clearly matches, and
leaves the field empty otherwise. The only clicks are on dropdown inputs and their options. It never clicks
Submit or any button, never presses keys, never solves CAPTCHAs, and never answers visa, authorization,
sponsorship, relocation, salary, referral, demographic or free-text questions; those stay for the human.
"""

from __future__ import annotations

import argparse
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Sequence, Any, Callable, Dict, Iterator, List, Optional, Tuple
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
    ("country", re.compile(r"\bcountry\b", re.I)),
    ("school", re.compile(r"\bschool\b|\buniversity\b|\binstitution\b", re.I)),
    ("degree", re.compile(r"\bdegree\b", re.I)),
    ("discipline", re.compile(r"\bdiscipline\b|field[\s_-]*of[\s_-]*study|\bmajor\b", re.I)),
    ("end_month", re.compile(r"(?:end|graduation)[\s_-]*(?:date)?[\s_-]*month", re.I)),
    ("end_year", re.compile(r"(?:end|graduation)[\s_-]*(?:date)?[\s_-]*year", re.I)),
    ("location", re.compile(r"\bcity\b|\blocation\b|where.*(?:based|located)|current[\s_-]*location", re.I)),
]
# Fields that depend on a short, plain label; a long question that merely mentions a country or a degree is never one.
NARROW_KEYS = {"country", "school", "degree", "discipline", "end_month", "end_year"}
MAX_NARROW_CHARS = 45
# Questions this tool must never answer, whatever else the label contains.
SENSITIVE = re.compile(
    r"authori[sz]|visa|sponsor|work permit|right to work|relative|friend|disab|veteran|gender|ethnic|race\b|racial|hispanic|latino|"
    r"pronoun|criminal|convict|salary|compensation|notice period|relocat|purple|referr",
    re.I,
)

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
      role: el.getAttribute('role') || '',
      options: el.tagName.toLowerCase() === 'select' ? Array.from(el.options).map(o => o.text.trim()) : [],
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


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
DEGREE_LEVELS = (
    ("doctor", 4, r"ph\.?d|doctor"),
    ("master", 3, r"master|m\.?sc|m[aá]ster|maestr"),
    ("bachelor", 2, r"bachelor|b\.?sc|licenciad|grado|engineer|ingenier"),
    ("associate", 1, r"associate|tecnic|technician"),
)
DEGREE_SYNONYMS = {
    "doctor": ["Doctor of Philosophy (PhD)", "Doctor of Philosophy", "PhD", "Doctorate"],
    "master": ["Master's Degree", "Master's", "Masters", "Master of Science", "MSc", "Master"],
    "bachelor": ["Bachelor's Degree", "Bachelor's", "Bachelors", "Bachelor of Science", "BSc", "Bachelor"],
    "associate": ["Associate's Degree", "Associate's", "Associate"],
}
FILLER_WORDS = {"de", "la", "el", "of", "the", "and", "y", "del", "universidad", "university"}


def _fold(text: str) -> str:
    import unicodedata
    plain = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", plain.casefold()).strip()


def pick_education(profile: MasterProfile, today: Optional[Any] = None) -> Dict[str, str]:
    """The highest completed degree, as the values application forms ask for. In-progress degrees are never used."""
    from datetime import date
    today = today or date.today()
    best: Optional[Tuple[int, Tuple[int, int], Any, str, Tuple[int, int]]] = None
    for entry in profile.education:
        if entry.completed is False:
            continue
        ends = re.findall(r"(\d{4})(?:-(\d{2}))?", entry.period)
        if not ends:
            continue
        year, month = int(ends[-1][0]), int(ends[-1][1] or 12)
        if (year, month) > (today.year, today.month):
            continue
        level, rank = next(((name, r) for name, r, pat in DEGREE_LEVELS if re.search(pat, entry.degree, re.I)), (None, 0))
        if level is None:
            continue
        candidate = (rank, (year, month), entry, level, (year, month))
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    if best is None:
        return {}
    _, _, entry, level, (year, month) = best
    discipline = entry.degree.split(",", 1)[1].strip() if "," in entry.degree else ""
    return {"school": entry.institution, "degree": level, "discipline": discipline,
            "end_month": MONTHS[month - 1], "end_year": str(year)}


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
        "city": ident.location.split(",")[0].strip(),
        "country": ident.location.split(",")[-1].strip() if "," in ident.location else "",
    }
    answers.update(pick_education(profile))
    return {k: v for k, v in answers.items() if v}


def classify_field(descriptor: str, tag: str = "input", input_type: str = "text") -> Optional[str]:
    """Map a form field to a profile key, or None when it needs a human."""
    if input_type in SKIP_INPUT_TYPES or tag == "textarea":
        return None
    descriptor = re.sub(r"\s+", " ", ID_NOISE.sub(" ", descriptor)).strip()
    if "?" in descriptor or len(descriptor) > MAX_LABEL_CHARS or SENSITIVE.search(descriptor):
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


OPTION_SELECTOR = '[role="option"]'


def option_candidates(key: str, value: str) -> List[str]:
    return DEGREE_SYNONYMS.get(value, [value]) if key == "degree" else [value]


def _token_hit(token: str, option_tokens: List[str]) -> bool:
    return any(t == token or (len(token) >= 6 and t.startswith(token[:6])) for t in option_tokens)


def pick_option(options: Sequence[str], key: str, value: str) -> Optional[int]:
    """Index of the option that is clearly the wanted one, or None. Never guesses between loose matches."""
    folded = [_fold(o) for o in options]
    if key == "location":
        parts = [_fold(x) for x in value.split(",") if x.strip()]
        if len(parts) < 2:
            return None
        city, country = parts[0], parts[-1]
        hits = [i for i, opt in enumerate(folded) if opt.split(" ")[: len(city.split())] == city.split() and opt.endswith(country)]
        return hits[0] if hits else None
    wanted = [_fold(c) for c in option_candidates(key, value) if c]
    for cand in wanted:
        for i, opt in enumerate(folded):
            if opt == cand:
                return i
    for cand in wanted:
        hits = [i for i, opt in enumerate(folded) if opt and (opt.startswith(cand) or cand in opt)]
        if hits:
            return min(hits, key=lambda i: len(folded[i]))
    if key in ("school", "discipline"):
        tokens = [t for t in _fold(value).split() if t not in FILLER_WORDS and len(t) > 2]
        need = len(tokens) if key == "school" else max(2, -(-len(tokens) * 6 // 10))
        if len(tokens) >= (1 if key == "school" else 2):
            scored = []
            for i, opt in enumerate(folded):
                parts = opt.split()
                if sum(1 for t in tokens if _token_hit(t, parts)) >= need:
                    scored.append((len(opt), i))
            if scored:
                return min(scored)[1]
    return None


def combo_queries(key: str, value: str) -> List[str]:
    queries = [value.split(",")[0].strip()] if key == "location" else [value]
    if key == "school":
        distinct = [t for t in value.split() if _fold(t) not in FILLER_WORDS and len(t) > 3]
        if distinct:
            queries.append(max(distinct, key=len))
    return queries


def short_label(descriptor: str, limit: int = 80) -> str:
    """A form field's own label without the duplicated words, ids and autocomplete hints that labelFor() appends."""
    words = ID_NOISE.sub(" ", descriptor or "").replace("*", " ").split()
    for size in range(1, len(words) // 2 + 1):
        if [w.casefold() for w in words[:size]] == [w.casefold() for w in words[size:2 * size]]:
            return " ".join(words[:size])[:limit]
    return " ".join(words)[:limit]


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
        if f.get("type") != "file" and not (f.get("descriptor") or "").strip():
            continue  # unlabeled helper inputs that sit behind a styled dropdown
        label = short_label(f.get("descriptor") or "") or f.get("type") or f.get("tag") or "field"
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
            if key and key in answers and key not in used:
                choice = pick_option(f.get("options") or [], key, answers[key])
                if choice is not None:
                    used.add(key)
                    fills.append({"index": f["index"], "kind": "select", "key": key, "label": label, "value": f["options"][choice]})
                    continue
            left.append(label)
            continue
        if f.get("value"):
            continue
        if key and key in answers and key not in used:
            used.add(key)
            if f.get("role") == "combobox":
                fills.append({"index": f["index"], "kind": "combo", "key": key, "label": label, "value": answers[key]})
            else:
                fills.append({"index": f["index"], "kind": "text", "key": key, "label": label, "value": answers[key]})
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


def _open_combo(page: Any, selector: str) -> None:
    page.click(selector)


def _option_selector(page: Any, selector: str) -> str:
    """Options of this combobox only; pages can hold other role=option lists, such as phone country codes."""
    try:
        controls = page.get_attribute(selector, "aria-controls")
    except Exception:
        controls = None
    return f'[id="{controls}"] {OPTION_SELECTOR}' if controls else f".select__menu {OPTION_SELECTOR}"


def _click_option(page: Any, options_selector: str, index: int) -> None:
    page.locator(options_selector).nth(index).click()


def fill_combo(page: Any, selector: str, key: str, value: str) -> bool:
    """Type into a dropdown and pick the matching option; leaves it empty when no option is clearly right."""
    for query in combo_queries(key, value):
        _open_combo(page, selector)
        page.fill(selector, query)
        options: List[str] = []
        options_selector = _option_selector(page, selector)
        for _ in range(6):
            page.wait_for_timeout(500)
            try:
                options = page.eval_on_selector_all(options_selector, "els => els.map(e => e.innerText.trim())")
            except Exception:
                options = []
            if options:
                break
        index = pick_option(options, key, value)
        if index is not None:
            _click_option(page, options_selector, index)
            return True
    page.fill(selector, "")
    page.evaluate("document.activeElement && document.activeElement.blur()")
    return False


def apply_fills(page: Any, fills: List[Dict[str, Any]]) -> int:
    """Fill what the plan lists. Each fill gets `done` set, so the caller can report what is left for the human."""
    done = 0
    for fill in fills:
        selector = f'[data-aja-idx="{fill["index"]}"]'
        fill["done"] = False
        try:
            if fill["kind"] == "file":
                page.set_input_files(selector, fill["value"])
            elif fill["kind"] == "select":
                page.select_option(selector, label=fill["value"])
            elif fill["kind"] == "combo":
                if not fill_combo(page, selector, fill["key"], fill["value"]):
                    logger.info(f"No clear option for {fill['key']}={fill['value']!r}; left for you.")
                    continue
            else:
                page.fill(selector, fill["value"])
            fill["done"] = True
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
    preflight_fn: Optional[Callable[..., Dict[str, Any]]] = None,
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
            left += [f.get("label") or f["key"] for f in fills if not f.get("done")]
            fills = [f for f in fills if f.get("done")]
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
            summary.append(_preflight_summary(conn, job_id, profile, app, fills, left, answers, preflight_fn))
            if not human_checkpoint("\n".join(summary), input_fn=input_fn):
                logger.info("No confirmation received. Job stays READY_TO_APPLY.")
                return "pending"

        confirm_application(conn, job_id, notes=f"Filled {filled} field(s); submitted by candidate")
        METRICS.increment("applied_total")
        logger.info(f"Recorded APPLIED for job {job_id}; cooldown started for {app['company_name']}.")
        return "applied"


def _preflight_summary(
    conn: Any, job_id: int, profile: MasterProfile, app: Any, fills: List[Dict[str, Any]], left: List[str],
    answers: Dict[str, str], preflight_fn: Optional[Callable[..., Dict[str, Any]]],
) -> str:
    """Review of this exact application (role, documents, form values) shown at the checkpoint. Never blocks."""
    try:
        from tools.preflight import render_preflight, review_application
        cv = Path(app["cv_path"])
        result = (preflight_fn or review_application)(conn, job_id, profile, cv, cv.parent, fills=fills, left=left, answers=answers)
        return render_preflight(result)
    except Exception as exc:
        return f"  PRE-FLIGHT: skipped ({exc})"


def record_applied(job_id: int, db_path: Optional[Path] = None) -> str:
    """Record an application you submitted yourself after a pre-fill left the window open. Returns 'applied' or 'blocked'."""
    init_db(db_path)
    with get_db(db_path) as conn:
        try:
            confirm_application(conn, job_id, notes="Submitted by candidate; recorded afterwards")
        except ValueError as exc:
            logger.error(str(exc))
            return "blocked"
    METRICS.increment("applied_total")
    logger.info(f"Recorded APPLIED for job {job_id}; 30-day company cooldown started.")
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
    parser.add_argument("--record-applied", action="store_true", help="After you submitted the form yourself, record it and start the cooldown")
    parser.add_argument("--allow-host", action="append", default=[], help="Extra host (and its subdomains) to trust for this run")
    args = parser.parse_args()
    if args.list or args.job_id is None:
        for r in list_ready():
            print(f"[{r['id']}] {r['company']} - {r['title']}\n     {r['url']}")
        sys.exit(0)
    if args.record_applied:
        sys.exit(0 if record_applied(args.job_id) == "applied" else 1)
    outcome = run_apply(args.job_id, cdp_url=args.cdp_url, extra_hosts=tuple(args.allow_host))
    sys.exit(0 if outcome in ("applied", "pending") else 1)

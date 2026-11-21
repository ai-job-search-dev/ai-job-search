#!/usr/bin/env python3
"""Application assistant (Playwright): pre-fills the form, reviews it, and stops before Submit unless told otherwise.

Opens a READY_TO_APPLY job in a dedicated, headed Chrome profile, pre-fills identity, location and education
fields (dropdowns included, when exactly one option clearly matches), uploads the tailored CV and cover letter,
and answers the remaining questions from the answer bank (tools/answer_bank.py) or an approved answers.yaml.
It then prints a pre-flight review and stops at a checkpoint: you review the form and click Submit yourself.

`--submit --confirm-company "<exact name>"` lets the tool click Submit for that one application, and only when
every gate passes: the company name you typed matches, nothing required is left empty (consent boxes and
questions without an approved answer count), the pre-flight has no failed checks and no HOLD, and a form was
recognised. Otherwise it falls back to the checkpoint. It never solves CAPTCHAs, never answers a question
without an approved answer, never presses keys, and its only clicks are on dropdown inputs, dropdown options
and the Submit button. `--check` reports whether auto-submit would be allowed and sends nothing.
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
    STATUS_IGNORED,
    STATUS_READY_TO_APPLY,
    check_company_cooldown,
    confirm_application,
    get_db,
    get_ready_application,
    init_db,
    update_job_status,
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
    ("full_name", re.compile(r"first\s*(?:and|&|/)\s*last\s*name|name\s*(?:and|&)\s*surname|nombre\s+y\s+apellido", re.I)),
    ("first_name", re.compile(r"first[\s_-]*name|given[\s_-]*name|\bfname\b", re.I)),
    ("last_name", re.compile(r"last[\s_-]*name|family[\s_-]*name|surname|\blname\b", re.I)),
    ("full_name", re.compile(r"full[\s_-]*name|^\s*name\b|\byour[\s_-]*name", re.I)),
    ("citizenship", re.compile(r"passport|citizenship|nationality|nacionalidad|ciudadan[ií]a|pasaporte", re.I)),
    ("country", re.compile(r"\bcountry\b|\bpa[ií]s\b", re.I)),
    ("school", re.compile(r"\bschool\b|\buniversity\b|\binstitution\b", re.I)),
    ("degree", re.compile(r"\bdegree\b", re.I)),
    ("discipline", re.compile(r"\bdiscipline\b|field[\s_-]*of[\s_-]*study|\bmajor\b", re.I)),
    ("end_month", re.compile(r"(?:end|graduation)[\s_-]*(?:date)?[\s_-]*month", re.I)),
    ("end_year", re.compile(r"(?:end|graduation)[\s_-]*(?:date)?[\s_-]*year", re.I)),
    ("current_company", re.compile(r"(?:current|most recent)[\s/a-z-]*company|current employer|^\s*company(?: name)?\b|empresa actual", re.I)),
    ("current_title", re.compile(r"(?:current|most recent)[\s/a-z-]*(?:job )?title|^\s*(?:job )?title\b|cargo actual|puesto actual", re.I)),
    ("start_month", re.compile(r"start[\s_-]*(?:date)?[\s_-]*month", re.I)),
    ("start_year", re.compile(r"start[\s_-]*(?:date)?[\s_-]*year", re.I)),
    ("location", re.compile(r"\bcity\b|\blocation\b|where.*(?:based|located)|current[\s_-]*location", re.I)),
]
WHERE_BASED = re.compile(r"preferred (?:main )?location|^\s*current location\b|^\s*where (?:are you|do you (?:currently )?live|are you currently)\s*(?:based|located|living)?\b(?!.*\b(?:hear|find|see|learn)\b)|^\s*d[oó]nde (?:viv|est[aá]s)", re.I)
# Fields that depend on a short, plain label; a long question that merely mentions a country or a degree is never one.
REUSABLE_KEYS = {"country"}  # forms ask for it twice: phone country code and country of residence
NARROW_KEYS = {"start_month", "start_year", "citizenship", "country", "school", "degree", "discipline", "end_month", "end_year"}
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
    if (!parts.length) {
      const q = el.closest('li.application-question, .application-question, [class*=fieldEntry]');
      const t = q && q.querySelector('.application-label, .text, label');
      if (t && t.innerText.trim()) parts.push(t.innerText.split('\\n')[0]);
    }
    ['aria-label', 'placeholder', 'name', 'id', 'autocomplete'].forEach(a => { const v = el.getAttribute(a); if (v) parts.push(v); });
    return parts.join(' ').replace(/\\s+/g, ' ').trim();
  };
  const tagged = Array.from(document.querySelectorAll('[data-aja-idx]')).map(e => Number(e.getAttribute('data-aja-idx')));
  let next = tagged.length ? Math.max(...tagged) + 1 : 0;
  document.querySelectorAll('input, textarea, select').forEach((el, n) => {
    const style = window.getComputedStyle(el);
    const isFile = el.type === 'file';
    if (!isFile && (el.offsetParent === null || style.visibility === 'hidden')) return;
    const kept = el.getAttribute('data-aja-idx');
    const i = kept !== null ? Number(kept) : (tagged.length ? next++ : n);
    el.setAttribute('data-aja-idx', String(i));
    out.push({
      index: i,
      tag: el.tagName.toLowerCase(),
      type: (el.type || '').toLowerCase(),
      descriptor: labelFor(el),
      value: (el.type === 'checkbox' || el.type === 'radio') ? (el.checked ? (el.value || 'on') : '') : (el.value || ''),
      role: el.getAttribute('role') || (el.parentElement && el.parentElement.querySelector('.dropdown-results') ? 'combobox' : ''),
      required: !!(el.required || el.getAttribute('aria-required') === 'true' ||
                   (el.closest('[class*=fieldEntry]') && el.closest('[class*=fieldEntry]').querySelector('label[class*=required], [class*=required]'))),
      name: el.name || '',
      group: (() => { const g = el.closest('fieldset, [role=radiogroup], [role=group]'); const t = g && g.querySelector('legend, [class*=label], label');
                      return t ? t.innerText.replace(/\\s+/g, ' ').trim() : ''; })(),
      options: el.tagName.toLowerCase() === 'select' ? Array.from(el.options).map(o => o.text.trim()) : [],
    });
  });
  const seen = new Set();
  document.querySelectorAll('button').forEach(b => {
    const text = (b.innerText || '').trim();
    if (!/^(yes|no|s[ií]|sim|n[aã]o)$/i.test(text) || b.closest('form') === null && !b.closest('[class*=application]')) return;
    const box = b.parentElement && b.parentElement.parentElement;
    if (!box || seen.has(box)) return;
    const choices = Array.from(box.querySelectorAll('button')).filter(x => /^(yes|no|s[ií]|sim|n[aã]o)$/i.test((x.innerText || '').trim()));
    if (choices.length < 2) return;
    seen.add(box);
    const k = 100000 + seen.size;
    const question = (box.innerText || '').replace(/\\s+/g, ' ').replace(/(?:\\s(?:yes|no|s[ií]|sim|n[aã]o))+$/i, '').trim();
    choices.forEach(x => x.setAttribute('data-aja-choice', String(k)));
    out.push({index: k, tag: 'button', type: 'choice', descriptor: question, value: '', role: 'buttons',
              required: /\\*/.test(question) || !!box.querySelector('[class*=required]') || !!(box.parentElement && box.parentElement.querySelector('label[class*=required]')),
              name: '', group: question, options: choices.map(x => x.innerText.trim())});
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


COUNTRY_CODES = {"argentina": ("ar", "arg"), "italy": ("it", "ita"), "spain": ("es", "esp"), "united states": ("us", "usa"),
                 "germany": ("de", "deu"), "france": ("fr", "fra"), "brazil": ("br", "bra"), "portugal": ("pt", "prt")}
COUNTRY_BY_CODE = {"IT": "Italy", "AR": "Argentina", "ES": "Spain", "US": "United States", "DE": "Germany", "FR": "France", "PT": "Portugal", "BR": "Brazil"}
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
    citizenships = profile.preferences.work_authorization.citizenships
    if citizenships:
        answers["citizenship"] = COUNTRY_BY_CODE.get(citizenships[0].upper(), citizenships[0])
    current = next((e for e in profile.experience if e.is_current), None)
    if current is not None:
        answers["current_company"], answers["current_title"] = current.company, current.role
        started = re.match(r"(\d{4})(?:-(\d{2}))?", current.start_date or "")
        if started:
            answers["start_year"] = started.group(1)
            if started.group(2):
                answers["start_month"] = MONTHS[int(started.group(2)) - 1]
    answers.update(pick_education(profile))
    return {k: v for k, v in answers.items() if v}


def classify_field(descriptor: str, tag: str = "input", input_type: str = "text") -> Optional[str]:
    """Map a form field to a profile key, or None when it needs a human."""
    if input_type in SKIP_INPUT_TYPES or tag == "textarea":
        return None
    descriptor = re.sub(r"\s+", " ", ID_NOISE.sub(" ", descriptor)).strip()
    if WHERE_BASED.search(descriptor) and input_type != "file" and len(descriptor) <= MAX_LABEL_CHARS:
        return "location"
    if len(descriptor) > MAX_LABEL_CHARS and "?" not in descriptor:
        head = re.split(r"\s*[(\[]|\s[:.–—-]\s", descriptor, maxsplit=1)[0]
        if len(head) <= 40 and head != descriptor:
            return classify_field(head, tag, input_type)
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


YES_OPTIONS = ["Yes", "Sí", "Si", "Sim", "Oui", "Ja", "Sì", "I agree", "Agree", "Acknowledge", "Confirm", "Accept", "Acepto"]
NO_OPTIONS = ["No", "Não", "Nao", "Non", "Nein"]
DECLINE_OPTIONS = ["Decline to self-identify", "Decline to answer", "I don't wish to answer", "I do not wish to answer",
                   "Prefer not to say", "Prefer not to answer", "I prefer not to say", "I prefer not to answer", "Decline",
                   "I do not want to answer", "I don't want to answer", "Prefiero no decir", "Prefiero no responder"]
DEMONYMS = {"Italy": ["Italian"], "Argentina": ["Argentine", "Argentinian", "Argentinean"], "Spain": ["Spanish"],
            "United States": ["American"], "Germany": ["German"], "France": ["French"], "Brazil": ["Brazilian"]}


def option_candidates(key: str, value: str) -> List[str]:
    if key == "degree":
        return DEGREE_SYNONYMS.get(value, [value])
    if key == "citizenship":
        return [value, *DEMONYMS.get(value, [])]
    if value.casefold().startswith("decline"):
        return DECLINE_OPTIONS
    if value.casefold() in ("male", "man", "masculino", "hombre"):
        return ["Male", "Man", "Masculino", "Hombre", "Cisgender man", "Male/Man"]
    if value.casefold() in ("female", "woman", "femenino", "mujer"):
        return ["Female", "Woman", "Femenino", "Mujer", "Cisgender woman"]
    if re.search(r"not a (?:protected )?veteran", value, re.I):
        return [value, "I am not a protected veteran", "I do not identify as a protected veteran", "I am not a veteran", "Not a veteran",
                "Not a protected veteran", "No, I am not a protected veteran", "No"]
    if value.casefold() in ("a man", "a human", "human being"):
        return ["A man", "A human", "Human being", "A human being", "Man", "Human"]
    if "careers page" in value.casefold():
        return [value, "Company careers page", "Careers page", "Careers site", "Career site", "Careers website", "Company career site",
                "Company website", "Website", "Job board", "Job posting", "Other"]
    if value.casefold() == "advanced":
        return ["Advanced", "Very familiar", "Proficient", "Highly proficient"]
    if value in YES_OPTIONS:
        return YES_OPTIONS
    if value in NO_OPTIONS:
        return NO_OPTIONS
    return [value]


ACKNOWLEDGE_OPTION = re.compile(r"^(?:i )?(?:acknowledge|accept|agree|consent|understand|have read|confirm)", re.I)


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
        endings = {country, *COUNTRY_CODES.get(country, ())}
        hits = [i for i, opt in enumerate(folded) if opt.split(" ")[: len(city.split())] == city.split()
                and any(opt.endswith(" " + e) or opt == e for e in endings)]
        if not hits:  # some forms ask for the country only
            hits = [i for i, opt in enumerate(folded) if opt == country]
        return hits[0] if hits else None
    wanted = [_fold(c) for c in option_candidates(key, value) if c]
    for cand in wanted:
        for i, opt in enumerate(folded):
            if opt == cand:
                return i
    if value in YES_OPTIONS[:7] or value in NO_OPTIONS:
        hits = [i for i, opt in enumerate(folded) for cand in wanted if opt.split(" ")[:1] == [cand]]
        if not hits and value in YES_OPTIONS:  # consent dropdowns: "I acknowledge receipt of the notice"
            hits = [i for i, opt in enumerate(folded) if ACKNOWLEDGE_OPTION.search(opt)]
            return hits[0] if len(hits) == 1 else None
        return hits[0] if hits else None
    for cand in wanted:
        hits = [i for i, opt in enumerate(folded) if opt and re.search(rf"(?<![a-z0-9]){re.escape(cand)}(?![a-z0-9])", opt)]
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
    if key == "answer":
        return pick_range(options, value)
    return None


_AMOUNT = re.compile(r"\d[\d,.]*\s*[kK]?")


def _amounts(text: str) -> List[float]:
    out = []
    for m in _AMOUNT.findall(text):
        raw = m.strip()
        mult = 1000.0 if raw[-1:] in ("k", "K") else 1.0
        try:
            out.append(float(raw.rstrip("kK ").rstrip(".,").replace(",", "")) * mult)
        except ValueError:
            pass
    return out


def pick_range(options: Sequence[str], value: str) -> Optional[int]:
    """Index of the one numeric range option ("$15–$29", "$150+", "Under $10") that contains the first amount in `value`."""
    wanted = _amounts(value)
    if not wanted:
        return None
    amount = wanted[0]
    hits = []
    for i, opt in enumerate(options):
        nums = _amounts(opt)
        if not nums:
            continue
        if len(nums) >= 2:
            lo, hi = nums[0], nums[1]
        elif "+" in opt or re.search(r"or more|and above|above|over|more than|m[aá]s de", opt, re.I):
            lo, hi = nums[0], float("inf")
        elif re.search(r"under|less than|below|up to|menos de|hasta", opt, re.I):
            lo, hi = 0.0, nums[0]
        else:
            continue
        if lo <= amount <= hi:
            hits.append(i)
    return hits[0] if len(hits) == 1 else None


def combo_queries(key: str, value: str) -> List[str]:
    queries = [value.split(",")[0].strip(), value.split(",")[-1].strip()] if key == "location" else [value]
    if key == "answer":
        queries += [c for c in option_candidates(key, value)[1:4] if len(c) <= 30]
    if key in ("school", "discipline"):
        queries += re.findall(r"\(([^)]{2,12})\)", value)
        plain = re.sub(r"\([^)]*\)", " ", value)
        words = [w for w in plain.split() if _fold(w) not in FILLER_WORDS and len(w) > 3]
        queries += [" ".join(words[-2:]), *sorted(words, key=len, reverse=True)[:2]]
    return list(dict.fromkeys(q for q in queries if q))


def short_label(descriptor: str, limit: int = 80) -> str:
    """A form field's own label without the duplicated words, ids and autocomplete hints that labelFor() appends."""
    text = re.sub(r"\btype here\.*|\bselect\.\.\.|_systemfield_\w+", " ", ID_NOISE.sub(" ", descriptor or ""), flags=re.I)
    words = text.replace("*", " ").split()
    for size in range(1, len(words) // 2 + 1):
        if [w.casefold() for w in words[:size]] == [w.casefold() for w in words[size:2 * size]]:
            return " ".join(words[:size])[:limit]
    return " ".join(words)[:limit]


def plan_fills(
    fields: List[Dict[str, Any]],
    answers: Dict[str, str],
    cv_path: Optional[str],
    cover_letter_path: Optional[str],
    approved_answers: Sequence[Any] = (),
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Decide what to fill. Returns (fills, labels left for the human).

    `approved_answers` (answer bank or answers.yaml) are the only way a visa, authorization or other question
    beyond the profile keys is ever answered by this tool.
    """
    fills: List[Dict[str, Any]] = []
    left: List[str] = []
    used: set = set()
    files = {"resume_file": cv_path, "cover_letter_file": cover_letter_path}
    for f in fields:
        if f.get("type") != "file" and not (f.get("descriptor") or "").strip():
            continue  # unlabeled helper inputs that sit behind a styled dropdown
        label = short_label(f.get("descriptor") or "") or f.get("type") or f.get("tag") or "field"
        approved = next((a for a in approved_answers if getattr(a, "approved", False) and re.search(a.pattern, f.get("descriptor") or "", re.I)), None)
        if approved is not None and f.get("type") not in ("file", "checkbox", "radio") and not f.get("value") and ("answer", approved.pattern) not in used:
            if f.get("tag") in ("select", "button"):
                choice = pick_option(f.get("options") or [], "answer", approved.answer)
                if choice is not None:
                    used.add(("answer", approved.pattern))
                    kind = "select" if f.get("tag") == "select" else "choice"
                    fills.append({"index": f["index"], "kind": kind, "key": "answer", "label": label, "value": f["options"][choice]})
                    continue
            else:
                used.add(("answer", approved.pattern))
                kind = "combo" if f.get("role") == "combobox" else "text"
                fills.append({"index": f["index"], "kind": kind, "key": "answer", "label": label, "value": approved.answer})
                continue
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
        if f.get("value") or f.get("type") in ("checkbox", "radio"):
            continue  # boxes and radios are ticked from the answer bank; required_missing reports the unticked ones
        if key and key in answers and (key not in used or key in REUSABLE_KEYS):
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


def _tick(page: Any, selector: str) -> None:
    page.check(selector)


def _open_combo(page: Any, selector: str) -> None:
    page.click(selector)


def _click_choice(page: Any, group: int, text: str) -> None:
    """Click the Yes/No button of a question drawn as buttons."""
    page.locator(f'[data-aja-choice="{group}"]', has_text=text).first.click()


def _option_selector(page: Any, selector: str) -> str:
    """Options of this combobox only; pages can hold other role=option lists, such as phone country codes."""
    try:
        controls = page.get_attribute(selector, "aria-controls")
    except Exception:
        controls = None
    if controls:
        return f'[id="{controls}"] {OPTION_SELECTOR}'
    try:
        if page.evaluate("(s) => !!document.querySelector(s).parentElement.querySelector('.dropdown-results')", selector):
            return f"{selector} ~ .dropdown-container .dropdown-results > *"
        if page.evaluate("(s) => !!document.querySelector(s).closest('[class*=fieldEntry]')", selector):
            return 'div[class*="_result_"]:not([class*="resultContainer"]):not([class*="noResults"])'
    except Exception:
        pass
    return f".select__menu {OPTION_SELECTOR}"


def _click_option(page: Any, options_selector: str, index: int) -> None:
    page.locator(options_selector).nth(index).click()


def fill_combo(page: Any, selector: str, key: str, value: str) -> bool:
    """Pick the matching option of a dropdown; leaves it empty when no option is clearly right.

    The open list is tried first, untyped, because typing a long answer filters out short options such as "Prefer
    not to say"; then the queries from `combo_queries` are typed one by one for long or searched lists.
    """
    for query in ["", *combo_queries(key, value)]:
        page.evaluate("document.activeElement && document.activeElement.blur()")
        _open_combo(page, selector)
        if query:
            page.fill(selector, "")
            page.locator(selector).press_sequentially(query, delay=40)
        options: List[str] = []
        options_selector = _option_selector(page, selector)
        index = None
        for _ in range(2 if not query else 10):
            page.wait_for_timeout(500)
            try:
                options = page.eval_on_selector_all(options_selector, "els => els.map(e => e.innerText.trim())")
            except Exception:
                options = []
            index = pick_option(options, key, value) if options else None
            if index is not None:
                break
        if index is not None:
            _click_option(page, options_selector, index)
            return True
    page.fill(selector, "")
    page.evaluate("document.activeElement && document.activeElement.blur()")
    return False


ACTION_TIMEOUT_MS = 8000     # one click, fill or upload; a stuck element fails fast instead of the default 30 s
FILL_BUDGET_SECONDS = 240    # the whole fill phase; past it the form is reported as stuck


def apply_fills(page: Any, fills: List[Dict[str, Any]], budget_seconds: float = FILL_BUDGET_SECONDS) -> int:
    """Fill what the plan lists. Each fill gets `done` (and `error` when it failed), so the caller can report what is left."""
    import time
    deadline = time.monotonic() + budget_seconds
    try:
        page.set_default_timeout(ACTION_TIMEOUT_MS)
    except Exception:
        pass
    done = 0
    for fill in fills:
        selector = f'[data-aja-idx="{fill["index"]}"]'
        fill["done"] = False
        if time.monotonic() > deadline:
            fill["error"] = "stuck: the fill phase ran out of time"
            continue
        try:
            if fill["kind"] == "file":
                page.set_input_files(selector, fill["value"])
            elif fill["kind"] == "select":
                page.select_option(selector, label=fill["value"])
            elif fill["kind"] == "tick":
                _tick(page, selector)
            elif fill["kind"] == "choice":
                _click_choice(page, fill["index"], fill["value"])
            elif fill["kind"] == "combo":
                picked = fill_combo(page, selector, fill["key"], fill["value"])
                if not picked and fill["key"] == "school" and fill_combo(page, selector, "answer", "Other"):
                    fill["value"] = "Other"  # the school is not in the form's list; "Other" is the truthful pick
                    picked = True
                if not picked:
                    logger.info(f"No clear option for {fill['key']}={fill['value']!r}; left for you.")
                    continue
            else:
                page.fill(selector, fill["value"])
            fill["done"] = True
            done += 1
        except Exception as exc:
            fill["error"] = str(exc).splitlines()[0][:120]
            logger.warning(f"Could not fill {fill['key']}: {fill['error']}")
    return done


READBACK_JS = """
() => Array.from(document.querySelectorAll('[data-aja-idx]')).map(el => {
  const shell = el.closest('.select__control, .select-shell, .select, [class*=select__container]');
  const single = shell && shell.querySelector('[class*=singleValue], [class*=single-value], [class*=multiValue], [class*=multi-value]');
  return {idx: Number(el.getAttribute('data-aja-idx')), value: el.value || '', checked: !!el.checked,
          display: single ? single.innerText.trim() : '', files: el.files ? el.files.length : 0};
})
"""


def verify_commits(page: Any, fills: List[Dict[str, Any]]) -> List[str]:
    """Read the page back. Fills that report done but are not on the page get done=False; returns their labels.

    Some widgets clear a value on blur, or upload a file and reset the input; a fill is only trusted once the page
    shows it. Files count as committed when the input holds them or their name appears on the page.
    """
    try:
        state = {r["idx"]: r for r in page.evaluate(READBACK_JS)}
        body = page.evaluate("() => document.body.innerText")
    except Exception:
        return []
    lost: List[str] = []
    for fill in fills:
        if not fill.get("done"):
            continue
        if fill["kind"] == "choice":
            try:
                ok = page.evaluate("""([k, t]) => Array.from(document.querySelectorAll('[data-aja-choice="' + k + '"]')).some(b =>
                    b.innerText.trim() === t && (b.getAttribute('aria-pressed') === 'true' || /active|selected|checked/i.test(b.className)))""",
                                   [str(fill["index"]), fill["value"]])
            except Exception:
                ok = True
            if not ok:
                fill["done"], fill["error"] = False, "not kept by the page"
                lost.append(fill.get("label") or fill["key"])
            continue
        st = state.get(fill["index"])
        if st is None:
            continue
        if fill["kind"] == "file":
            ok = st["files"] > 0 or Path(str(fill["value"])).name in body
        elif fill["kind"] == "tick":
            ok = st["checked"]
        elif fill["kind"] in ("combo", "select"):
            ok = bool(st["display"] or st["value"])
        else:
            ok = _fold(st["value"]) == _fold(str(fill["value"]))
        if not ok:
            fill["done"], fill["error"] = False, "not kept by the page"
            lost.append(fill.get("label") or fill["key"])
    return lost
SUBMIT_NAME = re.compile(r"^\s*submit(?:\s+application)?\s*$", re.I)
CONFIRMED = re.compile(r"thank you for (?:applying|your application|submitting)|application (?:has been |was )?(?:successfully )?(?:submitted|received)|"
                       r"application success|successfully submitted|we(?:'|\u2019)?ve received your application", re.I)
REJECTED = re.compile(r"this field is required|please (?:fix|correct|complete)|is required|invalid", re.I)


def required_missing(fields: Sequence[Dict[str, Any]], fills: Sequence[Dict[str, Any]]) -> List[str]:
    """Labels of required fields that nothing filled. Consent checkboxes count: only a person can tick them."""
    done = {f["index"] for f in fills if f.get("done")}
    answered_groups = {f.get("name") for f in fields if (f.get("index") in done or f.get("value")) and f.get("type") == "radio" and f.get("name")}
    missing: List[str] = []
    for f in fields:
        if not f.get("required") or f.get("index") in done or f.get("value") or not (f.get("descriptor") or "").strip():
            continue
        if f.get("type") == "radio" and f.get("name") in answered_groups:
            continue
        label = short_label((f.get("type") == "radio" and f.get("group")) or f["descriptor"])
        if label not in missing:
            missing.append(label)
    return missing


RESIDENCE_LOCK = re.compile(
    r"state where you will reside|(?:u\.?s\.?|united states) state (?:or canadian province )?(?:do )?you (?:reside|live)"
    r"|must (?:hold|have) (?:united states|u\.?s\.?) citizenship|must be (?:a )?(?:united states|u\.?s\.?) citizen", re.I)


def form_ineligible(fields: Sequence[Dict[str, Any]], missing: Sequence[str], profile: Any) -> Optional[str]:
    """The required question that no truthful answer can satisfy (US-state residence, US citizenship), if any."""
    location = (profile.identity.location or "").casefold()
    if any(place in location for place in ("united states", "usa", "canada")) or "US" in [c.upper() for c in profile.preferences.work_authorization.citizenships]:
        return None
    for f in fields:
        if short_label(f.get("descriptor") or "") in missing and RESIDENCE_LOCK.search(f.get("descriptor") or ""):
            return short_label(f["descriptor"], limit=160)
    return None


def submission_gates(
    company: str, confirm_company: str, fields: Sequence[Dict[str, Any]], fills: Sequence[Dict[str, Any]],
    preflight: Optional[Dict[str, Any]], recognised: bool, allow_model_fix: bool = False,
) -> List[str]:
    """Reasons this application may not be submitted by the tool. An empty list means every gate passed."""
    reasons: List[str] = []
    if (confirm_company or "").strip().casefold() != company.strip().casefold():
        reasons.append(f"--confirm-company must be exactly '{company}'")
    if not recognised:
        reasons.append("no application form was recognised on the page")
    missing = required_missing(fields, fills)
    if missing:
        reasons.append("required fields nobody filled: " + "; ".join(missing[:6]))
    if preflight is None:
        reasons.append("the pre-flight review did not run")
    else:
        if preflight.get("checks"):
            reasons.append("pre-flight checks failed: " + " | ".join(preflight["checks"][:3]))
        verdict = (preflight.get("model") or {}) if isinstance(preflight.get("model"), dict) else {}
        if verdict and "error" in verdict and not allow_model_fix:
            reasons.append("the model review failed to run (use --allow-model-fix to proceed on the exact checks alone)")
        if preflight.get("verdict") == "hold":
            reasons.append("the model review says HOLD")
        elif preflight.get("verdict") == "fix" and not preflight.get("checks") and not allow_model_fix and verdict and "error" not in verdict:
            reasons.append("the model review asks for fixes (use --allow-model-fix once you read them)")
    return reasons


def _click_submit(page: Any) -> None:
    page.get_by_role("button", name=SUBMIT_NAME).click()


def submission_outcome(page: Any) -> str:
    """'confirmed' when the page shows an acknowledgement, 'rejected' when it shows validation errors, else 'unknown'."""
    try:
        text = page.evaluate("() => document.body.innerText")  # visible text: the HTML also holds hidden error templates
        if not isinstance(text, str):
            text = page.content()
    except Exception:
        return "unknown"
    if CONFIRMED.search(text) or re.search(r"confirmation|thank", getattr(page, "url", "") or "", re.I):
        return "confirmed"
    return "rejected" if REJECTED.search(text) else "unknown"


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


def _ensure_cdp_window(cdp_url: str, force: bool = False) -> None:
    """A Chrome whose windows were all closed refuses CDP sessions; open a blank tab first."""
    import json
    import urllib.request
    base = cdp_url.rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/json/list", timeout=5) as resp:
            if any(t.get("type") == "page" for t in json.load(resp)) and not force:
                return
        urllib.request.urlopen(urllib.request.Request(f"{base}/json/new?about:blank", method="PUT"), timeout=5).read()
        import time
        for _ in range(10):
            time.sleep(0.5)
            with urllib.request.urlopen(f"{base}/json/list", timeout=5) as resp:
                if any(t.get("type") == "page" for t in json.load(resp)):
                    time.sleep(1.0)
                    return
    except Exception as exc:
        logger.warning(f"Could not check the Chrome window at {cdp_url}: {exc}")


def _connect_cdp(pw: Any, cdp_url: str) -> Any:
    """Attach to the dedicated Chrome; a Chrome that just lost its last window needs a moment and a fresh tab."""
    import time
    last: Optional[Exception] = None
    for attempt in range(6):
        try:
            return pw.chromium.connect_over_cdp(cdp_url)
        except Exception as exc:
            last = exc
            if "context management" not in str(exc):
                raise
            time.sleep(1.5)
            _ensure_cdp_window(cdp_url, force=attempt % 2 == 1)
    raise RuntimeError(f"Chrome at {cdp_url} refuses automation sessions. Click its window or open a new tab there, then run again.") from last


def _form_key(url: str) -> str:
    """Path and query without the host: boards.greenhouse.io and job-boards.greenhouse.io serve the same form."""
    parsed = urlparse(url or "")
    return parsed.path.rstrip("/") + ("?" + parsed.query if parsed.query else "")


def _close_previous_attempts(context: Any, url: str) -> None:
    """Earlier tabs of the same form: left open after a check, they pile up and slow Chrome down."""
    key = _form_key(url)
    open_pages = [p for p in context.pages if _form_key(p.url) == key]
    for page in open_pages:
        try:
            page.close()
        except Exception:
            pass


@contextmanager
def open_browser_page(url: str, cdp_url: Optional[str] = None) -> Iterator[Any]:
    """Yield a Playwright page in a headed, dedicated Chrome profile (never the daily profile)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        if cdp_url:
            browser = _connect_cdp(pw, cdp_url)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            _close_previous_attempts(context, url)
            page = context.new_page()
            page.bring_to_front()
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
    submit: bool = False,
    confirm_company: str = "",
    allow_model_fix: bool = False,
    check_only: bool = False,
) -> str:
    """Returns 'applied', 'pending' or 'blocked'. Nothing is filled unless the final page is on an approved https host.

    By default the tool stops before Submit. With `submit=True` it clicks Submit itself, but only when every gate in
    `submission_gates` passes, including `confirm_company` equal to the company name. `check_only` fills nothing
    permanently and reports whether auto-submit would be allowed.
    """
    profile = load_profile(profile_path or DEFAULT_PROFILE_FILE)
    init_db(db_path)
    with get_db(db_path) as conn:
        app = get_ready_application(conn, job_id)
        if app is None or app["job_status"] != STATUS_READY_TO_APPLY:
            logger.error(f"Job {job_id} is not READY_TO_APPLY. Run tools/tailor.py first.")
            return "blocked"
        in_cooldown, reason = check_company_cooldown(conn, app["company_name"])
        if in_cooldown and profile.preferences.company_cooldown_days > 0:
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
            for _ in range(15):  # single-page ATS forms (Ashby, Lever) render after load
                if form_recognised(fields):
                    break
                page.wait_for_timeout(1000)
                fields = page.evaluate(COLLECT_FIELDS_JS)
            from tools.answer_bank import load_job_answers
            job_answers = load_job_answers(Path(app["cv_path"]).parent)
            base_fills, _ = plan_fills(fields, answers, app["cv_path"], app["cover_letter_path"], job_answers)
            bank = _bank_answers(conn, job_id, profile, fields, [f["index"] for f in base_fills])
            fills, left = plan_fills(fields, answers, app["cv_path"], app["cover_letter_path"], [*job_answers, *bank["answers"]])
            for index in bank["ticks"]:
                fills.append({"index": index, "kind": "tick", "key": "tick", "label": short_label(next((f.get("descriptor") or "") for f in fields if f["index"] == index)), "value": "checked"})
            left = [l for l in left if not any(short_label(next((f.get("descriptor") or "") for f in fields if f["index"] == i)) == l for i in bank["ticks"])]
            filled = apply_fills(page, fills)
            filled += _fill_follow_ups(conn, job_id, profile, page, fields, fills, answers, app)
            lost = verify_commits(page, fills)
            stuck = [f.get("label") or f["key"] for f in fills if (f.get("error") or "").startswith("stuck")]
            filled -= len(lost)
            left += [f.get("label") or f["key"] for f in fills if not f.get("done")]
            fills = [f for f in fills if f.get("done")]
            _save_form_values(Path(app["cv_path"]).parent, fills, left)
            summary = [
                f"  Job:      {app['company_name']} - {app['title']}",
                f"  Form:     language {bank['language']}" + ("; asks for your own words" if bank["own_words"] else ""),
                *[f"  Bank:     {line}" for line in bank["report"]],
                f"  Filled:   {filled} field(s) ({', '.join(f['key'] for f in fills) or 'none'})",
                f"  For you:  {len(left)} field(s) left untouched"
                + (f": {'; '.join(left[:8])}" if left else ""),
            ]
            if lost:
                summary.append("  NOT KEPT: the page dropped " + "; ".join(l[:40] for l in lost[:6]) + " (left for you)")
            if stuck:
                summary.append(f"  STUCK:    the form stopped responding; {len(stuck)} field(s) were not attempted")
            if not form_recognised(fields):
                summary.append("  WARNING:  No application form was recognised on this page (login wall, iframe or a different site).")
                summary.append("            Open the real application page yourself; nothing useful was filled.")
            if detect_captcha(page.content()):
                summary.append("  NOTE:     A CAPTCHA widget is on the page. If it asks, solve it yourself; this tool will not.")
            result = _run_preflight(conn, job_id, profile, app, fills, left, answers, preflight_fn)
            summary.append(render_preflight_text(result))
            recognised = form_recognised(fields)
            blockers = submission_gates(app["company_name"], confirm_company if submit else "(auto-submit off)", fields, fills, result, recognised, allow_model_fix)
            if stuck:
                blockers.append("the form got stuck while filling")
            lock = form_ineligible(fields, required_missing(fields, fills), profile)
            if lock and not check_only:
                update_job_status(conn, job_id, STATUS_IGNORED, rejection_reason=f"form requires: {lock}")
                conn.commit()
                print("\n".join(summary + [f"  NOT ELIGIBLE: the form requires '{lock}'. Marked IGNORED; nothing was sent."]))
                try:
                    page.close()
                except Exception:
                    pass
                return "ineligible"
            if check_only:
                summary.append("  AUTO-SUBMIT: " + ("allowed once you add --submit --confirm-company" if not [b for b in blockers if not b.startswith("--confirm-company")] else "blocked: " + "; ".join(b for b in blockers if not b.startswith("--confirm-company"))))
                print("\n".join(summary))
                return "pending"
            try:
                (Path(app["cv_path"]).parent / "apply_summary.txt").write_text("\n".join(summary), encoding="utf-8")
            except OSError:
                pass
            if submit and not blockers:
                print("\n".join(summary))
                logger.info(f"All gates passed for job {job_id}; submitting.")
                _click_submit(page)
                outcome = "unknown"
                for _ in range(40):  # Ashby verifies its invisible CAPTCHA before showing the result; this can take 30 s or more
                    page.wait_for_timeout(1500)
                    outcome = submission_outcome(page)
                    if outcome != "unknown":
                        break
                try:
                    page.screenshot(path=str(Path(app["cv_path"]).parent / "submitted.png"))
                except Exception:
                    pass
                if outcome == "confirmed":
                    confirm_application(conn, job_id, cooldown_days=profile.preferences.company_cooldown_days,
                                        notes=f"Filled {filled} field(s); submitted by the tool after explicit confirmation; confirmation page seen")
                    try:
                        page.close()  # a confirmed tab is done; open tabs pile up over a batch and slow Chrome
                    except Exception:
                        pass
                    METRICS.increment("applied_total")
                    logger.info(f"Submitted and recorded APPLIED for job {job_id}; cooldown started for {app['company_name']}.")
                    return "applied"
                what = "validation errors" if outcome == "rejected" else "no confirmation after 60 s (stuck or still sending)"
                logger.warning(f"Submit was clicked but the page shows {what}. Check the window; if it went through, run --record-applied.")
                return "pending"
            if submit:
                summary.append("  AUTO-SUBMIT BLOCKED: " + "; ".join(blockers) + ". Review and submit yourself, or fix these and run again.")
            if not human_checkpoint("\n".join(summary), input_fn=input_fn):
                logger.info("No confirmation received. Job stays READY_TO_APPLY.")
                return "pending"
        confirm_application(conn, job_id, cooldown_days=profile.preferences.company_cooldown_days, notes=f"Filled {filled} field(s); submitted by candidate")
        METRICS.increment("applied_total")
        logger.info(f"Recorded APPLIED for job {job_id}; cooldown started for {app['company_name']}.")
        return "applied"


def _run_preflight(
    conn: Any, job_id: int, profile: MasterProfile, app: Any, fills: List[Dict[str, Any]], left: List[str],
    answers: Dict[str, str], preflight_fn: Optional[Callable[..., Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    """Review of this exact application (role, documents, form values). None when it could not run."""
    try:
        from tools.preflight import review_application
        cv = Path(app["cv_path"])
        return (preflight_fn or review_application)(conn, job_id, profile, cv, cv.parent, fills=fills, left=left, answers=answers)
    except Exception as exc:
        logger.warning(f"Pre-flight skipped: {exc}")
        return None


def _fill_follow_ups(conn: Any, job_id: int, profile: MasterProfile, page: Any, fields: List[Dict[str, Any]],
                     fills: List[Dict[str, Any]], answers: Dict[str, str], app: Any) -> int:
    """Fields revealed by earlier answers. Each is read together with the question above it, then answered the same way."""
    try:
        page.wait_for_timeout(800)
        now = page.evaluate(COLLECT_FIELDS_JS)
    except Exception:
        return 0
    known = {f["index"] for f in fields}
    by_index = {f["index"]: f for f in fields}
    new = []
    for f in now:
        if f["index"] in known or not (f.get("descriptor") or "").strip():
            continue
        before = max((i for i in known if i < f["index"]), default=None)
        context = by_index[before]["descriptor"] if before is not None else ""
        new.append({**f, "descriptor": f"{context} {f['descriptor']}".strip()})
    if not new:
        return 0
    bank = _bank_answers(conn, job_id, profile, new, [])
    extra, _ = plan_fills(new, answers, app["cv_path"], app["cover_letter_path"], bank["answers"])
    for index in bank["ticks"]:
        extra.append({"index": index, "kind": "tick", "key": "tick", "label": "follow-up", "value": "checked"})
    done = apply_fills(page, extra)
    fields.extend(new)
    fills.extend(extra)
    return done


def _save_form_values(folder: Path, fills: List[Dict[str, Any]], left: List[str]) -> None:
    """What was typed, uploaded or ticked, and what was left, beside the documents: the record of what you sent."""
    import json
    record = {"filled": [{"field": f.get("label") or f["key"], "kind": f["kind"], "value": Path(f["value"]).name if f["kind"] == "file" else f["value"]} for f in fills],
              "left_for_you": left}
    try:
        (folder / "form_values.json").write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _bank_answers(conn: Any, job_id: int, profile: MasterProfile, fields: List[Dict[str, Any]], skip: List[int]) -> Dict[str, Any]:
    """Answers from the answer bank for what the profile keys do not cover. Never raises."""
    try:
        from tools.answer_bank import answer_form
        from tools.llm import LLMGateway, llm_configured
        from tools.preflight import load_job
        gateway = LLMGateway(conn, use_cache=True) if llm_configured() else None
        return answer_form(conn, profile, load_job(conn, job_id), fields, gateway, skip_indexes=skip)
    except Exception as exc:
        logger.warning(f"Answer bank skipped: {exc}")
        return {"answers": [], "ticks": [], "report": [f"skipped ({exc})"], "language": "en", "own_words": False}


def render_preflight_text(result: Optional[Dict[str, Any]]) -> str:
    if result is None:
        return "  PRE-FLIGHT: skipped"
    from tools.preflight import render_preflight
    return render_preflight(result)


def record_applied(job_id: int, db_path: Optional[Path] = None, cooldown_days: Optional[int] = None) -> str:
    """Record an application you submitted yourself after a pre-fill left the window open. Returns 'applied' or 'blocked'."""
    init_db(db_path)
    with get_db(db_path) as conn:
        try:
            days = cooldown_days if cooldown_days is not None else load_profile(DEFAULT_PROFILE_FILE).preferences.company_cooldown_days
            confirm_application(conn, job_id, cooldown_days=days,
                                notes="Submitted by candidate; recorded afterwards")
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
    parser.add_argument("--check", action="store_true", help="Fill, review and report whether auto-submit would be allowed; send nothing")
    parser.add_argument("--submit", action="store_true", help="Let the tool click Submit, only when every gate passes (needs --confirm-company)")
    parser.add_argument("--confirm-company", default="", help="The company's exact name, typed by you, to authorise --submit for this one application")
    parser.add_argument("--allow-model-fix", action="store_true", help="Allow --submit when only the model review asks for fixes, after you read them")
    parser.add_argument("--auto", action="store_true", help="Submit without asking when every gate passes; the model review is advisory, HOLD still blocks")
    parser.add_argument("--record-applied", action="store_true", help="After you submitted the form yourself, record it and start the cooldown")
    parser.add_argument("--allow-host", action="append", default=[], help="Extra host (and its subdomains) to trust for this run")
    args = parser.parse_args()
    if args.list or args.job_id is None:
        for r in list_ready():
            print(f"[{r['id']}] {r['company']} - {r['title']}\n     {r['url']}")
        sys.exit(0)
    if args.record_applied:
        sys.exit(0 if record_applied(args.job_id) == "applied" else 1)
    if args.auto:
        from tools.db import get_db as _get_db
        with _get_db() as _conn:
            _row = _conn.execute("SELECT c.name FROM jobs j JOIN companies c ON c.id = j.company_id WHERE j.id = ?", (args.job_id,)).fetchone()
        args.submit, args.allow_model_fix = True, True
        args.confirm_company = args.confirm_company or (_row[0] if _row else "")
    if args.submit and not args.confirm_company:
        sys.exit("--submit needs --confirm-company with the company's exact name")
    outcome = run_apply(args.job_id, cdp_url=args.cdp_url, extra_hosts=tuple(args.allow_host), submit=args.submit,
                        confirm_company=args.confirm_company, allow_model_fix=args.allow_model_fix, check_only=args.check)
    sys.exit(0 if outcome in ("applied", "pending") else 1)

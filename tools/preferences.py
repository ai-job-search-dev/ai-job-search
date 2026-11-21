#!/usr/bin/env python3
"""Preference-driven screening: one place that turns a user's `preferences` into decisions.

The ranker (heuristic and LLM paths), the database threshold and the tailor all read the
same `CareerPreferences` loaded from profile/master_profile.yaml, so changing a rule means
editing that YAML block and nothing else. This module is pure: it never touches the database.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple, Any, Dict, List, Optional, Set

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.profile_schema import CareerPreferences, RuleSetting

PROFILE_PATH = ROOT_DIR / "profile" / "master_profile.yaml"

EU = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU",
      "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"}
EEA = EU | {"IS", "LI", "NO"}
REGION_GROUPS: Dict[str, Set[str]] = {"EU": EU, "EEA": EEA}

COUNTRY_NAMES: Dict[str, str] = {
    "US": r"united states(?: of america)?", "CA": r"canada", "UK": r"united kingdom|great britain|england|scotland|wales",
    "AU": r"australia", "NZ": r"new zealand", "AR": r"argentina", "CH": r"switzerland", "IN": r"india",
    "BR": r"brazil|brasil", "MX": r"mexico", "CL": r"chile", "UY": r"uruguay", "CO": r"colombia",
    "SG": r"singapore", "IL": r"israel", "JP": r"japan", "NO": r"norway", "IS": r"iceland", "LI": r"liechtenstein",
    "AT": r"austria", "BE": r"belgium", "BG": r"bulgaria", "HR": r"croatia", "CY": r"cyprus",
    "CZ": r"czech republic|czechia", "DK": r"denmark", "EE": r"estonia", "FI": r"finland", "FR": r"france",
    "DE": r"germany", "GR": r"greece", "HU": r"hungary", "IE": r"(?<!northern )ireland", "IT": r"italy",
    "LV": r"latvia", "LT": r"lithuania", "LU": r"luxembourg", "MT": r"malta", "NL": r"netherlands|holland",
    "PL": r"poland", "PT": r"portugal", "RO": r"romania", "SK": r"slovakia", "SI": r"slovenia", "ES": r"spain",
    "SE": r"sweden",
    "PE": r"peru|per[uú]", "EC": r"ecuador", "PY": r"paraguay", "BO": r"bolivia", "VE": r"venezuela", "CR": r"costa rica",
    "GT": r"guatemala", "DO": r"dominican republic", "PA": r"panama|panam[aá]", "SV": r"el salvador", "HN": r"honduras",
    "NI": r"nicaragua", "TR": r"turkey|t[uü]rkiye", "EG": r"egypt", "ID": r"indonesia", "TH": r"thailand", "VN": r"vietnam|viet nam",
    "PH": r"philippines", "MY": r"malaysia", "PK": r"pakistan", "NG": r"nigeria", "KE": r"kenya", "ZA": r"south africa",
    "MA": r"morocco", "AE": r"united arab emirates|\buae\b|dubai", "SA": r"saudi arabia", "KR": r"south korea|korea",
    "CN": r"china", "TW": r"taiwan", "HK": r"hong kong", "UA": r"ukraine", "RS": r"serbia", "GE": r"georgia(?!,? (?:usa|us|united))",
}
US_STATE = re.compile(r",\s*(?:AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\b")
CA_PROVINCE = re.compile(r",\s*(?:ON|QC|BC|AB|MB|SK|NS|NB|NL|PE)\b|ontario|qu[eé]bec|british columbia|alberta", re.IGNORECASE)
COUNTRY_REGEX = {c: re.compile(r"(?<![A-Za-z])(?:" + p + r")(?![A-Za-z])", re.IGNORECASE) for c, p in COUNTRY_NAMES.items()}
ABBREVIATIONS = {"US": re.compile(r"\b(?:US|USA|U\.S\.A?\.?)(?!\w)"), "UK": re.compile(r"\b(?:UK|U\.K\.)(?!\w)")}
EU_PHRASE = re.compile(r"\bEU\b|\bE\.U\.|european union|\bEEA\b|european economic area|\bEurope\b|\bEMEA\b", re.IGNORECASE)

RESTRICTION_TRIGGER = re.compile(
    r"(?:authori[sz]ed to work in|eligible to work in|right to work in|legally (?:authori[sz]ed|permitted|entitled) to work in|"
    r"(?:must|should|need to)(?: currently| be able to)? (?:reside|live|be located|be based|be resident) in|"
    r"(?:must|should) be (?:a )?(?:resident|citizen) of|work authori[sz]ation (?:for|in)|"
    r"open to candidates (?:located )?in|candidates (?:must be )?(?:located|based) in)\s+(?:the\s+)?(.{0,60})",
    re.IGNORECASE,
)
NO_SPONSOR = re.compile(
    r"(?:will not|does not|do not|unable to|cannot|can't|not able to) (?:provide )?(?:visa )?sponsor|"
    r"not eligible for (?:visa|sponsorship)|no (?:visa )?sponsorship",
    re.IGNORECASE,
)
UNPAID = re.compile(
    r"\bunpaid\b(?!\s+(?:leave|time off|vacation|holiday|days?|pto|sabbatical))|volunteer (?:position|role|opportunity|basis)|"
    r"without (?:pay|compensation)|no (?:compensation|stipend|salary)|non-?paid|pro bono|for (?:course|academic) credit only",
    re.IGNORECASE,
)
EMPLOYMENT_PATTERNS = {
    "internship": re.compile(r"\bintern(?:ship)?s?\b|\bco-?op\b|\btrainee\b|working student|werkstudent|apprentice", re.IGNORECASE),
    "part_time": re.compile(r"\bpart[- ]time\b", re.IGNORECASE),
    "contract": re.compile(r"\bcontract(?:or)?\b|freelance|fixed[- ]term|\btemporary\b", re.IGNORECASE),
}

RULE_TEXT = {
    "hardware_lab": ("wet-lab, cryogenics, laser or cleanroom hardware roles", r"hardware|wet-?lab|cryo|cleanroom|laser|fabricat"),
    "research_phd": ("research-only roles that require a PhD", r"ph\.?d|doctor|research|academic|theoretical"),
    "non_engineering": ("non-engineering roles (sales, support, recruiting, operations)",
                        r"non-?engineering|not a software|not an engineering|sales|support|recruit|operations|marketing"),
    "management": ("management roles (manager, director, head of)", r"manage|director|head of|leadership"),
    "citizenship_clearance": ("postings that require US citizenship or a security clearance", r"citizen|clearance|itar|export"),
    "unpaid": ("unpaid or volunteer positions", r"unpaid|volunteer|no compensation|without pay"),
    "work_authorization": ("postings restricted to regions where the candidate cannot work without a visa", r"visa|authori[sz]|sponsor|reside"),
    "onsite_abroad": ("on-site, hybrid or country-locked remote postings outside the candidate's country, when they will not relocate", r"on-?site|in-?office|hybrid|relocat|country|reside"),
}


@dataclass
class AuthorizationAssessment:
    penalty: float = 0.0
    required_regions: Set[str] = field(default_factory=set)
    no_sponsorship: bool = False
    note: str = ""


def expand_regions(tokens: List[str]) -> Set[str]:
    """Turn ['EU', 'AR'] into a set of country codes."""
    out: Set[str] = set()
    for token in tokens:
        token = token.strip().upper()
        out |= REGION_GROUPS.get(token, {token})
    return out


def regions_in(text: str) -> Set[str]:
    """Country codes mentioned in `text`; 'Europe' or 'EU' expand to the EU group."""
    found = {code for code, rx in COUNTRY_REGEX.items() if rx.search(text)}
    found |= {code for code, rx in ABBREVIATIONS.items() if rx.search(text)}
    if re.search(r"north america", text, re.IGNORECASE):
        found |= {"US", "CA"}
    if US_STATE.search(text):
        found.add("US")
    if CA_PROVINCE.search(text):
        found.add("CA")
    if EU_PHRASE.search(text):
        found |= EEA
    return found


def posting_regions(location: str, description: str) -> Set[str]:
    """Regions a posting is tied to: its location plus 'authorized to work in X' style clauses."""
    regions = regions_in(location or "")
    for match in RESTRICTION_TRIGGER.finditer(description or ""):
        regions |= regions_in(match.group(1))
    return regions


def assess_work_authorization(prefs: CareerPreferences, location: str, description: str) -> AuthorizationAssessment:
    wa = prefs.work_authorization
    authorized = expand_regions(wa.authorized_regions)
    if wa.current_country:
        authorized |= expand_regions([wa.current_country])
    visa = expand_regions(wa.visa_possible_regions)
    required = posting_regions(location, description)
    no_sponsor = bool(NO_SPONSOR.search(description or ""))
    result = AuthorizationAssessment(required_regions=required, no_sponsorship=no_sponsor)

    if required:
        if required & authorized:
            return result
        if required & visa:
            result.penalty = max(wa.visa_region_penalty, wa.no_sponsorship_penalty if no_sponsor else 0.0)
            result.note = f"tied to {sorted(required)}: a work visa would be needed"
        else:
            result.penalty = max(wa.unauthorized_region_penalty, wa.no_sponsorship_penalty if no_sponsor else 0.0)
            result.note = f"tied to {sorted(required)}: no work authorization"
    elif no_sponsor:
        result.penalty = wa.no_sponsorship_penalty
        result.note = "employer will not sponsor a visa"
    return result


def detect_employment_types(title: str, description: str) -> Set[str]:
    """Employment types signalled in the title or the opening of the description; default full_time."""
    head = f"{title}\n{(description or '')[:400]}"
    found = {kind for kind, rx in EMPLOYMENT_PATTERNS.items() if rx.search(head)}
    return found or {"full_time"}


def is_unpaid(title: str, description: str) -> bool:
    return bool(UNPAID.search(f"{title}\n{description or ''}"))


def load_preferences(path: Optional[Path] = None) -> CareerPreferences:
    """Read only the `preferences` block of the profile. Missing file or block gives the defaults."""
    p = Path(path) if path else PROFILE_PATH
    if not p.exists():
        return CareerPreferences()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return CareerPreferences.model_validate(data.get("preferences") or {})


def load_candidate_context(path: Optional[Path] = None) -> str:
    """Short candidate description for the LLM prompt, built from the profile without writing to it."""
    p = Path(path) if path else PROFILE_PATH
    if not p.exists():
        return "No candidate profile available."
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    skills = [s["name"] for s in data.get("skills", []) if isinstance(s, dict) and s.get("name")]
    for exp in data.get("experience", []):
        skills += [x for x in exp.get("skills_used", []) if x not in skills]
    roles = (data.get("preferences") or {}).get("target_roles", [])
    lines = [f"Summary: {data.get('summary', '').strip()}"]
    if roles:
        lines.append("Target roles: " + ", ".join(roles))
    if skills:
        lines.append("Skills the candidate can actually claim: " + ", ".join(skills))
    return "\n".join(lines)


GLOBAL_MARKER = re.compile(r"worldwide|anywhere|global|latam|latin america|americas|any country|emea and latam", re.IGNORECASE)


def onsite_abroad_effect(prefs: CareerPreferences, location: str, is_remote: Optional[bool]) -> Tuple[bool, float]:
    """(veto, penalty) when a posting needs presence in a country the candidate does not live in and will not move to.

    Covers on-site and hybrid postings abroad, and remote postings locked to named countries (such as
    'Remote, Canada'), which in practice require living there. Global or Latin American remote roles are untouched.
    """
    setting = prefs.rules.onsite_abroad
    home = (prefs.work_authorization.current_country or "").upper()
    place = (location or "").strip()
    if prefs.relocation or setting.mode == "off" or is_remote is None or not home or not place:
        return False, 0.0
    named = regions_in(place)
    if home in named:
        return False, 0.0
    if is_remote:
        if prefs.accept_country_locked_remote or GLOBAL_MARKER.search(place) or not named:
            return False, 0.0
    elif re.search(r"remote|anywhere|worldwide|work from home", place, re.IGNORECASE) and not named:
        return False, 0.0
    return (True, 0.0) if setting.mode == "veto" else (False, setting.penalty)


PAY_CONTEXT = re.compile(r"salary|compensation|pay(?: range)?|base|rate|ote|usd|per (?:hour|month|year|annum)|/\s*(?:hr|hour|mo|month|yr|year)", re.IGNORECASE)
PAY_RANGE = re.compile(
    r"(?:US\$|USD\s*|\$)\s*(\d[\d,.]*)\s*([kK])?\s*(?:-|–|—|to)\s*(?:US\$|USD\s*|\$)?\s*(\d[\d,.]*)\s*([kK])?"
    r"(?:\s*(?:USD|US\$))?\s*(?:/|per|an?|each)?\s*(hour|hr|month|mo|year|yr|annum|annually)?",
    re.IGNORECASE,
)
HOURS_PER_MONTH = 168.0


def posted_monthly_pay(description: str) -> Optional[float]:
    """The top of a USD pay range stated in the posting, per month. None when no range with pay context is found."""
    best: Optional[float] = None
    for m in PAY_RANGE.finditer(description or ""):
        context = description[max(0, m.start() - 120):m.end() + 40]
        if not PAY_CONTEXT.search(context):
            continue
        try:
            high = float(m.group(3).replace(",", "").rstrip(".")) * (1000 if m.group(4) else 1)
        except ValueError:
            continue
        period = (m.group(5) or "").lower()
        if not period:
            period = "hour" if high < 300 else "year" if high > 20000 else "month"
        monthly = high * HOURS_PER_MONTH if period in ("hour", "hr") else high / 12 if period in ("year", "yr", "annum", "annually") else high
        best = monthly if best is None else max(best, monthly)
    return best


def below_salary_floor(prefs: CareerPreferences, description: str) -> Optional[str]:
    """A note when the posting states pay whose top is under the candidate's floor; None when it meets it or says nothing."""
    if not prefs.salary_floor_usd:
        return None
    floor = prefs.salary_floor_usd * {"hour": HOURS_PER_MONTH, "month": 1.0, "year": 1 / 12}[prefs.salary_floor_period]
    pay = posted_monthly_pay(description)
    if pay is not None and pay < floor:
        return f"posted pay tops out at about USD {pay:,.0f}/month, under the USD {floor:,.0f}/month floor"
    return None


def describe_preferences(prefs: CareerPreferences) -> str:
    """Plain-text rules for the LLM prompt, generated so the prompt can never disagree with the config."""
    wa, emp = prefs.work_authorization, prefs.employment
    lines = ["Work mode: " + ("remote only." if prefs.remote_only else "remote or on-site.")]
    if wa.citizenships or wa.authorized_regions or wa.current_country:
        lines.append(
            f"Work authorization: citizenship {', '.join(wa.citizenships) or 'unspecified'}; lives in "
            f"{wa.current_country or 'unspecified'}; can work WITHOUT a visa in {', '.join(wa.authorized_regions) or 'no listed region'}; "
            f"would need a visa in {', '.join(wa.visa_possible_regions) or 'no listed region'}. "
            "A remote posting open to any country is fine. One tied to another region lowers the score."
        )
    lines.append(
        f"Employment types accepted: {', '.join(emp.accepted_types)}; "
        + ("only paid positions." if emp.paid_only else "unpaid positions are acceptable.")
    )
    if not prefs.relocation:
        lines.append(
            f"The candidate will NOT relocate. On-site or hybrid postings outside {wa.current_country or 'their country'}, and remote "
            "postings limited to named countries (for example 'Remote, Canada' or 'Germany (Remote)'), should score much lower; "
            "remote postings open worldwide or to Latin America are fine."
        )
    lines.append(f"Seniority: penalise senior titles by {prefs.seniority.senior_penalty} and staff/principal by {prefs.seniority.staff_penalty} points.")
    lines.append(f"Shortlist threshold: overall_score >= {prefs.shortlist_threshold}.")
    if prefs.scoring.domain_keywords:
        lines.append(
            f"Target field: {', '.join(prefs.scoring.domain_keywords)}. Field fit accounts for "
            f"{round(prefs.scoring.domain_weight * 100)}% of the score and engineering skills for the rest."
        )
    for track in prefs.scoring.tracks:
        lines.append(
            f"Also a target: {track.name} ({', '.join(track.keywords)}). A posting that fits this track well scores like a "
            f"field match; use the better of the two fits."
        )
    lines.append("Screening rules:")
    for name, (label, _) in RULE_TEXT.items():
        setting: RuleSetting = getattr(prefs.rules, name)
        if name == "work_authorization" and setting.mode == "penalty":
            lines.append(
                f"- {label}: lower the score by {wa.visa_region_penalty} when a visa would be needed, "
                f"{wa.unauthorized_region_penalty} when there is no route to authorization, "
                f"and {wa.no_sponsorship_penalty} when the employer refuses sponsorship. Do NOT veto."
            )
        elif setting.mode == "veto":
            lines.append(f"- {label}: set should_veto=true.")
        elif setting.mode == "penalty":
            lines.append(f"- {label}: lower the score by about {setting.penalty} points, but do NOT veto.")
        else:
            lines.append(f"- {label}: ignore this rule. Do NOT veto and do NOT lower the score for it.")
    if prefs.deal_breakers:
        lines.append("Other deal-breakers: " + "; ".join(prefs.deal_breakers))
    return "\n".join(lines)


def reason_matches(rule: str, reason: Optional[str]) -> bool:
    return bool(reason) and bool(re.search(RULE_TEXT[rule][1], reason or "", re.IGNORECASE))

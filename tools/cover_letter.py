#!/usr/bin/env python3
"""Deterministic cover letter from approved profile lines, plus an optional model review.

Every sentence comes from a `letter_line` you wrote or approved in the master profile (or, when a profile
entry has none, from one of its own bullets). The job only decides which lines are used and in which order,
so the letter cannot claim anything the profile does not already say. The review step asks the configured
model to read the finished letter and report problems; it never rewrites the letter.
"""
from __future__ import annotations

import re
import zlib
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field

from tools.profile_schema import MasterProfile

MAX_EVIDENCE = 2
VERB_START = re.compile(r"^[A-Z][a-z]+(?:ed|e|d)?\b")


class Evidence:
    def __init__(self, kind: str, label: str, sentence: str, skills: Sequence[str]) -> None:
        self.kind, self.label, self.sentence, self.skills = kind, label, sentence, [s for s in skills if s]


def _sentence(text: str) -> str:
    text = " ".join(text.split()).strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def _first_person(bullet: str) -> Optional[str]:
    """A verb-led bullet ('Built X', 'Develop X') read as first person: 'I built X'. None when it does not fit."""
    bullet = " ".join(bullet.split()).strip().rstrip(".")
    if ":" in bullet[:60] or not VERB_START.match(bullet):
        return None
    return "I " + bullet[0].lower() + bullet[1:] + "."


def collect_evidence(profile: MasterProfile) -> List[Evidence]:
    items: List[Evidence] = []
    for exp in profile.experience:
        line = getattr(exp, "letter_line", None)
        if line:
            items.append(Evidence("role", exp.company, _sentence(line), exp.skills_used))
            continue
        for bullet in exp.achievements:
            spoken = _first_person(bullet)
            if spoken:
                items.append(Evidence("role", exp.company, f"At {exp.company}, {spoken}", exp.skills_used))
                break
    for project in profile.projects:
        line = getattr(project, "letter_line", None)
        if line:
            items.append(Evidence("project", project.name, _sentence(line), project.technologies))
        else:
            result = ""
            if project.impact_highlights:
                result = " " + (_first_person(project.impact_highlights[0]) or _sentence(project.impact_highlights[0]))
            items.append(Evidence(
                "project", project.name,
                f"{project.name} is my closest work to this role: {project.description.rstrip('.')}.{result}", project.technologies))
    return items


TITLE_STOPWORDS = {
    "engineer", "developer", "software", "senior", "junior", "staff", "intern", "internship", "and", "the", "for", "with", "lead",
    "principal", "associate", "remote", "team", "role", "specialist",
}


def title_terms(title: str) -> List[str]:
    return [w for w in re.findall(r"[a-z][a-z+#]{2,}", title.casefold()) if w not in TITLE_STOPWORDS]


def _score(item: Evidence, matched: Sequence[str], title: str = "") -> int:
    wanted = {m.casefold() for m in matched}
    text = item.sentence.casefold()
    haystack = text + " " + " ".join(item.skills).casefold() + " " + item.label.casefold()
    domain = sum(3 for term in title_terms(title) if term in haystack)
    return domain + sum(2 for s in item.skills if s.casefold() in wanted) + sum(1 for w in wanted if w in text)


def choose_evidence(profile: MasterProfile, matched: Sequence[str], title: str = "", posting: str = "") -> List[Evidence]:
    """The best-matching entry and the current job; when the best entry is the current job, the best of the other kind.

    The current role is always one of the two: a letter that skips the job you hold now reads as hiding it.
    """
    items = collect_evidence(profile)
    text = posting.casefold()
    def score(i: Evidence) -> int:
        return _score(i, matched, title) + sum(1 for s in i.skills if len(s) > 2 and s.casefold() in text)
    ranked = sorted(items, key=lambda i: (-score(i), items.index(i)))
    if not ranked:
        return []
    current = {e.company for e in profile.experience if e.is_current}
    chosen = [ranked[0]]
    now = next((i for i in ranked if i.kind == "role" and i.label in current), None)
    if now is not None and now is not ranked[0]:
        return [ranked[0], now]
    others = [i for i in ranked[1:] if i.kind != ranked[0].kind and score(i) > 0]
    rest = [i for i in ranked[1:] if i not in others]
    for item in others + rest:
        if len(chosen) == MAX_EVIDENCE:
            break
        chosen.append(item)
    return chosen


def _join(words: Sequence[str]) -> str:
    words = list(words)
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


OPENINGS = (
    "I am applying for the {title} role at {company}.",
    "I would like to be considered for the {title} role at {company}.",
    "I am writing about the {title} role at {company}.",
)
BRIDGES = (
    "The posting mentions {skills}, which I have used in the work above.",
    "You ask for {skills}; I work with these, as described above.",
    "This role centres on {skills}, the same stack behind the work above.",
)
CLOSINGS = (
    "I would be glad to talk through any of this. Thank you for your time.",
    "I would welcome the chance to discuss the role. Thank you for considering my application.",
    "I would welcome a short call to discuss how I could contribute to your team. Thank you for your time.",
)
SENIORITY_ONLY = {"intern", "senior", "junior", "staff", "principal", "lead", "associate", "sr", "jr"}


def pick(options: Sequence[str], company: str, title: str, salt: str) -> str:
    """A stable choice per company, role and slot, so letters differ between jobs but never change on a re-run."""
    return options[zlib.crc32(f"{company}|{title}|{salt}".encode("utf-8")) % len(options)]


def short_title(title: str) -> str:
    """The role as a person would write it: no team suffix, location or remote note."""
    text = re.sub(r"\s*\((?:[^)]*\b(?:remote|hybrid|on-?site)\b[^)]*)\)", "", title, flags=re.I)
    text = re.sub(r"\s+[-–]\s+(?:100%\s+)?(?:remote|hybrid|on-?site)\b.*$", "", text, flags=re.I)
    text = re.sub(r"\s+for (?:a|an|our)\s+(?:[\w.-]+\s+){0,3}(?:company|client|startup|team)\b.*$", "", text, flags=re.I)
    text = text.split(" | ")[0].strip()
    parts = [p.strip() for p in text.split(", ") if p.strip()]
    if len(parts) > 1:
        head = parts[0]
        text = ", ".join(parts[:2]) if head.casefold() in SENIORITY_ONLY else head
    return text.strip(" -") or title


LETTER_TOPICS = ("ai_usage", "remote_async", "open_source")  # answer-bank topics a letter may borrow one entry from


def fit_paragraph(profile: MasterProfile, title: str, posting: str) -> Optional[str]:
    """The first two sentences of the approved bank entry that matches what the role is about, title first, then posting."""
    from tools.answer_bank import TOPICS
    patterns = dict(TOPICS)
    for text, needed in ((title, 1), (posting[:3000], 3)):  # the posting must stress a topic, not mention it once
        for topic in LETTER_TOPICS:
            if len(re.findall(patterns[topic], text, re.I)) >= needed:
                entry = next((e for e in profile.answer_bank.entries if e.approved and e.topics and e.topics[0] == topic), None)
                if entry:
                    sentences = re.split(r"(?<=[.!?])\s+", entry.text.strip())
                    return " ".join(sentences[:2])
    return None


def compose_letter(
    profile: MasterProfile, company: str, title: str, matched: Sequence[str], remote: bool = True, posting: str = "",
) -> List[str]:
    chosen = choose_evidence(profile, matched, title, posting)
    verified = {s.name.casefold(): s.name for s in profile.skills}
    in_evidence = [verified[s.casefold()] for item in chosen for s in item.skills if s.casefold() in verified]
    shared = [verified[m.casefold()] for m in matched if m.casefold() in verified and verified[m.casefold()] in in_evidence]
    shared = list(dict.fromkeys(shared))[:4]

    paragraphs = [f"Dear {company} hiring team,"]
    role = short_title(title)
    opening = pick(OPENINGS, company, title, "open").format(title=role, company=company)
    lead = re.sub(r"^I also ", "I ", chosen[0].sentence) if chosen else ""
    paragraphs.append(f"{opening} {lead}" if chosen else opening)
    if len(chosen) > 1:
        paragraphs.append(chosen[1].sentence)
    fit = fit_paragraph(profile, title, posting)
    for item in chosen:  # "At Globant, ..." already opened the letter; do not introduce the same employer twice
        if fit and fit.startswith(f"At {item.label} "):
            fit = fit[len(f"At {item.label} "):]
            fit = fit[0].upper() + fit[1:]
    if fit and fit not in paragraphs:
        paragraphs.append(fit)
    if len(shared) >= 2:
        paragraphs.append(pick(BRIDGES, company, title, "bridge").format(skills=_join(shared)))
    prefs = profile.preferences
    if not remote and getattr(prefs, "relocation", False):
        paragraphs.append("I am ready to relocate for this role.")
    paragraphs.append(pick(CLOSINGS, company, title, "close"))
    return paragraphs


def profile_facts(profile: MasterProfile) -> str:
    lines = [profile.summary]
    for exp in profile.experience:
        lines += [f"{exp.role} at {exp.company} ({exp.start_date} to {exp.end_date or 'Present'}): " + " ".join(exp.achievements)]
    for project in profile.projects:
        lines.append(f"Project {project.name}: {project.description} " + " ".join(project.impact_highlights))
    lines.append("Skills: " + ", ".join(s.name for s in profile.skills))
    return "\n".join(lines)

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


def choose_evidence(profile: MasterProfile, matched: Sequence[str], title: str = "") -> List[Evidence]:
    """The best-matching entry, then the best match of the other kind (role or project), then the next best."""
    items = collect_evidence(profile)
    ranked = sorted(items, key=lambda i: (-_score(i, matched, title), items.index(i)))
    if not ranked:
        return []
    chosen = [ranked[0]]
    others = [i for i in ranked[1:] if i.kind != ranked[0].kind and _score(i, matched, title) > 0]
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
    "I am applying for the {title} position at {company}.",
    "I would like to apply for the {title} role at {company}.",
    "Please consider my application for the {title} position at {company}.",
)
BRIDGES = (
    "The posting mentions {skills}, which I have used in the work above.",
    "You ask for {skills}; I work with these, as described above.",
    "{skills} come up in this role, and they are part of the work I described.",
)
CLOSINGS = (
    "I would be glad to talk through any of this. Thank you for your time.",
    "I would welcome the chance to discuss the role. Thank you for considering my application.",
    "If useful, I can walk you through any of these projects. Thank you for your time.",
)
SENIORITY_ONLY = {"intern", "senior", "junior", "staff", "principal", "lead", "associate", "sr", "jr"}


def pick(options: Sequence[str], company: str, title: str, salt: str) -> str:
    """A stable choice per company, role and slot, so letters differ between jobs but never change on a re-run."""
    return options[zlib.crc32(f"{company}|{title}|{salt}".encode("utf-8")) % len(options)]


def short_title(title: str) -> str:
    """The role as a person would write it: no team suffix, location or remote note."""
    text = re.sub(r"\s*\((?:[^)]*\b(?:remote|hybrid|on-?site)\b[^)]*)\)", "", title, flags=re.I)
    text = text.split(" | ")[0].strip()
    parts = [p.strip() for p in text.split(", ") if p.strip()]
    if len(parts) > 1:
        head = parts[0]
        text = ", ".join(parts[:2]) if head.casefold() in SENIORITY_ONLY else head
    return text.strip(" -") or title


def compose_letter(
    profile: MasterProfile, company: str, title: str, matched: Sequence[str], remote: bool = True,
) -> List[str]:
    chosen = choose_evidence(profile, matched, title)
    verified = {s.name.casefold(): s.name for s in profile.skills}
    in_evidence = [verified[s.casefold()] for item in chosen for s in item.skills if s.casefold() in verified]
    shared = [verified[m.casefold()] for m in matched if m.casefold() in verified and verified[m.casefold()] in in_evidence]
    shared = list(dict.fromkeys(shared))[:4]

    paragraphs = [f"Dear {company} hiring team,"]
    role = short_title(title)
    opening = pick(OPENINGS, company, title, "open").format(title=role, company=company)
    paragraphs.append(f"{opening} {chosen[0].sentence}" if chosen else opening)
    if len(chosen) > 1:
        paragraphs.append(chosen[1].sentence)
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

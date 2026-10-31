#!/usr/bin/env python3
"""Flag claims in a CV or cover letter that the candidate's sources don't back.

A mechanical second check next to the reviewer's Factual Grounding Audit: it
pulls every number (counts, percentages, years, "2.2M", "4.2 to 4.8"), every
capitalised name or acronym that does not start a sentence, and every
credential word out of the document's prose, and reports the ones that appear
nowhere in the sources:

- `.claude/skills/job-application-assistant/01-candidate-profile.md`
- `cv/main_example.tex`
- `CLAUDE.md`
- the job posting, when passed with `--posting` (company names, product names
  and the posting's own terms are fair to quote back)

It catches a changed or escalated number and an invented employer, tool or
certificate. It cannot catch invented wording with no number or name in it
("the team still runs"), so it complements the reviewer and never replaces it.
A flag is a "check this", not proof of fabrication: the user confirms or the
draft changes. Exit 1 when anything is flagged, 0 when clean.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCES = [
    ROOT / ".claude/skills/job-application-assistant/01-candidate-profile.md",
    ROOT / "cv/main_example.tex",
    ROOT / "CLAUDE.md",
]

# Words that are capitalised for grammar or LaTeX reasons, not names.
NOT_NAMES = {
    "i", "dear", "kind", "regards", "sincerely", "page", "present", "remote", "online",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "monday", "tuesday", "wednesday", "thursday", "friday",
}

CREDENTIAL = re.compile(r"\b(certified|certification|certificate|licensed|accredited|chartered)\b", re.I)
NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*([kKmM]\b)?")


def latex_to_text(source: str) -> str:
    """Strip a .tex file down to its prose: drop the preamble, comments and commands."""
    if "\\begin{document}" in source:
        source = source.split("\\begin{document}", 1)[1]
    source = re.sub(r"(?<!\\)%.*", "", source)
    source = source.replace("\\%", "%").replace("\\&", "&").replace("\\#", "#").replace("\\$", "$")
    # \href{url}{text} -> text; then commands keep only their braced arguments.
    source = re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", source)
    source = re.sub(r"\\(?:fontspec|fontsize|usepackage|needspace|vspace|hspace|enlargethispage)(\[[^\]]*\])?(\{[^}]*\})*", " ", source)
    source = re.sub(r"\\begin\{[^}]*\}|\\end\{[^}]*\}", "\n", source)
    source = re.sub(r"\\[a-zA-Z@]+\*?(\[[^\]]*\])?", " ", source)
    source = source.replace("{", " ").replace("}", " ").replace("~", " ").replace("--", "-")
    return re.sub(r"[ \t]+", " ", source)


def number_values(text: str) -> set[float]:
    values = set()
    for match in NUMBER.finditer(text):
        digits, suffix = match.group(1), (match.group(2) or "").lower()
        value = float(digits.replace(",", ""))
        values.add(value * (1_000 if suffix == "k" else 1_000_000 if suffix == "m" else 1))
        values.add(value)  # "2.2M" in the draft also matches "2.2M" written as "2.2" + "M"
    return values


def sentence_starts(text: str) -> set[int]:
    starts = {0}
    for match in re.finditer(r"(?:[.!?:;|]\s+|\n\s*(?:[-*•]\s+)?)(?=\S)", text):
        starts.add(match.end())
    return starts


def unverified(draft: str, sources: str) -> list[tuple[str, str]]:
    """(kind, value) pairs in `draft` that `sources` don't contain, in order, once each."""
    lower_sources = sources.lower()
    source_words = set(re.findall(r"[\w&+#.'-]+", lower_sources))
    source_numbers = number_values(sources)
    flags: list[tuple[str, str]] = []

    def flag(kind: str, value: str) -> None:
        if (kind, value) not in flags:
            flags.append((kind, value))

    for match in NUMBER.finditer(draft):
        value = float(match.group(1).replace(",", ""))
        suffix = (match.group(2) or "").lower()
        scaled = value * (1_000 if suffix == "k" else 1_000_000 if suffix == "m" else 1)
        if value in source_numbers or scaled in source_numbers:
            continue
        if value <= 2 and not suffix:  # "1 data analyst", "2 designers" are in the sources; tiny counts are rarely claims
            continue
        flag("number", match.group(0).strip())

    starts = sentence_starts(draft)
    for match in re.finditer(r"\b[A-Z][\w&+#'-]*", draft):
        word = re.sub(r"(['’]s|[-'’]+)$", "", match.group(0))
        lower = word.lower()
        acronym = sum(1 for c in word if c.isupper()) >= 2
        if match.start() in starts and not acronym:
            continue
        if lower in NOT_NAMES or lower in source_words or lower in lower_sources:
            continue
        flag("name", word)

    for match in CREDENTIAL.finditer(draft):
        if match.group(0).lower() not in lower_sources:
            flag("credential", match.group(0))
    return flags


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("drafts", nargs="+", type=Path, help="CV / cover letter .tex (or .md/.txt) files to check")
    parser.add_argument("--posting", type=Path, help="the job posting text; its own names and terms count as sources")
    parser.add_argument("--source", type=Path, action="append", default=[], help="extra source file (repeatable)")
    args = parser.parse_args(argv)

    source_files = DEFAULT_SOURCES + args.source + ([args.posting] if args.posting else [])
    missing = [str(path) for path in source_files if not path.is_file()]
    if missing:
        print(f"fact_guard: source not found: {', '.join(missing)}", file=sys.stderr)
        return 2
    sources = "\n".join(
        latex_to_text(path.read_text(encoding="utf-8")) if path.suffix == ".tex" else path.read_text(encoding="utf-8")
        for path in source_files
    )

    total = 0
    for draft_path in args.drafts:
        raw = draft_path.read_text(encoding="utf-8")
        draft = latex_to_text(raw) if draft_path.suffix == ".tex" else raw
        flags = unverified(draft, sources)
        total += len(flags)
        if flags:
            print(f"{draft_path}: {len(flags)} claim(s) not found in the sources - check each:")
            for kind, value in flags:
                print(f"  - {kind}: {value}")
        else:
            print(f"{draft_path}: clean")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())

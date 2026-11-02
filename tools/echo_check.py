#!/usr/bin/env python3
"""Find text a CV or cover letter copied from the job posting, or from other letters.

`10-echo-traps.md` Trap 2 (phrase lifts) and Trap 4 (reuse across applications) are
mechanical: a run of N or more words shared between two texts. Asking the reviewer
agent to spot them means pasting every text into its prompt and having it compare
by eye, which is slow, costly in tokens and misses lifts. This script does the
comparison and prints only the hits, so the reviewer judges a short list instead.

What it does not do: decide whether a single word is a voice word (Trap 1, "messy").
That needs judgment about the field's register and stays with the reviewer.

Not counted as a hit:
  - text inside quotation marks in the draft ("your 'what you bring' list" is an open
    reference to the posting, which `10-echo-traps.md` allows);
  - phrases passed with --ignore (company name, job title, team name);
  - runs made only of function words ("in the context of the");
  - URLs, LaTeX commands and the LaTeX preamble.

Usage:
    python3 tools/echo_check.py --posting documents/applications/<dir>/job_posting.md \\
        --draft cv/main_<company>_<role>.tex --draft cover_letters/cover_<company>_<role>.tex \\
        --ignore "<Company>" --ignore "<Job title>" \\
        --others cover_letters/cover_*.tex

Output: one line per hit, `LIFT` (shared with the posting) or `REUSE <file>` (shared
with another application), with the draft's file:line and the shared words.

Exit codes: 0 no hits, 1 hits found, 2 bad invocation.

Tests live in tests/test_echo_check.py.
"""

from __future__ import annotations

import argparse
import glob
import re
import sys
from pathlib import Path

DEFAULT_MIN = 5

# English function words: a run made only of these is not a lift. Letters in another
# language still get every lift reported; a run of that language's function words may
# then show up as a hit for the reviewer to drop. A fork writing in one language can
# add its function words here.
STOPWORDS = frozenset("""
a an the and or but of to in on at by for with from as is are was were be been being
it its this that these those which who whom what when where how than then so such
into onto over under about across through between within without also not no can
will would should could may might must do does did have has had i we you they he she
my our your their his her me us them
""".split())

TOKEN = re.compile(r"\w+(?:['’]\w+)*")
# URLs, e-mail addresses and anything with a digit (phone numbers, font sizes, dates):
# shared between every letter by design, never prose.
URL = re.compile(r"https?://\S+|www\.\S+|mailto:\S+|\S+@\S+|\S*\d\S*")
# Commands whose arguments are layout or addresses, not prose: drop them with their
# arguments. Every other command is dropped and its argument text kept.
LATEX_NOPROSE = re.compile(
    r"\\(?:begin|end|documentclass|usepackage|fontspec|fontsize|setlength|vspace|hspace|"
    r"needspace|enlargethispage|includegraphics|urlstyle|url|href|color|textcolor)\*?"
    r"(?:\s*\[[^\]]*\])*(?:\s*\{[^{}]*\})?(?:\s*\[[^\]]*\])*"
)
LATEX_CMD = re.compile(r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?|\\.")
LATEX_COMMENT = re.compile(r"(?<!\\)%.*")
# Quoted spans: “…”, ``…'', «…», "…" and ‘…’ (single curly quotes only, so the
# apostrophe in "the team's" or "l'équipe" never opens a quote).
QUOTED = re.compile(r"“[^”]*”|``.*?''|«[^»]*»|\"[^\"\n]*\"|‘[^’]*’", re.S)


def preprocess(text: str, latex: bool) -> str:
    """Return text with URLs and markup blanked out. Newlines are kept for line numbers."""
    if latex:
        start = text.find(r"\begin{document}")
        if start != -1:
            # Keep the preamble's newlines so line numbers still match the file.
            text = "\n" * text.count("\n", 0, start) + text[start:]
        text = LATEX_COMMENT.sub("", text)
        text = LATEX_NOPROSE.sub(" ", text)
    text = URL.sub(" ", text)
    if latex:
        text = LATEX_CMD.sub(" ", text)
    return text


def tokenize(text: str):
    """Return [(word, start, end)] with the word lowercased."""
    return [(m.group().lower(), m.start(), m.end()) for m in TOKEN.finditer(text)]


def blocked_mask(text: str, tokens, ignore_phrases) -> list[bool]:
    """Mark tokens that cannot be part of a hit: quoted text and ignored phrases."""
    blocked = [False] * len(tokens)
    spans = [(m.start(), m.end()) for m in QUOTED.finditer(text)]
    for i, (_, start, _end) in enumerate(tokens):
        if any(a <= start < b for a, b in spans):
            blocked[i] = True
    words = [t[0] for t in tokens]
    for phrase in ignore_phrases:
        target = [w for w, _, _ in tokenize(phrase)]
        n = len(target)
        if not n:
            continue
        for i in range(len(words) - n + 1):
            if words[i:i + n] == target:
                for j in range(i, i + n):
                    blocked[j] = True
    return blocked


def shared_spans(draft_text: str, draft_tokens, blocked, source_tokens, n: int):
    """Return maximal runs of draft tokens covered by an n-gram that also occurs in source."""
    source = {tuple(w for w, _, _ in source_tokens[i:i + n])
              for i in range(len(source_tokens) - n + 1)}
    words = [t[0] for t in draft_tokens]
    hit = [False] * len(words)
    for i in range(len(words) - n + 1):
        if any(blocked[i:i + n]):
            continue
        if tuple(words[i:i + n]) in source:
            for j in range(i, i + n):
                hit[j] = True
    spans, i = [], 0
    while i < len(hit):
        if not hit[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(hit) and hit[j + 1]:
            j += 1
        run = words[i:j + 1]
        if sum(w not in STOPWORDS for w in run) >= 2:
            line = draft_text.count("\n", 0, draft_tokens[i][1]) + 1
            spans.append((line, " ".join(run)))
        i = j + 1
    return spans


def load(path: str) -> tuple[str, list]:
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    text = preprocess(raw, latex=path.lower().endswith(".tex"))
    return text, tokenize(text)


def _force_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)  # absent on a StringIO under test
        if reconfigure:
            reconfigure(encoding="utf-8")


def main(argv=None) -> int:
    _force_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--posting", required=True, help="job posting text or markdown")
    parser.add_argument("--draft", action="append", required=True, help="CV or letter to check (repeatable)")
    parser.add_argument("--ignore", action="append", default=[], help="phrase that may be shared, e.g. company or job title")
    parser.add_argument("--others", nargs="*", default=[], help="other applications' files or globs, for reuse")
    parser.add_argument("--min", type=int, default=DEFAULT_MIN, help=f"shortest shared run in words (default {DEFAULT_MIN})")
    args = parser.parse_args(argv)
    if args.min < 2:
        parser.error("--min must be at least 2")

    try:
        _, posting_tokens = load(args.posting)
        drafts = {d: load(d) for d in args.draft}
    except OSError as exc:
        print(f"echo_check: {exc}", file=sys.stderr)
        return 2

    draft_paths = {Path(d).resolve() for d in args.draft}
    others = []
    for pattern in args.others:
        for path in sorted(glob.glob(pattern)) or [pattern]:
            if Path(path).resolve() not in draft_paths and Path(path).is_file():
                others.append(path)

    lines = []
    for path, (text, tokens) in drafts.items():
        blocked = blocked_mask(text, tokens, args.ignore)
        for line, words in shared_spans(text, tokens, blocked, posting_tokens, args.min):
            lines.append(f"LIFT {path}:{line}  {words}")
        for other in others:
            # Letters against letters, CVs against CVs: a CV bullet retold in a letter
            # is the same fact, not a reused sentence.
            if Path(other).resolve().parent != Path(path).resolve().parent:
                continue
            _, other_tokens = load(other)
            for line, words in shared_spans(text, tokens, blocked, other_tokens, args.min):
                lines.append(f"REUSE {other}  {path}:{line}  {words}")

    lifts = sum(l.startswith("LIFT") for l in lines)
    print(f"echo_check: {lifts} phrase lift(s), {len(lines) - lifts} reused span(s), min {args.min} words")
    for l in lines:
        print(l)
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main())

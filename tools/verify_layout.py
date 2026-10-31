#!/usr/bin/env python3
"""Measure a compiled CV or cover letter's page layout, instead of eyeballing it.

The compile-and-inspect loop in `05-cv-templates.md` and the verification checklist in
CLAUDE.md already require the layout properties below. Nothing executes them: they are
checked by looking at the rendered page, which is exactly how they get missed. Each
failure below produces a clean compile, a correct page count, and a PDF that passes
`tools/verify_pdf.py`:

  orphaned entry      A moderncv \\cventry renders as a tabular, so a job entry is one
                      unbreakable block. When it does not fit, the whole entry moves to
                      the next page - or its header lands at the bottom of one page with
                      the bullets resuming on the next. CLAUDE.md calls this "the most
                      common failure".
  internal hole       The space an ejected entry leaves behind. Observed in the wild at
                      273pt, roughly 19 blank lines, mid-page, on a document whose page
                      count was correct and whose visual read looked fine.
  page ends early     A non-final page that stops well short of the bottom.
  final page thin     A last page mostly empty, which reads as an unfinished document.
  footer collision    Body text pushed into the page-number band, the usual result of
                      rescuing a page with \\enlargethispage or a negative \\vspace.

Page count is deliberately NOT checked here: `tools/verify_pdf.py --pages` already does
that, and CI runs it. Two implementations of one rule drift.

Geometry comes from Poppler word bounding boxes (`pdftotext -bbox`). Poppler is optional
repo-wide - since #369 `verify_pdf.py` prefers pypdf and falls back to Poppler - but word
bounding boxes have no pypdf equivalent, so this is the one step that still wants it.
Without it, or with a broken extractor, the check reports `skipped:` and exits 2 rather
than inventing a layout failure. A broken extractor has two distinct causes: no `pdftotext`
on PATH has the `-bbox` flag (Git for Windows ships an xpdf build without it, and the check
skips past it to any Poppler further down PATH), and Poppler 26.0x before 26.05 aborts `-bbox`/`-bbox-layout`/`-htmlmeta` on a PDF
whose Info dictionary carries an empty string in any field - which `hyperref` writes for
every field it does not set, so any `lualatex`/`pdflatex` document built with `hyperref`
and no `\\hypersetup{pdftitle=...}` triggers a real Poppler crashing on a legal PDF (#451).
Line height serves
as a font-size proxy to spot section headings. An orphaned entry header is found by where
each line's TEXT starts, after any list marker merged into it, not by its left edge (see
`find_orphans`).

The thresholds below are calibrated for the stock moderncv (`cv/`) and cover.cls
(`cover_letters/`) geometry. A template registered via `/add-template` may need them
retuned - an article-class page number sitting outside the 90pt footer band, for
instance, is read as body text and turns the space above it into a phantom hole.

Usage:
    python tools/verify_layout.py cv/main_acme_ml_engineer.pdf
    python tools/verify_layout.py cover_letters/cover_acme_ml_engineer.pdf

Exit codes: 0 clean, 1 layout problem, 2 bad invocation or no usable extractor.

The shipped `cv/main_example.pdf` exits 1 by design: its placeholder page 2 is mostly
empty, which is the thin-final-page failure the checklist asks you to fix before sending.

Tests live in tests/test_verify_layout.py and run against synthetic pages, so the
suite needs neither Poppler nor a compiled PDF.
"""

from __future__ import annotations

import argparse
import functools
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# A gap larger than this between consecutive lines is a hole, not spacing. Section
# spacing in the stock templates runs to roughly 45pt; 100pt is about seven lines.
GAP_LIMIT_PT = 100.0

# Bottom whitespace on a page that is not the last one. The stock geometry leaves ~85pt.
BOTTOM_LIMIT_FRACTION = 0.25

# A final page emptier than this reads as an unfinished document.
LAST_PAGE_THIN_FRACTION = 0.35

# The page-number footer lives in the bottom margin and is a text line like any other to
# Poppler. Ignore this band when measuring the body, or every page looks like it has a
# hole above its footer.
FOOTER_BAND_PT = 90.0

# A line indented at least this far past the page's left edge is a bullet or a
# continuation, not an entry header or a section heading.
INDENT_PT = 8.0

# Glyphs that label a list item when a template merges them into the item's first line.
# Unicode only: ASCII `-` and `*`, and the dashes, also open real text (the continuation
# of a bullet may start with an em dash), so a line opening with one is read as text.
LIST_MARKERS = frozenset("\u2022\u2023\u25aa\u25cf\u00b7")

# Lines whose tops are this close are parts of one printed line. pdftotext -bbox output
# is grouped by rounded yMin, which splits a printed line wherever words in another
# font sit a fraction of a point off and a .5 boundary falls between them (a bold word
# 0.2pt off; a list marker set 1.3pt below its item's first line on the stock CV).
# Consecutive printed lines are ~12pt apart.
SAME_PRINTED_LINE_PT = 3.0

# A list sets its text in by a label's width: 11-25pt past the margin on the stock CV
# and cover letter. A line whose text starts further in than this is set right or
# centred (a running header, a date on a line of its own), not where a page's body
# resumes.
SET_IN_LIMIT_PT = 60.0

# A line this much taller than the body median is a section heading.
HEADING_HEIGHT_RATIO = 1.25

PAGE_RE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', re.S)
WORD_RE = re.compile(
    r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="[\d.]+" yMax="([\d.]+)">([^<]*)</word>'
)
TEXT_RE = re.compile(r"\w")


@dataclass
class Line:
    top: float
    bottom: float
    left: float
    height: float
    text: str
    # Where the text starts once leading LIST_MARKERS words are set aside; None when the
    # line has no such marker, so a Line built from `left` alone behaves as before.
    text_left: float | None = None

    @property
    def text_x(self) -> float:
        return self.left if self.text_left is None else self.text_left


class Page:
    def __init__(self, height: float, lines: list[Line]):
        self.height = height
        self.lines = sorted(lines, key=lambda l: l.top)

    @property
    def body(self) -> list[Line]:
        cutoff = self.height - FOOTER_BAND_PT
        return [l for l in self.lines if l.top < cutoff]

    @property
    def empty(self) -> bool:
        return not self.body

    @property
    def bottom_space(self) -> float:
        return self.height - max(l.bottom for l in self.body) if self.body else self.height

    @property
    def left_edge(self) -> float:
        return min(l.left for l in self.body) if self.body else 0.0

    @property
    def body_median_height(self) -> float:
        heights = sorted(l.height for l in self.body)
        return heights[len(heights) // 2] if heights else 0.0

    @property
    def footer_crowded(self) -> bool:
        """One line in the bottom band is a page number; two means body text spilled in."""
        band = self.height - FOOTER_BAND_PT
        return len({round(l.top, 1) for l in self.lines if l.top >= band}) > 1

    def is_indented(self, line: Line) -> bool:
        return line.left > self.left_edge + INDENT_PT

    def is_heading(self, line: Line) -> bool:
        median = self.body_median_height
        return bool(median) and line.height > median * HEADING_HEIGHT_RATIO

    def largest_gap(self) -> tuple[float, float]:
        """Largest top-to-top distance between body lines, and where it starts.

        Measured top-to-top rather than bottom-to-top, so a tall line inflates the gap
        by its own height (a 160pt void under a heading reads as ~174pt). That errs
        toward over-detection, which is the right direction for a check whose job is
        to stop a hole from shipping.
        """
        tops = sorted({round(l.top, 1) for l in self.body})
        if len(tops) < 2:
            return (0.0, 0.0)
        return max((tops[i + 1] - tops[i], tops[i]) for i in range(len(tops) - 1))


@functools.cache
def find_pdftotext() -> str | None:
    """Find a pdftotext binary that supports Poppler's -bbox flag.

    Git for Windows prepends Git's mingw64\\bin to PATH under Git Bash (used by
    Claude Code), shipping an xpdf pdftotext without -bbox. If a working Poppler
    is installed further down PATH (e.g. MiKTeX, Chocolatey, Scoop), probe each
    candidate with -h and pick the first that advertises -bbox (#655). Falls back
    to the first candidate if none advertises -bbox, so the caller keeps today's
    skip message on failure. Returns None if no pdftotext is found at all.

    Cached at module level to avoid re-probing candidates with subprocesses on
    every parse_pdf call within a process.
    """
    candidates: list[str] = []
    seen: set[str] = set()
    for entry in os.get_exec_path():
        candidate = shutil.which("pdftotext", path=entry)
        if candidate and candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)
    if not candidates:
        return None
    for candidate in candidates:
        try:
            res = subprocess.run(
                [candidate, "-h"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            output = (res.stdout or "") + (res.stderr or "")
            if "-bbox" in output:
                return candidate
        except (subprocess.SubprocessError, OSError):
            continue
    return candidates[0]


def parse_pdf(path: Path) -> list[Page]:
    binary = find_pdftotext()
    if not binary:
        raise RuntimeError("pdftotext (Poppler) not found; install poppler-utils")
    try:
        out = subprocess.run(
            [binary, "-bbox", "-enc", "UTF-8", str(path), "-"],
            capture_output=True,
            text=True,
            # pdftotext emits UTF-8; without this Windows decodes it as cp1252 and
            # a non-ASCII glyph in the CV crashes the run (same fix as verify_pdf.py).
            encoding="utf-8",
            errors="replace",
            check=True,
        ).stdout
    except subprocess.CalledProcessError as exc:
        # A broken extractor has two distinct causes, not one: an xpdf-based pdftotext
        # has no -bbox at all and exits 99 (Git for Windows puts one ahead of Poppler
        # in PATH); a real Poppler before 26.05 aborts -bbox on a PDF whose Info dict
        # has an empty string in any field - which hyperref writes for every field it
        # does not set, so a lualatex/pdflatex document with hyperref and no pdftitle
        # triggers this even with a working Poppler (#451). Either way this is a
        # broken extractor, not a broken document, so it degrades to the skip path
        # instead of exit 1.
        stderr_lines = (exc.stderr or "").strip().splitlines()
        detail = stderr_lines[0] if stderr_lines else f"exit {exc.returncode}"
        raise RuntimeError(
            f"pdftotext could not produce bounding boxes for {path} ({detail}); "
            "either no pdftotext on PATH has the -bbox flag (Git for Windows ships an "
            "xpdf build without it), or Poppler aborted on this "
            "document - Poppler 26.0x before 26.05 aborts on a PDF whose Info "
            "dictionary carries an empty string, as hyperref writes when pdftitle is unset"
        ) from exc
    pages = []
    for _w, h, body in PAGE_RE.findall(out):
        buckets: dict[float, list[tuple[float, float, float, str]]] = {}
        for x_min, y_min, y_max, text in WORD_RE.findall(body):
            key = round(float(y_min), 0)  # words on one line share a rounded yMin
            buckets.setdefault(key, []).append((float(x_min), float(y_min), float(y_max), text))
        lines = [
            Line(
                top=min(w[1] for w in words),
                bottom=max(w[2] for w in words),
                left=min(w[0] for w in words),
                height=max(w[2] - w[1] for w in words),
                text=" ".join(w[3] for w in sorted(words)),
                text_left=text_start(sorted(words)),
            )
            for words in buckets.values()
        ]
        pages.append(Page(float(h), lines))
    return pages


def text_start(words: list[tuple[float, float, float, str]]) -> float | None:
    """xMin of the first word after leading LIST_MARKERS words, if there were any."""
    lead = 0
    while lead < len(words) and words[lead][3] and set(words[lead][3]) <= LIST_MARKERS:
        lead += 1
    return words[lead][0] if 0 < lead < len(words) else None


def printed_text_x(lines: list[Line]) -> float | None:
    """Where the text of one printed line starts, from the bbox lines that make it up."""
    xs = [l.text_x for l in lines if TEXT_RE.search(l.text)]
    return min(xs) if xs else None


def find_orphans(pages: list[Page]) -> list[str]:
    """A page ending on an entry header or section heading whose content resumes overleaf.

    Two shapes, both documented failures:
      * the last body line of a page is a section heading (stranded heading)
      * the next page's text starts further in than the text of the page's last printed
        line (an entry header), i.e. the entry was split across the break

    The second compares where the TEXT starts, not where the line starts. A template may
    merge a list marker into the line it labels, which moves the line's left edge but not
    its text: in the #481 repro a bullet's first line starts at x58.9 (the marker), level
    with the entry headers at x59.5, while its text and its continuation start at x72.1.
    Read by left edge, a bullet wrapping across the break looked like a header followed by
    indented text, and a real header followed by such a bullet looked level. On the stock
    CV the outer marker may extract as a line of its own; then the header's text (x70.4)
    counted as indented and a header orphaned from its bullets (x81.3) went unreported.

    Only LIST_MARKERS glyphs are set aside. Where a marker is not among them - the stock
    CV's outer marker extracts as `○` on some font setups and as an emoji on others - the
    line keeps the marker's x, which is exactly what the left-edge rule read. That is how
    a stock `[Degree]` line and its description (both text at x70.4) stay reported when
    that marker merges into the header line (header x59.0), as they were. When the marker
    extracts as its own line, the two are level and nothing tells them from a wrapped
    line without font information, so neither this rule nor the left-edge one reports it.
    """
    problems = []
    # Indentation must be judged against the document's left margin, not each page's own
    # minimum: a page that *opens* with indented bullets would otherwise treat their
    # indent as its margin and report nothing.
    body_lines = [l for p in pages for l in p.body]
    if not body_lines:
        return problems
    doc_left = min(l.left for l in body_lines)

    def indented(line: Line) -> bool:
        return line.left > doc_left + INDENT_PT

    for i in range(len(pages) - 1):
        here, nxt = pages[i], pages[i + 1]
        if here.empty or nxt.empty:
            continue
        last, first = here.body[-1], nxt.body[0]

        if here.is_heading(last):
            problems.append(
                f"p{i + 1} ends on the section heading {last.text.strip()!r} with its content "
                f"on p{i + 2}. Shorten the entry that follows it, or let the heading and its "
                "first entry move to the next page together"
            )
            continue
        # The last printed line: the bbox lines within SAME_PRINTED_LINE_PT of the last one.
        tail = [l for l in here.body if l.top >= last.top - SAME_PRINTED_LINE_PT]
        last_x = printed_text_x(tail)
        if last_x is None:
            # moderncv puts an itemize marker in its own bbox line at the list's left
            # edge, so a list item split across the break looks like an un-indented
            # header followed by indented text. Different defect, different fix.
            if not indented(last) and indented(first):
                problems.append(
                    f"p{i + 1} ends on a lone list marker whose text continues on p{i + 2} "
                    f"({first.text.strip()[:60]!r}): a bullet is split across the page break. "
                    "Shorten the preceding content so the whole item fits on one page"
                )
            continue
        # The next page's first printed line with text, skipping a marker in its own line
        # and a line set right or centred.
        opener = next(
            (
                l
                for l in nxt.body
                if TEXT_RE.search(l.text) and l.text_x <= doc_left + SET_IN_LIMIT_PT
            ),
            None,
        )
        if opener is None:
            continue
        head = [l for l in nxt.body if abs(l.top - opener.top) <= SAME_PRINTED_LINE_PT]
        next_x = printed_text_x(head)
        if next_x is not None and next_x > last_x + INDENT_PT:
            shown = max((l for l in tail if TEXT_RE.search(l.text)), key=lambda l: len(l.text))
            problems.append(
                f"p{i + 1} ends on {shown.text.strip()[:60]!r} while the text on p{i + 2} "
                f"starts further in ({opener.text.strip()[:60]!r}): an entry header is "
                "orphaned from its bullets. Add \\needspace before that \\cventry, "
                "or shorten it"
            )
    return problems


def report(path: Path, pages: list[Page]) -> list[str]:
    problems: list[str] = []
    print(f"{path}: {len(pages)} page(s) (page count is verify_pdf.py's job, not checked here)")

    for i, page in enumerate(pages, 1):
        if page.empty:
            problems.append(f"p{i} contains no text")
            print(f"  p{i}: EMPTY")
            continue

        gap, gap_y = page.largest_gap()
        share = page.bottom_space / page.height
        print(
            f"  p{i}: text y {page.body[0].top:.0f}..{page.body[-1].bottom:.0f}"
            f" of {page.height:.0f}pt | bottom {page.bottom_space:.0f}pt ({share * 100:.0f}%)"
            f" | largest gap {gap:.0f}pt at y{gap_y:.0f}"
        )

        if gap > GAP_LIMIT_PT:
            problems.append(
                f"p{i} has a {gap:.0f}pt hole at y{gap_y:.0f} (~{gap / 14:.0f} blank lines). "
                "A moderncv \\cventry is an unbreakable tabular: shorten the entry that "
                "follows the hole so it fits, or move a shorter section above it"
            )
        if i < len(pages) and share > BOTTOM_LIMIT_FRACTION:
            problems.append(
                f"p{i} ends {page.bottom_space:.0f}pt ({share * 100:.0f}%) early although "
                "more pages follow, which reads as a broken page break"
            )
        if page.footer_crowded:
            problems.append(
                f"p{i} has body text inside the bottom margin band, colliding with the "
                "footer; stop stretching the page with \\enlargethispage and cut content"
            )
        if i == len(pages) > 1 and share > LAST_PAGE_THIN_FRACTION:
            problems.append(
                f"p{i} is the last page and {share * 100:.0f}% empty, which reads as an "
                "unfinished document; restore the highest-relevance content previously cut"
            )

    problems.extend(find_orphans(pages))
    return problems


def _force_utf8_output() -> None:
    """Write UTF-8 whatever the host's default encoding is.

    A piped stdout on Windows defaults to the ANSI code page (cp1252 on most
    Western installs), so printing a company, title or file name outside it
    raised UnicodeEncodeError before the workflow saw any output.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)  # absent on a StringIO under test
        if reconfigure:
            reconfigure(encoding="utf-8")


def main() -> int:
    _force_utf8_output()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf", nargs="?", type=Path)
    args = ap.parse_args()

    if not args.pdf:
        ap.error("pdf is required")
    if not args.pdf.exists():
        print(f"error: {args.pdf} not found", file=sys.stderr)
        return 2

    try:
        pages = parse_pdf(args.pdf)
    except RuntimeError as exc:
        print(f"skipped: {exc}", file=sys.stderr)
        return 2

    problems = report(args.pdf, pages)
    if problems:
        print("\nLAYOUT PROBLEMS:")
        for m in problems:
            print(f"  - {m}")
        return 1
    print("layout: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())

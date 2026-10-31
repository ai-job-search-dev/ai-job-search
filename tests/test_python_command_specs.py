"""Guard: the workflow specs settle on `python3`, with one Windows note each.

The specs used to split between spellings: `/apply` and the skill files it
reads (`04-job-evaluation`, `05-cv-templates`) ran bare `python ...` while
`/rank`, `/scrape` and `09-web-research` ran `python3 ...` (#669). The specs
now all use `python3`, and every spec that runs a tool carries a single
Windows note at its first use: a python.org install on Windows ships no
`python3`, so that name reaches the Microsoft Store alias, which either exits
49 with no output or starts a separate Store Python without the packages
`pip` installed. The note points at `python`, and `.claude/settings.json`
keeps granting both spellings, so no settings or guard change is needed.

Nothing enforced either half. A later edit could reintroduce a bare `python
tools/...` line, or a second Windows note, without any test noticing;
`lint_skills.py` reads frontmatter, not command bodies, and the changelog
guard does not read the specs either. This does - over every command, skill
and agent spec rather than a fixed list, so a bare `python` line added to a
spec this file never named still fails.

Only line-leading interpreter tokens are checked - lines that actually run
Python from a code block. Prose that mentions a spelling mid-sentence (the
setup hint, the permission list) is left alone, as are the
`Bash(python ...)`/`Bash(python3 ...)` permission entries, which must keep
naming every spelling a user's machine might actually expose.
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

SPEC_GLOBS = (
    ".claude/commands/*.md",
    ".claude/skills/**/*.md",
    ".claude/agents/*.md",
)

SPECS = sorted(
    {path.relative_to(REPO).as_posix() for pattern in SPEC_GLOBS for path in REPO.glob(pattern)}
)

# Specs known to run a tool today; the glob must keep finding them.
KNOWN_TOOL_SPECS = {
    ".claude/commands/rank.md",
    ".claude/commands/apply.md",
    ".claude/skills/job-scraper/SKILL.md",
    ".claude/skills/job-application-assistant/04-job-evaluation.md",
    ".claude/skills/job-application-assistant/05-cv-templates.md",
    ".claude/skills/job-application-assistant/09-web-research.md",
}

WINDOWS_NOTE = "> **Windows:** run these tools as `python`, not `python3`."

# A line that starts by running an interpreter: the token at line start,
# optionally behind list indentation. `python3` is the settled spelling and
# never flagged; `python` (bare) and any `"$PY"`/probe reintroduction are.
LINE_INTERPRETER = re.compile(r"""^\s*(python3|python|"\$PY"|PY=)""")


def command_lines(text):
    """Yield (lineno, line) for every line-leading interpreter invocation."""
    for lineno, line in enumerate(text.splitlines(), 1):
        m = LINE_INTERPRETER.match(line)
        if m and m.group(1) != "python3":
            yield lineno, line, m.group(1)


class SpecPythonSpellingTest(unittest.TestCase):
    def test_glob_covers_every_known_tool_spec(self):
        missing = KNOWN_TOOL_SPECS - set(SPECS)
        self.assertEqual(missing, set(), f"spec glob no longer finds {sorted(missing)}")

    def test_every_command_invokes_python3(self):
        problems = []
        for spec in SPECS:
            text = (REPO / spec).read_text(encoding="utf-8")
            for lineno, line, token in command_lines(text):
                problems.append(f"{spec}:{lineno}: {line.strip()}")
        self.assertEqual(
            problems,
            [],
            "workflow specs must invoke Python as `python3 ...` on every line - "
            "the settled spelling (#669); a bare `python` breaks where distros "
            "ship no python alias, and a `\"$PY\"` probe cannot work across "
            "Claude Code's per-call shells:\n" + "\n".join(problems),
        )

    def test_each_tool_spec_carries_exactly_one_windows_note_before_first_use(self):
        for spec in SPECS:
            text = (REPO / spec).read_text(encoding="utf-8")
            lines = text.splitlines()
            first_use = next(
                (i for i, l in enumerate(lines) if LINE_INTERPRETER.match(l)), None
            )
            notes = text.count(WINDOWS_NOTE)
            if first_use is None:
                self.assertEqual(
                    notes, 0, f"{spec} runs no tool but carries a Windows note"
                )
                continue
            self.assertEqual(
                notes,
                1,
                f"{spec} must carry exactly one Windows note (found {notes}) - "
                "the note explains why Windows runs the tools as `python` (#669) "
                "and must not be repeated after every command",
            )
            note_at = next(i for i, l in enumerate(lines) if l.startswith(WINDOWS_NOTE))
            self.assertLess(
                note_at,
                first_use,
                f"{spec} puts its Windows note after the first python3 command",
            )

if __name__ == "__main__":
    unittest.main()

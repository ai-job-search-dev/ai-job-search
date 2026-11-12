"""Tests for the /expand command specification."""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPAND_COMMAND_FILE = REPO_ROOT / ".claude" / "commands" / "expand.md"
BEHAVIORAL_PROFILE = REPO_ROOT / ".claude" / "skills" / "job-application-assistant" / "02-behavioral-profile.md"
CANDIDATE_PROFILE = REPO_ROOT / ".claude" / "skills" / "job-application-assistant" / "01-candidate-profile.md"


class ExpandCommandTests(unittest.TestCase):
    def test_expand_command_file_exists(self):
        self.assertTrue(EXPAND_COMMAND_FILE.exists(), "expand.md must exist under .claude/commands/")

    def test_expand_command_file_starts_with_correct_header(self):
        text = EXPAND_COMMAND_FILE.read_text(encoding="utf-8")
        first_line = text.lstrip().splitlines()[0]
        self.assertTrue(
            first_line.startswith("# /expand"),
            f"Command file must start with '# /expand', got: {first_line!r}",
        )

    def test_expand_covers_all_discovery_sources(self):
        text = EXPAND_COMMAND_FILE.read_text(encoding="utf-8")
        sources = [
            "documents/cv/",
            "documents/linkedin/",
            "documents/diplomas/",
            "documents/references/",
            "GitHub Profile",
        ]
        for src in sources:
            self.assertIn(src, text, f"expand.md must include discovery source: {src}")

    def test_expand_maps_github_projects_to_independent_projects_section(self):
        text = EXPAND_COMMAND_FILE.read_text(encoding="utf-8")
        self.assertIn("## Independent Projects", text)
        self.assertIn("Independent Projects & Portfolio", text)
        self.assertIn("GitHub — repo-name", text)
        self.assertIn("Portfolio & projects grounded in code", text)
        self.assertNotIn("documents/projects/", text)

    def test_behavioral_additions_name_sections_the_shipped_file_has(self):
        # Step 5 told the model to append to "Strongest Behavioral Traits" or
        # "How I Work Best" and to "match existing structure" - but the shipped
        # 02-behavioral-profile.md has neither heading (it has "Strongest
        # Behaviors" and "How You Work Best"), so the addition either landed
        # in a duplicate near-identical section or was dropped. Derived from
        # the shipped file so a renamed heading fails here until /expand follows.
        text = EXPAND_COMMAND_FILE.read_text(encoding="utf-8")
        start = text.index("### Additions to `02-behavioral-profile.md`")
        section = text[start: text.index("---", start)]
        quoted = re.findall(r'"([^"]+)"', section)
        self.assertTrue(quoted, "the 02-behavioral-profile step must name its target sections")
        headings = {
            line[3:].strip() for line in BEHAVIORAL_PROFILE.read_text(encoding="utf-8").splitlines()
            if line.startswith("## ")
        }
        for name in quoted:
            with self.subTest(section=name):
                self.assertIn(name, headings, f"02-behavioral-profile.md has no '## {name}' heading")

    def test_profile_additions_name_sections_the_shipped_file_has(self):
        # Same class as the 02 fix above (#520): Step 5 sent domain knowledge
        # to a "Domain Knowledge or Technical Skills section", but the shipped
        # 01-candidate-profile.md has `### Domain Expertise` under
        # `## Technical Skills` and no Domain Knowledge heading at all, so the
        # addition landed in a new near-duplicate section or on the wrong
        # subsection. Every "append to the X section" target in the 01 step
        # must be a heading the shipped file carries, backticked with its level.
        text = EXPAND_COMMAND_FILE.read_text(encoding="utf-8")
        start = text.index("### Additions to `01-candidate-profile.md`")
        section = text[start: text.index("### Additions to `02-behavioral-profile.md`", start)]
        targets = re.findall(r"append to the (.+?) (?:sub)?section", section)
        self.assertTrue(targets, "the 01-candidate-profile step must name its target sections")
        headings = {
            line.strip() for line in CANDIDATE_PROFILE.read_text(encoding="utf-8").splitlines()
            if line.startswith(("## ", "### "))
        }
        for target in targets:
            with self.subTest(target=target):
                quoted = re.findall(r"`(#+ [^`]+)`", target)
                self.assertTrue(quoted, f"name the heading as the file spells it, e.g. `## Section`: {target!r}")
                for heading in quoted:
                    self.assertIn(heading, headings, f"01-candidate-profile.md has no '{heading}' heading")

    def test_expand_enforces_additive_and_confirmation_principles(self):
        text = EXPAND_COMMAND_FILE.read_text(encoding="utf-8")
        self.assertIn("Additive only", text)
        self.assertIn("User confirms before writing", text)
        self.assertIn("`all`", text)
        self.assertIn("`review`", text)
        self.assertIn("`skip`", text)


if __name__ == "__main__":
    unittest.main()


"""Tests for platform lineage and keyword coverage status (#822).

Pins the 'unconfirmed (related tool)' status in apply.md Step 5d and the
'Platform lineage' guidance in 04-job-evaluation.md so vendor platforms
(ChatGPT, Gemini, Copilot) related to listed profile tools (Codex,
Antigravity, Claude Code) are flagged for user confirmation instead of
silently scored as genuine gaps.
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APPLY_MD = REPO_ROOT / ".claude" / "commands" / "apply.md"
EVALUATION_MD = REPO_ROOT / ".claude" / "skills" / "job-application-assistant" / "04-job-evaluation.md"


class KeywordCoverageLineageTests(unittest.TestCase):
    def test_apply_step_5d_has_unconfirmed_related_tool_status(self):
        text = APPLY_MD.read_text(encoding="utf-8")
        step_5d = re.search(r"### 5d\.(.*?)(?=### 5e\.)", text, re.DOTALL)
        self.assertIsNotNone(step_5d, "apply.md must contain Step 5d")
        step_5d_text = step_5d.group(1)

        # Table header must list the status
        self.assertIn("unconfirmed (related tool)", step_5d_text)

        # Bullet point explaining the status
        self.assertRegex(step_5d_text, r"\*\*unconfirmed \(related tool\)\*\*.*—")

        # Must mandate checking with user and writing back to 01-candidate-profile.md
        self.assertIn("01-candidate-profile.md", step_5d_text)

        # Must preserve the no-fabrication API boundary
        self.assertRegex(step_5d_text, r"never building on its API|use of the platform only")

    def test_job_evaluation_defines_platform_lineage_guidance(self):
        text = EVALUATION_MD.read_text(encoding="utf-8")
        skills_match = re.search(r"### 1\. Technical Skills Match(.*?)(?=### 2\. Experience Match)", text, re.DOTALL)
        self.assertIsNotNone(skills_match, "04-job-evaluation.md must contain Technical Skills Match")
        skills_text = skills_match.group(1)

        # Must define Platform lineage section
        self.assertIn("**Platform lineage:**", skills_text)

        # Must reference vendor platform examples and write-back
        self.assertIn("01-candidate-profile.md", skills_text)
        self.assertIn("ChatGPT", skills_text)
        self.assertIn("Codex", skills_text)
        # Must scope /apply and /rank behaviors
        self.assertIn("ask the user once before scoring", skills_text)
        self.assertIn("do not ask", skills_text)
        self.assertIn("unconfirmed (related tool)", skills_text)

    def test_job_evaluation_framework_version_bumped(self):
        text = EVALUATION_MD.read_text(encoding="utf-8")
        version_match = re.search(r"^framework_version:\s*(\d+)\.(\d+)\.(\d+)", text, re.MULTILINE)
        self.assertIsNotNone(version_match, "04-job-evaluation.md must have framework_version")
        version = tuple(int(p) for p in version_match.groups())
        self.assertGreaterEqual(version, (1, 3, 0), "framework_version must be at least 1.3.0")


if __name__ == "__main__":
    unittest.main()

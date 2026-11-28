"""Tests for the Eligibility Gate wording in 04-job-evaluation.md."""

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EVALUATION_FILE = REPO_ROOT / ".claude" / "skills" / "job-application-assistant" / "04-job-evaluation.md"


class EligibilityGateSpecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        text = EVALUATION_FILE.read_text(encoding="utf-8")
        start = text.index("## Eligibility Gate")
        end = text.index("\n## ", start + 1)
        cls.gate = text[start:end]

    def test_refusal_of_sponsorship_is_a_fail_row(self):
        row = next(
            (line for line in self.gate.splitlines() if "we do not offer visa sponsorship" in line),
            None,
        )
        self.assertIsNotNone(row, "the gate must name explicit sponsorship refusals")
        self.assertIn("FAIL", row)

    def test_explicit_support_still_passes(self):
        row = next((line for line in self.gate.splitlines() if '"we sponsor"' in line), None)
        self.assertIsNotNone(row, "the gate must keep the explicit-support row")
        self.assertIn("PASS", row)

    def test_keyword_hit_is_not_evidence_of_support(self):
        self.assertIn("A keyword hit is not evidence of support", self.gate)
        self.assertIn("where necessary", self.gate, "conditional wording must be treated as unverified")

    def test_relocation_refusal_is_not_listed_as_a_sponsorship_refusal(self):
        self.assertNotIn("unable to support relocation", self.gate)


if __name__ == "__main__":
    unittest.main()

"""Guard: `/add-template --use default` knows which guidance file it deactivates.

Switch Mode resolved `<type>` from the chosen template's manifest, but `default`
has no manifest, so the spec sent it to Step 5 with no type at all. Step 5 edits
"the relevant guidance file" by type, so with a CV template and a cover letter
template both active it was undefined which managed block `--use default`
removed. The step now derives the target from where a block is active, names
both guidance files, and says what to do when both or neither carry one.
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ADD_TEMPLATE = REPO / ".claude" / "commands" / "add-template.md"
GUIDANCE = ("05-cv-templates.md", "06-cover-letter-templates.md")


def switch_mode_default_step(text):
    switch = re.search(r"## Switch Mode(.*?)(?=\n## )", text, re.DOTALL).group(1)
    return re.search(r"\n1\. If `<name>` is `default`(.*?)(?=\n2\. )", switch, re.DOTALL).group(1)


class UseDefaultResolvesItsTarget(unittest.TestCase):
    def setUp(self):
        self.text = ADD_TEMPLATE.read_text(encoding="utf-8")
        self.step = switch_mode_default_step(self.text)

    def test_default_step_names_both_guidance_files(self):
        for name in GUIDANCE:
            with self.subTest(file=name):
                self.assertIn(name, self.step, "the step must say where it looks for an active block")
        self.assertIn("ACTIVE-TEMPLATE", self.step)

    def test_default_step_covers_one_both_and_neither(self):
        for case in ("One file carries a block", "Both files carry a block", "Neither file carries a block"):
            with self.subTest(case=case):
                self.assertIn(case, self.step)
        self.assertIn("ask the user", self.step, "two active blocks need a decision, not a guess")

    def test_step_5_default_rule_removes_only_the_resolved_block(self):
        rule = re.search(r"- \*\*`--use default`\*\*: (.*?)\n", self.text).group(1)
        self.assertIn("Switch Mode step 1 resolved", rule)
        self.assertIn("never touch the other guidance file", rule)


if __name__ == "__main__":
    unittest.main()

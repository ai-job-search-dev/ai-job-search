"""Guard: `/add-template`'s name rule and its design principle agree.

Step 2.1 said the name "must not collide with an existing folder in
`templates/`", while the Design Principles say "re-running with the same name
offers to update the existing template rather than duplicating it". Both were
read by the model on every run; one forbade what the other promised. The name
rule now describes the update path, and this test keeps the two statements
pointing the same way.
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ADD_TEMPLATE = REPO / ".claude" / "commands" / "add-template.md"


class NameRuleMatchesDesignPrinciple(unittest.TestCase):
    def setUp(self):
        self.text = ADD_TEMPLATE.read_text(encoding="utf-8")
        self.name_rule = re.search(r"\n1\. \*\*Name\*\* - (.*?)\n", self.text).group(1)
        principles = re.search(r"## Design Principles(.*?)(?=\n## |\Z)", self.text, re.DOTALL).group(1)
        self.idempotent = re.search(r"- Registration is idempotent: (.*?)\n", principles).group(1)

    def test_design_principle_still_promises_an_update_path(self):
        self.assertIn("offers to update the existing template", self.idempotent)

    def test_name_rule_offers_the_update_instead_of_forbidding_the_name(self):
        self.assertNotIn("Must not collide", self.name_rule)
        self.assertIn("update that template in place", self.name_rule)
        self.assertIn("Never create a second copy", self.name_rule)


if __name__ == "__main__":
    unittest.main()

"""Guard: /interview's Rule 5 names every write the command makes outside the
application archive.

Rule 5 said "write only to the application archive - with one exception", then
named two (STAR examples appended to 07-interview-prep.md, and new facts written
to 01-candidate-profile.md) and omitted a third the spec itself requires: Step 2
writes or refreshes the company research cache. A rule that undercounts its own
exceptions reads as a contradiction to the model following it - the cache write
looks like a violation of Rule 5, or Rule 5 looks like prose to ignore.

The writes are located by the sentences that order them, so a reworded or
removed step fails the anchor test rather than silently passing, and each one
must be named in Rule 5 with the count the rule states.
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INTERVIEW = REPO / ".claude" / "commands" / "interview.md"

# (the sentence in the body that orders the write, the path Rule 5 must name)
WRITES = (
    (r"write \(or overwrite\) the cache file", "company_research/<normalized-company-name>.json"),
    (r"append them to `07-interview-prep\.md`", "07-interview-prep.md"),
    (r"write the fact into the profile", "01-candidate-profile.md"),
)
COUNT_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}


def rule_five(text):
    rules = re.search(r"## Important Rules(.*?)(?=\n## |\Z)", text, re.DOTALL).group(1)
    return re.search(r"\n5\. (.*?)(?=\n\d+\. |\Z)", rules, re.DOTALL).group(1)


class RuleFiveNamesEveryWrite(unittest.TestCase):
    def setUp(self):
        self.text = INTERVIEW.read_text(encoding="utf-8")
        self.rule = rule_five(self.text)

    def test_body_still_orders_each_write(self):
        body = self.text[: self.text.index("## Important Rules")] + self.rule
        for sentence, path in WRITES:
            with self.subTest(path=path):
                self.assertRegex(body, sentence, "the write this test pins was reworded or removed; update WRITES")

    def test_rule_five_names_each_write(self):
        for _, path in WRITES:
            with self.subTest(path=path):
                self.assertIn(path, self.rule, "Rule 5 must name every write the spec makes outside the archive")

    def test_rule_five_states_the_right_count(self):
        self.assertNotIn("with one exception", self.rule)
        stated = re.search(r"with (\w+) exceptions", self.rule)
        self.assertIsNotNone(stated, "Rule 5 must say how many exceptions it has")
        self.assertEqual(COUNT_WORDS.get(stated.group(1)), len(WRITES))


if __name__ == "__main__":
    unittest.main()

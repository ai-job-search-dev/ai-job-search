"""Tests for tools/fact_guard.py, the mechanical grounding check /apply runs before compiling."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import fact_guard  # noqa: E402

SOURCES = "Increased app acquisition by 25% at Canva. App scaled from 2.2M to 17M+ users. Ratings 4.2 to 4.8. Led 10 engineers."


class UnverifiedTest(unittest.TestCase):
    def test_grounded_claims_pass(self):
        draft = "At Canva, growth experiments raised acquisition 25%; the app grew from 2.2M to 17M+ users and ratings rose from 4.2 to 4.8."
        self.assertEqual(fact_guard.unverified(draft, SOURCES), [])

    def test_escalated_number_is_flagged(self):
        self.assertIn(("number", "35"), fact_guard.unverified("Raised acquisition 35% at Canva.", SOURCES))

    def test_unknown_employer_and_acronym_are_flagged(self):
        flags = fact_guard.unverified("Shipped features at Canva and at Google, holding a PMP.", SOURCES)
        self.assertIn(("name", "Google"), flags)
        self.assertIn(("name", "PMP"), flags)

    def test_sentence_start_capitals_are_not_names(self):
        self.assertEqual(fact_guard.unverified("Shipped features. Built tools.", SOURCES), [])

    def test_credential_word_is_flagged(self):
        self.assertIn(("credential", "certified"), fact_guard.unverified("I am a certified coach.", SOURCES))

    def test_latex_is_stripped_to_prose(self):
        tex = "\\documentclass{x}\n\\begin{document}\n\\item {Raised acquisition \\textbf{25\\%} at Canva.} % note 99\n\\end{document}"
        text = fact_guard.latex_to_text(tex)
        self.assertNotIn("99", text)
        self.assertIn("25%", text)
        self.assertEqual(fact_guard.unverified(text, SOURCES), [])


if __name__ == "__main__":
    unittest.main()

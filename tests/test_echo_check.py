"""Tests for tools/echo_check.py - phrase lifts from the posting and reuse across letters.

The case that motivated `10-echo-traps.md` is the first fixture: the posting's
"messy operational and planning problems" came back in a CV profile almost verbatim.
"""
import io
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import echo_check  # noqa: E402

POSTING = (
    "We are looking for someone who understands messy operational and planning problems "
    "and turns solutions into practical tools. You are proficient in Gurobi, CPLEX.\n"
    "What you bring: you enjoy working close to users."
)


class EchoCheck(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)
        self.posting = self.write("job_posting.md", POSTING)

    def write(self, name, text):
        path = self.tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return str(path)

    def run_check(self, *argv, posting=None):
        out = io.StringIO()
        with redirect_stdout(out):
            code = echo_check.main(["--posting", posting or self.posting, *argv])
        return code, out.getvalue()

    def test_lift_is_reported_with_its_line(self):
        draft = self.write("cv/main_x.tex", "\\begin{document}\n\\small{Turns messy operational and planning problems into tools.}\n")
        code, out = self.run_check("--draft", draft)
        self.assertEqual(code, 1)
        self.assertIn("LIFT", out)
        self.assertIn("main_x.tex:2  messy operational and planning problems", out)

    def test_own_words_pass(self):
        draft = self.write("cv/main_x.tex", "Formalizes ill-structured planning problems as MILP models in Gurobi.")
        code, out = self.run_check("--draft", draft)
        self.assertEqual(code, 0, out)

    def test_quoted_reference_to_the_posting_is_allowed(self):
        draft = self.write("cover.tex", "Against your “you enjoy working close to users” point:")
        code, out = self.run_check("--draft", draft)
        self.assertEqual(code, 0, out)

    def test_latex_quotes_are_allowed_too(self):
        draft = self.write("cover.tex", "Against your ``you enjoy working close to users'' point:")
        code, out = self.run_check("--draft", draft)
        self.assertEqual(code, 0, out)

    def test_ignored_phrase_breaks_the_run(self):
        draft = self.write("cover.tex", "someone who understands messy operational")
        self.assertEqual(self.run_check("--draft", draft)[0], 1)
        self.assertEqual(self.run_check("--draft", draft, "--ignore", "understands messy")[0], 0)

    def test_function_word_runs_are_not_lifts(self):
        posting = self.write("p.md", "it is in the interest of all of us to be on time")
        draft = self.write("cover.tex", "it is in the interest of the team")
        code, out = self.run_check("--draft", draft, posting=posting)
        self.assertEqual(code, 0, out)

    def test_preamble_markup_and_contact_details_are_ignored(self):
        header = (
            "\\documentclass{cover}\n\\usepackage{fontspec}\n\\begin{document}\n"
            "\\namesection{}{Jane Doe}{\\href{mailto:jane@example.com}{jane@example.com} | +45 12 34 56 78 | "
            "\\urlstyle{same}\\href{https://example.com}{LinkedIn}}\n"
            "{\\fontspec[Path = OpenFonts/fonts/raleway/, BoldFont = Raleway-Bold]{Raleway-Medium}\\fontsize{11pt}{13pt}\\selectfont}\n"
        )
        draft = self.write("letters/cover_a.tex", header + "Body about rail scheduling for a freight operator.\n")
        self.write("letters/cover_b.tex", header + "Warehouse slotting for a grocery chain is the topic here.\n")
        code, out = self.run_check("--draft", draft, "--others", str(self.tmp / "letters" / "cover_*.tex"))
        self.assertEqual(code, 0, out)

    def test_reuse_between_letters_is_reported(self):
        shared = "My last project modelled the packing and testing lines of a medical device plant."
        draft = self.write("letters/cover_a.tex", shared + " Then something new.")
        other = self.write("letters/cover_b.tex", "Opening. " + shared)
        code, out = self.run_check("--draft", draft, "--others", str(self.tmp / "letters" / "cover_*.tex"))
        self.assertEqual(code, 1)
        self.assertIn("REUSE", out)
        self.assertIn(Path(other).name, out)

    def test_cv_is_not_compared_with_letters(self):
        shared = "modelled the packing and testing lines of a medical device plant"
        draft = self.write("cv/main_a.tex", shared)
        self.write("letters/cover_b.tex", shared)
        code, out = self.run_check("--draft", draft, "--others", str(self.tmp / "letters" / "cover_*.tex"))
        self.assertEqual(code, 0, out)

    def test_draft_is_not_compared_with_itself(self):
        draft = self.write("letters/cover_a.tex", "A long sentence about scheduling freight trains across Europe.")
        code, out = self.run_check("--draft", draft, "--others", str(self.tmp / "letters" / "cover_*.tex"))
        self.assertEqual(code, 0, out)

    def test_apostrophes_do_not_open_a_quote(self):
        # A straight or curly apostrophe is not a quotation mark: the lift after it
        # must still be reported, in English and in elided languages alike.
        posting = self.write("p.md", "you translate the client's business needs into data solutions")
        for text in (
            "The team's goal: translate the client's business needs into data solutions",
            "L’équipe: translate the client’s business needs into data solutions",
        ):
            draft = self.write("cover.tex", text)
            code, out = self.run_check("--draft", draft, posting=posting)
            self.assertEqual(code, 1, text)
            self.assertIn("business needs into data solutions", out)

    def test_missing_file_exits_2(self):
        code = subprocess.run(
            [sys.executable, str(REPO / "tools" / "echo_check.py"), "--posting", self.posting,
             "--draft", str(self.tmp / "nope.tex")],
            capture_output=True,
        ).returncode
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()

"""Tests for tools/echo_check.py - phrase lifts from the posting and reuse across letters.

The case that motivated `10-echo-traps.md` is the first fixture: the posting's
"messy operational and planning problems" came back in a CV profile almost verbatim.
"""
import io
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
        root, echo_check.ROOT = echo_check.ROOT, self.tmp
        self.addCleanup(setattr, echo_check, "ROOT", root)
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
        draft = self.write("cover_letters/cover.tex", "Against your “you enjoy working close to users” point:")
        code, out = self.run_check("--draft", draft)
        self.assertEqual(code, 0, out)

    def test_latex_quotes_are_allowed_too(self):
        draft = self.write("cover_letters/cover.tex", "Against your ``you enjoy working close to users'' point:")
        code, out = self.run_check("--draft", draft)
        self.assertEqual(code, 0, out)

    def test_straight_quotes_may_wrap_a_line_but_not_a_paragraph(self):
        wrapped = self.write("cover_letters/a.tex", 'Against your "you enjoy working\nclose to users" point:')
        self.assertEqual(self.run_check("--draft", wrapped)[0], 0)
        # An unclosed quote must not hide a lift in the next paragraph.
        unclosed = self.write("cover_letters/b.tex", 'A stray " quote.\n\nYou enjoy working close to users.\n\nEnd "here".')
        self.assertEqual(self.run_check("--draft", unclosed)[0], 1)

    def test_ignored_phrase_breaks_the_run(self):
        draft = self.write("cover_letters/cover.tex", "someone who understands messy operational")
        self.assertEqual(self.run_check("--draft", draft)[0], 1)
        self.assertEqual(self.run_check("--draft", draft, "--ignore", "understands messy")[0], 0)

    def test_function_word_runs_are_not_lifts(self):
        posting = self.write("p.md", "it is in the interest of all of us to be on time")
        draft = self.write("cover_letters/cover.tex", "it is in the interest of the team")
        code, out = self.run_check("--draft", draft, posting=posting)
        self.assertEqual(code, 0, out)

    def test_preamble_markup_and_contact_details_are_ignored(self):
        header = (
            "\\documentclass{cover}\n\\usepackage{fontspec}\n\\begin{document}\n"
            "\\namesection{}{Jane Doe}{\\href{mailto:jane@example.com}{jane@example.com} | +45 12 34 56 78 | "
            "\\urlstyle{same}\\href{https://example.com}{LinkedIn}}\n"
            "{\\fontspec[Path = OpenFonts/fonts/raleway/, BoldFont = Raleway-Bold]{Raleway-Medium}\\fontsize{11pt}{13pt}\\selectfont}\n"
        )
        draft = self.write("cover_letters/cover_a.tex", header + "Body about rail scheduling for a freight operator.\n")
        self.write("cover_letters/cover_b.tex", header + "Warehouse slotting for a grocery chain is the topic here.\n")
        code, out = self.run_check("--draft", draft, "--others", str(self.tmp / "cover_letters" / "cover_*.tex"))
        self.assertEqual(code, 0, out)

    def test_reuse_between_letters_is_reported(self):
        shared = "My last project modelled the packing and testing lines of a medical device plant."
        draft = self.write("cover_letters/cover_a.tex", shared + " Then something new.")
        other = self.write("cover_letters/cover_b.tex", "Opening. " + shared)
        code, out = self.run_check("--draft", draft, "--others", str(self.tmp / "cover_letters" / "cover_*.tex"))
        self.assertEqual(code, 1)
        self.assertIn("REUSE", out)
        self.assertIn(Path(other).name, out)

    def test_cv_is_not_compared_with_letters(self):
        shared = "modelled the packing and testing lines of a medical device plant"
        draft = self.write("cv/main_a.tex", shared)
        self.write("cover_letters/cover_b.tex", shared)
        code, out = self.run_check("--draft", draft, "--others", str(self.tmp / "cover_letters" / "cover_*.tex"))
        self.assertEqual(code, 0, out)

    def test_draft_is_not_compared_with_itself(self):
        draft = self.write("cover_letters/cover_a.tex", "A long sentence about scheduling freight trains across Europe.")
        code, out = self.run_check("--draft", draft, "--others", str(self.tmp / "cover_letters" / "cover_*.tex"))
        self.assertEqual(code, 0, out)

    def test_apostrophes_do_not_open_a_quote(self):
        # A straight or curly apostrophe is not a quotation mark: the lift after it
        # must still be reported, in English and in elided languages alike.
        posting = self.write("p.md", "you translate the client's business needs into data solutions")
        for text in (
            "The team's goal: translate the client's business needs into data solutions",
            "L’équipe: translate the client’s business needs into data solutions",
        ):
            draft = self.write("cover_letters/cover.tex", text)
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



class InputConfinement(unittest.TestCase):
    """settings.json pre-approves echo_check.py with any arguments, so it must not
    read files the Read deny rules protect (.env, ~/.ssh, ~/.aws, ~/.gnupg)."""

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.tmp = Path(directory.name)
        root, echo_check.ROOT = echo_check.ROOT, self.tmp
        self.addCleanup(setattr, echo_check, "ROOT", root)
        self.posting = self.tmp / "posting.txt"
        self.posting.write_text("API_KEY=secret value that must never be printed", encoding="utf-8")
        self.draft = self.tmp / "cover_letters" / "cover.tex"
        self.draft.parent.mkdir()
        self.draft.write_text("API_KEY=secret value that must never be printed", encoding="utf-8")

    def refused(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = echo_check.main(list(map(str, argv)))
        self.assertEqual(code, 2, out.getvalue())
        self.assertNotIn("secret", out.getvalue())
        self.assertIn("echo_check:", err.getvalue())

    def test_dotenv_is_refused(self):
        env = self.tmp / ".env"
        env.write_text("API_KEY=secret value that must never be printed", encoding="utf-8")
        self.assertIsNotNone(echo_check.check_input_path(env))
        self.refused("--posting", env, "--draft", self.draft)

    def test_file_under_a_dot_directory_is_refused(self):
        hidden = self.tmp / ".ssh" / "notes.txt"
        hidden.parent.mkdir()
        hidden.write_text("API_KEY=secret value that must never be printed", encoding="utf-8")
        self.assertIsNotNone(echo_check.check_input_path(hidden))
        self.refused("--posting", self.posting, "--draft", hidden)

    def test_other_suffix_is_refused(self):
        script = self.tmp / "settings.py"
        script.write_text("API_KEY=secret value that must never be printed", encoding="utf-8")
        self.refused("--posting", script, "--draft", self.draft)

    def test_symlink_is_refused(self):
        link = self.tmp / "posting_link.txt"
        try:
            link.symlink_to(self.posting)
        except (OSError, NotImplementedError):
            self.skipTest("creating symlinks needs privilege on this platform")
        self.assertEqual(echo_check.check_input_path(link), "must not be a symlink")
        self.refused("--posting", link, "--draft", self.draft)

    def test_refused_glob_hit_in_others_is_skipped(self):
        letters = self.tmp / "cover_letters"
        draft = letters / "cover_a.tex"
        draft.write_text("A long sentence about scheduling freight trains across Europe.", encoding="utf-8")
        (letters / "cover_b.py").write_text("A long sentence about scheduling freight trains across Europe.", encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out):
            code = echo_check.main(["--posting", str(self.posting), "--draft", str(draft),
                                    "--others", str(letters / "cover_*")])
        self.assertEqual(code, 0, out.getvalue())

    def test_draft_outside_the_application_folders_is_refused(self):
        # The same private file as posting and draft would otherwise print in full.
        notes = self.tmp / "private" / "notes.md"
        notes.parent.mkdir()
        notes.write_text("API_KEY=secret value that must never be printed", encoding="utf-8")
        self.assertIsNone(echo_check.check_input_path(notes), "a plain .md file passes the suffix rules")
        self.refused("--posting", notes, "--draft", notes)

    def test_others_outside_the_application_folders_are_skipped(self):
        outside = self.tmp / "private" / "cover_x.tex"
        outside.parent.mkdir()
        outside.write_text("API_KEY=secret value that must never be printed", encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out):
            echo_check.main(["--posting", str(self.posting), "--draft", str(self.draft), "--others", str(outside)])
        self.assertNotIn("REUSE", out.getvalue())

    def test_allowed_inputs_pass(self):
        for name in ("p.md", "p.txt", "c.tex", "c.typ"):
            self.assertIsNone(echo_check.check_input_path(self.tmp / name))


if __name__ == "__main__":
    unittest.main()

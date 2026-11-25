"""Guards for the LaTeX authoring guidance and the example documents.

Three silent-failure modes live here, all found by the 2026-08-19 review
(F9, F31, F34). Each one produces a clean compile and a green CI run
while the rendered document or its ATS extraction is wrong, so the spec
files and the example sources are the only place a test can catch them:

- F9: a bullet written as `\\item [text]` is parsed as moderncv's
  optional label, rendered off the left page edge, and dropped from the
  PDF text layer. The example CV shipped that way for months.
- F31: an unescaped `%` in body text silently truncates the rest of the
  line (`&` at least fails loudly). The guidance must name the escapes.
- F34: `pdftotext` without `-enc UTF-8` emits Latin-1 on Xpdf builds,
  so a correct Danish CV fails the documented "no replacement
  characters" check and the agent is sent to "fix" a healthy document.
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO / ".claude" / "skills" / "job-application-assistant"
CV_TEMPLATES = SKILL_DIR / "05-cv-templates.md"
COVER_TEMPLATES = SKILL_DIR / "06-cover-letter-templates.md"
APPLY = REPO / ".claude" / "commands" / "apply.md"
EXAMPLE_CV = REPO / "cv" / "main_example.tex"
EXAMPLE_COVER = REPO / "cover_letters" / "cover_example.tex"

# \item whose body starts with [ - with or without whitespace between.
# LaTeX skips spaces while scanning for the optional argument, so
# `\item [text]` and `\item[text]` both swallow the text as a label.
# The safe spelling `\item {[text]}` does not match.
UNBRACED_BRACKET_ITEM = re.compile(r"\\item\s*\[")

# The escapes both guidance files must document. `%` is the load-bearing
# one: it truncates silently. The others fail loudly or corrupt spacing.
REQUIRED_ESCAPES = ["\\&", "\\%", "\\$", "\\#", "\\_"]


def section(text, heading):
    """Return the body of a markdown section up to the next heading."""
    pattern = re.compile(
        rf"^#+ {re.escape(heading)}[^\n]*\n(.*?)(?=^#+ |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    return match.group(1) if match else None


class TestBulletBracketTrap(unittest.TestCase):
    """F9: no document or template doc may teach `\\item [text]`."""

    def assert_no_unbraced_bracket_items(self, path):
        offending = [
            f"{path.name}:{lineno}: {line.strip()}"
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if UNBRACED_BRACKET_ITEM.search(line)
        ]
        self.assertEqual(
            offending,
            [],
            "\\item followed by [ is parsed as an optional label and the "
            "text is clipped off the page; write \\item {[...]} instead:\n"
            + "\n".join(offending),
        )

    def test_example_cv_has_no_bracket_labelled_bullets(self):
        self.assert_no_unbraced_bracket_items(EXAMPLE_CV)

    def test_example_cover_letter_has_no_bracket_labelled_bullets(self):
        self.assert_no_unbraced_bracket_items(EXAMPLE_COVER)

    def test_cover_letter_guide_does_not_teach_the_broken_pattern(self):
        self.assert_no_unbraced_bracket_items(COVER_TEMPLATES)

    def test_cv_guide_does_not_teach_the_broken_pattern(self):
        self.assert_no_unbraced_bracket_items(CV_TEMPLATES)


class TestSpecialCharacterGuidance(unittest.TestCase):
    """F31: both template guides must document the LaTeX escapes."""

    def assert_escapes_documented(self, path):
        body = section(path.read_text(encoding="utf-8"), "LaTeX Special Characters")
        self.assertIsNotNone(
            body, f"{path.name} has no 'LaTeX Special Characters' section"
        )
        missing = [esc for esc in REQUIRED_ESCAPES if esc not in body]
        self.assertEqual(
            missing,
            [],
            f"{path.name}'s special-characters section is missing: {missing}",
        )

    def test_cv_guide_documents_the_escapes(self):
        self.assert_escapes_documented(CV_TEMPLATES)

    def test_cover_letter_guide_documents_the_escapes(self):
        self.assert_escapes_documented(COVER_TEMPLATES)

    def test_cv_guide_warns_that_percent_truncates_silently(self):
        body = section(
            CV_TEMPLATES.read_text(encoding="utf-8"), "LaTeX Special Characters"
        )
        self.assertIsNotNone(body)
        self.assertRegex(
            body,
            re.compile(r"silent", re.IGNORECASE),
            "the % failure mode must be called out as silent - it is the "
            "reason this section exists (a clean compile with the rest of "
            "the bullet gone)",
        )


class TestAtsExtractionEncoding(unittest.TestCase):
    """F34: every documented extraction command must pin the encoding."""

    def assert_pdftotext_commands_pin_utf8(self, path):
        offending = [
            f"{path.name}:{lineno}: {line.strip()}"
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if "pdftotext" in line and "-layout" in line and "-enc UTF-8" not in line
        ]
        self.assertEqual(
            offending,
            [],
            "pdftotext without -enc UTF-8 emits Latin-1 on Xpdf builds, so "
            "the ATS check reports phantom replacement characters on any "
            "non-ASCII CV; add -enc UTF-8:\n" + "\n".join(offending),
        )

    def test_apply_extraction_command_pins_utf8(self):
        self.assert_pdftotext_commands_pin_utf8(APPLY)

    def test_cv_guide_extraction_command_pins_utf8(self):
        self.assert_pdftotext_commands_pin_utf8(CV_TEMPLATES)


class TestPdflatexFontEncodingGuard(unittest.TestCase):
    """#384: the pdflatex fallback must load T1 fontenc, and only under pdflatex.

    Without T1, pdflatex stores accented letters decomposed in the text layer
    (`e` + U+0300), so an ATS keyword match on `Genève` fails while the PDF
    looks right. moderncv 2.5 loads T1 itself; the apt-packaged 2.3.1 does not.
    The line must be guarded so the documented lualatex path is untouched.
    """

    GUARDED_FONTENC = re.compile(r"\\ifpdftex\s*\\usepackage\[T1\]\{fontenc\}\s*\\fi")

    def assert_has_guarded_fontenc(self, path):
        text = path.read_text(encoding="utf-8")
        self.assertRegex(
            text,
            self.GUARDED_FONTENC,
            f"{path.name} must carry `\\ifpdftex\\usepackage[T1]{{fontenc}}\\fi` so a "
            "pdflatex fallback keeps accents precomposed in the text layer",
        )
        unguarded = [
            f"{path.name}:{lineno}: {line.strip()}"
            for lineno, line in enumerate(text.splitlines(), 1)
            if "fontenc" in line
            and not line.lstrip().startswith("%")
            and not self.GUARDED_FONTENC.search(line)
        ]
        self.assertEqual(
            unguarded,
            [],
            "fontenc must stay inside the \\ifpdftex guard - lualatex output "
            "must not change:\n" + "\n".join(unguarded),
        )

    def test_example_cv_guards_fontenc_for_pdflatex(self):
        self.assert_has_guarded_fontenc(EXAMPLE_CV)

    def test_cv_guide_preamble_guards_fontenc_for_pdflatex(self):
        self.assert_has_guarded_fontenc(CV_TEMPLATES)


class TestMissingPackageGuidance(unittest.TestCase):
    """A minimal TeX install only behaves like a full one if every compile
    loop installs a missing package instead of editing the source around it.
    The rule lives once in 05-cv-templates.md; every agent compile loop (05, 06,
    /apply, /add-template) points to it. SETUP.md's smoke tests are run by people
    and list every package up front, so they carry no pointer."""

    HEADING = "Missing LaTeX packages"

    def test_cv_guide_states_the_rule(self):
        body = section(CV_TEMPLATES.read_text(encoding="utf-8"), self.HEADING)
        self.assertIsNotNone(body, f"05-cv-templates.md needs a '{self.HEADING}' section")
        self.assertIn("tlmgr search --global --file", body)
        self.assertIn("never run `sudo`", body.lower(), "the rule must say what to do when an install needs root")

    def test_every_compile_loop_points_to_the_rule(self):
        add_template = REPO / ".claude" / "commands" / "add-template.md"
        for path in (COVER_TEMPLATES, APPLY, add_template):
            with self.subTest(path=path.name):
                self.assertIn(self.HEADING, path.read_text(encoding="utf-8"))


class TestGeometryScaleAlignment(unittest.TestCase):
    """The geometry scale specified in 05-cv-templates.md must match cv/main_example.tex."""

    GEOMETRY_SCALE = re.compile(r"\\usepackage\[scale=([0-9.]+)\]\{geometry\}")

    def test_cv_guide_and_example_cv_share_geometry_scale(self):
        cv_match = self.GEOMETRY_SCALE.search(EXAMPLE_CV.read_text(encoding="utf-8"))
        guide_match = self.GEOMETRY_SCALE.search(CV_TEMPLATES.read_text(encoding="utf-8"))
        self.assertIsNotNone(cv_match, "cv/main_example.tex must declare geometry scale")
        self.assertIsNotNone(guide_match, "05-cv-templates.md must declare geometry scale")
        self.assertEqual(
            guide_match.group(1),
            cv_match.group(1),
            f"05-cv-templates.md geometry scale ({guide_match.group(1)}) must match "
            f"cv/main_example.tex ({cv_match.group(1)})",
        )


if __name__ == "__main__":
    unittest.main()

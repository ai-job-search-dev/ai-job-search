"""Guard for /apply Step 5b's page-count check.

The 2-page CV and 1-page cover letter limits are the hard rules of
05-cv-templates.md and 06-cover-letter-templates.md, and two places defer
their enforcement to `tools/verify_pdf.py --pages`: `verify_layout.py`'s
docstring ("page count is verify_pdf.py's job, and CI runs it") and Step 5b's
own prose, which used to say "Step 5d already runs it". Step 5d's only
invocation is `--dump-text`, and no other step passed `--pages` at all, so
the one rule with a mechanical check had zero runnable implementations in
the workflow. These tests pin that the invocations exist where the prose says
they do, with the counts the guides require, and that no step defers the
check to another step that does not run it.
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APPLY = REPO / ".claude" / "commands" / "apply.md"
VERIFY_LAYOUT = REPO / "tools" / "verify_layout.py"


def section(path, heading):
    """The body of one markdown section, up to the next heading of any depth."""
    text = path.read_text(encoding="utf-8")
    start = text.index(heading) + len(heading)
    rest = text[start:]
    end = re.search(r"^#{1,4} ", rest, re.MULTILINE)
    return rest[: end.start()] if end else rest


def page_count_invocations(text):
    """(document path, page count) for every runnable verify_pdf --pages line."""
    return re.findall(
        r"^python3 tools/verify_pdf\.py (\S+) --pages (\d+)\s*$", text, re.MULTILINE
    )


class ApplyRunsThePageCountCheck(unittest.TestCase):
    def setUp(self):
        self.step_5b = section(APPLY, "### 5b. Inspect layout")

    def test_step_5b_checks_both_documents_with_the_guides_page_limits(self):
        invocations = dict(page_count_invocations(self.step_5b))
        self.assertEqual(
            invocations.get("cv/main_<company>_<role>.pdf"),
            "2",
            "Step 5b must run verify_pdf.py --pages 2 on the CV - the hard "
            "2-page limit has no other mechanical check",
        )
        self.assertEqual(
            invocations.get("cover_letters/cover_<company>_<role>.pdf"),
            "1",
            "Step 5b must run verify_pdf.py --pages 1 on the cover letter",
        )

    def test_page_count_runs_before_the_layout_measurement(self):
        # verify_layout.py's own docstring declines to check page count because
        # verify_pdf.py --pages does; the deferral only holds if that runs first.
        first_pages = self.step_5b.index("--pages")
        first_layout = self.step_5b.index("verify_layout.py")
        self.assertLess(first_pages, first_layout)

    def test_no_step_defers_the_check_to_a_step_that_does_not_run_it(self):
        text = APPLY.read_text(encoding="utf-8")
        self.assertNotIn(
            "Step 5d already runs it",
            text,
            "Step 5d's only verify_pdf call is --dump-text; the page-count "
            "invocation lives in 5b and the prose must point there",
        )

    def test_layout_tools_deferral_is_backed_by_a_runnable_invocation(self):
        docstring = VERIFY_LAYOUT.read_text(encoding="utf-8")
        self.assertIn("verify_pdf.py --pages", docstring)
        self.assertGreaterEqual(
            len(page_count_invocations(APPLY.read_text(encoding="utf-8"))),
            2,
            "verify_layout.py defers page count to verify_pdf.py --pages, so "
            "/apply must actually invoke it",
        )


if __name__ == "__main__":
    unittest.main()

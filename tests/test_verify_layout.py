"""Offline tests for tools/verify_layout.py.

Every case is built from synthetic Page/Line geometry rather than a compiled
PDF, so the suite needs neither Poppler nor a LaTeX toolchain - matching the
repo's CI policy of keeping the Python tool tests self-contained.

The cases marked SILENT FAILURE are the ones that motivated the tool: each
describes a document that compiles cleanly, reports the expected page count,
and passes tools/verify_pdf.py, while the rendered page is visibly broken.
"""

import io
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tools import verify_layout
from tools.verify_layout import Line, Page, find_orphans, main, parse_pdf, report, text_start

A4_HEIGHT = 842.0


def line(top: float, left: float = 50.0, height: float = 10.0, text: str = "x") -> Line:
    return Line(top=top, bottom=top + height, left=left, height=height, text=text)


class TestGapAndBottomSpace(unittest.TestCase):
    def setUp(self):
        # Three body lines, then a 322pt jump, then the page-number footer.
        self.holed = Page(
            A4_HEIGHT,
            [line(50), line(64), line(78), line(400), line(770, text="1/2")],
        )

    def test_largest_gap_reports_size_and_position(self):
        """SILENT FAILURE: the hole an ejected \\cventry leaves behind."""
        gap, y = self.holed.largest_gap()
        self.assertEqual((round(gap), round(y)), (322, 78))

    def test_bottom_space_ignores_the_footer_band(self):
        """Measured to the last body line (y410), not to the page number at y770."""
        self.assertEqual(round(self.holed.bottom_space), 432)

    def test_page_with_no_body_lines_is_empty(self):
        self.assertTrue(Page(A4_HEIGHT, []).empty)

    def test_report_flags_the_hole(self):
        with redirect_stdout(io.StringIO()):  # report() prints its per-page measurements
            problems = report(Path("synthetic"), [self.holed])
        self.assertTrue(any("hole" in m for m in problems), problems)


class TestFooterBand(unittest.TestCase):
    def test_single_line_in_band_is_just_the_page_number(self):
        self.assertFalse(Page(A4_HEIGHT, [line(50), line(800, text="2/2")]).footer_crowded)

    def test_two_lines_in_band_means_body_text_spilled_in(self):
        """SILENT FAILURE: \\enlargethispage pushing body text over the footer."""
        self.assertTrue(Page(A4_HEIGHT, [line(50), line(780), line(800)]).footer_crowded)


class TestHeadingAndIndentDetection(unittest.TestCase):
    def setUp(self):
        self.page = Page(
            A4_HEIGHT,
            [
                line(50, height=16.0, text="Professional Experience"),
                line(80, left=50.0),
                line(94, left=70.0),
            ],
        )

    def test_taller_line_is_a_heading(self):
        self.assertTrue(self.page.is_heading(self.page.body[0]))
        self.assertFalse(self.page.is_heading(self.page.body[1]))

    def test_left_edge_separates_bullets_from_headers(self):
        self.assertTrue(self.page.is_indented(self.page.body[2]))
        self.assertFalse(self.page.is_indented(self.page.body[1]))


class TestOrphans(unittest.TestCase):
    def test_page_ending_on_a_section_heading(self):
        """SILENT FAILURE: a heading stranded at the bottom, content overleaf."""
        p1 = Page(A4_HEIGHT, [line(50), line(64), line(700, height=16.0, text="Education")])
        p2 = Page(A4_HEIGHT, [line(60, text="Example University"), line(74, left=70.0)])
        self.assertTrue(any("ends on the section heading" in m for m in find_orphans([p1, p2])))

    def test_entry_header_orphaned_from_its_bullets(self):
        """SILENT FAILURE: the \\cventry title on one page, its bullets on the next."""
        q1 = Page(A4_HEIGHT, [line(50), line(700, left=50.0, text="Software Engineer")])
        q2 = Page(A4_HEIGHT, [line(60, left=70.0, text="- built the thing")])
        self.assertTrue(any("orphaned from its bullets" in m for m in find_orphans([q1, q2])))

    def test_lone_list_marker_is_a_split_bullet_not_an_orphaned_header(self):
        """moderncv gives the itemize marker its own bbox line: different defect, different fix."""
        m1 = Page(A4_HEIGHT, [line(50), line(700, left=50.0, text="●")])
        m2 = Page(A4_HEIGHT, [line(60, left=70.0, text="continued item text here")])
        self.assertTrue(any("lone list marker" in m for m in find_orphans([m1, m2])))

    def test_clean_break_reports_nothing(self):
        r1 = Page(A4_HEIGHT, [line(50), line(700, left=50.0)])
        r2 = Page(A4_HEIGHT, [line(60, left=50.0)])
        self.assertEqual(find_orphans([r1, r2]), [])

    def test_indent_is_judged_against_the_document_margin(self):
        """A page that OPENS with bullets must not mistake their indent for its margin."""
        s1 = Page(A4_HEIGHT, [line(50, left=50.0), line(700, left=50.0, text="Data Analyst")])
        s2 = Page(A4_HEIGHT, [line(60, left=70.0, text="- first bullet")])
        self.assertTrue(any("orphaned from its bullets" in m for m in find_orphans([s1, s2])))


def at(top, left, text, text_left=None, bottom=None):
    """A bbox line; text_left is where its text starts after a merged list marker."""
    bottom = top + 14.0 if bottom is None else bottom
    return Line(top=top, bottom=bottom, left=left, height=bottom - top, text=text,
                text_left=text_left)


class TestOrphansReadTextX(unittest.TestCase):
    """The orphan rule compares where the TEXT starts, not where the line starts.

    Geometry from the #481 repro: entry headers at x59.5; a bullet's marker merged into
    its first line at x58.9, its text at x71.6-72.1, continuations at x72.1. From the
    stock CV: header text at x70.4, an inner bullet's marker in its own line at x71.5
    with the text at x81.3, and an outer marker at x59.0 that extracts either as a line
    of its own or merged into the header line, depending on the font setup.
    """

    def test_bullet_wrapping_across_the_break_is_not_an_orphaned_header(self):
        p1 = Page(A4_HEIGHT, [
            at(50, 59.5, "Plant Grower Example Garden 1981-1990"),
            at(700, 58.9, "\u2022 Replanted the long border with plants raised", 72.1),
        ])
        p2 = Page(A4_HEIGHT, [at(60, 72.1, "from seed, lifting the old clumps")])
        self.assertEqual(find_orphans([p1, p2]), [])

    def test_bullet_whose_text_opens_with_a_symbol_is_still_a_wrapped_bullet(self):
        p1 = Page(A4_HEIGHT, [
            at(50, 59.5, "Plant Grower Example Garden 1981-1990"),
            at(700, 58.9, "\u2022 (in winter) relaid the gravel paths along", 72.1),
        ])
        p2 = Page(A4_HEIGHT, [at(60, 72.1, "the old orchard wall and round the pond")])
        self.assertEqual(find_orphans([p1, p2]), [])

    def test_continuation_opening_with_a_dash_is_still_a_wrapped_bullet(self):
        """Dashes are not markers: an em dash can open a bullet's second line."""
        p1 = Page(A4_HEIGHT, [at(700, 58.9, "\u2022 Laid a new cobbled path, so", 72.1)])
        p2 = Page(A4_HEIGHT, [at(60, 72.1, "\u2014 that rain ran off into the borders")])
        self.assertEqual(find_orphans([p1, p2]), [])

    def test_header_level_with_merged_bullets_is_an_orphan(self):
        """The bullet line starts at x58.9, left of the header; its text is further in."""
        p1 = Page(A4_HEIGHT, [at(700, 59.5, "Kitchen Gardener 1975-1980")])
        p2 = Page(A4_HEIGHT, [at(60, 58.9, "\u2022 Worked in the kitchen garden", 71.6)])
        self.assertTrue(any("orphaned from its bullets" in m for m in find_orphans([p1, p2])))

    def test_stock_header_whose_outer_marker_is_its_own_line(self):
        """The header's text at x70.4 counts as indented, so the left-edge rule missed it."""
        p1 = Page(A4_HEIGHT, [
            at(323, 59.5, "Professional Experience"),
            at(695.9, 59.0, "\u25cb", bottom=707.6),
            at(700.5, 70.4, "[Job Title] [YYYY-YYYY]"),
        ])
        p2 = Page(A4_HEIGHT, [
            at(62.4, 71.5, "-", bottom=79.5),
            at(64.4, 81.3, "[Achievement or responsibility 1]"),
        ])
        self.assertTrue(any("orphaned from its bullets" in m for m in find_orphans([p1, p2])))

    def test_stock_header_with_its_outer_marker_merged_in(self):
        """`○` is not a LIST_MARKERS glyph, so the header keeps x59.0, as the left-edge rule read it."""
        p1 = Page(A4_HEIGHT, [at(700, 59.0, "\u25cb [Job Title] [YYYY-YYYY]")])
        p2 = Page(A4_HEIGHT, [at(62.4, 71.5, "-", bottom=79.5), at(64.4, 81.3, "[Achievement 1]")])
        self.assertTrue(any("orphaned from its bullets" in m for m in find_orphans([p1, p2])))

    def test_stock_degree_and_description_stay_reported_with_the_marker_merged(self):
        """Both texts at x70.4, but the merged `○` keeps the header at x59.0."""
        p1 = Page(A4_HEIGHT, [at(700, 59.0, "\u25cb [Degree] in [Field] [YYYY-YYYY]")])
        p2 = Page(A4_HEIGHT, [at(60, 70.4, "[Brief description or key topics.]")])
        self.assertTrue(any("orphaned from its bullets" in m for m in find_orphans([p1, p2])))

    def test_outer_marker_set_below_a_whole_item_is_not_a_split_bullet(self):
        """Stock CV: the outer marker sits 1.3pt below its one-line item and sorts last.

        The left-edge rule read it as a lone marker whose item was split, although the
        whole item is on p1 and p2 opens with the next item.
        """
        p1 = Page(A4_HEIGHT, [
            at(50, 59.5, "Core Competencies", bottom=70.5),
            at(728.81, 70.4, "[Skill Category 4]: [Domain expertise, methods.]", bottom=743.4),
            at(730.09, 59.0, "\U0001f7e4", bottom=741.8),
        ])
        p2 = Page(A4_HEIGHT, [
            at(66.8, 70.4, "[Skill Category 5]: [Tools, platforms, software.]", bottom=81.4),
            at(68.1, 59.0, "\U0001f7e4", bottom=79.8),
        ])
        self.assertEqual(find_orphans([p1, p2]), [])

    def test_two_line_item_wrapping_across_the_break_is_read_like_a_wrapped_bullet(self):
        """A behaviour change, not a fix: master called this a split bullet, and it is split.

        The item's first line is on p1 with its marker 1.3pt below it, and its second line
        opens p2 at the item's text x - the #481 wrapped bullet, which is expected clean.
        """
        p1 = Page(A4_HEIGHT, [
            at(696.4, 59.5, "Honors and Awards", bottom=716.9),
            at(722.13, 70.4, "[Award Name] for the long-running community orchard", bottom=736.7),
            at(723.41, 59.0, "\U0001f7e4", bottom=735.09),
        ])
        p2 = Page(A4_HEIGHT, [at(66.8, 69.4, "\u2013 [Event/Organization] ([Year]).")])
        self.assertEqual(find_orphans([p1, p2]), [])

    def test_a_printed_line_split_by_font_is_read_as_one(self):
        """pdftotext's rounded-yMin buckets split a printed line when a bold word sits 0.2pt off."""
        p1 = Page(A4_HEIGHT, [
            at(721.44, 125.6, "operations for the data team and later modules.]"),
            at(721.64, 70.4, "Kubernetes Terraform"),
        ])
        p2 = Page(A4_HEIGHT, [at(66.8, 70.4, "[Skill Category 3]: [Specific skills]")])
        self.assertEqual(find_orphans([p1, p2]), [])

    def test_stock_bullet_wrapping_across_the_break(self):
        p1 = Page(A4_HEIGHT, [at(700, 81.3, "[Achievement or responsibility 1 - be")])
        p2 = Page(A4_HEIGHT, [at(60, 81.3, "specific, use numbers where possible]")])
        self.assertEqual(find_orphans([p1, p2]), [])

    def test_a_running_header_on_the_next_page_is_not_where_the_body_resumes(self):
        """A right-aligned header at x459.8 is not text 'set further in' than a bullet."""
        p1 = Page(A4_HEIGHT, [
            at(50, 59.5, "Professional Experience"),
            at(700, 81.3, "[Achievement or responsibility 2]"),
        ])
        p2 = Page(A4_HEIGHT, [
            at(27, 459.8, "[First] [Last] \u2013 CV"),
            at(62, 70.4, "[Company] [City, Country]"),
        ])
        self.assertEqual(find_orphans([p1, p2]), [])

    def test_an_orphan_behind_a_running_header_is_still_found(self):
        p1 = Page(A4_HEIGHT, [at(50, 59.5, "Professional Experience"), at(700, 70.4, "[Job Title]")])
        p2 = Page(A4_HEIGHT, [
            at(27, 459.8, "[First] [Last] \u2013 CV"),
            at(62.4, 71.5, "-", bottom=79.5),
            at(64.4, 81.3, "[Achievement or responsibility 1]"),
        ])
        self.assertTrue(any("orphaned from its bullets" in m for m in find_orphans([p1, p2])))


class TestTextStart(unittest.TestCase):
    """parse_pdf's text_left, from (xMin, yMin, yMax, text) words sorted left to right."""

    def test_a_leading_list_marker_is_set_aside(self):
        self.assertEqual(text_start([(58.9, 0, 1, "\u2022"), (72.1, 0, 1, "Laid")]), 72.1)

    def test_no_marker_means_no_separate_text_start(self):
        self.assertIsNone(text_start([(59.5, 0, 1, "Kitchen"), (94.9, 0, 1, "Gardener")]))

    def test_ascii_dashes_and_other_glyphs_are_text(self):
        for glyph in ("-", "*", "\u2013", "\u2014", "\u25cb"):
            with self.subTest(glyph=glyph):
                self.assertIsNone(text_start([(69.4, 0, 1, glyph), (77.7, 0, 1, "[Event]")]))

    def test_a_bare_marker_has_no_text(self):
        self.assertIsNone(text_start([(59.0, 0, 1, "\u2022")]))

class TestExtractorFailure(unittest.TestCase):
    """A broken extractor must not masquerade as a broken document.

    Git for Windows ships an xpdf-based pdftotext with no -bbox flag; it shadows
    Poppler in a default PATH and exits 99. Reported as a layout problem it would
    send /apply chasing a phantom hole, so it has to land on the skip path.
    """

    def setUp(self):
        super().setUp()
        verify_layout.find_pdftotext.cache_clear()

    def tearDown(self):
        super().tearDown()
        verify_layout.find_pdftotext.cache_clear()

    def test_pdftotext_without_bbox_raises_a_skippable_error(self):
        failure = subprocess.CalledProcessError(99, "pdftotext", stderr="Error: unknown flag")
        with patch("tools.verify_layout.shutil.which", return_value="/usr/bin/pdftotext"), patch(
            "tools.verify_layout.subprocess.run", side_effect=failure
        ):
            with self.assertRaisesRegex(RuntimeError, "bounding boxes"):
                parse_pdf(Path("cv/main_example.pdf"))

    def test_first_candidate_lacks_bbox_and_second_has_it(self):
        """When the first pdftotext candidate lacks -bbox (e.g. Git for Windows's xpdf)
        and a subsequent candidate has it (e.g. Poppler), the tool walks PATH, probes
        with -h, and runs the working candidate with its full path (#655).
        """
        def fake_which(cmd, path=None):
            if path == "/git/bin":
                return "/git/bin/pdftotext"
            if path == "/miktex/bin":
                return "/miktex/bin/pdftotext"
            return None

        def fake_run(cmd, *args, **kwargs):
            binary = cmd[0]
            if "-h" in cmd:
                if binary == "/git/bin/pdftotext":
                    return subprocess.CompletedProcess(cmd, 99, stdout="", stderr="Usage: pdftotext ...")
                if binary == "/miktex/bin/pdftotext":
                    return subprocess.CompletedProcess(
                        cmd, 99, stdout="", stderr="Usage: pdftotext ...\n  -bbox : output bounding box"
                    )
            if "-bbox" in cmd:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout='<page width="100" height="100"></page>', stderr=""
                )
            raise AssertionError(f"unexpected command: {cmd}")

        with patch("tools.verify_layout.os.get_exec_path", return_value=["/git/bin", "/miktex/bin"]), patch(
            "tools.verify_layout.shutil.which", side_effect=fake_which
        ), patch("tools.verify_layout.subprocess.run", side_effect=fake_run) as mock_run:
            pages = parse_pdf(Path("cv/main_example.pdf"))
            self.assertEqual(len(pages), 1)
            bbox_calls = [call for call in mock_run.call_args_list if "-bbox" in call[0][0]]
            self.assertEqual(len(bbox_calls), 1)
            self.assertEqual(bbox_calls[0][0][0][0], "/miktex/bin/pdftotext")

    def test_no_candidate_has_bbox_keeps_skip_path(self):
        """When no candidate on PATH supports -bbox, the check skips with today's message (#655)."""
        def fake_which(cmd, path=None):
            if path == "/git/bin":
                return "/git/bin/pdftotext"
            if path == "/other/bin":
                return "/other/bin/pdftotext"
            return None

        failure = subprocess.CalledProcessError(99, "/git/bin/pdftotext", stderr="Error: unknown flag")

        def fake_run(cmd, *args, **kwargs):
            if "-h" in cmd:
                return subprocess.CompletedProcess(cmd, 99, stdout="", stderr="Usage: pdftotext (xpdf, no bbox)")
            if "-bbox" in cmd:
                raise failure
            raise AssertionError(f"unexpected command: {cmd}")

        with patch("tools.verify_layout.os.get_exec_path", return_value=["/git/bin", "/other/bin"]), patch(
            "tools.verify_layout.shutil.which", side_effect=fake_which
        ), patch("tools.verify_layout.subprocess.run", side_effect=fake_run):
            with self.assertRaisesRegex(RuntimeError, "bounding boxes") as ctx:
                parse_pdf(Path("cv/main_example.pdf"))
            message = str(ctx.exception)
            self.assertIn("xpdf", message)
            self.assertIn("Poppler aborted", message)

    def test_find_pdftotext_deduplicates_path_candidates(self):
        """Duplicate entries or multiple PATH directories resolving to the same binary
        are probed only once (#655).
        """
        def fake_which(cmd, path=None):
            return "/same/pdftotext"

        h_calls = []

        def fake_run(cmd, *args, **kwargs):
            if "-h" in cmd:
                h_calls.append(cmd[0])
                return subprocess.CompletedProcess(
                    cmd, 99, stdout="", stderr="Usage: pdftotext ...\n  -bbox : output bounding box"
                )
            raise AssertionError(f"unexpected command: {cmd}")

        with patch("tools.verify_layout.os.get_exec_path", return_value=["/bin1", "/bin2", "/bin3"]), patch(
            "tools.verify_layout.shutil.which", side_effect=fake_which
        ), patch("tools.verify_layout.subprocess.run", side_effect=fake_run):
            selected = verify_layout.find_pdftotext()
            self.assertEqual(selected, "/same/pdftotext")
            self.assertEqual(h_calls, ["/same/pdftotext"])

    def test_find_pdftotext_caches_resolution(self):
        """Resolved binary is cached at module level so repeated parse_pdf calls
        avoid re-probing PATH candidates with subprocesses (#655).
        """
        def fake_which(cmd, path=None):
            return "/miktex/bin/pdftotext"

        h_calls = []
        bbox_calls = []

        def fake_run(cmd, *args, **kwargs):
            binary = cmd[0]
            if "-h" in cmd:
                h_calls.append(binary)
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="Usage: pdftotext ...\n  -bbox : output bounding box", stderr=""
                )
            if "-bbox" in cmd:
                bbox_calls.append(binary)
                return subprocess.CompletedProcess(
                    cmd, 0, stdout='<page width="100" height="100"></page>', stderr=""
                )
            raise AssertionError(f"unexpected command: {cmd}")

        with patch("tools.verify_layout.os.get_exec_path", return_value=["/miktex/bin"]), patch(
            "tools.verify_layout.shutil.which", side_effect=fake_which
        ), patch("tools.verify_layout.subprocess.run", side_effect=fake_run):
            pages1 = parse_pdf(Path("cv/main_example.pdf"))
            pages2 = parse_pdf(Path("cv/main_example.pdf"))
            self.assertEqual(len(pages1), 1)
            self.assertEqual(len(pages2), 1)
            self.assertEqual(h_calls, ["/miktex/bin/pdftotext"])
            self.assertEqual(bbox_calls, ["/miktex/bin/pdftotext", "/miktex/bin/pdftotext"])

    def test_poppler_abort_on_empty_info_string_names_that_cause_too(self):
        """Poppler 26.0x before 26.05 aborts -bbox on an empty Info-dict string, e.g.
        the empty /Title hyperref writes when pdftitle is unset (#451). That crash is
        not the xpdf-shadowing case - it has no -bbox flag and exits 99 - so the
        message must name both, not just the one the exit code happens to match.
        """
        failure = subprocess.CalledProcessError(
            1,
            "pdftotext",
            stderr="libc++abi: terminating due to uncaught exception of type "
            "std::out_of_range: basic_string",
        )
        with patch("tools.verify_layout.shutil.which", return_value="/usr/bin/pdftotext"), patch(
            "tools.verify_layout.subprocess.run", side_effect=failure
        ):
            with self.assertRaisesRegex(RuntimeError, "bounding boxes") as ctx:
                parse_pdf(Path("cv/main_example.pdf"))
        message = str(ctx.exception)
        self.assertIn("xpdf", message)
        self.assertIn("Poppler aborted", message)
        self.assertIn("hyperref", message)

    def test_missing_poppler_raises_a_skippable_error(self):
        with patch("tools.verify_layout.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "not found"):
                parse_pdf(Path("cv/main_example.pdf"))

    def test_extractor_failure_exits_2_not_1(self):
        """Exit 1 means "your document is broken"; a dead extractor must never claim that."""
        with patch("tools.verify_layout.parse_pdf", side_effect=RuntimeError("no -bbox")), patch(
            "sys.argv", ["verify_layout.py", __file__]
        ):
            err = io.StringIO()
            with redirect_stdout(io.StringIO()), patch("sys.stderr", err):
                self.assertEqual(main(), 2)
            self.assertIn("skipped:", err.getvalue())


class ToolsCompileWithoutWarnings(unittest.TestCase):
    """Every tool's source must compile clean.

    `verify_layout.py`'s docstring documents LaTeX macros, and a bare `\\h` in a
    non-raw docstring is an invalid escape sequence: Python 3.12+ emits a
    SyntaxWarning when the module is compiled (CPython gh-98401), 3.10 and 3.11
    a DeprecationWarning, so the test records both; the language
    reference still documents it as a warning in 3.15, with a SyntaxError only
    in a future Python version. The tool is run per-document from `/apply`, so
    the warning lands in the middle of a verification report. Compiling is a
    pure `compile()` over the source text - no cache file, no temp file - so the
    check costs nothing and covers every `tools/*.py`, guarding the next
    docstring that quotes a macro too. It covers `tests/*.py` as well: a test
    docstring that quotes a regex (`\\s*` in `test_verify_pdf.py`) warned in CI
    on every matrix Python while the run stayed green.
    """

    def test_sources_have_no_invalid_escape_sequences(self):
        import warnings

        tools_dir = Path(verify_layout.__file__).resolve().parent
        tests_dir = Path(__file__).resolve().parent
        sources = sorted(tools_dir.glob("*.py")) + sorted(tests_dir.glob("*.py"))
        self.assertIn(Path(verify_layout.__file__).resolve(), sources)
        self.assertIn(Path(__file__).resolve(), sources)
        offenders: dict[str, list[str]] = {}
        for source in sources:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                compile(source.read_text(encoding="utf-8"), str(source), "exec")
            syntax = [
                str(w.message)
                for w in caught
                if issubclass(w.category, (SyntaxWarning, DeprecationWarning))
            ]
            if syntax:
                offenders[f"{source.parent.name}/{source.name}"] = syntax
        self.assertEqual({}, offenders, "tools/*.py or tests/*.py warn about invalid escapes when compiled")


if __name__ == "__main__":
    unittest.main()

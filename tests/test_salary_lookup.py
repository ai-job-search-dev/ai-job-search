"""Tests for salary_lookup.py — format_entry, match_score, and search_company."""

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import salary_lookup
from salary_lookup import (
    format_entry,
    normalize,
    anglicize,
    extract_core_words,
    match_score,
    search_company,
    validate_data,
    collect_validation_issues,
)


# ---------------------------------------------------------------------------
# format_entry tests (from #75 / #98)
# ---------------------------------------------------------------------------

class FormatEntryTests(unittest.TestCase):
    PRIVACY_FOOTNOTE = "* N/A = Too few employees to publish (privacy)"

    def test_privacy_footnote_is_omitted_when_no_row_is_suppressed(self):
        # The footnote explains the N/A* marker. Printed under a table where
        # every row has an index, it asserts a privacy suppression that never
        # happened (residual noted on #470).
        entry = {
            "company": "Example Corp",
            "city": "",
            "categories": {"all_employees": {"count": 500, "index": 108.5}},
        }

        rendered = format_entry(entry, {"index_baseline": 100, "index_label": "Index"})

        self.assertNotIn("N/A", rendered)
        self.assertNotIn(self.PRIVACY_FOOTNOTE, rendered)
        self.assertRegex(rendered, r"All Employees\s+500\s+108\.5\s+\+8\.5%")

    def test_privacy_footnote_is_printed_when_a_row_is_suppressed(self):
        entry = {
            "company": "Example Corp",
            "city": "",
            "categories": {
                "all_employees": {"count": 500, "index": 108.5},
                "small_team": {"count": 3, "index": None},
            },
        }

        rendered = format_entry(entry, {"index_baseline": 100, "index_label": "Index"})

        self.assertRegex(rendered, r"Small Team\s+3\s+N/A\*")
        self.assertIn(self.PRIVACY_FOOTNOTE, rendered)

    def test_zero_count_is_displayed_as_zero(self):
        entry = {
            "company": "Example Corp",
            "city": "",
            "categories": {
                "public_data": {
                    "count": 0,
                    "index": 100.0,
                },
            },
        }

        rendered = format_entry(entry, {"index_baseline": 100, "index_label": "Index"})

        self.assertRegex(rendered, r"Public Data\s+0\s+100\.0")

    def test_text_index_does_not_crash(self):
        entry = {
            "company": "Example Corp",
            "city": "",
            "categories": {
                "sample": {
                    "count": 3,
                    "index": "private",
                },
            },
        }

        rendered = format_entry(entry, {"index_baseline": 100, "index_label": "Index"})

        self.assertIn("private", rendered)

    def test_format_entry_with_zero_baseline(self):
        entry = {
            "company": "Example Corp",
            "city": "",
            "categories": {
                "it": {
                    "count": None,
                    "index": 45000.0,
                },
            },
        }
        rendered = format_entry(entry, {"index_baseline": 0, "index_label": "Salary"})
        self.assertIn("45000.0", rendered)
        self.assertNotIn("%", rendered)

    def test_format_entry_with_custom_baseline(self):
        entry = {
            "company": "Example Corp",
            "city": "",
            "categories": {
                "it": {
                    "count": None,
                    "index": 45000.0,
                },
            },
        }
        rendered = format_entry(entry, {"index_baseline": 40000, "index_label": "Salary"})
        self.assertIn("45000.0", rendered)
        self.assertIn("+12.5%", rendered)

    def test_null_categories_with_sibling_dict_does_not_crash(self):
        # --validate accepts "categories": null, so format_entry must not crash
        # on it. entry.get("categories", {}) returns None (not {}) for an
        # explicit null, and the numeric-field fallback then did None[key] = ....
        entry = {
            "company": "Example Corp",
            "city": "",
            "categories": None,
            "engineering": {"count": 10, "index": 105.0},
        }

        rendered = format_entry(entry, {"index_baseline": 100, "index_label": "Index"})

        self.assertRegex(rendered, r"Engineering\s+10\s+105\.0")

    def test_null_metadata_does_not_crash(self):
        # --validate accepts "metadata": null the same way; format_entry then did
        # None.get("index_label", ...) -> AttributeError.
        entry = {
            "company": "Example Corp",
            "city": "",
            "categories": {"eng": {"count": 5, "index": 108.0}},
        }

        rendered = format_entry(entry, None)

        self.assertRegex(rendered, r"Eng\s+5\s+108\.0")


# ---------------------------------------------------------------------------
# match_score tests (from #106)
# ---------------------------------------------------------------------------

class TestMatchScoreExactMatch(unittest.TestCase):
    def test_exact_match_returns_100(self):
        self.assertEqual(match_score("Novo Nordisk", "Novo Nordisk"), 100)

    def test_exact_match_case_insensitive(self):
        self.assertEqual(match_score("NOVO NORDISK", "Novo Nordisk"), 100)

    def test_exact_match_after_suffix_stripping(self):
        self.assertEqual(match_score("Mærsk", "Mærsk A/S"), 100)

    def test_exact_match_after_dotted_amba_suffix_stripping(self):
        # "A.M.B.A." (dotted) is the same legal-suffix family as the
        # undotted "amba" pattern above it in STRIP_PATTERNS and must
        # strip just as cleanly.
        self.assertEqual(match_score("Arla Foods", "Arla Foods A.M.B.A."), 100)


class TestMatchScoreSubstring(unittest.TestCase):
    def test_query_contained_in_entry_gives_high_score(self):
        score = match_score("Carlsberg", "Carlsberg Danmark A/S")
        self.assertGreaterEqual(score, 80)

    def test_entry_contained_in_query_gives_high_score(self):
        score = match_score("Carlsberg Danmark", "Carlsberg")
        self.assertGreaterEqual(score, 80)


class TestMatchScoreShortQuery(unittest.TestCase):
    def test_short_query_no_word_overlap_returns_zero(self):
        score = match_score("ab", "Something Unrelated Company")
        self.assertEqual(score, 0)

    def test_short_query_with_word_overlap_scores(self):
        score = match_score("IBM", "IBM Corporation")
        self.assertGreater(score, 0)


class TestMatchScoreAnglicize(unittest.TestCase):
    def test_oe_variant_matches_o_with_slash(self):
        score = match_score("Maersk", "Mærsk A/S")
        self.assertGreater(score, 0)

    def test_aa_variant_matches_aa(self):
        self.assertEqual(match_score("Aarsleff", "Aarsleff"), 100)

    def test_danish_characters_roundtrip(self):
        score = match_score("Maersk", "Mærsk A/S")
        self.assertGreater(score, 0)


class TestMatchScoreNoOverlap(unittest.TestCase):
    def test_completely_unrelated_names_return_zero(self):
        self.assertEqual(match_score("Apple", "Vestas Wind Systems"), 0)

    def test_empty_query_returns_zero(self):
        self.assertEqual(match_score("", "Novo Nordisk"), 0)

    def test_empty_entry_returns_zero(self):
        self.assertEqual(match_score("Novo Nordisk", ""), 0)


# ---------------------------------------------------------------------------
# search_company tests (from #75 / #98 and #106)
# ---------------------------------------------------------------------------

def _make_data(*entries):
    return {"companies": list(entries)}


def _entry(company, city=""):
    return {"company": company, "city": city}


class SearchCompanyTests(unittest.TestCase):
    def test_search_company_with_none_city(self):
        data = {
            "companies": [
                {
                    "company": "Acme",
                    "city": None,
                }
            ]
        }
        results = search_company(data, "Acme", city="Aarhus")
        self.assertEqual(results, [])


class ValidateDataTests(unittest.TestCase):
    def assert_invalid_data(self, data, expected_message):
        stderr = io.StringIO()
        with self.assertRaises(SystemExit) as raised:
            with redirect_stderr(stderr):
                validate_data(data)

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("Error: invalid salary_data.json", stderr.getvalue())
        self.assertIn(expected_message, stderr.getvalue())
        self.assertIn("tools/README_SALARY_TOOL.md", stderr.getvalue())

    def test_valid_minimal_data_is_returned(self):
        data = {"metadata": {}, "companies": [{"company": "Example Corp"}]}

        self.assertIs(validate_data(data), data)

    def test_top_level_value_must_be_object(self):
        self.assert_invalid_data([], "top-level JSON value must be an object")

    def test_companies_must_be_list(self):
        self.assert_invalid_data({"companies": {"company": "Example Corp"}}, "'companies' must be a list")

    def test_metadata_must_be_object_when_provided(self):
        self.assert_invalid_data(
            {"metadata": [], "companies": [{"company": "Example Corp"}]},
            "'metadata' must be an object when provided",
        )

    def test_load_data_reports_json_parse_errors_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_file = Path(tmpdir) / "salary_data.json"
            data_file.write_text('{"companies": [', encoding="utf-8")

            original_data_file = salary_lookup.DATA_FILE
            salary_lookup.DATA_FILE = data_file
            try:
                stderr = io.StringIO()
                with self.assertRaises(SystemExit) as raised:
                    with redirect_stderr(stderr):
                        salary_lookup.load_data()
            finally:
                salary_lookup.DATA_FILE = original_data_file

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("invalid JSON at line", stderr.getvalue())
        self.assertIn("tools/README_SALARY_TOOL.md", stderr.getvalue())

    def test_company_entry_must_be_object(self):
        self.assert_invalid_data({"companies": ["Example Corp"]}, "companies[1] must be an object")

    def test_company_name_is_required(self):
        self.assert_invalid_data({"companies": [{"city": "Aarhus"}]}, "companies[1].company must be a non-empty string")

    def test_company_name_must_not_be_blank(self):
        self.assert_invalid_data({"companies": [{"company": "  "}]}, "companies[1].company must be a non-empty string")

    def test_city_must_be_string_when_provided(self):
        self.assert_invalid_data(
            {"companies": [{"company": "Example Corp", "city": 123}]},
            "companies[1].city must be a string when provided",
        )

    def test_categories_must_be_object_when_provided(self):
        self.assert_invalid_data(
            {"companies": [{"company": "Example Corp", "categories": []}]},
            "companies[1].categories must be an object when provided",
        )


class ValidateDataShapeTests(ValidateDataTests):
    """Category-shape and duplicate-name checks (reuses assert_invalid_data)."""

    def test_malformed_category_value_rejected(self):
        data = {"companies": [{"company": "Acme", "categories": {"eng": "not_a_dict"}}]}
        self.assert_invalid_data(data, "must be an object with 'count' and/or 'index'")

    def test_non_numeric_count_rejected(self):
        data = {
            "companies": [
                {"company": "Acme", "categories": {"eng": {"count": "many"}}}
            ]
        }
        self.assert_invalid_data(data, "count must be a number")

    def test_duplicate_company_name_is_warning(self):
        data = {
            "companies": [
                {"company": "Acme"},
                {"company": "Other Corp"},
                {"company": "Acme"},
            ]
        }
        errors, warnings = collect_validation_issues(data)
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("Duplicate company name 'Acme'", warnings[0])

    def test_valid_categories_have_no_issues(self):
        data = {
            "companies": [
                {"company": "Acme", "categories": {"eng": {"count": 5, "index": 108.5}}}
            ]
        }
        errors, warnings = collect_validation_issues(data)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])


class ValidateFlagTests(unittest.TestCase):
    """End-to-end checks for the --validate pre-flight flow."""

    def _run_validate(self, payload):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_file = Path(tmpdir) / "salary_data.json"
            data_file.write_text(payload, encoding="utf-8")
            original_data_file = salary_lookup.DATA_FILE
            salary_lookup.DATA_FILE = data_file
            argv_patch = mock.patch("sys.argv", ["salary_lookup.py", "--validate"])
            argv_patch.start()
            try:
                stdout = io.StringIO()
                with self.assertRaises(SystemExit) as raised:
                    with redirect_stdout(stdout):
                        salary_lookup.main()
                return raised.exception.code, stdout.getvalue()
            finally:
                argv_patch.stop()
                salary_lookup.DATA_FILE = original_data_file

    def test_validate_flag_exits_1_on_errors(self):
        code, out = self._run_validate(
            '{"companies": [{"company": "Acme", "categories": {"eng": "not_a_dict"}}]}'
        )
        self.assertEqual(code, 1)
        self.assertIn("must be an object with 'count' and/or 'index'", out)

    def test_validate_flag_exits_0_on_clean(self):
        code, out = self._run_validate(
            '{"companies": [{"company": "Acme", "categories": {"eng": {"count": 5}}}]}'
        )
        self.assertEqual(code, 0)
        self.assertIn("OK", out)

    def test_validate_flag_exits_0_on_duplicates_only(self):
        code, out = self._run_validate(
            '{"companies": [{"company": "Acme"}, {"company": "Acme"}]}'
        )
        self.assertEqual(code, 0)
        self.assertIn("Duplicate company name", out)


class NullShapeEndToEndTests(unittest.TestCase):
    """The disagreement in full: --validate blesses a file with a null
    metadata/categories, then the lookup path must render it, not crash.

    Both payloads pass --validate on master; the second command then dies
    (TypeError in the categories fallback, AttributeError on metadata.get).
    """

    def _run_main(self, payload, *argv_tail):
        """Run main() against `payload` with the given argv. Returns
        (exit_code_or_None, stdout). main() returns normally on a successful
        render, so a missing SystemExit is success, not an error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_file = Path(tmpdir) / "salary_data.json"
            data_file.write_text(payload, encoding="utf-8")
            original_data_file = salary_lookup.DATA_FILE
            salary_lookup.DATA_FILE = data_file
            argv_patch = mock.patch("sys.argv", ["salary_lookup.py", *argv_tail])
            argv_patch.start()
            try:
                stdout = io.StringIO()
                try:
                    with redirect_stdout(stdout):
                        salary_lookup.main()
                    return None, stdout.getvalue()
                except SystemExit as exc:
                    return exc.code, stdout.getvalue()
            finally:
                argv_patch.stop()
                salary_lookup.DATA_FILE = original_data_file

    def test_null_categories_passes_validate_then_renders(self):
        payload = (
            '{"metadata": {"index_label": "Index", "index_baseline": 100},'
            ' "companies": [{"company": "Foo A/S", "city": "Aarhus",'
            ' "categories": null,'
            ' "engineering": {"count": 10, "index": 105}}]}'
        )

        code, out = self._run_main(payload, "--validate")
        self.assertEqual(code, 0)
        self.assertIn("OK", out)

        code, out = self._run_main(payload, "Foo")
        self.assertIsNone(code)
        self.assertIn("Foo A/S", out)
        self.assertRegex(out, r"Engineering\s+10\s+105")

    def test_null_metadata_passes_validate_then_renders(self):
        payload = (
            '{"metadata": null,'
            ' "companies": [{"company": "Foo A/S", "city": "Aarhus",'
            ' "categories": {"engineering": {"count": 10, "index": 105}}}]}'
        )

        code, out = self._run_main(payload, "--validate")
        self.assertEqual(code, 0)
        self.assertIn("OK", out)

        code, out = self._run_main(payload, "Foo")
        self.assertIsNone(code)
        self.assertRegex(out, r"Engineering\s+10\s+105")


class UtilityTests(unittest.TestCase):
    def test_normalize_strips_suffix_and_noise(self):
        self.assertEqual(normalize("Novo Nordisk A/S"), "novonordisk")
        self.assertEqual(normalize("Ørsted (VG) Holding"), "ørsted")
        self.assertEqual(normalize("Chr. Hansen, Denmark Division"), "chrhansen")
        self.assertEqual(normalize("Simple Corp ApS"), "simplecorp")

    def test_normalize_strips_dotted_amba_suffix_same_as_undotted(self):
        # The dotted form ("A.M.B.A.") must normalize identically to the
        # undotted form ("amba"), same as A/S vs ApS variants above.
        self.assertEqual(
            normalize("Arla Foods A.M.B.A."), normalize("Arla Foods amba")
        )
        self.assertEqual(normalize("Arla Foods A.M.B.A."), "arlafoods")

    def test_anglicize_replaces_danish_chars(self):
        self.assertEqual(anglicize("ørsted"), "orsted")
        self.assertEqual(anglicize("mærsk"), "maersk")
        self.assertEqual(anglicize("ålborg"), "aalborg")

    def test_extract_core_words(self):
        self.assertEqual(extract_core_words("Novo Nordisk A/S"), ["novo", "nordisk"])
        self.assertEqual(extract_core_words("A/S"), [])
        self.assertEqual(extract_core_words("Test Company (Sub-entity)"), ["test", "company"])


class MatchScoreTests(unittest.TestCase):
    def test_exact_match_score(self):
        self.assertEqual(match_score("Novo Nordisk", "Novo Nordisk"), 100)
        self.assertEqual(match_score("novo nordisk", "Novo Nordisk A/S"), 100)

    def test_partial_match_score(self):
        self.assertGreater(match_score("Novo", "Novo Nordisk A/S"), 80)
        self.assertEqual(match_score("Novo Nordisk", "Novo"), 75)

    def test_anglicized_match_score(self):
        self.assertEqual(match_score("Orsted", "Ørsted A/S"), 85)

    def test_overlap_match_score(self):
        # Overlap of multiple words
        self.assertGreater(match_score("Novo Tech", "Novo Nordisk Tech A/S"), 30)

    def test_no_match_score(self):
        self.assertEqual(match_score("Google", "Microsoft"), 0)


class SearchCompanyRefactoredTests(unittest.TestCase):
    def setUp(self):
        self.data = {
            "companies": [
                {"company": "Novo Nordisk A/S", "city": "Bagsværd"},
                {"company": "Ørsted", "city": "Fredericia"},
                {"company": "Vestas Wind Systems", "city": "Aarhus"},
            ]
        }

    def test_search_by_name(self):
        results = search_company(self.data, "Novo")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["company"], "Novo Nordisk A/S")

    def test_search_with_city_filter(self):
        results = search_company(self.data, "Ørsted", city="Fredericia")
        self.assertEqual(len(results), 1)

        # Mismatching city
        results_wrong_city = search_company(self.data, "Ørsted", city="Bagsværd")
        self.assertEqual(len(results_wrong_city), 0)


class TestSearchCompanyBasicMatch(unittest.TestCase):
    def test_exact_name_returns_match(self):
        data = _make_data(_entry("Novo Nordisk", "Bagsværd"))
        results = search_company(data, "Novo Nordisk")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["company"], "Novo Nordisk")

    def test_no_match_returns_empty_list(self):
        data = _make_data(_entry("Vestas Wind Systems", "Aarhus"))
        results = search_company(data, "Apple")
        self.assertEqual(results, [])

    def test_multiple_candidates_all_returned(self):
        data = _make_data(
            _entry("Carlsberg A/S", "Copenhagen"),
            _entry("Carlsberg Danmark", "Fredericia"),
            _entry("Unrelated Corp", "Odense"),
        )
        results = search_company(data, "Carlsberg")
        companies = [r["company"] for r in results]
        self.assertIn("Carlsberg A/S", companies)
        self.assertIn("Carlsberg Danmark", companies)
        self.assertNotIn("Unrelated Corp", companies)


class TestSearchCompanyCityFilter(unittest.TestCase):
    def test_matching_city_is_included(self):
        data = _make_data(
            _entry("Novo Nordisk", "Bagsværd"),
            _entry("Novo Nordisk", "Aarhus"),
        )
        results = search_company(data, "Novo Nordisk", city="Aarhus")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["city"], "Aarhus")

    def test_non_matching_city_is_excluded(self):
        data = _make_data(_entry("Novo Nordisk", "Bagsværd"))
        results = search_company(data, "Novo Nordisk", city="Odense")
        self.assertEqual(results, [])

    def test_no_city_filter_returns_all_cities(self):
        data = _make_data(
            _entry("Novo Nordisk", "Bagsværd"),
            _entry("Novo Nordisk", "Aarhus"),
        )
        results = search_company(data, "Novo Nordisk")
        self.assertEqual(len(results), 2)

    def test_city_filter_case_insensitive(self):
        data = _make_data(_entry("Novo Nordisk", "København"))
        results = search_company(data, "Novo Nordisk", city="københavn")
        self.assertEqual(len(results), 1)

    def test_anglicized_city_matches_danish_city(self):
        data = _make_data(_entry("Novo Nordisk", "København"))
        results = search_company(data, "Novo Nordisk", city="kobenhavn")
        self.assertEqual(len(results), 1)


class TestSearchCompanyScoreThreshold(unittest.TestCase):
    def test_low_score_matches_excluded(self):
        data = _make_data(_entry("Novo Nordisk", "Bagsværd"))
        results = search_company(data, "xyz")
        self.assertEqual(results, [])

    def test_results_sorted_by_relevance_descending(self):
        data = _make_data(
            _entry("Novo Nordisk International", "Bagsværd"),
            _entry("Novo Nordisk", "Bagsværd"),
        )
        results = search_company(data, "Novo Nordisk")
        self.assertEqual(results[0]["company"], "Novo Nordisk")


class TestSalaryFileEncoding(unittest.TestCase):
    """Exercise user-created UTF-8 files through the documented CLI."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.script = self.root / "salary_lookup.py"
        shutil.copyfile(salary_lookup.__file__, self.script)
        self.data_file = self.root / "salary_data.json"
        self.entry = {
            "company": "Novo Nordisk A/S",
            "city": "Bagsværd",
            "categories": {"engineering": {"count": 120, "index": 112.3}},
        }

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            cwd=self.root, capture_output=True, text=True, encoding="utf-8",
            timeout=30,
        )

    def test_utf8_with_and_without_bom(self):
        payload = {"metadata": {"index_baseline": 100}, "companies": [self.entry]}
        for encoding in ("utf-8", "utf-8-sig"):
            with self.subTest(encoding=encoding):
                self.data_file.write_text(
                    json.dumps(payload, ensure_ascii=False), encoding=encoding,
                )
                validated = self.run_cli("--validate")
                self.assertEqual(validated.returncode, 0, validated.stderr)
                self.assertIn("OK - no issues found.", validated.stdout)
                lookup = self.run_cli("Novo Nordisk", "--json")
                self.assertEqual(lookup.returncode, 0, lookup.stderr)
                self.assertEqual(json.loads(lookup.stdout), [self.entry])

    def test_bom_does_not_hide_invalid_json(self):
        self.data_file.write_text('{"companies": [}', encoding="utf-8-sig")
        result = self.run_cli("--validate")
        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid JSON at line 1, column 16", result.stderr)
        self.assertNotIn("Unexpected UTF-8 BOM", result.stderr)

    def test_non_utf8_file_has_actionable_cli_error(self):
        payload = {"companies": [self.entry]}
        self.data_file.write_bytes(json.dumps(payload, ensure_ascii=False).encode("cp1252"))
        for args in (("--validate",), ("Novo Nordisk", "--json")):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 1)
                self.assertIn("Error: invalid salary_data.json: expected UTF-8", result.stderr)
                self.assertIn("README_SALARY_TOOL.md", result.stderr)
                self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()

import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace

try:
    import openpyxl
except ImportError:
    openpyxl = None

from salary_lookup import format_entry
from tools.convert_salary_excel import (
    INDEX_PATTERNS,
    detect_column_type,
    COMPANY_PATTERNS,
    header_matches,
    parse_numeric_cell,
    parse_sheet,
)


class FakeWorksheet:
    title = "Sheet1"

    def __init__(self, rows):
        self.rows = rows

    def iter_rows(self, min_row=1, max_row=None, values_only=False):
        rows = self.rows[min_row - 1:max_row]
        for row in rows:
            if values_only:
                yield row
            else:
                yield [SimpleNamespace(value=value) for value in row]

    def __getitem__(self, row_number):
        return [SimpleNamespace(value=value) for value in self.rows[row_number - 1]]


@unittest.skipUnless(openpyxl is not None, "openpyxl is not installed")
class BooleanCellCliTests(unittest.TestCase):
    def test_excel_logical_cells_are_not_salary_numbers(self):
        # Exercise the documented converter CLI with actual Excel boolean cells,
        # including partial pairs and numeric 0/1 controls.
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "salary.xlsx"
            target = Path(tmp) / "salary.json"
            workbook = openpyxl.Workbook()
            sheet = workbook.active
            sheet.append(["Company", "City", "Count", "Index", "Verified"])
            sheet.append(["Logical Pair", "Berlin", True, False, True])
            sheet.append(["Logical Count", "Berlin", False, 108.5, False])
            sheet.append(["Logical Index", "Berlin", 12, True, None])
            sheet.append(["Numeric Values", "Berlin", 0, 1, 0])
            sheet.append(["Numeric Count", "Berlin", 1, 0, 1])
            workbook.save(source)
            workbook.close()

            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve().parents[1] /
                                     "tools/convert_salary_excel.py"),
                 str(source), "--output", str(target)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            companies = json.loads(target.read_text(encoding="utf-8"))["companies"]

        self.assertEqual(companies[0]["categories"], {})
        self.assertEqual(companies[1]["categories"], {
            "all_employees": {"count": None, "index": 108.5},
        })
        self.assertEqual(companies[2]["categories"], {
            "all_employees": {"count": 12, "index": None},
        })
        self.assertEqual(companies[3]["categories"], {
            "all_employees": {"count": 0, "index": 1.0},
            "verified": {"index": 0.0},
        })
        self.assertEqual(companies[4]["categories"], {
            "all_employees": {"count": 1, "index": 0.0},
            "verified": {"index": 1.0},
        })


class DetectColumnTypeTests(unittest.TestCase):
    def test_index_headers_are_not_misclassified_as_count(self):
        for header in ("Index", "Salary Index", "Engineering Index", "Median salary"):
            with self.subTest(header=header):
                self.assertEqual(detect_column_type(header), "index")

    def test_single_letter_n_only_matches_as_a_token(self):
        self.assertEqual(detect_column_type("Employee n"), "count")
        self.assertEqual(detect_column_type("Engineering"), None)

    def test_count_headers_still_match_common_labels(self):
        for header in ("Count", "Engineering Count", "Antal medarbejdere"):
            with self.subTest(header=header):
                self.assertEqual(detect_column_type(header), "count")

    def test_count_inside_word_does_not_make_count_header(self):
        self.assertIsNone(detect_column_type("Accounting Total"))
        self.assertEqual(detect_column_type("Accounting Index"), "index")

    def test_danish_compound_headers_still_match(self):
        self.assertEqual(detect_column_type("Lønindeks"), "index")

    def test_compound_patterns_match_as_substring_but_others_do_not(self):
        # A compound token (Danish "løn") matches inside a glued header word.
        self.assertTrue(header_matches("lønindeks", INDEX_PATTERNS))
        # A pattern that is not a compound token ("salary") only matches as a
        # whole token, so it must not match inside an unrelated glued word.
        self.assertFalse(header_matches("salaryindex", INDEX_PATTERNS))
        self.assertTrue(header_matches("salary index", INDEX_PATTERNS))

    def test_parse_sheet_preserves_category_name_with_letter_n(self):
        ws = FakeWorksheet([
            ("Company", "Engineering Count", "Engineering Index"),
            ("Example Corp", 12, 105.5),
        ])

        companies = parse_sheet(ws)

        self.assertEqual(companies[0]["categories"]["engineering"], {"count": 12, "index": 105.5})

    def test_parse_sheet_groups_accounting_count_index_pair(self):
        ws = FakeWorksheet([
            ("Company", "Accounting Count", "Accounting Index"),
            ("Example Corp", 12, 105.5),
        ])

        companies = parse_sheet(ws)

        self.assertEqual(companies[0]["categories"]["accounting"], {"count": 12, "index": 105.5})

    def test_parse_sheet_normalizes_paired_category_name_with_underscores(self):
        ws = FakeWorksheet([
            ("Company", "Software Engineering Count", "Software Engineering Index"),
            ("Example Corp", 8, 110.0),
        ])

        companies = parse_sheet(ws)

        self.assertEqual(companies[0]["categories"]["software_engineering"], {"count": 8, "index": 110.0})

    def test_parse_sheet_detects_company_column_with_token_header(self):
        # Real-world salary sheets rarely use the bare token "Company";
        # headers like "Company Name" / "Employer Name" must still be
        # detected as the company column (previously silently skipped -> []).
        for header in ("Company", "Company Name", "Employer Name"):
            with self.subTest(header=header):
                ws = FakeWorksheet([
                    (header, "Salary"),
                    ("Example Corp", 105.5),
                ])
                companies = parse_sheet(ws)
                self.assertEqual(len(companies), 1)
                self.assertEqual(companies[0]["company"], "Example Corp")
                self.assertEqual(
                    companies[0]["categories"]["salary"], {"index": 105.5}
                )

    def test_parse_sheet_detects_danish_compound_company_header(self):
        # The IDA workbook heads its company column "Firmanavn og by": "firma"
        # glued to "navn". Whole-token matching alone skipped the entire sheet
        # ("Could not find header row") from #75 until "firma" joined
        # COMPOUND_PATTERNS, so the shipped demonstration data no longer
        # converted at all.
        self.assertTrue(header_matches("Firmanavn og by", COMPANY_PATTERNS))
        ws = FakeWorksheet([
            ("Firmanavn og by", "Antal", "Indeks"),
            ("Example A/S, Aarhus", 12, 105.0),
        ])
        companies = parse_sheet(ws)
        self.assertEqual(len(companies), 1)
        self.assertEqual(companies[0]["company"], "Example A/S, Aarhus")
        self.assertEqual(companies[0]["categories"]["all_employees"], {"count": 12, "index": 105.0})

    def test_parse_sheet_detects_city_column_with_token_header(self):
        # City headers are matched with the same token-based header_matches()
        # used for the company column, not exact string equality. Real-world
        # sheets rarely use the bare token "City" or "Kommune" alone; headers
        # like "City Name" / "City/Kommune" must still be detected as the city
        # column (previously silently left as city_col=None -> empty city).
        for header in ("City", "City Name", "Kommune", "City/Kommune"):
            with self.subTest(header=header):
                ws = FakeWorksheet([
                    ("Company", header, "Salary"),
                    ("Example Corp", "Aarhus", 105.5),
                ])
                companies = parse_sheet(ws)
                self.assertEqual(len(companies), 1)
                self.assertEqual(companies[0]["city"], "Aarhus")

    def test_parse_sheet_handles_ragged_rows(self):
        # openpyxl's read_only mode yields ragged tuples for dimension-less
        # workbooks: a row can be shorter than the header. A company row that
        # omits its city and category cells must parse without an IndexError,
        # be retained, and get an empty city.
        ws = FakeWorksheet([
            ("Company", "City", "Engineering Count", "Engineering Index"),
            ("Example Corp",),
            ("Other Corp", "Aarhus", 12, 105.5),
        ])

        companies = parse_sheet(ws)

        self.assertEqual(len(companies), 2)
        self.assertEqual(companies[0]["company"], "Example Corp")
        self.assertEqual(companies[0]["city"], "")
        self.assertEqual(companies[0]["categories"], {})
        self.assertEqual(companies[1]["categories"]["engineering"], {"count": 12, "index": 105.5})

    def test_parse_sheet_skips_row_shorter_than_company_column(self):
        # A ragged row that ends before the company column has no company cell
        # at all; it must be skipped, not crash the parse.
        ws = FakeWorksheet([
            ("Notes", "Company", "Salary Index"),
            ("stray",),
            ("", "Example Corp", 105.5),
        ])

        companies = parse_sheet(ws)

        self.assertEqual(len(companies), 1)
        self.assertEqual(companies[0]["company"], "Example Corp")

    def test_skips_free_text_column(self):
        # A free-text "Notes" column must not become a bogus salary category.
        ws = FakeWorksheet([
            ("Company", "Salary Index", "Notes"),
            ("Example Corp", 105.5, "good"),
        ])

        companies = parse_sheet(ws)

        self.assertIn("salary_index", companies[0]["categories"])
        self.assertNotIn("notes", companies[0]["categories"])

    def test_skips_numeric_identifier_column(self):
        # A numeric "Id" column (employee id) must not be treated as a salary index.
        ws = FakeWorksheet([
            ("Company", "Salary Index", "Id"),
            ("Example Corp", 105.5, 7),
        ])

        companies = parse_sheet(ws)

        self.assertIn("salary_index", companies[0]["categories"])
        self.assertNotIn("id", companies[0]["categories"])

    def test_keeps_numeric_salary_column(self):
        # A genuine numeric salary column still produces a salary category.
        ws = FakeWorksheet([
            ("Company", "Salary Index"),
            ("Example Corp", 105.5),
        ])

        companies = parse_sheet(ws)

        self.assertIn("salary_index", companies[0]["categories"])
        self.assertEqual(companies[0]["categories"]["salary_index"], {"index": 105.5})

    def test_parse_sheet_accepts_comma_decimal_string_values(self):
        # Locale-formatted Excel exports can carry numeric cells as strings.
        # Danish decimal commas must not be silently dropped by float().
        ws = FakeWorksheet([
            ("Company", "Engineering Count", "Engineering Index"),
            ("Example Corp", "12,0", "108,5"),
        ])

        companies = parse_sheet(ws)

        self.assertEqual(
            companies[0]["categories"]["engineering"],
            {"count": 12, "index": 108.5},
        )

    def test_parse_sheet_accepts_danish_thousands_and_decimal_string(self):
        ws = FakeWorksheet([
            ("Company", "Salary Index"),
            ("Example Corp", "1.234,5"),
        ])

        companies = parse_sheet(ws)

        self.assertEqual(
            companies[0]["categories"]["salary_index"],
            {"index": 1234.5},
        )

    def test_parse_sheet_skips_ambiguous_single_comma_thousands_string(self):
        # In an English-locale export, "1,234" is probably 1234, but in a
        # decimal-comma locale it could be 1.234. Preserve the old safe-skip
        # behavior instead of guessing and writing a 1000x-wrong salary value.
        ws = FakeWorksheet([
            ("Company", "Salary Index"),
            ("Example Corp", "1,234"),
        ])

        companies = parse_sheet(ws)

        self.assertEqual(companies[0]["categories"], {})

    def test_parse_sheet_skips_ambiguous_single_dot_thousands_string(self):
        # "1.234" is the dot-side mirror of the comma guard above: in a
        # decimal-dot locale it is 1.234, while a Danish export (whole
        # thousands, no decimal comma, e.g. "60.000") means 1234/60000.
        # float() used to write the 1000x-smaller value silently - the
        # same never-guess policy must apply to both separators.
        ws = FakeWorksheet([
            ("Company", "Salary Index"),
            ("Example Corp", "1.234"),
        ])

        companies = parse_sheet(ws)

        self.assertEqual(companies[0]["categories"], {})

    def test_parse_sheet_pairs_interleaved_count_index_columns_by_name(self):
        ws = FakeWorksheet([
            ("Company", "Antal kvinder", "Antal mænd", "Kvinder indeks", "Mænd indeks"),
            ("Example Corp", 15, 20, 95.0, 108.0),
        ])

        companies = parse_sheet(ws)

        categories = companies[0]["categories"]
        self.assertEqual(categories["kvinder"], {"count": 15, "index": 95.0})
        self.assertEqual(categories["mænd"], {"count": 20, "index": 108.0})

    def test_standalone_count_column_is_stored_as_count_not_index(self):
        # A count column with no matching index column (e.g. a lone total
        # headcount) is still count data. It must not be emitted as a salary
        # index, which salary_lookup would render with a bogus "vs baseline"
        # percentage. The paired category alongside it is unaffected.
        ws = FakeWorksheet([
            ("Company", "Antal", "IT Count", "IT Index"),
            ("Example Corp", 250, 30, 108.5),
        ])

        companies = parse_sheet(ws)

        categories = companies[0]["categories"]
        self.assertEqual(categories["antal"], {"count": 250})
        self.assertEqual(categories["it"], {"count": 30, "index": 108.5})

    def test_parse_sheet_non_adjacent_columns_no_cross_match(self):
        ws = FakeWorksheet([
            ("Company", "Count_A", "Count_B", "Index_A", "Index_B"),
            ("Example Corp", 10, 20, 100.0, 200.0),
        ])

        companies = parse_sheet(ws)

        categories = companies[0]["categories"]
        self.assertEqual(categories["a"], {"count": 10, "index": 100.0})
        self.assertEqual(categories["b"], {"count": 20, "index": 200.0})

    def test_parse_sheet_ignores_citation_row_mentioning_company_pattern_word(self):
        # A title/source-citation row above the real header - standard in
        # real Danish union/statistics exports - can contain a stray
        # company-pattern word ("arbejdsgiver" = employer) in running prose.
        # It must not be mistaken for the header: that misreads the real
        # header row as data (producing a bogus "Firma" company) and drops
        # every real company's salary data (issue #414).
        ws = FakeWorksheet([
            ("Lønstatistik 2025",),
            ("Kilde: Medlemsundersøgelse opdelt efter arbejdsgiver og branche",),
            (),
            ("Firma", "By", "Antal alle", "Lønindeks alle"),
            ("Novo Nordisk A/S", "Bagsværd", 500, 108.5),
            ("Ørsted A/S", "Fredericia", 200, 105.2),
        ])

        companies = parse_sheet(ws)

        self.assertEqual(len(companies), 2)
        self.assertEqual(companies[0]["company"], "Novo Nordisk A/S")
        self.assertEqual(companies[0]["city"], "Bagsværd")
        self.assertEqual(companies[0]["categories"]["alle"], {"count": 500, "index": 108.5})
        self.assertEqual(companies[1]["company"], "Ørsted A/S")

    def test_parse_sheet_rejects_citation_row_with_count_word_in_same_cell(self):
        # Corroboration must come from a DIFFERENT cell than the company
        # match. A single free-text sentence can pack both a company-pattern
        # word and a count-pattern word together (e.g. "... opdelt efter
        # arbejdsgiver, antal svar 1234") - same-cell corroboration must not
        # be enough, or this citation row reintroduces the bogus-header bug.
        ws = FakeWorksheet([
            ("Lønstatistik 2025",),
            ("Kilde: undersøgelse opdelt efter arbejdsgiver, antal svar 1234",),
            (),
            ("Firma", "By", "Antal alle", "Lønindeks alle"),
            ("Novo Nordisk A/S", "Bagsværd", 500, 108.5),
        ])

        companies = parse_sheet(ws)

        self.assertEqual(len(companies), 1)
        self.assertEqual(companies[0]["company"], "Novo Nordisk A/S")
        self.assertEqual(companies[0]["categories"]["alle"], {"count": 500, "index": 108.5})

    def test_parse_sheet_falls_back_when_no_row_has_cross_cell_corroboration(self):
        # A header with only untyped salary columns (no header matches a
        # known city/count/index pattern - "Base pay"/"Bonus" don't) has
        # nothing to corroborate against in any row. The strict cross-cell
        # check must fall back to the original any-cell-mentions-company
        # rule rather than failing to find a header at all.
        ws = FakeWorksheet([
            ("Company", "Base pay 2025", "Bonus 2025"),
            ("Example Corp", 55000, 5000),
        ])

        companies = parse_sheet(ws)

        self.assertEqual(len(companies), 1)
        self.assertEqual(companies[0]["company"], "Example Corp")
        self.assertEqual(companies[0]["categories"]["base_pay_2025"], {"index": 55000.0})
        self.assertEqual(companies[0]["categories"]["bonus_2025"], {"index": 5000.0})

    def test_parse_sheet_warns_when_no_salary_columns_detected(self):
        # A header row with only company/city columns and no salary data
        # is a strong signal something is wrong (a misdetected header row,
        # or a sheet with no salary data at all) - it should be flagged,
        # not silently reported as a successful conversion.
        ws = FakeWorksheet([
            ("Company", "City"),
            ("Example Corp", "Aarhus"),
        ])

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            companies = parse_sheet(ws)

        self.assertEqual(companies[0]["categories"], {})
        self.assertIn("No salary data columns detected", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()


class ParseNumericCellLocaleTests(unittest.TestCase):
    # The separator that appears LAST is the decimal separator. Assuming
    # European ("." thousands, "," decimal) for every both-separator string
    # turned a US "1,234.56" into 1.23456 - a silent 1000x corruption that
    # flowed into salary_data.json and negotiation advice.

    def test_us_thousands_and_decimal_string(self):
        self.assertEqual(parse_numeric_cell("1,234.56"), 1234.56)

    def test_us_multiple_thousands_groups(self):
        self.assertEqual(parse_numeric_cell("1,234,567.89"), 1234567.89)

    def test_european_thousands_and_decimal_string(self):
        self.assertEqual(parse_numeric_cell("1.234,56"), 1234.56)

    def test_european_multiple_thousands_groups(self):
        self.assertEqual(parse_numeric_cell("1.234.567,89"), 1234567.89)


class BareCountIndexPairingTests(unittest.TestCase):
    """A count/index pair whose headers carry no category word is still a pair.

    "Count" + "Index" (Danish "Antal" + "Lønindeks") both strip to an empty
    category name, and the pairing loop used to require a non-empty name on
    both sides, so the single-category layout the README describes as
    "auto-pairs count/index columns" came out as two unrelated standalone
    columns. salary_lookup then rendered the count row with "N/A*" for the
    index - "too few employees to publish (privacy)" - about a company whose
    headcount was right there in the file. The literal name is asserted (not
    the module constant) so the cases run, and fail, against the old converter.
    """

    DEFAULT_CATEGORY = "all_employees"

    def test_bare_english_pair_is_paired_under_the_default_category(self):
        ws = FakeWorksheet([
            ("Company", "City", "Count", "Index"),
            ("Acme Corp", "Copenhagen", 500, 108.5),
        ])

        companies = parse_sheet(ws)

        self.assertEqual(
            companies[0]["categories"],
            {self.DEFAULT_CATEGORY: {"count": 500, "index": 108.5}},
        )

    def test_bare_danish_pair_is_paired_under_the_default_category(self):
        ws = FakeWorksheet([
            ("Firma", "By", "Antal", "Lønindeks"),
            ("Acme Corp", "Aarhus", 500, 108.5),
        ])

        companies = parse_sheet(ws)

        self.assertEqual(
            companies[0]["categories"],
            {self.DEFAULT_CATEGORY: {"count": 500, "index": 108.5}},
        )

    def test_bare_pair_does_not_cross_pair_with_a_named_category(self):
        # The bare pair and the named pair coexist; neither steals the other's
        # column, and a lone "Antal" with no bare index column stays standalone
        # (pinned separately by test_standalone_count_column_is_stored_as_count_not_index).
        ws = FakeWorksheet([
            ("Company", "Antal", "IT Count", "IT Index", "Lønindeks"),
            ("Acme Corp", 500, 30, 112.0, 108.5),
        ])

        companies = parse_sheet(ws)

        self.assertEqual(
            companies[0]["categories"],
            {
                self.DEFAULT_CATEGORY: {"count": 500, "index": 108.5},
                "it": {"count": 30, "index": 112.0},
            },
        )

    def test_lookup_renders_the_bare_pair_as_one_row_without_the_privacy_footnote_firing(self):
        # End to end through the documented path: converter output is what
        # salary_lookup.format_entry displays during /apply. Before the fix the
        # same sheet produced a "Count  500  N/A*" row plus an "Index  -  108.5"
        # row - the N/A* asserting a privacy suppression that never happened.
        ws = FakeWorksheet([
            ("Company", "City", "Count", "Index"),
            ("Acme Corp", "Copenhagen", 500, 108.5),
        ])
        entry = parse_sheet(ws)[0]

        rendered = format_entry(entry, {"index_baseline": 100, "index_label": "Index"})

        self.assertNotIn("N/A*", rendered.split("* N/A =")[0])
        self.assertRegex(rendered, r"All Employees\s+500\s+108\.5\s+\+8\.5%")
        self.assertNotRegex(rendered, r"^\s*Count\s+500", )


class QualifiedCompanyHeaderNextToQualifiedSalaryHeader(unittest.TestCase):
    """A name header must win over a salary header that also carries the word.

    The detection loop kept the LAST header matching a company pattern, so
    "Company Name | City | Company Count | Company Index" made the index
    column the company column and every row's company read "108.5". #571
    added an exact-match preference, which only helps when a header is
    exactly "Company"; the qualified name header stayed exposed. The Danish
    case below is a constructed header in the same shape, not the IDA
    workbook, whose pairs are headed "Antal" and "Indeks" and already convert.
    """

    def test_company_name_wins_over_company_index(self):
        ws = FakeWorksheet([
            ("Company Name", "City", "Company Count", "Company Index"),
            ("Acme Corp", "Berlin", 25, 108.5),
        ])

        entry = parse_sheet(ws)[0]

        self.assertEqual(entry["company"], "Acme Corp")
        self.assertEqual(entry["city"], "Berlin")
        self.assertEqual(entry["categories"], {"company": {"count": 25, "index": 108.5}})

    def test_danish_name_header_wins_over_qualified_index_header(self):
        # The cell carries no comma so the case tests which column wins and
        # does not depend on how a combined name-and-city cell is split (#660).
        ws = FakeWorksheet([
            ("Firmanavn og by", "Antal", "Firmaets indeks"),
            ("Acme A/S", 25, 108.5),
        ])

        entry = parse_sheet(ws)[0]

        self.assertEqual(entry["company"], "Acme A/S")
        self.assertEqual(sum(1 for c in entry["categories"].values() if c.get("index") == 108.5), 1)
        self.assertEqual(sum(1 for c in entry["categories"].values() if c.get("count") == 25), 1)

    def test_exact_header_still_wins_over_a_qualified_name_header(self):
        # Tier 1 (#571) is unchanged: "Company" beats "Company Name" wherever it sits.
        ws = FakeWorksheet([
            ("Company Name", "Company", "Salary Index"),
            ("Acme Corp (legal)", "Acme", 108.5),
        ])

        self.assertEqual(parse_sheet(ws)[0]["company"], "Acme")

    def test_city_column_never_aliases_the_company_column(self):
        # "Firmanavn og by" carries both a company and a city pattern word.
        ws = FakeWorksheet([
            ("Firmanavn og by", "Antal", "Indeks"),
            ("Acme A/S", 25, 108.5),
        ])

        entry = parse_sheet(ws)[0]

        self.assertEqual(entry["company"], "Acme A/S")
        self.assertEqual(entry["city"], "")
        self.assertEqual(entry["categories"], {"all_employees": {"count": 25, "index": 108.5}})

    def test_combined_header_before_a_real_city_column_keeps_the_city(self):
        # The city lookup must skip the company column: with the combined
        # header first, the first city-pattern match used to land on it and
        # was then cleared, dropping a real city column that followed.
        for headers, row, city in (
            (("Company and city", "City name", "Count", "Index"), ("Acme Corp", "Berlin", 25, 108.5), "Berlin"),
            (("Firmanavn og by", "City name", "Antal", "Indeks"), ("Acme A/S", "Aarhus", 25, 108.5), "Aarhus"),
        ):
            with self.subTest(headers=headers):
                entry = parse_sheet(FakeWorksheet([headers, row]))[0]
                self.assertEqual(entry["company"], row[0])
                self.assertEqual(entry["city"], city)
                self.assertEqual(entry["categories"], {"all_employees": {"count": 25, "index": 108.5}})


class CompoundCategoryPairingTests(unittest.TestCase):
    def test_parse_sheet_pairs_danish_compound_index_with_count(self):
        # "Lønindeks alle" is *detected* as an index column via
        # COMPOUND_PATTERNS, but the derived category name must also lose the
        # compound word or it can never pair with "Antal alle" ("alle" vs
        # "lønindeks alle") - exactly the locale the compound support exists for.
        ws = FakeWorksheet([
            ("Firma", "Antal alle", "Lønindeks alle"),
            ("Example Corp", 12, 118.0),
        ])

        companies = parse_sheet(ws)

        self.assertEqual(
            companies[0]["categories"]["alle"],
            {"count": 12, "index": 118.0},
        )

    def test_parse_sheet_sheet_level_us_locale_value(self):
        ws = FakeWorksheet([
            ("Company", "Salary Index"),
            ("Example Corp", "1,234.56"),
        ])

        companies = parse_sheet(ws)

        self.assertEqual(
            companies[0]["categories"]["salary_index"],
            {"index": 1234.56},
        )

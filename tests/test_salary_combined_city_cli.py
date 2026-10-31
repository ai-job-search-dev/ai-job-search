"""A combined name-and-city workbook column must feed salary_lookup --city.

The IDA demonstration workbook heads its company column "Firmanavn og by"
and stores "Ørsted A/S,Fredericia" in that one cell (no space after the
comma). convert_salary_excel used to copy the whole cell into company with
city "". The documented
lookup `python3 salary_lookup.py "Ørsted" --city "Fredericia"` then missed,
even though the city was in the converted file - just not in the field
--city reads.
"""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    import openpyxl
except ImportError:
    openpyxl = None

REPO = Path(__file__).resolve().parent.parent


@unittest.skipUnless(openpyxl, "requires optional openpyxl (installed in CI)")
class CombinedCityWorkbookCLI(unittest.TestCase):
    def convert_and_lookup(self, headers, row, *lookup_args):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workbook = root / "salary.xlsx"
            output = root / "salary_data.json"
            book = openpyxl.Workbook()
            try:
                book.active.append(headers)
                book.active.append(row)
                book.save(workbook)
            finally:
                book.close()
            conversion = subprocess.run(
                [sys.executable, str(REPO / "tools/convert_salary_excel.py"),
                 str(workbook), "--output", str(output)],
                capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(conversion.returncode, 0, conversion.stderr)
            companies = json.loads(output.read_text(encoding="utf-8"))["companies"]
            shutil.copyfile(REPO / "salary_lookup.py", root / "salary_lookup.py")
            lookup = subprocess.run(
                [sys.executable, str(root / "salary_lookup.py"), *lookup_args],
                capture_output=True, text=True, encoding="utf-8",
            )
            return companies, lookup

    def test_ida_firmanavn_og_by_row_is_findable_with_city_filter(self):
        companies, lookup = self.convert_and_lookup(
            ["Firmanavn og by", "Antal", "Indeks"],
            ["Ørsted A/S,Fredericia", 200, 105.2],
            "Ørsted", "--city", "Fredericia",
        )
        self.assertEqual(companies, [{
            "company": "Ørsted A/S",
            "city": "Fredericia",
            "categories": {"all_employees": {"count": 200, "index": 105.2}},
        }])
        self.assertEqual(lookup.returncode, 0, lookup.stdout + lookup.stderr)
        self.assertIn("Ørsted A/S", lookup.stdout)
        self.assertIn("Fredericia", lookup.stdout)
        self.assertNotIn("No results found", lookup.stdout)

    def test_separate_city_column_is_unchanged(self):
        companies, lookup = self.convert_and_lookup(
            ["Company", "City", "Count", "Index"],
            ["Novo Nordisk A/S", "Bagsværd", 500, 108.5],
            "Novo Nordisk", "--city", "Bagsværd",
        )
        self.assertEqual(companies[0]["company"], "Novo Nordisk A/S")
        self.assertEqual(companies[0]["city"], "Bagsværd")
        self.assertEqual(lookup.returncode, 0, lookup.stdout + lookup.stderr)
        self.assertIn("Novo Nordisk A/S", lookup.stdout)

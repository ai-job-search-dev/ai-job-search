"""Wrapped workbook header cells must convert like their one-line spelling."""
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

try:
    import openpyxl
except ImportError:
    openpyxl = None


TOOL = Path(__file__).resolve().parent.parent / "tools" / "convert_salary_excel.py"


@unittest.skipUnless(openpyxl, "requires optional openpyxl (installed in CI)")
class WrappedHeaderCLI(unittest.TestCase):
    def convert(self, header):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            workbook, output = root / "salary.xlsx", root / "salary_data.json"
            book = openpyxl.Workbook()
            try:
                book.active.append(header)
                book.active.append(["Acme", "Aarhus", 12, 104.5])
                book.save(workbook)
            finally:
                book.close()
            result = subprocess.run(
                [sys.executable, str(TOOL), str(workbook), "--output", str(output)],
                cwd=root, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(output.read_text(encoding="utf-8"))["companies"][0]["categories"]

    def test_wrapped_headers_pair_like_their_single_line_spelling(self):
        expected = {"all_employees": {"count": 12, "index": 104.5}}
        self.assertEqual(self.convert(["Company", "City", "Employees", "Median salary"]), expected)
        self.assertEqual(self.convert(["Company", "City", "Employees", "Median\nsalary"]), expected)
        self.assertEqual(self.convert(["Firma", "By", "Antal\nmedarbejdere", "Løn-\nindeks"]), expected)

    def test_wrapped_category_name_has_no_line_break(self):
        categories = self.convert(["Company", "City", "Count\nengineers", "Index\nengineers"])
        self.assertEqual(categories, {"engineers": {"count": 12, "index": 104.5}})


if __name__ == "__main__":
    unittest.main()

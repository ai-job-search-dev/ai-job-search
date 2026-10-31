"""A workbook's prose notes tab must not be read as a company table."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

try:
    import openpyxl
except ImportError:
    openpyxl = None

REPO = Path(__file__).resolve().parent.parent

NOTES = [
    ["About this data"],
    ["Each row is one company in the 2025 survey."],
    ["Indexes are relative to the national median."],
    ["Categories with fewer than five respondents are suppressed."],
]


@unittest.skipUnless(openpyxl, "requires optional openpyxl (installed in CI)")
class NotesSheetCLI(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        shutil.copyfile(REPO / "salary_lookup.py", self.root / "salary_lookup.py")
        self.output = self.root / "salary_data.json"

    def convert(self, with_data):
        workbook = self.root / "salary.xlsx"
        book = openpyxl.Workbook()
        try:
            sheet = book.active
            if with_data:
                sheet.title = "Data"
                sheet.append(["Company", "City", "Count", "Index"])
                sheet.append(["Acme", "Aarhus", 10, 105])
                sheet = book.create_sheet("Notes")
            else:
                sheet.title = "Notes"
            for line in NOTES:
                sheet.append(line)
            book.save(workbook)
        finally:
            book.close()
        return subprocess.run(
            [sys.executable, str(REPO / "tools" / "convert_salary_excel.py"), str(workbook),
             "--output", str(self.output)],
            cwd=self.root, capture_output=True, text=True,
        )

    def test_notes_tab_sentences_are_not_listed_as_companies(self):
        result = self.convert(with_data=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Skipping sheet 'Notes'", result.stderr)
        companies = json.loads(self.output.read_text(encoding="utf-8"))["companies"]
        self.assertEqual([entry["company"] for entry in companies], ["Acme"])
        listed = subprocess.run(
            [sys.executable, "salary_lookup.py", "--list-all"],
            cwd=self.root, capture_output=True, text=True,
        )
        self.assertEqual(listed.stdout.splitlines(), ["Acme (Aarhus)"])

    def test_notes_only_workbook_fails_without_writing_output(self):
        result = self.convert(with_data=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("No data could be parsed", result.stderr)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()

"""The converter must not write baselines rejected by salary_lookup --validate."""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    import openpyxl
except ImportError:
    openpyxl = None

TOOL = Path(__file__).resolve().parents[1] / "tools" / "convert_salary_excel.py"


@unittest.skipUnless(openpyxl, "requires optional openpyxl (installed in CI)")
class BaselineCLI(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.workbook = self.root / "salary.xlsx"
        self.output = self.root / "salary.json"
        book = openpyxl.Workbook()
        try:
            book.active.append(["Company", "Count", "Index"])
            book.active.append(["Acme", 12, 108.5])
            book.save(self.workbook)
        finally:
            book.close()

    def convert(self, baseline):
        return subprocess.run(
            [sys.executable, str(TOOL), str(self.workbook),
             "--output", str(self.output), f"--baseline={baseline}"],
            capture_output=True, text=True, encoding="utf-8",
        )

    def test_nonfinite_baseline_is_rejected_without_overwriting_output(self):
        for baseline in ("nan", "inf", "-inf", "1e309"):
            with self.subTest(baseline=baseline):
                self.output.write_text("existing salary data\n", encoding="utf-8")
                result = self.convert(baseline)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("--baseline", result.stderr)
                self.assertIn("finite", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(self.output.read_text(encoding="utf-8"), "existing salary data\n")

    def test_finite_custom_and_zero_baselines_are_preserved(self):
        for baseline in ("150", "0"):
            with self.subTest(baseline=baseline):
                result = self.convert(baseline)
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(self.output.read_text(encoding="utf-8"))
                self.assertEqual(data["metadata"]["index_baseline"], float(baseline))

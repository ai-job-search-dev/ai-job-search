"""Company identity survives the documented salary-data-file -> CLI path."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


TOOL = Path(__file__).resolve().parent.parent / "salary_lookup.py"


class UnicodeSalaryCLI(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        shutil.copyfile(TOOL, self.root / TOOL.name)
        # Same layout as README_SALARY_TOOL.md's manual-data setup. Keep the
        # fixture next to an isolated copy of the CLI, never a user's dataset.
        names = [
            "腾讯", "東京 AI", "北京 AI", "Acme Corp", "Café Étoile",
            "Nestlé Danmark A/S", "Ørsted A/S", "Mærsk A/S", "Åland A/S",
            "Þekkingarmiðlun ehf.",
        ]
        data = {"companies": [
            {"company": name, "categories": {"all_employees": {"index": 105}}}
            for name in names
        ]}
        (self.root / "salary_data.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )

    def lookup(self, query):
        result = subprocess.run(
            [sys.executable, str(self.root / TOOL.name), query, "--json"],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return [row["company"] for row in json.loads(result.stdout)]

    def test_exact_non_latin_company_can_be_found(self):
        self.assertEqual(self.lookup("腾讯"), ["腾讯"])

    def test_mixed_script_companies_do_not_collapse_to_a_latin_fragment(self):
        self.assertEqual(self.lookup("東京 AI")[0], "東京 AI")

    def test_canonically_equivalent_spelling_finds_the_same_company(self):
        self.assertEqual(self.lookup("Cafe\u0301 E\u0301toile"), ["Café Étoile"])

    def test_ascii_control_still_matches(self):
        self.assertEqual(self.lookup("acme"), ["Acme Corp"])

    def test_ascii_query_finds_accented_company(self):
        self.assertEqual(self.lookup("Nestle"), ["Nestlé Danmark A/S"])

    def test_nordic_spelling_variants_survive_accent_folding(self):
        for query, expected in [("Orsted", "Ørsted A/S"),
                                ("Maersk", "Mærsk A/S"),
                                ("Aaland", "Åland A/S")]:
            with self.subTest(query=query):
                self.assertEqual(self.lookup(query), [expected])

    def test_official_ascii_and_native_nordic_company_spellings_match(self):
        # The company uses Þekkingarmiðlun and thekkingarmidlun.is publicly.
        # As in the other cases, the index is synthetic, not its salary data.
        for query in ("Thekkingarmidlun", "THEKKINGARMIDLUN", "Þekkingarmiðlun", "ÞEKKINGARMIÐLUN"):
            with self.subTest(query=query):
                self.assertEqual(self.lookup(query), ["Þekkingarmiðlun ehf."])

    def test_native_nordic_query_finds_ascii_company_data(self):
        (self.root / "salary_data.json").write_text(json.dumps({"companies": [
            {"company": "Thekkingarmidlun", "categories": {"all_employees": {"index": 105}}},
        ]}), encoding="utf-8")
        for query in ("Þekkingarmiðlun", "ÞEKKINGARMIÐLUN"):
            with self.subTest(query=query):
                self.assertEqual(self.lookup(query), ["Thekkingarmidlun"])

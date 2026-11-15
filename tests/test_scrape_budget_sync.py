"""The paid-scraper budget module is duplicated across two self-contained CLIs.

Each portal CLI is installed and tested on its own in CI, so firecrawl-fetch and
apify-jobs-search each carry a copy of src/budget.ts. Both copies read and write
the same ledger file; if they drift, one CLI can misread the other's entries and
the shared monthly allowance stops being enforced.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COPIES = [
    ROOT / ".agents" / "skills" / "firecrawl-fetch" / "cli" / "src" / "budget.ts",
    ROOT / ".agents" / "skills" / "apify-jobs-search" / "cli" / "src" / "budget.ts",
]


class ScrapeBudgetSyncTests(unittest.TestCase):
    def test_copies_are_identical(self):
        present = [p for p in COPIES if p.is_file()]
        if len(present) < 2:
            self.skipTest("one of the paid scraper CLIs is not installed")
        first, second = (p.read_bytes() for p in present)
        self.assertEqual(first, second, "budget.ts copies differ: edit one, copy it over the other")


if __name__ == "__main__":
    unittest.main()

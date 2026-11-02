"""Tracker writers delegate CSV quoting to the application-record tool.

Behavioral round-trip and column-preservation tests live in
 test_application_state.py and invoke the real CLI.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TrackerWritersUseCSVTool(unittest.TestCase):
    def test_status_writers_use_the_tool_instead_of_stripping_notes(self):
        for name in ("outcome", "gmail-sync"):
            text = (ROOT / ".claude" / "commands" / f"{name}.md").read_text(encoding="utf-8")
            self.assertIn("application_state.py update", text)
            self.assertIn("CSV quoting", text)
            self.assertNotIn("containing no commas, double quotes or line breaks", text)
            self.assertNotIn("with every comma, double quote and line break deleted", text)


if __name__ == "__main__":
    unittest.main()

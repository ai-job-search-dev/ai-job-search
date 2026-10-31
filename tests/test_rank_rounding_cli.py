"""Documented scoring JSON -> apply CLI -> exact weighted score and verdict."""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

TOOL = Path(__file__).resolve().parent.parent / "tools/rank_state.py"
DIMENSIONS = ("technical", "experience", "behavioral", "career")


class ExactRankRoundingCLI(unittest.TestCase):
    def apply(self, cases):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state, results = root / "seen_jobs.json", root / "scores.json"
            state.write_text(json.dumps({"seen": {key: {
                "title": "SOC Analyst", "company": "Acme", "status": "new",
                "url": f"https://example.com/jobs/{key}", "deadline": None,
            } for key in cases}}), encoding="utf-8")
            results.write_text(json.dumps([{
                "key": key, "status": "scored", "scores": dict(zip(DIMENSIONS, values)),
                "location_verdict": "PASS", "language_gate": "PASS", "deadline": None,
            } for key, values in cases.items()]), encoding="utf-8")
            result = subprocess.run([
                sys.executable, str(TOOL), "apply", "--state", str(state),
                "--results", str(results), "--today", "2026-10-16",
            ], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            stored = json.loads(state.read_text(encoding="utf-8"))["seen"]
            self.assertEqual(output["errors"], [])
            actual = {row["key"]: (row["score"], row["verdict"]) for row in output["ranked"]}
            self.assertEqual(actual, {key: (value["rank_score"], value["rank_verdict"])
                                     for key, value in stored.items()})
            return actual

    def test_halfpoint_boundaries_do_not_drop_a_verdict_band(self):
        # Integer percent weights give numerators 5950 and 2950 over 100,
        # exactly 59.5 and 29.5. The existing nearest/half-up rule yields 60/30.
        self.assertEqual(self.apply({
            "good": (31, 55, 51, 96), "weak": (31, 31, 57, 13),
        }), {"good": (60, "Good Fit"), "weak": (30, "Weak Fit")})

    def test_fractional_and_endpoint_controls(self):
        self.assertEqual(self.apply({
            "below": (31, 55, 51, 95.9), "above": (31, 55, 51, 96.1),
            "fractional": (72.5, 72.5, 72.5, 72.5),
            "zero": (0, 0, 0, 0), "hundred": (100, 100, 100, 100),
        }), {"below": (59, "Moderate Fit"), "above": (60, "Good Fit"),
             "fractional": (73, "Good Fit"), "zero": (0, "Poor Fit"),
             "hundred": (100, "Strong Fit")})

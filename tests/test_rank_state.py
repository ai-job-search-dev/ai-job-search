"""Tests for tools/rank_state.py - /rank's state helper (#395).

/rank used to pull the whole of seen_jobs.json through the model's context to
select candidates, then emit it back to record scores. That cost the whole
backlog per run no matter how few jobs were being scored, and it grew for the
life of the workspace. These pin the behaviour the three subcommands took
over: selection matches Step 1's existing rules, the sweep matches rule 6
exactly (including its two defensive-parse edge cases), and the write-back
matches Step 4's existing rules exactly - the location_verdict legacy
migration, the deadline null-is-not-a-correction rule, and verbatim
strengths/gaps persistence.
"""
import csv
import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "rank_state.py"

TODAY = "2026-09-03"


def entry(**over):
    base = {
        "title": "SOC Analyst",
        "company": "Acme",
        "url": "https://example.com/job",
        "first_seen": "2026-08-30",
        "deadline": None,
        "status": "new",
        "portal": "linkedin-search",
    }
    base.update(over)
    return base


class RankStateCase(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.state = self.tmp / "seen_jobs.json"
        self.addCleanup(self._tmp.cleanup)

    def write_state(self, seen):
        self.state.write_text(json.dumps({"seen": seen}), encoding="utf-8")

    def run_tool(self, *args, expect=0, today=TODAY, now=None):
        clock = ["--now", now] if now else []
        proc = subprocess.run(
            [sys.executable, str(TOOL), *args, "--state", str(self.state), "--today", today, *clock],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(proc.returncode, expect, proc.stderr)
        return json.loads(proc.stdout)

    def read_state(self):
        return json.loads(self.state.read_text(encoding="utf-8"))["seen"]


class Candidates(RankStateCase):
    def test_selects_only_new_entries_and_projects_a_compact_row(self):
        self.write_state(
            {
                "a": entry(),
                "b": entry(status="ranked", rank_score=70),
                "c": entry(status="skipped"),
                "d": entry(status="expired"),
            }
        )
        out = self.run_tool("candidates", "--tracker", str(self.tmp / "none.csv"))
        self.assertEqual([row["key"] for row in out["selected"]], ["a"])
        self.assertEqual(
            set(out["selected"][0]),
            {"key", "title", "company", "url", "portal", "deadline", "posted_date"},
            "the projection is the point: strengths/gaps and every other stored field "
            "stay on disk rather than entering the conversation",
        )

    def test_limit_defers_the_rest_and_reports_the_count(self):
        self.write_state({f"k{i}": entry(title=f"Role {i}") for i in range(25)})
        out = self.run_tool("candidates", "--limit", "10", "--tracker", str(self.tmp / "none.csv"))
        self.assertEqual(len(out["selected"]), 10)
        self.assertEqual(out["eligible"], 25)
        self.assertEqual(
            out["deferred"],
            15,
            "a backlog larger than the batch limit must be reported, not silently truncated - "
            "the user has to know a re-run continues it",
        )

    def test_limit_zero_means_no_cap(self):
        self.write_state({f"k{i}": entry(title=f"Role {i}") for i in range(15)})
        out = self.run_tool("candidates", "--limit", "0", "--tracker", str(self.tmp / "none.csv"))
        self.assertEqual(len(out["selected"]), 15)
        self.assertEqual(out["deferred"], 0)

    def test_tracker_pairs_are_excluded(self):
        self.write_state({"a": entry(company="Acme", title="SOC Analyst"), "b": entry(company="Other")})
        tracker = self.tmp / "tracker.csv"
        tracker.write_text("date,company,role\n2026-08-01,ACME,soc analyst\n", encoding="utf-8")
        out = self.run_tool("candidates", "--tracker", str(tracker))
        self.assertEqual([row["key"] for row in out["selected"]], ["b"])
        self.assertEqual(out["excluded_by_tracker"], 1)

    def test_tracker_exclusion_handles_utf8_bom_and_reordered_columns(self):
        self.write_state({"a": entry(company="Acme", title="SOC Analyst"), "b": entry(company="Other")})
        tracker = self.tmp / "tracker.csv"
        # A spreadsheet export can prepend a BOM to either matching column.
        for encoding in ("utf-8", "utf-8-sig"):
            for csv_text in (
                "date,company,role\r\n2026-08-01,ACME,soc analyst\r\n",
                "company,role,date\r\nACME,soc analyst,2026-08-01\r\n",
                "role,company,date\r\nsoc analyst,ACME,2026-08-01\r\n",
            ):
                with self.subTest(encoding=encoding, header=csv_text.splitlines()[0]):
                    tracker.write_bytes(csv_text.encode(encoding))
                    out = self.run_tool("candidates", "--tracker", str(tracker))
                    self.assertEqual([row["key"] for row in out["selected"]], ["b"])
                    self.assertEqual(out["excluded_by_tracker"], 1)

    def test_tracker_exclusion_preserves_unicode_identity(self):
        # Use the standard /outcome header and the real candidates CLI.
        header = (
            "date,company,sector,role,role_type,channel,status,contact_person,"
            "fit_rating,notes,cv_file,cover_letter_file,source,deadline"
        ).split(",")
        cases = (
            # Tracked company/role, candidate company/title, expected exclusion.
            ("Acme", "设计师", "Acme", "工程师", False),
            ("Acme", "工程师", "Acme", "工程师", True),
            ("腾讯", "Engineer", "腾讯", "Engineer", True),
            ("腾讯", "Engineer", "阿里巴巴", "Engineer", False),
            ("Компания", "Инженер", "КОМПАНИЯ", "ИНЖЕНЕР", True),
            ("Café", "Engineer", "Cafe\u0301", "Engineer", True),
            ("Straße", "Engineer", "STRASSE", "Engineer", True),
            ("कला Labs", "Engineer", "कल Labs", "Engineer", False),
            ("Acme, Inc.", "SOC Analyst", "ACME_INC", "soc-analyst", True),
        )
        tracker = self.tmp / "tracker.csv"
        for company, role, candidate_company, title, excluded in cases:
            with self.subTest(tracked=(company, role), candidate=(candidate_company, title)):
                self.write_state({
                    "candidate": entry(company=candidate_company, title=title),
                    "other": entry(company="Other", title="Untracked role"),
                })
                with tracker.open("w", encoding="utf-8", newline="") as fh:
                    writer = csv.DictWriter(fh, fieldnames=header)
                    writer.writeheader()
                    writer.writerow({"date": TODAY, "company": company, "role": role, "status": "applied"})
                out = self.run_tool("candidates", "--tracker", str(tracker))
                self.assertEqual(
                    [row["key"] for row in out["selected"]],
                    ["other"] if excluded else ["candidate", "other"],
                )
                self.assertEqual(out["excluded_by_tracker"], int(excluded))

    def test_tracker_saved_in_a_legacy_code_page_is_reported_not_a_traceback(self):
        # A spreadsheet's plain "CSV" save on Windows writes cp1252, not UTF-8.
        self.write_state({"a": entry()})
        tracker = self.tmp / "tracker.csv"
        tracker.write_bytes("date,company,role\r\n2026-08-01,Ørsted,SOC Analyst\r\n".encode("cp1252"))
        proc = subprocess.run(
            [sys.executable, str(TOOL), "candidates", "--state", str(self.state), "--tracker", str(tracker)],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("is not UTF-8 text - re-save it as CSV UTF-8", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_focus_filters_on_title_company_and_stored_fit_notes(self):
        self.write_state(
            {
                "a": entry(title="Data Scientist"),
                "b": entry(title="SOC Analyst"),
                "c": entry(title="Engineer", strengths=["strong data science match"]),
            }
        )
        out = self.run_tool("candidates", "--focus", "data scien", "--tracker", str(self.tmp / "n.csv"))
        self.assertEqual(sorted(row["key"] for row in out["selected"]), ["a", "c"])

    def test_all_flag_includes_every_status_but_skipped(self):
        self.write_state(
            {
                "a": entry(status="ranked"),
                "b": entry(status="expired"),
                "c": entry(status="skipped"),
                "d": entry(status="new"),
            }
        )
        out = self.run_tool("candidates", "--all", "--tracker", str(self.tmp / "n.csv"))
        self.assertEqual(sorted(row["key"] for row in out["selected"]), ["a", "b", "d"])

    def test_missing_state_file_exits_nonzero(self):
        proc = subprocess.run(
            [sys.executable, str(TOOL), "candidates", "--state", str(self.tmp / "nope.json"),
             "--tracker", str(self.tmp / "n.csv")],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not found", proc.stderr + proc.stdout)


class Sweep(RankStateCase):
    def test_retires_past_deadlines_and_flags_the_closing_ones(self):
        self.write_state(
            {
                "past": entry(status="ranked", deadline="2026-09-01"),
                "soon": entry(status="ranked", deadline="2026-09-07"),
                "later": entry(status="ranked", deadline="2026-12-01"),
            }
        )
        out = self.run_tool("sweep", "--write")
        self.assertEqual([r["key"] for r in out["newly_expired"]], ["past"])
        self.assertEqual([r["key"] for r in out["closing_soon"]], ["soon"])
        self.assertEqual(self.read_state()["past"]["status"], "expired")
        self.assertEqual(self.read_state()["soon"]["status"], "ranked")

    def test_entries_without_a_deadline_are_left_alone(self):
        """The majority case. Inferring one from first_seen would retire jobs
        on a date nobody set."""
        self.write_state({"a": entry(status="ranked", deadline=None), "b": entry(status="ranked")})
        out = self.run_tool("sweep", "--write")
        self.assertEqual(out["newly_expired"], [])
        self.assertTrue(all(e["status"] == "ranked" for e in self.read_state().values()))

    def test_non_iso_deadlines_are_reported_not_compared(self):
        """Portals have shipped "ASAP", DD.MM.YYYY and free text into this field."""
        self.write_state(
            {
                "asap": entry(status="ranked", deadline="ASAP", portal="jobindex-search"),
                "euro": entry(status="ranked", deadline="31.08.2026", portal="jobbank-search"),
            }
        )
        out = self.run_tool("sweep", "--write")
        self.assertEqual(out["newly_expired"], [])
        self.assertEqual(
            sorted(r["portal"] for r in out["unparseable_deadlines"]),
            ["jobbank-search", "jobindex-search"],
            "a bad stored value is traced back to the portal that wrote it",
        )
        self.assertTrue(all(e["status"] == "ranked" for e in self.read_state().values()))

    def test_only_ranked_entries_are_swept_and_excluded_keys_are_skipped(self):
        self.write_state(
            {
                "new_past": entry(status="new", deadline="2026-09-01"),
                "rescored": entry(status="ranked", deadline="2026-09-01"),
                "other": entry(status="ranked", deadline="2026-09-01"),
            }
        )
        out = self.run_tool("sweep", "--write", "--exclude", "rescored")
        self.assertEqual([r["key"] for r in out["newly_expired"]], ["other"])
        self.assertEqual(out["swept"], 1)
        self.assertEqual(self.read_state()["new_past"]["status"], "new")

    def test_excluded_keys_tolerate_spaces_after_commas(self):
        self.write_state(
            {
                "a": entry(status="ranked", deadline="2026-09-01"),
                "b": entry(status="ranked", deadline="2026-09-01"),
                "other": entry(status="ranked", deadline="2026-09-01"),
            }
        )
        out = self.run_tool("sweep", "--write", "--exclude", "a, b")
        self.assertEqual([r["key"] for r in out["newly_expired"]], ["other"])
        self.assertEqual(out["swept"], 1)
        self.assertEqual(self.read_state()["b"]["status"], "ranked", "a key scored this run is never swept")

    def test_without_write_nothing_is_persisted(self):
        self.write_state({"past": entry(status="ranked", deadline="2026-09-01")})
        out = self.run_tool("sweep")
        self.assertEqual([r["key"] for r in out["newly_expired"]], ["past"])
        self.assertFalse(out["written"])
        self.assertEqual(self.read_state()["past"]["status"], "ranked")


class Apply(RankStateCase):
    def results(self, payload):
        path = self.tmp / "results.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    def _scored(self, **extra):
        base = {"key": "a", "status": "scored", "deadline": None,
                "scores": {d: 80 for d in ("technical", "experience", "behavioral", "career")},
                "location_verdict": "PASS", "language_gate": "PASS", "language": "en",
                "strengths": ["s"], "gaps": ["g"]}
        base.update(extra)
        return base

    def test_apply_persists_guard_verdict(self):
        # The scoring agent runs tools/posting_guard.py on each fetched body and
        # returns the verdict line as `guard`; it is stored additively and echoed
        # in the rows so Step 5 can show a ⚠ marker without re-reading state.
        verdict = "posting_guard: ⚠ 1 instruction-like line - review before continuing (1 hidden block removed)"
        stored = "⚠ 1 instruction-like line - review before continuing (1 hidden block removed)"
        self.write_state({"a": entry(status="new"), "b": entry(status="new")})
        out = self.run_tool("apply", "--results", self.results([
            self._scored(guard=verdict),
            self._scored(key="b", guard="clean", location_verdict="FAIL"),
        ]))
        self.assertEqual(self.read_state()["a"]["guard"], stored)
        self.assertEqual(out["ranked"][0]["guard"], stored)
        self.assertEqual(self.read_state()["b"]["guard"], "clean")
        self.assertEqual(out["vetoed"][0]["guard"], "clean")

    def test_apply_without_guard_key_unchanged(self):
        # An agent on the old prompt returns no `guard`: nothing is written and
        # the row carries None, exactly as before the field existed.
        self.write_state({"a": entry(status="new")})
        out = self.run_tool("apply", "--results", self.results([self._scored()]))
        self.assertNotIn("guard", self.read_state()["a"])
        self.assertIsNone(out["ranked"][0]["guard"])

    def test_apply_normalises_the_guard_verdict(self):
        # The agent may return the gate's first line verbatim; the stored value
        # is "clean" for any clean verdict (with or without the context
        # parenthetical) and the ⚠ text without the "posting_guard:" prefix
        # otherwise, so Step 5's "anything other than clean" rule holds.
        self.write_state({k: entry(status="new") for k in "abc"})
        out = self.run_tool("apply", "--results", self.results([
            self._scored(key="a", guard="posting_guard: clean"),
            self._scored(key="b", guard="posting_guard: clean (8 hidden blocks removed, none instruction-like)"),
            self._scored(key="c", guard="posting_guard: ⚠ 1 instruction-like line - review before continuing"),
        ]))
        state = self.read_state()
        self.assertEqual(state["a"]["guard"], "clean")
        self.assertEqual(state["b"]["guard"], "clean")
        self.assertEqual(state["c"]["guard"], "⚠ 1 instruction-like line - review before continuing")
        self.assertEqual([row["guard"] for row in out["ranked"]], ["clean", "clean", "⚠ 1 instruction-like line - review before continuing"])

    def test_apply_drops_a_stale_guard_when_rescored_without_one(self):
        self.write_state({"a": entry(status="ranked", guard="⚠ old", rank_score=50, rank_date="2026-08-01")})
        out = self.run_tool("apply", "--results", self.results([self._scored()]))
        self.assertNotIn("guard", self.read_state()["a"])
        self.assertIsNone(out["ranked"][0]["guard"])

    def test_apply_truncates_and_rejects_non_string_guard(self):
        self.write_state({"a": entry(status="new"), "b": entry(status="new")})
        out = self.run_tool("apply", "--results", self.results([
            self._scored(guard="x" * 500),
            self._scored(key="b", guard={"not": "a string"}),
        ]))
        self.assertEqual(len(self.read_state()["a"]["guard"]), 200)
        self.assertNotIn("guard", self.read_state()["b"])
        self.assertIsNone(out["ranked"][1]["guard"])

    def test_scored_keys_keep_past_deadlines_out_of_the_shortlist(self):
        for status in ("new", "ranked", "expired"):
            with self.subTest(status=status):
                original = entry(status=status, deadline="2026-09-02")
                if status != "new":
                    original.update(rank_score=61, rank_date="2026-08-31", strengths=["stored strength"])
                self.write_state({"a": original})
                args = ["candidates", "--tracker", str(self.tmp / "none.csv")]
                if status != "new":
                    args.append("--all")
                selected = self.run_tool(*args)["selected"]
                self.assertEqual([row["key"] for row in selected], ["a"])
                result = {"key": "a", "status": "scored", "deadline": None,
                          "scores": {dimension: 80 for dimension in ("technical", "experience", "behavioral", "career")},
                          "location_verdict": "PASS", "language_gate": "PASS", "language": "en",
                          "strengths": ["fresh strength"], "gaps": []}
                out = self.run_tool("apply", "--results", self.results([result]))
                self.assertEqual(out["ranked"], [])
                self.assertEqual(out["vetoed"], [])
                self.assertEqual([row["key"] for row in out["expired"]], ["a"])
                self.assertEqual(self.read_state()["a"], dict(original, status="expired"))
                swept = self.run_tool("sweep", "--write", "--exclude", "a")
                self.assertEqual(swept["swept"], 0)
                self.assertEqual(self.read_state()["a"]["status"], "expired")

    def test_deadline_apply_boundaries_and_dry_run_keep_existing_semantics(self):
        cases = [
            (TODAY, None, "scored", "ranked"),
            ("2026-09-04", None, "scored", "ranked"),
            ("ASAP", None, "scored", "ranked"),
            (None, None, "scored", "ranked"),
            ("2026-09-02", "2026-09-04", "scored", "ranked"),
            (None, None, "expired", "expired"),
            ("2026-09-02", None, "scored", "expired"),
        ]
        for stored, fresh, status, expected in cases:
            with self.subTest(stored=stored, fresh=fresh, status=status):
                self.write_state({"a": entry(deadline=stored)})
                before = self.state.read_bytes()
                result = {"key": "a", "status": status, "deadline": fresh,
                          "scores": {dimension: 80 for dimension in ("technical", "experience", "behavioral", "career")}}
                path = self.results([result])
                dry = self.run_tool("apply", "--results", path, "--dry-run")
                self.assertFalse(dry["written"])
                self.assertEqual(self.state.read_bytes(), before)
                self.assertEqual([row["key"] for row in dry[expected]], ["a"])
                out = self.run_tool("apply", "--results", path)
                self.assertEqual([row["key"] for row in out[expected]], ["a"])
                self.assertEqual(self.read_state()["a"]["status"], expected)
                self.assertEqual(self.read_state()["a"]["deadline"], fresh or stored)

    def test_past_deadline_does_not_bypass_scoring_errors(self):
        for result in ({"key": "a", "status": "scored", "scores": {"technical": 80}},
                       {"key": "missing", "status": "scored", "scores": {}}):
            with self.subTest(result=result):
                original = entry(deadline="2026-09-02")
                self.write_state({"a": original})
                before = self.state.read_bytes()
                path = self.results([result])
                dry = self.run_tool("apply", "--results", path, "--dry-run", expect=1)
                self.assertEqual(len(dry["errors"]), 1)
                self.assertEqual(self.state.read_bytes(), before)
                out = self.run_tool("apply", "--results", path, expect=1)
                self.assertEqual(len(out["errors"]), 1)
                self.assertEqual(out["expired"], [])
                self.assertEqual(self.read_state()["a"], original)

    def test_weights_bands_and_persisted_fields(self):
        self.write_state({"a": entry()})
        out = self.run_tool(
            "apply",
            "--results",
            self.results(
                [
                    {
                        "key": "a",
                        "status": "scored",
                        "scores": {"technical": 80, "experience": 60, "behavioral": 70, "career": 75},
                        "location_verdict": "PASS",
                        "language_gate": "PASS",
                        "deadline": "2026-09-05",
                        "strengths": ["s1", "s2"],
                        "gaps": ["g1"],
                    }
                ]
            ),
        )
        stored = self.read_state()["a"]
        # 80*.30 + 60*.25 + 70*.15 + 75*.30 = 72
        self.assertEqual(stored["rank_score"], 72)
        self.assertEqual(stored["rank_verdict"], "Good Fit")
        self.assertEqual(stored["status"], "ranked")
        self.assertEqual(stored["rank_date"], TODAY)
        self.assertEqual(stored["strengths"], ["s1", "s2"])
        self.assertEqual(stored["gaps"], ["g1"])
        self.assertEqual(stored["deadline"], "2026-09-05")
        self.assertTrue(out["ranked"][0]["urgent"], "a deadline inside 7 days carries the urgency marker")

    def test_expired_status_is_written_through(self):
        self.write_state({"a": entry()})
        out = self.run_tool("apply", "--results", self.results([{"key": "a", "status": "expired"}]))
        self.assertEqual(self.read_state()["a"]["status"], "expired")
        self.assertEqual([r["key"] for r in out["expired"]], ["a"])

    def test_null_deadline_does_not_erase_a_stored_one(self):
        """Absence is not a correction: a fetch that degraded to a listing page
        returns no deadline, and blanking the stored date would also put the
        entry out of the sweep's reach forever."""
        self.write_state({"a": entry(deadline="2026-10-01")})
        self.run_tool(
            "apply",
            "--results",
            self.results(
                [
                    {
                        "key": "a",
                        "status": "scored",
                        "scores": {"technical": 50, "experience": 50, "behavioral": 50, "career": 50},
                        "deadline": None,
                    }
                ]
            ),
        )
        self.assertEqual(self.read_state()["a"]["deadline"], "2026-10-01")

    def test_legacy_verdict_stored_under_location_is_migrated(self):
        self.write_state({"a": entry(location="FLAG")})
        self.run_tool(
            "apply",
            "--results",
            self.results(
                [
                    {
                        "key": "a",
                        "status": "scored",
                        "scores": {"technical": 50, "experience": 50, "behavioral": 50, "career": 50},
                    }
                ]
            ),
        )
        stored = self.read_state()["a"]
        self.assertEqual(stored["location_verdict"], "FLAG")
        self.assertNotIn("location", stored, "a legacy verdict is moved, never left to read as a place")

    def test_a_real_place_in_location_survives(self):
        self.write_state({"a": entry(location="Athens, Greece")})
        self.run_tool(
            "apply",
            "--results",
            self.results(
                [
                    {
                        "key": "a",
                        "status": "scored",
                        "scores": {"technical": 50, "experience": 50, "behavioral": 50, "career": 50},
                        "location_verdict": "PASS",
                    }
                ]
            ),
        )
        self.assertEqual(self.read_state()["a"]["location"], "Athens, Greece")

    def test_vetoed_rows_are_separated_from_the_ranking(self):
        self.write_state({"a": entry(), "b": entry(), "c": entry()})
        scores = {"technical": 90, "experience": 90, "behavioral": 90, "career": 90}
        out = self.run_tool(
            "apply",
            "--results",
            self.results(
                [
                    {"key": "a", "status": "scored", "scores": scores, "location_verdict": "FAIL"},
                    {"key": "b", "status": "scored", "scores": scores, "language_gate": "FAIL",
                     "language_note": "requires fluent Polish"},
                    {"key": "c", "status": "scored", "scores": {"technical": 40, "experience": 40,
                                                                "behavioral": 40, "career": 40}},
                ]
            ),
        )
        self.assertEqual(sorted(r["key"] for r in out["vetoed"]), ["a", "b"])
        self.assertEqual([r["key"] for r in out["ranked"]], ["c"])
        self.assertEqual(self.read_state()["b"]["language_note"], "requires fluent Polish")

    def test_language_note_is_dropped_when_gate_passes(self):
        self.write_state({"a": entry(language_note="stale note from a prior run")})
        self.run_tool(
            "apply",
            "--results",
            self.results(
                [
                    {
                        "key": "a",
                        "status": "scored",
                        "scores": {"technical": 50, "experience": 50, "behavioral": 50, "career": 50},
                        "language_gate": "PASS",
                    }
                ]
            ),
        )
        self.assertNotIn("language_note", self.read_state()["a"])

    def test_strengths_and_gaps_are_capped_and_stored_verbatim(self):
        self.write_state({"a": entry()})
        self.run_tool(
            "apply",
            "--results",
            self.results(
                [
                    {
                        "key": "a",
                        "status": "scored",
                        "scores": {"technical": 50, "experience": 50, "behavioral": 50, "career": 50},
                        "strengths": ["one", "two", "three", "four"],
                        "gaps": ["<script>not sanitized on purpose, stored as plain data</script>"],
                    }
                ]
            ),
        )
        stored = self.read_state()["a"]
        self.assertEqual(len(stored["strengths"]), 3, "at most 3 bullets, matching the spec")
        self.assertEqual(
            stored["gaps"],
            ["<script>not sanitized on purpose, stored as plain data</script>"],
            "gaps are stored verbatim - untrusted data, never reformatted",
        )

    def test_all_replaces_rather_than_accumulates_arrays(self):
        self.write_state({"a": entry(status="ranked", strengths=["old strength"], gaps=["old gap"])})
        self.run_tool(
            "apply",
            "--results",
            self.results(
                [
                    {
                        "key": "a",
                        "status": "scored",
                        "scores": {"technical": 50, "experience": 50, "behavioral": 50, "career": 50},
                        "strengths": ["new strength"],
                        "gaps": ["new gap"],
                    }
                ]
            ),
        )
        stored = self.read_state()["a"]
        self.assertEqual(stored["strengths"], ["new strength"])
        self.assertEqual(stored["gaps"], ["new gap"])

    def test_unknown_key_is_an_error_not_a_silent_drop(self):
        self.write_state({"a": entry()})
        out = self.run_tool(
            "apply", "--results", self.results([{"key": "ghost", "status": "scored", "scores": {}}]), expect=1
        )
        self.assertEqual(out["errors"][0]["key"], "ghost")

    def test_missing_score_dimension_is_an_error(self):
        self.write_state({"a": entry()})
        out = self.run_tool(
            "apply",
            "--results",
            self.results([{"key": "a", "status": "scored", "scores": {"technical": 80}}]),
            expect=1,
        )
        self.assertIn("experience", out["errors"][0]["error"])
        self.assertEqual(self.read_state()["a"]["status"], "new", "a rejected result never half-writes an entry")

    def test_dry_run_prints_but_never_writes(self):
        self.write_state({"a": entry()})
        self.run_tool(
            "apply",
            "--results",
            self.results(
                [{"key": "a", "status": "scored",
                  "scores": {"technical": 50, "experience": 50, "behavioral": 50, "career": 50}}]
            ),
            "--dry-run",
        )
        self.assertEqual(self.read_state()["a"]["status"], "new")

    def test_invalid_scores_report_errors_without_changing_the_entry(self):
        dimensions = ("technical", "experience", "behavioral", "career")
        invalid = (-1, 101, True, False, float("nan"), float("inf"), -float("inf"), 10**400)
        for dimension in dimensions:
            for value in invalid:
                with self.subTest(dimension=dimension, value=value):
                    original = entry(status="ranked", rank_score=70, strengths=["keep"])
                    self.write_state({"a": original, "b": entry()})
                    scores = dict.fromkeys(dimensions, 50)
                    scores[dimension] = value
                    out = self.run_tool(
                        "apply", "--results", self.results([
                            {"key": "a", "status": "scored", "scores": scores},
                            {"key": "b", "status": "scored", "scores": dict.fromkeys(dimensions, 80)},
                        ]), expect=1,
                    )
                    self.assertEqual(len(out["errors"]), 1)
                    self.assertEqual(out["errors"][0]["key"], "a")
                    self.assertIn(dimension, out["errors"][0]["error"])
                    self.assertEqual(self.read_state()["a"], original)
                    self.assertEqual([row["key"] for row in out["ranked"]], ["b"])
                    self.assertEqual(self.read_state()["b"]["rank_score"], 80)

    def test_score_boundaries_and_fractional_scores_remain_valid(self):
        for value, expected in ((0, 0), (100, 100), (72.5, 73)):
            with self.subTest(value=value):
                self.write_state({"a": entry()})
                out = self.run_tool("apply", "--results", self.results([
                    {"key": "a", "status": "scored", "scores": dict.fromkeys(
                        ("technical", "experience", "behavioral", "career"), value)},
                ]))
                self.assertEqual(out["errors"], [])
                self.assertEqual(self.read_state()["a"]["rank_score"], expected)

    def test_re_scoring_an_already_ranked_job_is_idempotent(self):
        """Re-running /rank never re-scores an already-ranked job unless --all
        says so (Step 4), but if it does score one again, apply must produce
        the same result deterministically rather than accumulating state."""
        self.write_state({"a": entry(status="ranked", rank_score=40, strengths=["old"])})
        scores = {"technical": 90, "experience": 90, "behavioral": 90, "career": 90}
        self.run_tool(
            "apply", "--results",
            self.results([{"key": "a", "status": "scored", "scores": scores, "strengths": ["new"]}]),
        )
        stored = self.read_state()["a"]
        self.assertEqual(stored["rank_score"], 90)
        self.assertEqual(stored["strengths"], ["new"])


class Unreachable(RankStateCase):
    """#534: a posting that could not be fetched is not a closed posting.

    Both used to become `expired`, which `candidates` never selects again, so a
    403 or a timeout on ranking day removed an open job from the pipeline for
    good. `unreachable` is retried on later runs and only gives up to `expired`
    after 3 attempts with at least 48 hours between the first and the last, so a
    scheduled run plus a manual one on the same day cannot burn every attempt on
    one outage.
    """

    RETRY_FIELDS = ("fetch_attempts", "first_unreachable", "last_fetch_error")

    def results(self, payload):
        path = self.tmp / "results.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    def fail_fetch(self, now, error="403 after curl retry"):
        return self.run_tool(
            "apply",
            "--results",
            self.results([{"key": "a", "status": "unreachable", "fetch_error": error}]),
            today=now[:10],
            now=now,
        )

    def test_a_failed_fetch_is_recorded_as_unreachable_not_expired(self):
        self.write_state({"a": entry()})
        out = self.fail_fetch("2026-09-03T09:00:00Z")
        stored = self.read_state()["a"]
        self.assertEqual(stored["status"], "unreachable")
        self.assertEqual(stored["fetch_attempts"], 1)
        self.assertEqual(stored["first_unreachable"], "2026-09-03T09:00:00Z")
        self.assertEqual(stored["last_fetch_error"], "403 after curl retry")
        self.assertEqual([r["key"] for r in out["unreachable"]], ["a"])
        self.assertEqual(out["unreachable"][0]["fetch_attempts"], 1)
        self.assertEqual(out["expired"], [])
        self.assertEqual(out["gave_up"], [])

    def test_unreachable_entries_are_selected_again_on_the_next_run(self):
        self.write_state(
            {
                "a": entry(status="unreachable", fetch_attempts=1, first_unreachable="2026-09-02T09:00:00Z"),
                "b": entry(status="expired"),
            }
        )
        out = self.run_tool("candidates", "--tracker", str(self.tmp / "none.csv"))
        self.assertEqual([row["key"] for row in out["selected"]], ["a"])

    def test_three_failures_inside_48_hours_keep_retrying(self):
        """A scheduled run and a manual one the same day, then one more short of
        48 hours: the attempt count is reached, the time floor is not."""
        self.write_state({"a": entry()})
        for now in ("2026-09-03T09:00:00Z", "2026-09-03T18:00:00Z", "2026-09-05T08:59:00Z"):
            out = self.fail_fetch(now)
        stored = self.read_state()["a"]
        self.assertEqual(stored["status"], "unreachable", "47h59m between first and last must not give up")
        self.assertEqual(stored["fetch_attempts"], 3)
        self.assertEqual(stored["first_unreachable"], "2026-09-03T09:00:00Z")
        self.assertEqual(out["gave_up"], [])

    def test_gives_up_after_three_failures_48_hours_apart(self):
        self.write_state({"a": entry()})
        for now, error in (
            ("2026-09-03T09:00:00Z", "403"),
            ("2026-09-04T09:00:00Z", "timeout"),
            ("2026-09-05T09:00:00Z", "login wall"),
        ):
            out = self.fail_fetch(now, error)
        stored = self.read_state()["a"]
        self.assertEqual(stored["status"], "expired")
        self.assertEqual(stored["expired_reason"], "unreachable")
        self.assertEqual(out["unreachable"], [])
        self.assertEqual(
            out["gave_up"],
            [
                {
                    "key": "a",
                    "title": "SOC Analyst",
                    "company": "Acme",
                    "url": "https://example.com/job",
                    "fetch_attempts": 3,
                    "last_fetch_error": "login wall",
                }
            ],
            "a job given up on is reported once with its link, since it may still be live",
        )
        out = self.run_tool("candidates", "--tracker", str(self.tmp / "none.csv"), today="2026-09-06")
        self.assertEqual(out["selected"], [])

    def test_a_later_successful_fetch_clears_the_retry_fields(self):
        self.write_state(
            {"a": entry(status="unreachable", fetch_attempts=2, first_unreachable="2026-09-01T09:00:00Z",
                        last_fetch_error="timeout")}
        )
        self.run_tool(
            "apply",
            "--results",
            self.results(
                [{"key": "a", "status": "scored",
                  "scores": {"technical": 60, "experience": 60, "behavioral": 60, "career": 60}}]
            ),
        )
        stored = self.read_state()["a"]
        self.assertEqual(stored["status"], "ranked")
        for field in self.RETRY_FIELDS:
            self.assertNotIn(field, stored)

    def test_a_confirmed_closed_posting_still_expires_at_once(self):
        self.write_state(
            {"a": entry(status="unreachable", fetch_attempts=1, first_unreachable="2026-09-02T09:00:00Z",
                        last_fetch_error="timeout")}
        )
        out = self.run_tool("apply", "--results", self.results([{"key": "a", "status": "expired"}]))
        stored = self.read_state()["a"]
        self.assertEqual(stored["status"], "expired")
        self.assertNotIn("expired_reason", stored)
        for field in self.RETRY_FIELDS:
            self.assertNotIn(field, stored)
        self.assertEqual([r["key"] for r in out["expired"]], ["a"])

    def test_a_failed_fetch_past_the_stored_deadline_still_expires(self):
        """A posting that closed on 2026-09-20 and returns 403 on 2026-10-05 is
        closed by its own deadline, not unreachable: it expires at once, as the
        scored path would, rather than being retried and later listed under
        "Gave up, check manually" as possibly live."""
        self.write_state(
            {"a": entry(deadline="2026-09-20", status="unreachable", fetch_attempts=1,
                        first_unreachable="2026-10-04T09:00:00Z", last_fetch_error="timeout")}
        )
        out = self.fail_fetch("2026-10-05T09:00:00Z")
        stored = self.read_state()["a"]
        self.assertEqual(stored["status"], "expired")
        self.assertNotIn("expired_reason", stored)
        for field in self.RETRY_FIELDS:
            self.assertNotIn(field, stored)
        self.assertEqual([r["key"] for r in out["expired"]], ["a"])
        self.assertEqual(out["unreachable"], [])
        self.assertEqual(out["gave_up"], [])

    def test_fetch_error_is_stored_as_bounded_plain_text(self):
        self.write_state({"a": entry()})
        self.fail_fetch("2026-09-03T09:00:00Z", error="x" * 500)
        self.assertLessEqual(len(self.read_state()["a"]["last_fetch_error"]), 200)

    def test_an_unknown_status_is_an_error_not_a_missing_score(self):
        self.write_state({"a": entry()})
        out = self.run_tool(
            "apply", "--results", self.results([{"key": "a", "status": "unreachble"}]), expect=1
        )
        self.assertIn("unknown status 'unreachble'", out["errors"][0]["error"])
        self.assertEqual(self.read_state()["a"]["status"], "new", "a rejected result never half-writes an entry")

    def test_malformed_results_are_errors_not_a_traceback(self):
        self.write_state({"a": entry(), "b": entry(title="Other")})
        out = self.run_tool(
            "apply",
            "--results",
            self.results(
                [
                    7,
                    "a",
                    {"key": ["a"], "scores": {}},
                    {"key": "a", "scores": [90, 80]},
                    {"key": "b", "scores": {"technical": 80, "experience": 80, "behavioral": 80, "career": 80}},
                ]
            ),
            expect=1,
        )
        self.assertEqual(
            [e["error"] for e in out["errors"]],
            [
                "result is not an object",
                "result is not an object",
                "no such key in seen_jobs.json",
                "scores must be an object",
            ],
        )
        self.assertEqual([r["key"] for r in out["ranked"]], ["b"], "valid results in the same batch still apply")
        self.assertEqual(self.read_state()["a"]["status"], "new")

    def test_an_unknown_gate_verdict_is_an_error_not_a_shortlisted_job(self):
        scores = {dimension: 90 for dimension in ("technical", "experience", "behavioral", "career")}
        for field, value in (("location_verdict", "Fail"), ("language_gate", "fail"), ("location_verdict", "REJECT")):
            with self.subTest(field=field, value=value):
                self.write_state({"a": entry()})
                result = {"key": "a", "status": "scored", "scores": scores, field: value}
                out = self.run_tool("apply", "--results", self.results([result]), expect=1)
                self.assertEqual(out["ranked"], [], "a deal-breaker spelled differently must not reach the shortlist")
                self.assertIn(f"unknown {field} {value!r}", out["errors"][0]["error"])
                self.assertEqual(self.read_state()["a"]["status"], "new", "a rejected result never half-writes an entry")


if __name__ == "__main__":
    unittest.main()

"""Guards for the /upskill skill spec.

The skill is a markdown spec (the spec IS the implementation), so these
tests pin the invariants that would break silently: the header format that
lint_skills.py enforces, and aggregate mode's merge of tracker rows with
/rank's recorded gaps from seen_jobs.json (previously aggregate mode only
read the tracker and inferred skills from free-text columns).
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    import yaml  # noqa: F401 - only probing availability for the lint integration test
    _HAVE_YAML = True
except ImportError:
    _HAVE_YAML = False

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / ".claude" / "skills" / "upskill" / "SKILL.md"
SCRAPER = REPO / ".claude" / "skills" / "job-scraper" / "SKILL.md"
RANK_STATE = REPO / "tools" / "rank_state.py"


def _sections(text: str) -> dict[str, str]:
    """Split a skill spec into {heading: body} by '##' headers.

    Splitting this way lets a fork's extra sections sit between the ones
    under test without shifting which text a given assertion sees.
    """
    parts = text.split("\n## ")
    result = {}
    for part in parts[1:]:
        heading, _, body = part.partition("\n")
        result[heading.strip()] = body
    return result


class UpskillSkillSpec(unittest.TestCase):
    def test_skill_file_exists_with_lint_compliant_header(self):
        self.assertTrue(SKILL.is_file(), "skill spec missing")
        text = SKILL.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"), "skill spec must start with YAML frontmatter")
        self.assertIn("name: upskill", text)

    def test_step2_reads_ranked_jobs_with_moderate_fit_floor(self):
        sections = _sections(SKILL.read_text(encoding="utf-8"))
        step2 = sections.get("Step 2: Load Data", "")
        self.assertIn("seen_jobs.json", step2)
        self.assertIn("rank_score >= 45", step2)
        self.assertIn(
            "gap persistence",
            step2,
            "Step 2 must document the graceful-degradation clause for entries scored before gaps existed",
        )

    def test_step2_drops_jobs_rank_vetoed(self):
        """/rank vetoes a location or language FAIL only on its shortlist:
        tools/rank_state.py apply still stores the job as status "ranked" with
        its full score and gaps. Step 2's filter (ranked, rank_score >= 45)
        kept such a job, so a deal-breaker's gaps fed the heatmap and the
        learning plan. Drive the real write path to show the entry exists,
        then require Step 2 to drop it."""
        with TemporaryDirectory() as tmp:
            state, results = Path(tmp) / "seen_jobs.json", Path(tmp) / "results.json"
            state.write_text(
                json.dumps({"seen": {
                    "acme_data-engineer": {
                        "title": "Data Engineer", "company": "Acme", "url": "https://example.com/job/1",
                        "first_seen": "2026-09-01", "deadline": None, "status": "new",
                    },
                    "beta_analyst": {
                        "title": "Analyst", "company": "Beta", "url": "https://example.com/job/2",
                        "first_seen": "2026-09-01", "deadline": None, "status": "new",
                    },
                }}),
                encoding="utf-8",
            )
            scores = {"technical": 85, "experience": 80, "behavioral": 75, "career": 80}
            results.write_text(
                json.dumps([
                    {
                        "key": "acme_data-engineer", "status": "scored", "scores": scores,
                        "location_verdict": "FAIL", "language_gate": "PASS", "deadline": None,
                        "strengths": ["Python"], "gaps": ["Kubernetes in production"],
                    },
                    {
                        "key": "beta_analyst", "status": "scored", "scores": scores,
                        "location_verdict": "PASS", "language_gate": "FAIL", "deadline": None,
                        "strengths": ["SQL"], "gaps": ["Fluent German"],
                    },
                ]),
                encoding="utf-8",
            )
            proc = subprocess.run(
                [sys.executable, str(RANK_STATE), "apply", "--state", str(state),
                 "--results", str(results), "--today", "2026-09-03"],
                capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            seen = json.loads(state.read_text(encoding="utf-8"))["seen"]
        # Both vetoes, location-only and language-only, are stored as ranked jobs.
        for key, field in (("acme_data-engineer", "location_verdict"), ("beta_analyst", "language_gate")):
            with self.subTest(key=key):
                self.assertEqual(seen[key]["status"], "ranked")
                self.assertGreaterEqual(seen[key]["rank_score"], 45, "the vetoed job clears Step 2's score floor")
                self.assertEqual(seen[key][field], "FAIL")

        step2 = _sections(SKILL.read_text(encoding="utf-8")).get("Step 2: Load Data", "")
        item3 = step2[step2.index("3. Read `job_scraper/seen_jobs.json`"): step2.index("4. Read")]
        for field in ("`location_verdict`", "`language_gate`"):
            self.assertIn(field, item3, f"Step 2.3 must drop entries whose {field} is FAIL")
        self.assertIn("`FAIL`", item3)
        self.assertIn("legacy", item3, "entries ranked before the rename keep their verdict in `location`")
        # Only FAIL vetoes: a FLAG is a judgment call for the user and stays in.
        self.assertIn("A `FLAG` stays in", item3)

    def test_step2_column_list_keeps_in_phase_with_tracker_header(self):
        """/upskill reads the tracker, so its enumeration of the columns must
        match the header /apply writes - the deadline column (#319) is the
        first column to be added since the list was written."""
        sections = _sections(SKILL.read_text(encoding="utf-8"))
        step2 = sections.get("Step 2: Load Data", "")
        self.assertIn(
            "source, deadline",
            step2,
            "Step 2's column list lost the deadline column the tracker header now ends with",
        )

    def test_step3_documents_dedupe_and_gap_precedence(self):
        sections = _sections(SKILL.read_text(encoding="utf-8"))
        step3 = sections.get("Step 3: Pass 1 — Hard Skill Diff", "")
        self.assertIn("case-insensitive company + title", step3, "Step 3 must specify the dedupe key")
        self.assertIn(
            "/notion-sync",
            step3,
            "Step 3 must cite the upstream precedent for the dedupe key, not a fork-only file",
        )
        self.assertIn(
            "Recorded gaps beat inferred skills",
            step3,
            "Step 3 must state that recorded gaps take precedence over inferred skills",
        )
        self.assertIn("(100 - fit_rating) / 100", step3)
        self.assertIn("(100 - rank_score) / 100", step3)

    def test_step3_dedupe_names_the_field_ranked_entries_actually_carry(self):
        # The tracker has a `role` column; a seen_jobs.json entry has `title`
        # (job-scraper SKILL.md schema) and no `role` key at all. Step 3 told
        # the reader to match on "company + role" with "casefold + strip on
        # both fields", which reads as a literal lookup of `role` on the entry:
        # that never matches, so the dedupe silently failed and Step 3.2's
        # "one weight per job" counted the same job twice. The join is
        # role-vs-title, as tools/rank_state.py implements it.
        schema = SCRAPER.read_text(encoding="utf-8")
        schema_block = schema[schema.index('"<key from tools/job_key.py>"'): schema.index("```", schema.index('"<key from tools/job_key.py>"'))]
        self.assertIn('"title"', schema_block)
        self.assertNotIn('"role"', schema_block, "if entries gain a role key, revisit the join wording")
        step3 = _sections(SKILL.read_text(encoding="utf-8")).get("Step 3: Pass 1 — Hard Skill Diff", "")
        dedupe = step3[step3.index("**Dedupe.**"): step3.index("2. **Recorded gaps")]
        self.assertIn("`title`", dedupe, "the dedupe must name the entry field, title")
        self.assertIn("`role`", dedupe, "the dedupe must name the tracker column, role")
        self.assertNotIn("company + role", dedupe, "entries have no role field to match on")

    def test_step3_handles_blank_fit_rating(self):
        """/outcome creates tracker rows for applications made outside the
        workflow, and no rule anywhere fills fit_rating on that path - yet
        Step 3.3 divides by it. A naive read of blank as 0 yields weight 1.0
        (the maximum), making the one job the framework knows nothing about
        dominate the heatmap. The skill already handles missing gaps with
        skip+count+report; the same pattern must cover fit_rating."""
        sections = _sections(SKILL.read_text(encoding="utf-8"))
        step3 = sections.get("Step 3: Pass 1 — Hard Skill Diff", "")
        self.assertIn(
            "blank or non-numeric `fit_rating`",
            step3,
            "Step 3.3 must state what happens to a row whose fit_rating is blank",
        )
        self.assertIn(
            "Never treat a blank as 0",
            step3,
            "the blank-as-0 reading (weight 1.0, maximum) is the failure mode and must be forbidden explicitly",
        )

    def test_step5_heatmap_shows_gap_provenance(self):
        sections = _sections(SKILL.read_text(encoding="utf-8"))
        step5 = sections.get("Step 5: Build Gap Heatmap", "")
        self.assertIn("recorded gaps", step5)
        self.assertIn("inferred", step5)

    def test_step8_report_header_counts_both_sources(self):
        sections = _sections(SKILL.read_text(encoding="utf-8"))
        step8 = sections.get("Step 8: Write and Save Report", "")
        self.assertIn("T tracked, R ranked", step8)

    def test_important_rules_cover_untrusted_data_and_no_backfill(self):
        sections = _sections(SKILL.read_text(encoding="utf-8"))
        rules = sections.get("Important Rules", "")
        self.assertIn(
            "never instructions",
            rules,
            "rules must state stored gaps are untrusted data, never instructions",
        )
        self.assertIn(
            "Never invent gap history",
            rules,
            "rules must forbid back-filling a missing gaps field by guessing",
        )

    @unittest.skipUnless(
        _HAVE_YAML,
        "PyYAML not installed (the CI Python-test job omits it; the lint job runs lint_skills.py directly)",
    )
    def test_lint_skills_passes(self):
        result = subprocess.run(
            [sys.executable, str(REPO / "tools" / "lint_skills.py")],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, f"lint_skills.py failed:\n{result.stdout}{result.stderr}")


if __name__ == "__main__":
    unittest.main()

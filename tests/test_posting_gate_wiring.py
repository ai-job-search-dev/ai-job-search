"""Where the posting integrity gate (tools/posting_guard.py) is wired beyond /apply
Step 0 and /rank Step 2: the archive, /interview, and the trust-boundary rules.

Why this test exists: a gate that only runs in one place is bypassed the moment
a posting reaches the workflow another way - from the archive in /interview, or
pasted by hand. These pins keep each entry point naming the gate, and keep the
archive free of the excerpts the gate removed (counts only), so a stripped
payload is never re-read by a later session.
"""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APPLY = REPO / ".claude" / "commands" / "apply.md"
INTERVIEW = REPO / ".claude" / "commands" / "interview.md"
RESEARCH = REPO / ".claude" / "skills" / "job-application-assistant" / "09-web-research.md"


class ArchiveWiring(unittest.TestCase):
    def setUp(self):
        text = APPLY.read_text(encoding="utf-8")
        self.step6b = re.search(r"### Step 6b: Record the Application(.*?)(?=### Application-Form Fields)", text, re.DOTALL).group(1)

    def test_archive_stores_cleaned_text_with_counts_only(self):
        item7 = re.search(r"7\. \*\*Archive the posting now\.\*\*(.*?)(?=\n\n|\Z)", self.step6b, re.DOTALL).group(1)
        self.assertIn("verbatim", item7)  # still never a summary (test_apply_records_application pins it too)
        self.assertIn("posting_guard", item7)
        self.assertIn("counts", item7)
        self.assertRegex(item7, r"never .*excerpt", "removed content must not be re-archived as quotes")


class InterviewWiring(unittest.TestCase):
    def test_step1_gates_the_archived_posting_in_text_mode(self):
        text = INTERVIEW.read_text(encoding="utf-8")
        step1 = re.search(r"## Step 1: Load the Application Context(.*?)(?=\n## Step 2)", text, re.DOTALL).group(1)
        self.assertIn("posting_guard", step1)
        self.assertIn("--text", step1)

class RefusedVerdictWiring(unittest.TestCase):
    """Every caller of posting_guard specifies what to do when the gate
    refuses an empty posting (posting_guard: refused (no text to evaluate))."""

    def test_interview_step1_handles_refused_verdict(self):
        text = INTERVIEW.read_text(encoding="utf-8")
        step1 = re.search(r"## Step 1: Load the Application Context(.*?)(?=\n## Step 2)", text, re.DOTALL).group(1)
        self.assertIn("refused (no text to evaluate)", step1)
        self.assertIn("tell the user the archived posting is empty and ask for the text", step1)

    def test_apply_handles_refused_verdict(self):
        text = APPLY.read_text(encoding="utf-8")
        self.assertIn("refused (no text to evaluate)", text)
        self.assertIn("The `posting-fetcher` has already run the full escalation in `09-web-research.md`", text)
        self.assertIn("tell the user the posting produced no readable text and ask them to paste the job posting text", text)
        self.assertIn("Never evaluate fit on an empty posting", text)

    def test_rank_handles_refused_verdict(self):
        text = (REPO / ".claude" / "commands" / "rank.md").read_text(encoding="utf-8")
        self.assertIn("posting_guard: refused (no text to evaluate)", text)
        self.assertIn("record the job as `unreachable` for this run (not scored)", text)
        self.assertIn('"fetch_error": "no text to evaluate"', text)
        self.assertIn('"guard": "posting_guard: refused (no text to evaluate)"', text)
        self.assertIn("after exhausting escalation", text)
        self.assertIn("exempt from this ⚠ marker rule", text)

    def test_fetch_agents_handle_refused_verdict(self):
        fetcher = (REPO / ".claude" / "agents" / "posting-fetcher.md").read_text(encoding="utf-8")
        scorer = (REPO / ".claude" / "agents" / "posting-scorer.md").read_text(encoding="utf-8")
        self.assertIn("refused (no text to evaluate)", fetcher)
        self.assertIn("Treat that rung as failed and continue down the escalation order", fetcher)
        self.assertIn("If retrieval failed at every rung (including all rungs refused)", fetcher)
        self.assertIn("return nothing as posting text", fetcher)

        self.assertIn("refused (no text to evaluate)", scorer)
        self.assertIn("continue down the escalation order", scorer)
        self.assertIn("return status `unreachable`", scorer)
        self.assertIn('"fetch_error": "no text to evaluate"', scorer)
        self.assertIn('"guard": "posting_guard: refused (no text to evaluate)"', scorer)

    def test_skill_step1_handles_refused_verdict(self):
        text = (REPO / ".claude" / "skills" / "job-application-assistant" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("refused (no text to evaluate)", text)
        self.assertIn("continue down the fetch escalation in `09-web-research.md` until every rung fails", text)
        self.assertIn("tell the user the posting produced no readable text and ask them to paste it", text)
        self.assertIn("do not evaluate fit on an empty posting", text)


class OtherFrontDoors(unittest.TestCase):
    """The skill's own Step 1, /outcome's fresh-fetch archive and /interview's
    fetch fallback are the other places a posting enters; each names the gate."""

    def test_skill_step1_names_the_gate(self):
        text = (REPO / ".claude" / "skills" / "job-application-assistant" / "SKILL.md").read_text(encoding="utf-8")
        step1 = re.search(r"### Step 1: Research & Evaluate Fit(.*?)(?=### Step 2)", text, re.DOTALL).group(1)
        self.assertIn("posting_guard", step1)
        # The gate shipped in 1.3.6; any later edit bumps the marker again.
        version = re.search(r"^framework_version: (\d+)\.(\d+)\.(\d+)$", text, re.MULTILINE)
        self.assertIsNotNone(version)
        self.assertGreaterEqual(tuple(int(part) for part in version.groups()), (1, 3, 6))

    def test_outcome_fresh_fetch_is_gated(self):
        text = (REPO / ".claude" / "commands" / "outcome.md").read_text(encoding="utf-8")
        self.assertIn("posting_guard", text)

    def test_interview_fetch_fallback_is_gated(self):
        text = INTERVIEW.read_text(encoding="utf-8")
        step1 = re.search(r"## Step 1: Load the Application Context(.*?)(?=\n## Step 2)", text, re.DOTALL).group(1)
        fallback = re.search(r"2\. \*\*Fallbacks\*\*(.*?)(?=\n3\. )", step1, re.DOTALL).group(1)
        self.assertIn("posting_guard", fallback)

    def test_scratchpad_is_guarded_everywhere_the_gate_writes(self):
        for path in (APPLY, REPO / ".claude" / "agents" / "posting-fetcher.md", REPO / ".claude" / "agents" / "posting-scorer.md"):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotRegex(text, r"\$SCRATCHPAD/", "use ${SCRATCHPAD:?...} so an unset variable fails loudly")
                self.assertIn("${SCRATCHPAD:?", text)

    def test_fetch_agents_can_discover_installed_portals(self):
        for name in ("posting-fetcher.md", "posting-scorer.md"):
            text = (REPO / ".claude" / "agents" / name).read_text(encoding="utf-8")
            tools = re.search(r"^tools:\s*(.*)$", text, re.M).group(1)
            with self.subTest(agent=name):
                self.assertIn("Glob", tools, "rung 1 needs to find .agents/skills/*/SKILL.md")


class TrustBoundaryWiring(unittest.TestCase):
    def test_09_trust_boundary_names_the_gate(self):
        text = RESEARCH.read_text(encoding="utf-8")
        boundary = re.search(r"## Trust boundary(.*?)(?=\n## )", text, re.DOTALL).group(1)
        self.assertIn("posting_guard", boundary)
        self.assertIn("framework_version: 1.1.7", text)


if __name__ == "__main__":
    unittest.main()

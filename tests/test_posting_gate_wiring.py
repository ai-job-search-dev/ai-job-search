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

    def test_upskill_targeted_fetch_is_gated(self):
        """/upskill <URL> WebFetches a posting and read it straight as content;
        the Oct 18 trust-boundary line told it the text was untrusted but gave
        it no gate and no Bash to run one with. Pin the step and the grant."""
        path = REPO / ".claude" / "skills" / "upskill" / "SKILL.md"
        text = path.read_text(encoding="utf-8")
        targeted = re.search(r"### Targeted mode(.*?)(?=\n## Step 3)", text, re.DOTALL).group(1)
        self.assertIn("posting_guard", targeted)
        self.assertIn("--text", targeted)
        self.assertIn("${SCRATCHPAD:?", targeted)
        self.assertNotRegex(targeted, r"\$SCRATCHPAD/", "use ${SCRATCHPAD:?...} so an unset variable fails loudly")
        tools = re.search(r"^allowed-tools:\s*(.*)$", text, re.M).group(1)
        for spelling in ("python", "python3"):
            self.assertIn(f"Bash({spelling} tools/posting_guard.py:*)", tools,
                          "the gate is a python tool; without a scoped Bash grant the step cannot run it")

    def test_every_spec_that_webfetches_a_posting_names_the_gate(self):
        """Derived, not listed: any command or skill spec that fetches a posting
        with WebFetch must name the gate somewhere, so the next front door that
        appears fails here instead of reading raw third-party text."""
        specs = sorted(
            list((REPO / ".claude" / "commands").glob("*.md"))
            + list((REPO / ".claude" / "skills").glob("*/SKILL.md"))
        )
        fetches_posting = re.compile(r"WebFetch[^\n]{0,80}\bposting\b", re.IGNORECASE)
        # /scrape's WebSearch branch fetches a posting only to fill the listing
        # fields (title, company, URL); the text itself is read by /rank, whose
        # posting-scorer runs the gate. Every other fetch reads the posting.
        hands_off_to_rank = {".claude/skills/job-scraper/SKILL.md"}
        ungated = [
            p.relative_to(REPO).as_posix()
            for p in specs
            if p.relative_to(REPO).as_posix() not in hands_off_to_rank
            and fetches_posting.search(p.read_text(encoding="utf-8"))
            and "posting_guard" not in p.read_text(encoding="utf-8")
        ]
        self.assertIn(".claude/skills/upskill/SKILL.md", [p.relative_to(REPO).as_posix() for p in specs if fetches_posting.search(p.read_text(encoding="utf-8"))])
        self.assertEqual(ungated, [], "these specs WebFetch a posting without naming the gate")

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

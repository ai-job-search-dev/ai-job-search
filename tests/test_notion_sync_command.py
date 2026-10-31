"""Guards for the /notion-sync command spec.

The command is a markdown spec (the spec IS the implementation), so these
tests pin the invariants that would break silently: the header format that
lint_skills.py enforces, the gitignore entry that keeps the personal sync
state out of version control, and the privacy rule that forbids syncing
document content to Notion.
"""
import subprocess
import sys
import unittest
from pathlib import Path

try:
    import yaml  # noqa: F401 - only probing availability for the lint integration test
    _HAVE_YAML = True
except ImportError:
    _HAVE_YAML = False

REPO = Path(__file__).resolve().parent.parent
COMMAND = REPO / ".claude" / "commands" / "notion-sync.md"
GITIGNORE = REPO / ".gitignore"


class NotionSyncCommandSpec(unittest.TestCase):
    def test_command_file_exists_with_lint_compliant_header(self):
        self.assertTrue(COMMAND.is_file(), "command spec missing")
        first_line = COMMAND.read_text(encoding="utf-8").splitlines()[0]
        self.assertTrue(
            first_line.startswith("# /notion-sync"),
            f"header must start with '# /notion-sync' (lint_skills.py enforces it), got: {first_line!r}",
        )

    def test_command_file_is_substantive(self):
        text = COMMAND.read_text(encoding="utf-8")
        for section in ("## Step 0", "## Step 4", "## Important Rules"):
            self.assertIn(section, text, f"spec lost its {section!r} section")

    def test_personal_sync_state_is_gitignored(self):
        self.assertIn(
            "job_scraper/notion_sync.json",
            GITIGNORE.read_text(encoding="utf-8"),
            "notion_sync.json is personal state and must never be committable",
        )

    def test_step2_join_names_the_field_ranked_entries_actually_carry(self):
        # seen_jobs.json entries carry `title` (job-scraper SKILL.md schema), the
        # tracker carries `role`. "matched ... on company + role" read as a
        # lookup of `role` on the entry, which never matches, so one job became
        # two Notion rows with different Keys. The join is role-vs-title.
        text = COMMAND.read_text(encoding="utf-8")
        line = next(l for l in text.splitlines() if "matched to `job_scraper/seen_jobs.json` entries" in l)
        self.assertIn("company + title", line)
        self.assertIn("`title`", line)
        self.assertIn("`role`", line)
        self.assertNotIn("company + role", line)

    def test_privacy_rule_documents_never_sync(self):
        text = COMMAND.read_text(encoding="utf-8")
        self.assertIn(
            "never upload, attach, or embed",
            text,
            "spec lost the rule that CV/cover-letter content never syncs to Notion",
        )

    def test_expired_jobs_are_update_only_and_never_create_or_rebuild_pages(self):
        text = COMMAND.read_text(encoding="utf-8")
        step2 = text.split("## Step 2:", 1)[1].split("## Step 3:", 1)[0]
        step3 = text.split("## Step 3:", 1)[1].split("## Step 4:", 1)[0]
        step4 = text.split("## Step 4:", 1)[1].split("## Step 5:", 1)[0]
        step5 = text.split("## Step 5:", 1)[1].split("## Step 6:", 1)[0]

        self.assertIn("entries with status `expired` join the sync set regardless of score as update-only candidates", step2)
        self.assertIn("An entry selected only because it is `expired` and has no matching tracker row", step2)
        self.assertIn("If a matching tracker row exists, it remains an ordinary sync item and tracker status precedence applies.", step2)
        self.assertIn("sync set contains only expired-only entries", step3)
        self.assertIn("stop without asking where to create a database", step3)
        self.assertIn("without creating the database or writing `job_scraper/notion_sync.json`", step3)
        self.assertIn("if the job is an expired-only entry, skip it - do not create a page or write a body", step4)
        self.assertIn("Match, expired-only entry** → update **Status only** to `expired`", step4)
        self.assertIn("Never write or rebuild a body for an expired-only entry, including when `--rebuild` is set.", step5)

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

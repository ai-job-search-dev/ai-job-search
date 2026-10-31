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

    def test_key_miss_falls_back_to_an_exact_url_match_and_rekeys_the_row(self):
        # `job_key.py --audit --migrate` (#637) renames keys in seen_jobs.json, but
        # Step 4 upserted on Key alone, so the next sync created a second row for
        # every migrated job. On a Key miss, one keyed row with the same URL gets
        # the new Key; several are reported and left alone.
        text = COMMAND.read_text(encoding="utf-8")
        step4 = text.split("## Step 4:", 1)[1].split("## Step 5:", 1)[0]
        step6 = text.split("## Step 6:", 1)[1].split("## Important Rules", 1)[0]
        rules = text.split("## Important Rules", 1)[1].split("## Adapting", 1)[0]

        self.assertIn("**No match on `Key`** → fall back to the posting URL before creating anything", step4)
        self.assertIn("whose `URL` equals the job's URL exactly", step4)
        self.assertIn("**Exactly one URL match** → re-key it: set its `Key` to the job's key", step4)
        self.assertIn("**More than one URL match** → create nothing and update nothing for this job", step4)
        self.assertIn("**No URL match** → if the job is an expired-only entry, skip it", step4)
        # Hand-added rows (no Key) stay invisible, fallback included.
        self.assertIn("**and** whose `Key` is non-empty", step4)
        self.assertIn("invisible to this command, including to the URL fallback", step4)
        self.assertIn("<R> re-keyed by URL", step6)
        self.assertIn("never on fuzzy title comparison", rules)
        self.assertIn("exact match on the `URL` property", rules)

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

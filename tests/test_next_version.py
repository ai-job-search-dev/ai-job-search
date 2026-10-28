"""tools/next_version.py picks the weekly release's SemVer bump from commit messages.

Each case builds a real git history after a tag and runs the CLI the release
workflow runs, so the test covers the git range as well as the bump rule.
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "tools" / "next_version.py"
WORKFLOW = REPO / ".github" / "workflows" / "release.yml"


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


class NextVersionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        git(self.root, "init", "-b", "master")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        self.commit("feat: before the release, must not count")
        git(self.root, "tag", "v1.7.4")

    def commit(self, message: str) -> None:
        git(self.root, "commit", "--allow-empty", "-m", message)

    def run_tool(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=self.root, capture_output=True, text=True,
        )

    def next_after(self, *messages: str) -> str:
        for message in messages:
            self.commit(message)
        result = self.run_tool("v1.7.4")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_fixes_and_untyped_subjects_bump_patch(self):
        self.assertEqual(self.next_after("fix(rank): x", "freehire-search: docs", "fix: mention feat: here"), "v1.7.5")

    def test_feat_bumps_minor(self):
        self.assertEqual(self.next_after("fix: x", "feat(scrape): add a portal"), "v1.8.0")

    def test_bang_subject_bumps_major(self):
        self.assertEqual(self.next_after("feat: y", "refactor(tracker)!: rename a column"), "v2.0.0")

    def test_breaking_change_footer_bumps_major(self):
        self.assertEqual(self.next_after("fix: x\n\nBREAKING CHANGE: the tracker moved"), "v2.0.0")

    def test_feat_only_in_body_is_a_patch(self):
        self.assertEqual(self.next_after("fix: x\n\n* feat: squashed branch commit"), "v1.7.5")

    def test_empty_range_fails(self):
        result = self.run_tool("v1.7.4")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no commits", result.stderr)

    def test_non_semver_tag_fails(self):
        result = self.run_tool("v1.8")
        self.assertEqual(result.returncode, 1)
        self.assertIn("not a vMAJOR.MINOR.PATCH tag", result.stderr)

    def test_unknown_tag_fails_without_a_traceback(self):
        result = self.run_tool("v9.9.9")
        self.assertEqual(result.returncode, 1)
        self.assertIn("v9.9.9", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class ReleaseWorkflowTests(unittest.TestCase):
    """GitHub Actions can't run offline, so pin the workflow's safety properties."""

    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_release_runs_only_on_upstream(self):
        self.assertIn("if: github.repository == 'ai-job-search-dev/ai-job-search'", self.text)

    def test_write_access_is_granted_to_the_job_not_the_workflow(self):
        top_level = self.text.split("\njobs:", 1)[0]
        self.assertIn("permissions:\n  contents: read", top_level)
        self.assertIn("contents: write", self.text.split("\njobs:", 1)[1])

    def test_actions_are_sha_pinned(self):
        for line in self.text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("- uses:", "uses:")):
                ref = stripped.split("uses:", 1)[1].strip()
                self.assertRegex(ref.split("@", 1)[1].split()[0], r"^[0-9a-f]{40}$", f"action not SHA-pinned: {ref}")


if __name__ == "__main__":
    unittest.main()

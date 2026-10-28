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

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "next_version.py"


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


if __name__ == "__main__":
    unittest.main()

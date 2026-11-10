import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "check_upstream_updates.py"

TEMPLATE_URL = "https://github.com/ai-job-search-dev/ai-job-search.git"
FORK_URL = "https://github.com/octocat/ai-job-search.git"

FRAMEWORK_FILES = [
    ".claude/skills/job-application-assistant/01-candidate-profile.md",
    ".claude/skills/job-application-assistant/02-behavioral-profile.md",
    ".claude/skills/job-application-assistant/03-writing-style.md",
    ".claude/skills/job-application-assistant/04-job-evaluation.md",
    ".claude/skills/job-application-assistant/05-cv-templates.md",
    ".claude/skills/job-application-assistant/06-cover-letter-templates.md",
    ".claude/skills/job-application-assistant/07-interview-prep.md",
    ".claude/skills/job-application-assistant/08-application-forms.md",
    ".claude/skills/job-application-assistant/09-web-research.md",
    ".claude/skills/job-application-assistant/SKILL.md",
    "AGENTS.md",
]

FRONTMATTER = "---\nframework_version: 1.0.0\n---\n"


class UpstreamCheckerRepoFixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

        tools = self.root / "tools"
        tools.mkdir()
        shutil.copy(SCRIPT, tools / "check_upstream_updates.py")

        for rel in FRAMEWORK_FILES:
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(FRONTMATTER, encoding="utf-8")

        subprocess.run(["git", "init", "-b", "master"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.root, check=True, capture_output=True)

    def add_remote(self, name: str, url: str) -> None:
        subprocess.run(["git", "remote", "add", name, url], cwd=self.root, check=True, capture_output=True)

    def materialize_remote_ref(self, name: str) -> None:
        subprocess.run(
            ["git", "update-ref", f"refs/remotes/{name}/master", "HEAD"],
            cwd=self.root,
            check=True,
            capture_output=True,
        )

    def run_checker(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.root / "tools" / "check_upstream_updates.py"), "--no-fetch", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
        )

    def run_checker_with_fetch(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.root / "tools" / "check_upstream_updates.py"), *args],
            cwd=self.root,
            capture_output=True,
            text=True,
        )


class ForkWithoutUpstreamRemoteTests(UpstreamCheckerRepoFixture):
    def setUp(self):
        super().setUp()
        self.add_remote("origin", FORK_URL)
        self.materialize_remote_ref("origin")

    def test_fork_fallback_warns_that_check_is_against_own_fork(self):
        result = self.run_checker("--remote", "upstream")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Falling back to 'origin'", result.stdout)
        self.assertIn("does not point to the ai-job-search template repo", result.stdout)
        self.assertNotIn("up to date with upstream!", result.stdout)
        self.assertIn("up to date with origin/master", result.stdout)


class DirectCloneFallbackTests(UpstreamCheckerRepoFixture):
    def setUp(self):
        super().setUp()
        self.add_remote("origin", TEMPLATE_URL)
        self.materialize_remote_ref("origin")

    def test_clone_of_template_falls_back_without_fork_warning(self):
        result = self.run_checker("--remote", "upstream")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Falling back to 'origin'", result.stdout)
        self.assertNotIn("does not point to the ai-job-search template repo", result.stdout)
        self.assertIn("up to date with origin/master", result.stdout)

    def test_clone_with_lowercased_template_url_falls_back_without_fork_warning(self):
        # GitHub serves repo paths case-insensitively, so a clone from
        # https://github.com/madslorentzen/ai-job-search is still the template.
        subprocess.run(
            ["git", "remote", "set-url", "origin", TEMPLATE_URL.lower()],
            cwd=self.root,
            check=True,
            capture_output=True,
        )

        result = self.run_checker("--remote", "upstream")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Falling back to 'origin'", result.stdout)
        self.assertNotIn("does not point to the ai-job-search template repo", result.stdout)


class UpstreamRemotePresentTests(UpstreamCheckerRepoFixture):
    def setUp(self):
        super().setUp()
        self.add_remote("origin", FORK_URL)
        self.add_remote("upstream", TEMPLATE_URL)
        self.materialize_remote_ref("upstream")

    def test_explicit_upstream_remote_is_used_without_warning(self):
        result = self.run_checker()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Falling back to 'origin'", result.stdout)
        self.assertNotIn("does not point to the ai-job-search template repo", result.stdout)
        self.assertIn("up to date with upstream/master", result.stdout)


class FailedFetchTests(UpstreamCheckerRepoFixture):
    def test_cached_ref_after_failed_fetch_is_not_reported_as_current(self):
        # Normal CLI path: a fork has a previous upstream/master ref, but its
        # remote is unavailable today. The cached comparison can be useful,
        # but it cannot establish whether the fork is currently up to date.
        self.add_remote("upstream", str(self.root / "unavailable-remote"))
        self.materialize_remote_ref("upstream")

        result = self.run_checker_with_fetch()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Failed to fetch", result.stdout)
        self.assertIn("cached", result.stdout.lower())
        self.assertIn("[WARNING]", result.stdout)
        self.assertNotIn("[OK]", result.stdout)


class UpstreamRefMissingFileTests(UpstreamCheckerRepoFixture):
    """Simulates upstream renaming/deleting one framework file while the
    fork still has its own copy: git show then fails, and the checker used
    to swallow the error and report a clean '[OK]'."""

    def setUp(self):
        super().setUp()
        self.add_remote("origin", FORK_URL)
        self.add_remote("upstream", TEMPLATE_URL)

        # Upstream drops AGENTS.md (rename/delete) in a new commit.
        subprocess.run(["git", "rm", "-q", "AGENTS.md"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "drop AGENTS.md"], cwd=self.root, check=True, capture_output=True)
        self.materialize_remote_ref("upstream")

        # The fork keeps its own copy locally, so only the upstream side
        # lacks the file.
        (self.root / "AGENTS.md").write_text(FRONTMATTER, encoding="utf-8")

    def test_file_missing_upstream_is_reported_instead_of_silent_ok(self):
        result = self.run_checker()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("AGENTS.md", result.stdout)
        self.assertNotIn("[OK] All framework files are up to date", result.stdout)
        self.assertIn("[WARNING]", result.stdout)


class LegacyEncodingTests(UpstreamCheckerRepoFixture):
    def setUp(self):
        super().setUp()
        # Use the actual shipped framework files, whose UTF-8 prose git show
        # returns, rather than a constructed string passed to run_git().
        for rel in FRAMEWORK_FILES:
            shutil.copy(REPO_ROOT / rel, self.root / rel)
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "copy actual framework files"], cwd=self.root, check=True, capture_output=True)
        self.add_remote("upstream", TEMPLATE_URL)
        self.materialize_remote_ref("upstream")

    def run_legacy_checker(self, *args, ascii_git_codec=True):
        env = dict(os.environ, LC_ALL="C", LANG="C", PYTHONUTF8="0",
                   PYTHONCOERCECLOCALE="0", PYTHONIOENCODING="cp1252")
        if not ascii_git_codec:
            # Keep valid Unicode argv while independently forcing legacy stdout.
            # Linux's ASCII filesystem decoder would instead create surrogate
            # escapes, testing argument decoding rather than console output.
            env["PYTHONUTF8"] = "1"
        return subprocess.run(
            [sys.executable, str(self.root / "tools/check_upstream_updates.py"), "--no-fetch", *args],
            cwd=self.root, env=env, capture_output=True,
        )

    def test_actual_framework_prose_is_decoded_as_utf8(self):
        result = self.run_legacy_checker()
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        self.assertIn("[OK]", result.stdout.decode("utf-8"))

    def test_non_latin_branch_is_printed_as_utf8(self):
        branch = "release-版本"
        subprocess.run(["git", "update-ref", f"refs/remotes/upstream/{branch}", "HEAD"],
                       cwd=self.root, check=True, capture_output=True)
        result = self.run_legacy_checker("--branch", branch, ascii_git_codec=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
        self.assertIn(f"upstream/{branch}", result.stdout.decode("utf-8"))


class IncompleteComparisonTests(UpstreamCheckerRepoFixture):
    def setUp(self):
        super().setUp()
        self.add_remote("upstream", TEMPLATE_URL)
        self.materialize_remote_ref("upstream")

    def test_missing_local_file_does_not_report_all_files_up_to_date(self):
        (self.root / "AGENTS.md").unlink()

        result = self.run_checker()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("AGENTS.md", result.stdout)
        self.assertIn("[WARNING] Framework check incomplete", result.stdout)
        self.assertNotIn("[OK]", result.stdout)

    def test_missing_upstream_marker_does_not_report_all_files_up_to_date(self):
        agents = self.root / "AGENTS.md"
        agents.write_text("# Upstream guidelines without a version marker\n", encoding="utf-8")
        subprocess.run(["git", "add", "AGENTS.md"], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "remove upstream version marker"], cwd=self.root, check=True, capture_output=True)
        self.materialize_remote_ref("upstream")
        agents.write_text(FRONTMATTER, encoding="utf-8")

        result = self.run_checker()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("AGENTS.md", result.stdout)
        self.assertIn("[WARNING] Framework check incomplete", result.stdout)
        self.assertNotIn("[OK]", result.stdout)


if __name__ == "__main__":
    unittest.main()

"""Guards for tools/check_framework_version.py - the CI gate itself.

This gate is what stops a PR from editing a profile-bearing framework
file without bumping `framework_version` (the fork-rebase safety marker).
It ran in CI with zero tests, so a one-line mutation
(`return meaningful_changes > 0` -> `return False`) disabled it while
the whole suite stayed green (review finding F22, 2026-08-19). A broken
guard is silent by construction: nothing fails, it just stops catching.

Each test builds an isolated git repo with the script copied inside it
(the script resolves ROOT from __file__), so the real repo is never read
or written.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path, PureWindowsPath

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "check_framework_version.py"

FRONTMATTER = "---\nframework_version: 1.0.0\n---\n"
BODY = "# Test framework file\n\nOriginal guidance sentence.\n"


class CheckerRepoFixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

        tools = self.root / "tools"
        tools.mkdir()
        shutil.copy(SCRIPT, tools / "check_framework_version.py")

        self.skill_dir = self.root / ".claude" / "skills" / "job-application-assistant"
        self.skill_dir.mkdir(parents=True)
        self.framework_file = self.skill_dir / "01-test-profile.md"
        self.framework_file.write_text(FRONTMATTER + BODY, encoding="utf-8")

        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")

    def git(self, *args):
        subprocess.run(
            ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        )

    def run_checker(self):
        # Strip GitHub Actions variables so get_base_commit() takes the
        # local path (uncommitted changes vs HEAD) regardless of where the
        # test suite itself runs.
        env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_")}
        return subprocess.run(
            [sys.executable, str(self.root / "tools" / "check_framework_version.py")],
            capture_output=True,
            text=True,
            env=env,
        )


class FrameworkVersionGateTests(CheckerRepoFixture):
    def test_clean_tree_passes(self):
        result = self.run_checker()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Framework Version Check: OK", result.stdout)

    def test_unbumped_edit_fails(self):
        self.framework_file.write_text(
            FRONTMATTER + BODY + "\nA new sentence without a version bump.\n",
            encoding="utf-8",
        )

        result = self.run_checker()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("modified without bumping 'framework_version'", result.stdout)

    def test_bumped_edit_passes(self):
        bumped = FRONTMATTER.replace("1.0.0", "1.0.1")
        self.framework_file.write_text(
            bumped + BODY + "\nA new sentence with a version bump.\n",
            encoding="utf-8",
        )

        result = self.run_checker()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_same_version_reformatted_does_not_count_as_a_bump(self):
        for marker in ['framework_version: "1.0.0"', "framework_version:  1.0.0"]:
            with self.subTest(marker=marker):
                self.framework_file.write_text(
                    FRONTMATTER.replace("framework_version: 1.0.0", marker)
                    + BODY + "\nChanged guidance with the same version.\n",
                    encoding="utf-8",
                )
                result = self.run_checker()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("modified without bumping 'framework_version'", result.stdout)

    def test_reformatting_version_alone_remains_allowed(self):
        self.framework_file.write_text(
            FRONTMATTER.replace("framework_version: 1.0.0", 'framework_version: "1.0.0"') + BODY,
            encoding="utf-8",
        )
        result = self.run_checker()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_file_without_version_marker_fails(self):
        (self.skill_dir / "02-unmarked.md").write_text(
            "# No frontmatter at all\n", encoding="utf-8"
        )

        result = self.run_checker()

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("missing 'framework_version'", result.stdout)

class WindowsGitPathTests(unittest.TestCase):
    def test_diff_and_show_use_posix_paths_for_windows_checkout(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("framework_windows_paths", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        root = PureWindowsPath("C:/checkout")
        profile = root / ".claude/skills/job-application-assistant/01-test-profile.md"
        relative = ".claude/skills/job-application-assistant/01-test-profile.md"
        diff = '-framework_version: 1.0.0\n+framework_version: "1.0.0"\n-Old guidance\n+New guidance\n'
        with unittest.mock.patch.object(module, "ROOT", root), \
             unittest.mock.patch.object(module, "run_git", side_effect=[
                 (0, diff, ""), (0, FRONTMATTER + BODY, "")
             ]) as run_git, \
             unittest.mock.patch.object(module, "parse_frontmatter", return_value={
                 "framework_version": "1.0.0"
             }):
            self.assertTrue(module.has_non_trivial_changes(profile, "base"))
        self.assertEqual(run_git.call_args_list, [
            unittest.mock.call(["diff", "-U0", "base", "--", relative]),
            unittest.mock.call(["show", f"base:{relative}"]),
        ])


class RunGitEncodingTests(unittest.TestCase):
    """run_git() must decode `git diff` output as UTF-8 explicitly, not via
    whatever `subprocess.run(text=True)` falls back to on the host locale.

    On a real Windows checkout, the un-pinned call decodes as cp1252 (the
    locale default there) and a byte cp1252 leaves undefined - e.g.
    Cyrillic Ё/ё, much CJK, or Á-class Latin - in a framework file's diff
    raises UnicodeDecodeError before the gate ever evaluates the change
    (reported 2026-10-01, reproduced on Windows with Cyrillic Ё). An em
    dash does not trigger this - cp1252 maps it fine. `LC_ALL`/`LANG` don't
    influence this on Windows, so the regression is pinned directly against
    the subprocess.run() call rather than by trying to simulate the OS
    locale."""

    def test_run_git_pins_utf8_encoding(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "check_framework_version", SCRIPT
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with unittest.mock.patch.object(module.subprocess, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["git"], returncode=0, stdout="", stderr=""
            )
            module.run_git(["diff", "-U0", "HEAD"])

        _, kwargs = mock_run.call_args
        self.assertEqual(
            kwargs.get("encoding"),
            "utf-8",
            "run_git() must pass encoding=\"utf-8\" to subprocess.run() - "
            "omitting it falls back to the host locale's default codec "
            "(cp1252 on Windows), which crashes on non-ASCII diff content.",
        )


if __name__ == "__main__":
    unittest.main()



class FrontmatterLineEndings(unittest.TestCase):
    """A Windows checkout has CRLF files (core.autocrlf) and Notepad adds a
    BOM; the version marker is the same file either way."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location("check_framework_version_frontmatter", Path(__file__).resolve().parent.parent / "tools" / "check_framework_version.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_crlf_and_bom_do_not_hide_the_version_marker(self):
        text = "---\nframework_version: 1.2.7\n---\n# Title\n"
        for label, variant in (("crlf", text.replace("\n", "\r\n")), ("bom", "\ufeff" + text), ("bom+crlf", "\ufeff" + text.replace("\n", "\r\n"))):
            with self.subTest(label=label):
                self.assertEqual(self.module.parse_frontmatter_text(variant), self.module.parse_frontmatter_text(text))
                self.assertEqual(self.module.parse_frontmatter_text(text).get("framework_version"), "1.2.7")

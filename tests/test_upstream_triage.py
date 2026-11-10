import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "upstream_triage.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "upstream-watch.yml"
UPSTREAM_SLUG = "ai-job-search-dev/ai-job-search"


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


class TriageRepoFixture(unittest.TestCase):
    """Builds a real git history: a shared base, then an `upstream/master`
    ref that runs ahead, so the triage script can be exercised fully offline.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

        (self.root / "tools").mkdir()
        shutil.copy(SCRIPT, self.root / "tools" / "upstream_triage.py")
        (self.root / ".github").mkdir()

        git(self.root, "init", "-b", "master")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        git(self.root, "remote", "add", "upstream",
            f"https://github.com/{UPSTREAM_SLUG}.git")

        self.write("shared.txt", "base\n")
        self.write("kept.py", "print('hi')\n")
        self.commit("init")

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, msg: str) -> str:
        git(self.root, "add", "-A")
        git(self.root, "commit", "-m", msg)
        return git(self.root, "rev-parse", "HEAD").strip()

    def set_upstream_to_head(self) -> None:
        git(self.root, "update-ref", "refs/remotes/upstream/master", "HEAD")

    def run_triage(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.root / "tools" / "upstream_triage.py"), *args],
            cwd=self.root, capture_output=True, text=True,
        )


class UpToDateTests(TriageRepoFixture):
    def test_reports_up_to_date_when_not_behind(self):
        self.set_upstream_to_head()
        result = self.run_triage()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Up to date", result.stdout)


class RelevanceFilterTests(TriageRepoFixture):
    def test_commit_touching_only_removed_files_is_skipped(self):
        # Both start with the portal; upstream edits it while the fork drops it.
        self.write("portals/removed_portal.py", "x = 1\n")
        self.commit("shared: add portal")
        self.write("portals/removed_portal.py", "x = 2\n")
        self.commit("upstream: update portal")
        self.set_upstream_to_head()
        git(self.root, "reset", "--hard", "HEAD~1")
        git(self.root, "rm", "portals/removed_portal.py")
        self.commit("fork: remove demo portal")

        result = self.run_triage()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("touches only files not in this fork", result.stdout)
        self.assertIn("Probably skip", result.stdout)

    def test_commit_touching_kept_files_is_worth_reviewing(self):
        self.write("kept.py", "print('changed')\n")
        self.commit("upstream: change kept.py")
        self.set_upstream_to_head()
        git(self.root, "reset", "--hard", "HEAD~1")

        result = self.run_triage()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Worth reviewing", result.stdout)
        self.assertIn("kept.py", result.stdout)
        # Ready-to-run cherry-pick lines are offered, not executed.
        self.assertIn("git cherry-pick", result.stdout)

    def test_changelog_only_footprint_is_skipped(self):
        self.write("portals/gone.py", "y = 1\n")
        self.write("CHANGELOG.md", "- base\n")
        self.commit("shared: add portal and changelog")
        self.write("portals/gone.py", "y = 2\n")
        self.write("CHANGELOG.md", "- did a thing\n")
        self.commit("upstream: feature living in removed area + changelog")
        self.set_upstream_to_head()
        # Fork ships CHANGELOG.md but not the removed portal file.
        git(self.root, "reset", "--hard", "HEAD~1")
        git(self.root, "rm", "portals/gone.py")
        self.commit("fork: remove demo portal")

        result = self.run_triage()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("changelog-only footprint in this fork", result.stdout)

    def test_new_upstream_tool_is_worth_reviewing(self):
        self.write("tools/new_workflow.py", "print('new workflow')\n")
        added = self.commit("upstream: add a new workflow tool")
        self.set_upstream_to_head()
        git(self.root, "reset", "--hard", "HEAD~1")
        before = git(self.root, "status", "--porcelain")

        result = self.run_triage()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("**1** worth reviewing", result.stdout)
        self.assertIn("tools/new_workflow.py", result.stdout)
        self.assertIn(f"git cherry-pick {added}", result.stdout)
        self.assertEqual(git(self.root, "status", "--porcelain"), before)
        self.assertFalse((self.root / "tools/new_workflow.py").exists())

    def test_new_file_in_removed_directory_is_skipped(self):
        # #586's shape: upstream adds a test file to a portal the fork dropped.
        self.write("portals/gone/cli/src/cli.ts", "run()\n")
        self.commit("shared: add portal")
        self.write("portals/gone/cli/tests/new.test.ts", "test()\n")
        self.commit("upstream: add a test to the portal")
        self.set_upstream_to_head()
        git(self.root, "reset", "--hard", "HEAD~1")
        git(self.root, "rm", "-r", "portals/gone")
        self.commit("fork: remove demo portal")

        result = self.run_triage()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("**0** worth reviewing", result.stdout)
        self.assertIn("touches only files not in this fork", result.stdout)

    def test_new_directory_inside_kept_directory_is_worth_reviewing(self):
        self.write("portals/kept/cli.ts", "run()\n")
        self.commit("shared: add portal")
        self.write("portals/kept/tests/new/case.test.ts", "test()\n")
        added = self.commit("upstream: add nested tests to a kept portal")
        self.set_upstream_to_head()
        git(self.root, "reset", "--hard", "HEAD~1")

        result = self.run_triage()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("**1** worth reviewing", result.stdout)
        self.assertIn(f"git cherry-pick {added}", result.stdout)

    def test_new_tool_with_changelog_is_not_changelog_only(self):
        self.write("CHANGELOG.md", "- base\n")
        self.commit("shared: add changelog")
        self.write("tools/new_workflow.py", "print('new workflow')\n")
        self.write("CHANGELOG.md", "- new workflow tool\n")
        added = self.commit("upstream: ship a new tool and changelog")
        self.set_upstream_to_head()
        git(self.root, "reset", "--hard", "HEAD~1")

        result = self.run_triage()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("**1** worth reviewing", result.stdout)
        self.assertIn(f"git cherry-pick {added}", result.stdout)
        self.assertNotIn("changelog-only footprint in this fork", result.stdout)


class AlreadyAppliedTests(TriageRepoFixture):
    def test_cherry_picked_commit_drops_off_via_patch_id(self):
        # Upstream adds a feature commit, then a second unrelated commit.
        self.write("kept.py", "print('feature')\n")
        upstream_sha = self.commit("upstream: add feature")
        self.write("shared.txt", "upstream edit\n")
        self.commit("upstream: unrelated change")
        self.set_upstream_to_head()

        # Fork diverges (its own commit first), then cherry-picks the feature.
        # The cherry-pick lands with a DIFFERENT sha but the same patch, so
        # only patch-id matching - not raw sha - can tell it is already ported.
        git(self.root, "reset", "--hard", "HEAD~2")
        self.write("fork_only.txt", "mine\n")
        self.commit("fork: divergent commit")
        git(self.root, "cherry-pick", upstream_sha)

        result = self.run_triage()
        self.assertEqual(result.returncode, 0, result.stderr)
        # The feature dropped off via patch-id; only the unrelated commit
        # remains worth reviewing.
        self.assertIn("already applied (cherry-picked)", result.stdout)
        self.assertIn("**1** worth reviewing", result.stdout)


class SubjectCollisionTests(TriageRepoFixture):
    def test_different_patch_with_same_subject_requests_verification(self):
        self.write("kept.py", "print('upstream fix')\n")
        upstream_sha = self.commit("Fix configuration")
        self.set_upstream_to_head()
        git(self.root, "reset", "--hard", "HEAD~1")
        self.write("shared.txt", "fork configuration\n")
        fork_sha = self.commit("Fix configuration")

        result = self.run_triage()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("**0** worth reviewing", result.stdout)
        self.assertIn(f"subject matches fork commit {fork_sha[:9]}, patch differs: verify", result.stdout)
        self.assertNotIn(f"git cherry-pick {upstream_sha}", result.stdout)
        self.assertNotIn("already applied (cherry-picked)", result.stdout)

    def test_conflict_resolved_personalized_port_has_distinct_verify_reason(self):
        profile = ".claude/skills/job-application-assistant/01-candidate-profile.md"
        self.write(profile, "Name: template\nGuidance: old\n")
        self.commit("Add template profile")
        self.write(profile, "Name: template\nGuidance: improved\n")
        upstream_sha = self.commit("Improve profile guidance")
        self.set_upstream_to_head()
        git(self.root, "reset", "--hard", "HEAD~1")
        self.write(profile, "Name: personal\nGuidance: old\n")
        self.commit("Personalize profile")
        picked = subprocess.run(["git", "cherry-pick", upstream_sha], cwd=self.root,
                                capture_output=True, text=True)
        self.assertNotEqual(picked.returncode, 0, "fixture must exercise a real conflict")
        self.write(profile, "Name: personal\nGuidance: improved\n")
        git(self.root, "add", profile)
        git(self.root, "cherry-pick", "--continue")
        fork_sha = git(self.root, "rev-parse", "HEAD").strip()
        result = self.run_triage()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("**0** worth reviewing", result.stdout)
        self.assertIn(f"subject matches fork commit {fork_sha[:9]}, patch differs: verify", result.stdout)
        self.assertNotIn("already applied (cherry-picked)", result.stdout)

    def test_same_patch_with_renamed_subject_is_still_already_applied(self):
        self.write("kept.py", "print('upstream fix')\n")
        upstream_sha = self.commit("Fix configuration")
        self.set_upstream_to_head()
        git(self.root, "reset", "--hard", "HEAD~1")
        self.write("fork_only.txt", "fork\n")
        self.commit("Fork configuration")
        git(self.root, "cherry-pick", upstream_sha)
        git(self.root, "commit", "--amend", "-m", "Port upstream configuration fix")

        result = self.run_triage()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("**0** worth reviewing", result.stdout)
        self.assertIn("already applied (cherry-picked)", result.stdout)


class SuggestedCherryPickOrderTests(TriageRepoFixture):
    def test_suggested_commands_apply_dependent_changes_in_ancestry_order(self):
        self.write("kept.py", "print('first upstream change')\n")
        first = self.commit("Introduce shared behavior")
        self.write("kept.py", "print('second upstream change')\n")
        second = self.commit("Refine shared behavior")
        self.set_upstream_to_head()
        git(self.root, "reset", "--hard", "HEAD~2")
        self.write("fork_only.txt", "fork customization\n")
        self.commit("Customize fork")

        result = self.run_triage()
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = [line.split("  #", 1)[0].split() for line in result.stdout.splitlines()
                    if line.startswith("git cherry-pick ")]
        self.assertEqual(len(commands), 2, result.stdout)
        for command in commands:
            replay = subprocess.run(command, cwd=self.root, capture_output=True, text=True)
            self.assertEqual(replay.returncode, 0, replay.stdout + replay.stderr)
        self.assertEqual([command[2] for command in commands], [first, second])
        self.assertEqual((self.root / "kept.py").read_text(), "print('second upstream change')\n")


class WontPortTests(TriageRepoFixture):
    def test_listed_sha_is_excluded(self):
        self.write("kept.py", "print('rejected feature')\n")
        rejected = self.commit("upstream: feature the fork rejects")
        self.set_upstream_to_head()
        git(self.root, "reset", "--hard", "HEAD~1")
        self.write(".github/upstream-wontport.txt",
                   f"{rejected[:9]}  # rejected on purpose\n")
        self.commit("fork: won't-port list")

        result = self.run_triage()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("on the fork's won't-port list", result.stdout)


class MissingUpstreamRefTests(TriageRepoFixture):
    def test_missing_ref_degrades_gracefully(self):
        # upstream/master ref never materialized.
        result = self.run_triage()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("was not available", result.stdout)


class WorkflowGuardTests(unittest.TestCase):
    """The workflow must no-op on the upstream template, so a template clone
    never opens an issue by surprise. GitHub Actions can't run offline, so we
    pin the guard by asserting the job's `if` condition excludes upstream."""

    def test_workflow_is_guarded_against_upstream(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(f"github.repository != '{UPSTREAM_SLUG}'", text)

    def test_workflow_uses_builtin_token_only(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("GH_TOKEN: ${{ github.token }}", text)
        # A cross-repo PAT is what let an early run write outside its own repo;
        # the built-in token can't. Make sure no PAT secret sneaks back in.
        self.assertNotIn("secrets.", text)

    def test_actions_are_sha_pinned(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- uses:") or stripped.startswith("uses:"):
                ref = stripped.split("uses:", 1)[1].strip()
                self.assertIn("@", ref)
                sha = ref.split("@", 1)[1].split()[0]
                self.assertRegex(sha, r"^[0-9a-f]{40}$",
                                 f"action not SHA-pinned: {ref}")


if __name__ == "__main__":
    unittest.main()

"""Tests for tools/install_portal_clis.py - the one-command portal CLI install.

No network and no real `bun install`: discovery and the safety gate run against
a temporary skills tree, the runner is injected, and the end-to-end cases run
the script with --dry-run from a temporary checkout with no portals and with
one copied in.
"""

import io
import json
import shutil
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from install_portal_clis import discover, unsafe_reason  # noqa: E402
import install_portal_clis  # noqa: E402


def install(*args):
    with redirect_stdout(io.StringIO()):
        return install_portal_clis.install(*args)


class FakeRun:
    def __init__(self, fail=()):
        self.calls = []
        self.fail = set(fail)

    def __call__(self, argv, cwd):
        self.calls.append(Path(cwd).parent.name)
        self.argvs = getattr(self, "argvs", []) + [list(argv)]
        return subprocess.CompletedProcess(argv, 1 if Path(cwd).parent.name in self.fail else 0)


class InstallPortalClis(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def add_cli(self, name, manifest=None):
        cli = self.root / ".agents" / "skills" / name / "cli"
        cli.mkdir(parents=True)
        (cli / "package.json").write_text(json.dumps(manifest or {"name": name}), encoding="utf-8")
        return cli

    def test_discovers_every_cli_on_the_ci_glob(self):
        """A fork-added portal is found without editing any list."""
        self.add_cli("board-search")
        self.add_cli("my-market-search")
        (self.root / ".agents" / "skills" / "no-cli-skill").mkdir(parents=True)
        self.assertEqual([p.parent.name for p in discover(self.root)], ["board-search", "my-market-search"])

    def test_no_skills_tree_discovers_nothing(self):
        self.assertEqual(discover(self.root), [])

    def test_lifecycle_script_is_skipped_not_run(self):
        self.add_cli("safe-search")
        self.add_cli("borrowed-search", {"scripts": {"postinstall": "curl x | sh"}})
        run = FakeRun()
        self.assertEqual(install(discover(self.root), "bun", False, self.root, run), 1)
        self.assertEqual(run.calls, ["safe-search"])

    def test_trusted_dependencies_is_unsafe(self):
        self.assertIn("trustedDependencies", unsafe_reason(self.add_cli("x", {"trustedDependencies": ["y"]})))

    # GHSA-wq7j-x5jq-pjrw: the gate read only the top-level manifest, but a
    # `workspaces` entry pulls in sub-packages with their own lifecycle scripts
    # (a workspace postinstall ran under Bun 1.3.10), and a bunfig.toml beside
    # the manifest reconfigures the install itself (registry, scopes, cache).
    def test_workspaces_is_unsafe(self):
        self.assertIn("workspaces", unsafe_reason(self.add_cli("x", {"workspaces": ["packages/*"]})))

    def test_bunfig_is_unsafe(self):
        cli = self.add_cli("x")
        (cli / "bunfig.toml").write_text('[install]\nregistry = "https://registry.example"\n', encoding="utf-8")
        self.assertIn("bunfig.toml", unsafe_reason(cli))

    def test_npmrc_is_unsafe(self):
        # Bun reads .npmrc too: a registry line beside the manifest fetches
        # every dependency from a server the portal's author picked.
        cli = self.add_cli("x")
        (cli / ".npmrc").write_text("registry=https://registry.example/\n", encoding="utf-8")
        self.assertIn(".npmrc", unsafe_reason(cli))

    def test_bun_install_never_runs_lifecycle_scripts(self):
        # #718: --ignore-scripts on every install, so a dependency's hook stays
        # off whatever the Bun release's default trust list says.
        self.add_cli("a-search")
        run = FakeRun()
        self.assertEqual(install(discover(self.root), "bun", False, self.root, run), 0)
        self.assertEqual(run.argvs, [["bun", "install", "--ignore-scripts"]])

    def test_one_failure_does_not_stop_the_rest(self):
        for name in ("a-search", "b-search", "c-search"):
            self.add_cli(name)
        run = FakeRun(fail={"b-search"})
        self.assertEqual(install(discover(self.root), "bun", False, self.root, run), 1)
        self.assertEqual(run.calls, ["a-search", "b-search", "c-search"])

    def test_dry_run_runs_nothing(self):
        self.add_cli("a-search")
        run = FakeRun()
        self.assertEqual(install(discover(self.root), "bun", True, self.root, run), 0)
        self.assertEqual(run.calls, [])

    def run_script(self):
        tools = self.root / "tools"
        tools.mkdir(exist_ok=True)
        for name in ("install_portal_clis.py", "security_guards.py"):
            shutil.copy(REPO_ROOT / "tools" / name, tools / name)
        return subprocess.run(
            [sys.executable, str(tools / "install_portal_clis.py"), "--dry-run"],
            capture_output=True, text=True, encoding="utf-8",
        )

    def test_cli_with_no_portals_installed_succeeds(self):
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("nothing to install", proc.stdout)

    def test_cli_dry_run_lists_a_copied_in_portal(self):
        self.add_cli("board-search")
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("would install  board-search", proc.stdout)


class FindBun(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_find_bun_prefers_path(self):
        import shutil
        orig_which = shutil.which
        shutil.which = lambda name: "/custom/bin/bun" if name == "bun" else None
        self.addCleanup(setattr, shutil, "which", orig_which)

        self.assertEqual(install_portal_clis.find_bun(), "/custom/bin/bun")

    def test_find_bun_falls_back_to_candidate_directory(self):
        import shutil
        orig_which = shutil.which
        shutil.which = lambda name: None
        self.addCleanup(setattr, shutil, "which", orig_which)

        binary = "bun.exe" if sys.platform == "win32" else "bun"
        fake_bin = self.root / ".bun" / "bin"
        fake_bin.mkdir(parents=True)
        fake_bun = fake_bin / binary
        fake_bun.write_text("#!/bin/sh\n", encoding="utf-8")
        if sys.platform != "win32":
            fake_bun.chmod(0o755)

        found = install_portal_clis.find_bun(candidate_dirs=(str(fake_bin),))
        self.assertEqual(found, str(fake_bun))

    def test_find_bun_returns_none_when_missing(self):
        import shutil
        orig_which = shutil.which
        shutil.which = lambda name: None
        self.addCleanup(setattr, shutil, "which", orig_which)

        self.assertIsNone(install_portal_clis.find_bun(candidate_dirs=(str(self.root / "empty"),)))

    def test_missing_bun_message_suggests_export(self):
        output = io.StringIO()
        orig_find = install_portal_clis.find_bun
        install_portal_clis.find_bun = lambda *args, **kwargs: None
        self.addCleanup(setattr, install_portal_clis, "find_bun", orig_find)
        orig_discover = install_portal_clis.discover
        install_portal_clis.discover = lambda: [self.root / "board-search" / "cli"]
        self.addCleanup(setattr, install_portal_clis, "discover", orig_discover)

        with redirect_stdout(output):
            rc = install_portal_clis.main([])
        self.assertEqual(rc, 1)
        text = output.getvalue()
        self.assertIn("bun was not found on PATH", text)
        self.assertIn("export PATH=", text)


if __name__ == "__main__":
    unittest.main()

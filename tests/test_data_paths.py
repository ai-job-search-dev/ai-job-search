"""Where the user's data lives: one resolver, shared by every checkout.

The state tools used to derive their paths from their own location, so a
session in a linked git worktree read and wrote an empty copy of the workspace
while the real history sat in the main checkout. `tools/data_paths.py` resolves
one data root instead:

  1. `JOB_SEARCH_DATA_DIR`, when set
  2. `user_data/` in the MAIN checkout, when it exists - found from a linked
     worktree through its `.git` pointer file
  3. the checkout itself (the layout before this module existed)
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "data_paths.py"

spec = importlib.util.spec_from_file_location("data_paths", TOOL)
data_paths = importlib.util.module_from_spec(spec)
spec.loader.exec_module(data_paths)


def make_main(tmp: Path) -> Path:
    main = tmp / "main"
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    return main


def make_worktree(tmp: Path, main: Path) -> Path:
    """A linked worktree as git lays it out: `.git` is a pointer file."""
    gitdir = main / ".git" / "worktrees" / "wt"
    (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    worktree = tmp / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {gitdir.as_posix()}\n", encoding="utf-8")
    return worktree


class DataRoot(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.main = make_main(self.tmp)
        self.worktree = make_worktree(self.tmp, self.main)

    def test_falls_back_to_the_checkout_without_user_data(self):
        self.assertEqual(data_paths.data_root(self.main, env={}), self.main)
        self.assertEqual(data_paths.data_root(self.worktree, env={}), self.worktree)

    def test_main_checkout_uses_its_user_data(self):
        (self.main / "user_data").mkdir()
        self.assertEqual(data_paths.data_root(self.main, env={}), self.main / "user_data")

    def test_linked_worktree_shares_the_main_checkouts_user_data(self):
        (self.main / "user_data").mkdir()
        self.assertEqual(data_paths.data_root(self.worktree, env={}), self.main / "user_data")

    def test_a_worktrees_own_user_data_is_not_used(self):
        (self.worktree / "user_data").mkdir()
        self.assertEqual(data_paths.data_root(self.worktree, env={}), self.worktree)

    def test_env_override_wins(self):
        (self.main / "user_data").mkdir()
        elsewhere = self.tmp / "cloud"
        env = {"JOB_SEARCH_DATA_DIR": str(elsewhere)}
        self.assertEqual(data_paths.data_root(self.worktree, env=env), elsewhere)

    def test_unreadable_git_pointer_falls_back_to_the_checkout(self):
        (self.worktree / ".git").write_text("not a pointer\n", encoding="utf-8")
        (self.main / "user_data").mkdir()
        self.assertEqual(data_paths.data_root(self.worktree, env={}), self.worktree)

    def test_init_creates_user_data_in_the_main_checkout(self):
        created = data_paths.init(self.worktree, env={})
        self.assertEqual(created, self.main / "user_data")
        self.assertTrue(created.is_dir())


    def test_init_creates_the_override_it_will_then_resolve(self):
        env = {"JOB_SEARCH_DATA_DIR": str(self.tmp / "cloud")}
        created = data_paths.init(self.worktree, env=env)
        self.assertTrue(created.is_dir())
        self.assertEqual(created, data_paths.data_root(self.worktree, env=env))
        self.assertFalse((self.main / "user_data").exists())


    def test_init_refuses_an_override_whose_parent_is_missing(self):
        # A typo or an unmounted cloud folder must fail, not grow a wrong tree.
        env = {"JOB_SEARCH_DATA_DIR": str(self.tmp / "unmounted" / "jobs")}
        with self.assertRaises(FileNotFoundError):
            data_paths.init(self.worktree, env=env)
        self.assertFalse((self.tmp / "unmounted").exists())


class StateToolsFollowTheDataRoot(unittest.TestCase):
    """The tools resolve their files at import, so check them in a subprocess."""

    def test_state_and_tracker_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "JOB_SEARCH_DATA_DIR": tmp}
            code = (
                "import rank_state, job_key, scrape_state\n"
                "print(rank_state.STATE); print(rank_state.TRACKER); print(job_key.STATE)"
            )
            out = subprocess.run(
                [sys.executable, "-c", code], cwd=ROOT / "tools", env=env,
                capture_output=True, text=True, check=True,
            ).stdout.split("\n")
            base = Path(tmp).resolve()
            self.assertEqual(Path(out[0]), base / "job_scraper" / "seen_jobs.json")
            self.assertEqual(Path(out[1]), base / "job_search_tracker.csv")
            self.assertEqual(Path(out[2]), base / "job_scraper" / "seen_jobs.json")

    def test_cli_prints_the_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "JOB_SEARCH_DATA_DIR": tmp}
            out = subprocess.run(
                [sys.executable, str(TOOL)], env=env, capture_output=True, text=True, check=True
            ).stdout.strip()
            self.assertEqual(Path(out), Path(tmp).resolve())


class UserDataIsNeverTracked(unittest.TestCase):
    def test_gitignore_covers_user_data(self):
        lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("/user_data/", lines)


if __name__ == "__main__":
    unittest.main()

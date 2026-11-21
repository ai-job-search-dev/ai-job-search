#!/usr/bin/env python3
"""Resolve where the user's data lives, the same way from every checkout.

The repo is the plugin; the profile, scrape history, tracker, CVs and letters
are the user's. Resolution order:

  1. `JOB_SEARCH_DATA_DIR`, when set (a cloud-synced folder, a private repo)
  2. `user_data/` in the MAIN checkout, when it exists - a linked git worktree
     finds it through its `.git` pointer file, so every worktree shares it
  3. the checkout itself: the layout before this module existed

Inside the data root the paths are the ones the specs already name
(`job_scraper/seen_jobs.json`, `job_search_tracker.csv`, `cv/`, ...).

Usage:
  python3 tools/data_paths.py          print the data root
  python3 tools/data_paths.py --init   create the data root (user_data/ in the main checkout,
                                       or JOB_SEARCH_DATA_DIR when set) and print it
"""

import os
import sys
from pathlib import Path

ENV = "JOB_SEARCH_DATA_DIR"
DIR_NAME = "user_data"
REPO = Path(__file__).resolve().parent.parent


def main_checkout(repo: Path = REPO) -> Path:
    """The main checkout's root: `repo` itself unless it is a linked worktree."""
    pointer = repo / ".git"
    if not pointer.is_file():
        return repo
    try:
        # `.git` holds "gitdir: <main>/.git/worktrees/<name>"; that directory's
        # `commondir` file points back at the shared `.git`.
        gitdir = repo / pointer.read_text(encoding="utf-8").split("gitdir:", 1)[1].strip()
        common = gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip()
        return common.resolve().parent
    except (OSError, IndexError, UnicodeDecodeError):
        return repo


def data_root(repo: Path = REPO, env=os.environ) -> Path:
    override = env.get(ENV)
    if override:
        return Path(override).expanduser().resolve()
    shared = main_checkout(repo) / DIR_NAME
    return shared if shared.is_dir() else repo


def init(repo: Path = REPO, env=os.environ) -> Path:
    """Create the root `data_root` will then return: the override when set.

    Only the last directory is created: a typo or an unmounted cloud folder in
    the override must fail, not grow a wrong tree."""
    override = env.get(ENV)
    target = Path(override).expanduser().resolve() if override else main_checkout(repo) / DIR_NAME
    target.mkdir(exist_ok=True)
    return target


if __name__ == "__main__":
    if sys.argv[1:] not in ([], ["--init"]):
        sys.exit(__doc__)
    if not sys.argv[1:]:
        print(data_root())
    else:
        try:
            print(init())
        except OSError as exc:
            sys.exit(f"data_paths: cannot create the data root: {exc}")

#!/usr/bin/env python3
"""Check for framework updates in the upstream repository.

Usage: python tools/check_upstream_updates.py [--remote <remote-name>] [--branch <branch-name>]

This script:
1. Identifies the upstream remote (defaults to 'upstream', falls back to 'origin').
2. Fetches the latest commits from the upstream remote.
3. Compares the 'framework_version' in your local files under
   .claude/skills/job-application-assistant/ with those in the upstream remote.
4. Alerts you if a file has been updated upstream with a newer version.
"""

from __future__ import annotations
import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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

UPSTREAM_REPO_SLUG = "ai-job-search-dev/ai-job-search"

def run_git(args: list[str]) -> tuple[int, str, str]:
    res = subprocess.run(
        ["git"] + args, cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return res.returncode, res.stdout, res.stderr

def get_remote_url(remote_name: str) -> str:
    rc, stdout, _ = run_git(["remote", "get-url", remote_name])
    return stdout.strip() if rc == 0 else ""

def get_framework_version_from_text(text: str) -> str | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    for line in text[4:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            if k.strip() == "framework_version":
                return v.strip().strip('"').strip("'")
    return None

def parse_semver(version_str: str) -> tuple[int, int, int]:
    # Clean version string (e.g. remove 'v' prefix)
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", version_str)
    if not match:
        return (0, 0, 0)
    return tuple(int(x) for x in match.groups())

def _force_utf8_output() -> None:
    """Use UTF-8 for piped output even on legacy-console hosts."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8")


def main() -> int:
    _force_utf8_output()
    parser = argparse.ArgumentParser(description="Check for framework updates upstream.")
    parser.add_argument("--remote", default="upstream", help="Name of the git remote for upstream (default: upstream)")
    parser.add_argument("--branch", default="master", help="Branch name of the upstream repo (default: master)")
    parser.add_argument("--no-fetch", action="store_true", help="Skip fetching from remote")
    args = parser.parse_args()

    # Verify remote exists
    rc, stdout, _ = run_git(["remote"])
    remotes = stdout.splitlines()
    remote = args.remote
    if remote not in remotes:
        if "origin" in remotes:
            print(f"Warning: Remote '{remote}' not found. Falling back to 'origin'.")
            remote = "origin"
        else:
            print("Error: No git remotes found.")
            return 1

    # A fork's own 'origin' can never reveal upstream updates: warn so the
    # user is not misled by the final '[OK]' line below. (Direct clones of
    # the template repo have origin == the upstream repo, so no warning.)
    # GitHub serves repo paths case-insensitively, so compare lowercased.
    if remote != args.remote and UPSTREAM_REPO_SLUG.lower() not in get_remote_url(remote).lower():
        print(
            f"Warning: Remote '{remote}' does not point to the ai-job-search "
            f"template repo ({UPSTREAM_REPO_SLUG}), so this check compares your "
            f"fork against itself and will never report upstream updates. "
            f"Add the template repo as a remote to track upstream changes, e.g.:\n"
            f"  git remote add upstream https://github.com/{UPSTREAM_REPO_SLUG}.git"
        )

    fetch_failed = False
    if not args.no_fetch:
        print(f"Fetching latest from remote '{remote}'...")
        rc, _, stderr = run_git(["fetch", remote])
        if rc != 0:
            fetch_failed = True
            print(f"Warning: Failed to fetch from remote '{remote}': {stderr.strip()}")
            print("Proceeding with cached remote tracking branches.")

    ref = f"{remote}/{args.branch}"
    # Verify ref exists
    rc, _, _ = run_git(["rev-parse", "--verify", ref])
    if rc != 0:
        print(f"Error: Ref '{ref}' does not exist. Make sure you fetched and specified the correct branch.")
        return 1

    print(f"Comparing local files against upstream '{ref}'...\n")
    
    updates_available = []
    errors = []
    missing_upstream = []

    for rel_path in FRAMEWORK_FILES:
        local_path = ROOT / rel_path
        if not local_path.exists():
            errors.append(f"Local file missing: {rel_path}")
            continue

        # Get local version
        local_text = local_path.read_text(encoding="utf-8")
        local_ver = get_framework_version_from_text(local_text)
        
        # Get upstream version
        rc, upstream_text, git_err = run_git(["show", f"{ref}:{rel_path}"])
        if rc != 0:
            # A file present locally but missing from the upstream ref means
            # it was renamed or deleted upstream; any other git failure means
            # the comparison is incomplete. Either way, never report a clean
            # '[OK]' while silently skipping the file.
            if "does not exist" in git_err or "exists on disk, but not in" in git_err:
                missing_upstream.append(rel_path)
            else:
                errors.append(f"Failed to read upstream version of {rel_path}: {git_err.strip()}")
            continue
            
        upstream_ver = get_framework_version_from_text(upstream_text)
        
        if not local_ver:
            errors.append(f"Local file {rel_path} is missing 'framework_version' in frontmatter.")
            continue
        if not upstream_ver:
            errors.append(f"Upstream file {rel_path} is missing 'framework_version' in frontmatter.")
            continue
            
        if parse_semver(upstream_ver) > parse_semver(local_ver):
            updates_available.append({
                "filename": Path(rel_path).name,
                "local": local_ver,
                "upstream": upstream_ver,
                "path": rel_path
            })


    if errors:
        print("Configuration errors:")
        for err in errors:
            print(f"  - {err}")
        print()

    if missing_upstream:
        print("Files present locally but missing from the upstream ref (possibly renamed or deleted upstream):")
        for path in missing_upstream:
            print(f"  - {path}")
        print()

    if updates_available:
        print("[UPDATE] Upstream updates available for framework methodology files:")
        for up in updates_available:
            print(f"  - {up['filename']}: local {up['local']} < upstream {up['upstream']}")
            print(f"    Diff command: git diff {ref} -- {up['path']}")
            print()
        print("Review these changes to see if they fit your personalized fork!")
    else:
        if errors or missing_upstream:
            print(
                f"[WARNING] Framework check incomplete against {ref}: "
                f"{len(errors)} configuration error(s), {len(missing_upstream)} file(s) missing upstream. "
                "Review the messages above before assuming you are up to date."
            )
        elif not fetch_failed:
            print(f"[OK] All framework files are up to date with {ref}!")
    if fetch_failed:
        print(
            f"[WARNING] Compared against cached {ref} because the fetch failed; "
            "the current upstream state could not be checked."
        )
    # Version stamps answer "which of my files changed"; commit-level triage
    # answers "which upstream commits deserve review". Point at the companion.
    print(
        f"\nFor commit-level triage of upstream commits, run: "
        f"python3 tools/upstream_triage.py --remote {shlex.quote(remote)} --branch {shlex.quote(args.branch)}"
    )
    return 0

if __name__ == "__main__":
    sys.exit(main())

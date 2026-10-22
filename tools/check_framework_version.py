#!/usr/bin/env python3
"""CI check: ensure that modified framework files have updated version markers.

Fails if any markdown file under .claude/skills/job-application-assistant/ is
modified in git without a change/bump to its 'framework_version' frontmatter key.
Also ensures all framework files have a valid 'framework_version' frontmatter key.
"""

from __future__ import annotations
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / ".claude/skills/job-application-assistant"
FRAMEWORK_FILES = sorted(SKILL_DIR.glob("*.md"))

# Add root AGENTS.md if it exists
root_agents = ROOT / "AGENTS.md"
if root_agents.exists():
    FRAMEWORK_FILES.append(root_agents)

def run_git(args: list[str]) -> tuple[int, str, str]:
    res = subprocess.run(
        ["git"] + args,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return res.returncode, res.stdout, res.stderr

def get_base_commit() -> str | None:
    # If in GitHub Actions PR, use the target branch's base ref
    base_ref = os.environ.get("GITHUB_BASE_REF")
    if base_ref:
        # Check if origin/base_ref is fetched
        rc, _, _ = run_git(["rev-parse", "--verify", f"origin/{base_ref}"])
        if rc == 0:
            return f"origin/{base_ref}"
        # Try without origin/
        rc, _, _ = run_git(["rev-parse", "--verify", base_ref])
        if rc == 0:
            return base_ref

    # If running in GitHub Actions but not a PR (e.g. push to master)
    if os.environ.get("GITHUB_ACTIONS"):
        rc, _, _ = run_git(["rev-parse", "--verify", "HEAD~1"])
        if rc == 0:
            return "HEAD~1"

    # Otherwise (running locally), check uncommitted changes against HEAD
    rc, _, _ = run_git(["rev-parse", "--verify", "HEAD"])
    if rc == 0:
        return "HEAD"

    return None

def parse_frontmatter(path: Path) -> dict:
    if not path.exists():
        return {}
    return parse_frontmatter_text(path.read_text(encoding="utf-8"))

def parse_frontmatter_text(text: str) -> dict:
    # A Windows checkout (core.autocrlf) has CRLF files and Notepad adds a
    # BOM; neither changes the frontmatter, so neither may hide the marker.
    text = text.lstrip("\ufeff").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    
    # Simple parser for YAML/frontmatter
    data = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            data[k.strip()] = v.strip().strip('"').strip("'")
    return data

def has_non_trivial_changes(file_path: Path, base_commit: str) -> bool:
    # Get diff of the file from base_commit to HEAD
    rel_path = file_path.relative_to(ROOT).as_posix()
    rc, stdout, stderr = run_git(["diff", "-U0", base_commit, "--", rel_path])
    if rc != 0:
        # If diff fails (e.g. file is new/untracked), it's a change
        return True
    
    # Parse diff lines
    # We want to count lines added/removed that:
    # - do not match framework_version line
    # - are not empty/whitespace only
    meaningful_changes = 0
    version_changed = False
    
    for line in stdout.splitlines():
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+") or line.startswith("-"):
            content = line[1:].strip()
            if not content:
                continue
            if re.match(r"^framework_version\s*:", content):
                version_changed = True
                continue
            # Check if it's just frontmatter syntax (e.g. ---)
            if content == "---":
                continue
            meaningful_changes += 1
            
    # Editing a version line is not necessarily a bump: YAML quoting and
    # whitespace can change while the parsed marker stays the same.
    if version_changed:
        rc, base_text, _ = run_git(["show", f"{base_commit}:{rel_path}"])
        if rc != 0 or (
            parse_frontmatter_text(base_text).get("framework_version")
            != parse_frontmatter(file_path).get("framework_version")
        ):
            return False
        
    # If there are meaningful changes but the version was not changed
    return meaningful_changes > 0

def main() -> int:
    errors = []
    
    # 1. Lint: Check that all framework files have framework_version in frontmatter
    for path in FRAMEWORK_FILES:
        rel_path = path.relative_to(ROOT).as_posix()
        fm = parse_frontmatter(path)
        if "framework_version" not in fm:
            errors.append(f"{rel_path}: missing 'framework_version' in frontmatter")
            
    # 2. Check for missing version bumps in modified files
    base_commit = get_base_commit()
    if base_commit:
        print(f"Comparing HEAD against base commit: {base_commit}")
        for path in FRAMEWORK_FILES:
            rel_path = path.relative_to(ROOT).as_posix()
            if "framework_version" not in parse_frontmatter(path):
                # Skip checking changes if it doesn't even have frontmatter (already reported above)
                continue
            if has_non_trivial_changes(path, base_commit):
                errors.append(
                    f"{rel_path}: modified without bumping 'framework_version'. "
                    f"Please update the version in the frontmatter."
                )
    else:
        print("No base commit found (e.g. initial commit or shallow clone without base branch). Skipping diff checks.")

    if errors:
        print("Framework Version Check Failed:")
        for err in errors:
            print(f"  - {err}")
        return 1
        
    print("Framework Version Check: OK")
    return 0

if __name__ == "__main__":
    sys.exit(main())

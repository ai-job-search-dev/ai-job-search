#!/usr/bin/env python3
"""Print the next SemVer release tag from the commits merged since the last one.

The weekly release workflow (.github/workflows/release.yml) calls this to pick
the version; run it locally to preview what Monday's release will be.

The rule is the Conventional Commits one: across every commit in the range the
biggest bump wins.

- major: a `type!:` / `type(scope)!:` subject, or a `BREAKING CHANGE:` footer
- minor: a `feat:` / `feat(scope):` subject
- patch: anything else, including subjects without a type prefix

Usage: python3 tools/next_version.py <last-tag> [<rev>]   (rev defaults to HEAD)
Prints e.g. `v1.8.0`. Exits 1 if <last-tag> is not vMAJOR.MINOR.PATCH or the
range has no commits.
"""
from __future__ import annotations

import re
import subprocess
import sys

TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
BREAKING_SUBJECT = re.compile(r"^[a-z]+(\([^)]*\))?!:")
BREAKING_FOOTER = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)
FEAT_SUBJECT = re.compile(r"^feat(\([^)]*\))?:")


def next_version(last: str, messages: list[str]) -> str:
    major, minor, patch = map(int, TAG.match(last).groups())
    subjects = [m.split("\n", 1)[0] for m in messages]
    if any(BREAKING_SUBJECT.match(s) for s in subjects) or any(BREAKING_FOOTER.search(m) for m in messages):
        return f"v{major + 1}.0.0"
    if any(FEAT_SUBJECT.match(s) for s in subjects):
        return f"v{major}.{minor + 1}.0"
    return f"v{major}.{minor}.{patch + 1}"


def main(argv: list[str]) -> int:
    if len(argv) not in (1, 2):
        print("usage: python3 tools/next_version.py <last-tag> [<rev>]", file=sys.stderr)
        return 1
    last, rev = argv[0], (argv[1] if len(argv) == 2 else "HEAD")
    if not TAG.match(last):
        print(f"'{last}' is not a vMAJOR.MINOR.PATCH tag", file=sys.stderr)
        return 1
    log = subprocess.run(
        ["git", "log", "--format=%B%x00", f"{last}..{rev}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    ).stdout
    messages = [m.strip() for m in log.split("\0") if m.strip()]
    if not messages:
        print(f"no commits in {last}..{rev}", file=sys.stderr)
        return 1
    print(next_version(last, messages))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

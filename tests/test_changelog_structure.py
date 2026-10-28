"""CHANGELOG.md is frozen at 1.7.4; releases after it are documented in GitHub Releases.

Every PR used to add an entry under `## [Unreleased]`, and PRs opened before the
freeze still carry one. Most of those entries sit mid-section, so git merges
them cleanly into the frozen 1.7.4 section - a heading check would not notice.
Pinning the file's content does: any edit fails here and points the author at
the PR title, which is what the release notes are generated from.
"""
import hashlib
import unittest
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
FROZEN_SHA256 = "c45bc7d6c6979a188544b506e454ef7a7d55ccd56c3ccec9f5f02b2b457f8a22"


class FrozenChangelogTests(unittest.TestCase):
    def test_changelog_is_unchanged(self):
        # Line endings normalized so a CRLF checkout on Windows hashes the same.
        content = CHANGELOG.read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(
            hashlib.sha256(content).hexdigest(), FROZEN_SHA256,
            "CHANGELOG.md is frozen at 1.7.4: drop your edit and describe the change in the PR title "
            "instead (release notes are generated from PR titles). A deliberate fix to the frozen text "
            "updates FROZEN_SHA256 in the same PR.",
        )


if __name__ == "__main__":
    unittest.main()

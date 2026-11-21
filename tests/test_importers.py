#!/usr/bin/env python3
"""Tests for the LinkedIn and GitHub profile importers."""
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools import import_github, import_linkedin
from tools.profile_schema import CandidateIdentity, CareerPreferences, MasterProfile

PROFILE_CSV = (
    "First Name,Last Name,Maiden Name,Address,Birth Date,Headline,Summary,Industry,Zip Code,Geo Location,"
    "Twitter Handles,Websites,Instant Messengers\n"
    'Ada,Example,,,May 19,Quantum R&D,"Line one.\n\nLine two.",,,"Tandil, Argentina",,,\n'
)
POSITIONS_CSV = (
    "Company Name,Title,Description,Location,Started On,Finished On\n"
    '"Acme, Inc",Engineer,"Built APIs\nCut latency 20%",Remote,Mar 2022,\n'
    "Old Co,Intern,Did things,Remote,Jan 2020,Dec 2020\n"
)
SKILLS_CSV = "Name\nPython\nTypeScript\n"


def empty_profile() -> MasterProfile:
    return MasterProfile(
        identity=CandidateIdentity(name="X", email="x@example.com", location="Y"),
        summary="s", preferences=CareerPreferences(),
    )


def make_zip(files, nested=False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text.encode("utf-8-sig"))
    if not nested:
        return buf.getvalue()
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as zf:
        zf.writestr("inner.zip", buf.getvalue())
    return outer.getvalue()


class LinkedInImportTests(unittest.TestCase):
    def _tables(self, nested=False):
        with tempfile.TemporaryDirectory() as tmp:
            z = Path(tmp) / "export.zip"
            z.write_bytes(make_zip(
                {"Profile.csv": PROFILE_CSV, "Positions.csv": POSITIONS_CSV, "Skills.csv": SKILLS_CSV, "other.csv": "a\n1"},
                nested=nested))
            return import_linkedin.read_export(z)

    def test_reads_zip_and_nested_zip_ignoring_unknown_files(self):
        for nested in (False, True):
            tables = self._tables(nested)
            self.assertEqual(set(tables), {"Profile", "Positions", "Skills"})

    def test_merge_profile_positions_skills(self):
        profile = empty_profile()
        import_linkedin.merge_profile(profile, self._tables())
        self.assertEqual(profile.identity.name, "Ada Example")
        self.assertEqual(profile.identity.title, "Quantum R&D")
        self.assertEqual(profile.summary, "Line one. Line two.")
        current = next(e for e in profile.experience if e.company == "Acme, Inc")
        self.assertTrue(current.is_current)
        self.assertEqual((current.start_date, current.end_date), ("2022-03", "Present"))
        self.assertEqual(current.achievements, ["Built APIs", "Cut latency 20%"])
        old = next(e for e in profile.experience if e.company == "Old Co")
        self.assertEqual((old.start_date, old.end_date, old.is_current), ("2020-01", "2020-12", False))
        self.assertEqual([s.name for s in profile.skills], ["Python", "TypeScript"])

    def test_merge_is_idempotent(self):
        profile = empty_profile()
        tables = self._tables()
        import_linkedin.merge_profile(profile, tables)
        before = profile.model_dump()
        self.assertEqual(
            [l for l in import_linkedin.merge_profile(profile, tables) if not l.startswith("profile:")], [])
        self.assertEqual(profile.model_dump(), before)

    def test_to_period(self):
        self.assertEqual(import_linkedin.to_period("Sep 2026"), "2026-09")
        self.assertEqual(import_linkedin.to_period("2019"), "2019")
        self.assertEqual(import_linkedin.to_period(""), "")

    def test_cli_requires_known_csvs(self):
        with tempfile.TemporaryDirectory() as tmp:
            junk = Path(tmp) / "junk.csv"
            junk.write_text("a\n1")
            self.assertEqual(import_linkedin.main([str(junk), "--profile", str(Path(tmp) / "p.yaml")]), 2)
            self.assertEqual(import_linkedin.main([str(Path(tmp) / "missing.zip")]), 2)


class GitHubImportTests(unittest.TestCase):
    REPOS = [
        {"name": "good", "description": "A thing", "language": "Python", "topics": ["qiskit", "Python"],
         "html_url": "https://github.com/u/good", "fork": False, "archived": False},
        {"name": "forked", "description": "x", "language": "Go", "fork": True, "html_url": "u1"},
        {"name": "old", "description": "x", "language": "Go", "archived": True, "html_url": "u2"},
        {"name": "bare", "description": None, "language": None, "html_url": "u3"},
    ]

    def test_selection_skips_forks_archived_and_empty(self):
        self.assertEqual([r["name"] for r in import_github.select_repos(self.REPOS)], ["good"])
        self.assertIn("bare", [r["name"] for r in import_github.select_repos(self.REPOS, include_empty=True)])

    def test_technologies_are_deduplicated_from_language_and_topics(self):
        project = import_github.repo_to_project(self.REPOS[0])
        self.assertEqual(project.technologies, ["Python", "qiskit"])

    def test_merge_skips_known_projects(self):
        profile = empty_profile()
        repos = import_github.select_repos(self.REPOS)
        self.assertEqual(import_github.merge_projects(profile, repos), ["good"])
        self.assertEqual(import_github.merge_projects(profile, repos), [])


if __name__ == "__main__":
    unittest.main()

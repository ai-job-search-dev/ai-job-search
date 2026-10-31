"""Guards for /reset's two scopes: documents and profile.

Both scopes have the same failure mode - /reset promises a clean slate it
does not deliver, because something that writes personal data is missing
from the Step 1 preview the user confirms and from the Step 3 execution.

Documents scope: /reset ends its documents pass by telling the user "The
`documents/` folder is now empty." That statement is only true if every
personal-data drop folder is actually covered by both the Step 1 preview
and the Step 3 delete block. `documents/postings/` was missing from both
while being documented in documents/README.md and protected as personal
data by tools/security_guards.py (review finding F26, 2026-08-19), so a
reset silently kept the user's hand-pasted job postings.

Profile scope: the same class of gap, one scope over. /setup Step 3
populates six skill files, and /reset profile cleared four of them -
`04-job-evaluation.md` (the user's match areas, career goals, financial
situation and schedule constraints) was listed by name as containing
"framework rules, not candidate data", and `job-scraper/search-queries.md`
(their role titles, city and commute tiers) appeared nowhere in reset.md.
Both are tracked and unignored, and CI's placeholder-integrity job guards
04-job-evaluation.md under "personal data may have been committed", so a
"blank" profile left /rank scoring against the old skills and /scrape
running the old city.

Both file lists are derived - the documents folders from the repository
tree, the profile files from /setup Step 3's own headings - so a new drop
folder or a new /setup target fails this test until /reset covers it.
"""
import re
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

try:
    from tests._bash import find_bash
    from tests._upstream import is_upstream_checkout
except ImportError:  # `python -m unittest discover -s tests` imports test modules top-level
    from _bash import find_bash
    from _upstream import is_upstream_checkout

REPO = Path(__file__).resolve().parent.parent
RESET = REPO / ".claude" / "commands" / "reset.md"
SETUP = REPO / ".claude" / "commands" / "setup.md"
BASH = find_bash()


def tracked_document_subfolders():
    """Names of documents/ subfolders tracked in git (ignores local noise)."""
    out = subprocess.run(
        ["git", "ls-files", "documents/"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    folders = set()
    for line in out.splitlines():
        parts = line.split("/")
        if len(parts) >= 3:  # documents/<subfolder>/<file...>
            folders.add(parts[1])
    return folders


class TestResetCoversEveryDocumentsSubfolder(unittest.TestCase):
    def setUp(self):
        self.text = RESET.read_text(encoding="utf-8")
        self.folders = tracked_document_subfolders()
        # The tree must actually contain the folders this test is about,
        # or the assertions below would pass vacuously.
        self.assertGreaterEqual(len(self.folders), 5, self.folders)

    def test_preview_lists_every_subfolder(self):
        missing = [
            f for f in sorted(self.folders) if f"documents/{f}/" not in self.text
        ]
        self.assertEqual(
            missing,
            [],
            "reset.md's preview never mentions these documents/ subfolders, "
            f"so the user confirms a deletion list that omits them: {missing}",
        )

    @unittest.skipUnless(BASH, "requires Bash to replay the documented /reset command")
    def test_delete_block_removes_every_subfolder(self):
        # Exercise the deletion contract, rather than requiring one particular
        # spelling of rm. Keep deriving the folder set from git so a newly
        # tracked document root still has to be handled by the real command.
        section = self.text.split("### Documents reset", 1)[1]
        script = re.search(r"```bash\n(.*?)```", section, re.S).group(1)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for folder in self.folders:
                path = root / "documents" / folder
                path.mkdir(parents=True)
                (path / ".gitkeep").write_text("Keep scaffold.\n")
                (path / "user.txt").write_text("Remove document.\n")
            result = subprocess.run([BASH, "-c", script], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for folder in self.folders:
                path = root / "documents" / folder
                self.assertEqual([entry.name for entry in path.iterdir()], [".gitkeep"])
                self.assertEqual((path / ".gitkeep").read_text(), "Keep scaffold.\n")


def section(text: str, start: str, end: str) -> str:
    """The slice of text from the start marker up to the end marker."""
    begin = text.index(start)
    return text[begin : text.index(end, begin)]


def setup_step3_skill_files():
    """Skill files /setup Step 3 populates, derived from its own headings.

    Step 3's targets are written as '### <n>. <verb> `<target>`', where the
    target is either a bare filename resolved against .claude/skills/ or a
    repo-relative path. Non-skill targets (CLAUDE.md, cv/main_example.tex)
    are dropped: /reset profile's scope is skill files only.
    """
    step3 = section(SETUP.read_text(encoding="utf-8"), "## Step 3:", "## Step 4:")
    files = set()
    for target in re.findall(r"^###\s+\d+\.\s+\w+\s+`([^`]+)`", step3, re.MULTILINE):
        if (REPO / target).exists():
            if target.startswith(".claude/skills/"):
                files.add(Path(target).name)
            continue
        matches = list((REPO / ".claude" / "skills").glob(f"*/{target}"))
        if matches:
            files.add(Path(target).name)
    return files


class TestResetCoversEveryPersonalizedSkillFile(unittest.TestCase):
    def setUp(self):
        self.text = RESET.read_text(encoding="utf-8")
        self.files = setup_step3_skill_files()
        # /setup must actually still name these targets, or every assertion
        # below would pass vacuously against an empty set.
        self.assertGreaterEqual(len(self.files), 6, self.files)
        self.assertIn("04-job-evaluation.md", self.files)
        self.assertIn("search-queries.md", self.files)

    def test_preview_lists_every_personalized_skill_file(self):
        preview = section(
            self.text, "### If scope includes `profile`:", "### If scope includes `documents`:"
        )
        missing = sorted(f for f in self.files if f not in preview)
        self.assertEqual(
            missing,
            [],
            "reset.md's profile preview never mentions these files that /setup "
            "Step 3 writes candidate data into, so the user types RESET against "
            f"a list that omits them: {missing}",
        )

    def test_execution_clears_every_personalized_skill_file(self):
        execution = section(self.text, "### Profile reset", "### Documents reset")
        missing = sorted(f for f in self.files if f not in execution)
        self.assertEqual(
            missing,
            [],
            "reset.md's Step 3 profile pass has no instruction for these files, "
            'yet the command then reports the skill files are "now blank": '
            f"{missing}",
        )

    def test_preserved_list_claims_no_personalized_file_is_framework_only(self):
        """A file /setup personalizes must never be listed as framework-only.

        This is the specific regression: 04-job-evaluation.md was named in the
        "NOT touched (they contain framework rules, not candidate data)" list,
        so merely searching reset.md for the filename would have found it.
        """
        preserved = section(self.text, "The following files are NOT touched", "```")
        mislabeled = sorted(f for f in self.files if f in preserved)
        self.assertEqual(
            mislabeled,
            [],
            "reset.md tells the user these files contain 'framework rules, not "
            "candidate data', but /setup Step 3 writes candidate data into them: "
            f"{mislabeled}",
        )

SKILL_DIR = REPO / ".claude" / "skills" / "job-application-assistant"


def skeleton_step(reset_text, filename):
    """(instruction sentence, fenced markdown) /reset uses to rewrite `filename` in Step 3."""
    marker = f"**For `{filename}`**"
    start = reset_text.index(marker)
    fence_open = reset_text.index("```markdown", start)
    fence_close = reset_text.index("```", fence_open + len("```markdown"))
    return reset_text[start:fence_open], reset_text[fence_open + len("```markdown"):fence_close]


def skeleton_block(reset_text, filename):
    """The fenced markdown /reset writes over `filename` in Step 3."""
    return skeleton_step(reset_text, filename)[1]


def frontmatter(text):
    """The leading `---` block of a skill file, or '' when it has none."""
    if not text.startswith("---\n"):
        return ""
    end = text.index("\n---", 4)
    return text[: end + len("\n---") + 1]


def h2_headings(text):
    return [line.strip() for line in text.splitlines() if line.startswith("## ")]


def appear_in_order(needles, haystack):
    """Whether every needle occurs in haystack, in the same relative order."""
    remaining = iter(haystack)
    return all(needle in remaining for needle in needles)


class TestResetTargetsTheHeadingsTheShippedFilesHave(unittest.TestCase):
    """Step 3's skeletons and anchors must name sections that exist.

    Two of them drifted from the files they rewrite. The behavioral-profile
    skeleton used "Strongest Behavioral Traits" / "How I Work Best" / "Growth
    Areas" where the shipped file has "Strongest Behaviors" / "How You Work
    Best" / "Growth Areas (frame positively in applications)", and dropped
    "Core Behavioral Drives" - the structured assessment table /setup collects
    and /interview reads - so one reset left the file's headings permanently
    different from every other fork's. The CV-guide step anchored on a line
    beginning "**Profile statement templates", which the guide has never
    contained (its heading is "**Create 2-3 profile statement templates for
    your main role types:**"), so the wipe found nothing and the user's
    role-tailored statements survived a reset that reported success. Both are
    derived here from the shipped files, so a renamed heading fails this test
    until /reset follows it.
    """

    def setUp(self):
        self.reset = RESET.read_text(encoding="utf-8")

    def test_full_file_skeletons_keep_the_shipped_headings(self):
        for filename in ("01-candidate-profile.md", "02-behavioral-profile.md"):
            with self.subTest(file=filename):
                shipped = (SKILL_DIR / filename).read_text(encoding="utf-8")
                skeleton = h2_headings(skeleton_block(self.reset, filename))
                if not is_upstream_checkout():
                    # On a fork this file is the user's personalized profile, which
                    # may carry ## sections of its own; a rename or a drop still fails.
                    self.assertTrue(
                        appear_in_order(skeleton, h2_headings(shipped)),
                        f"/reset's skeleton headings for {filename} must all appear, in "
                        f"order, in the personalized file: {skeleton}",
                    )
                    continue
                self.assertEqual(
                    skeleton,
                    h2_headings(shipped),
                    f"/reset's skeleton for {filename} must carry exactly the shipped "
                    "## headings, in order - a reset must not rename or drop sections",
                )

    def test_fork_heading_check_rejects_a_renamed_dropped_or_reordered_heading(self):
        # The fork branch above never runs in upstream CI, so pin its strictness here.
        skeleton = ["## Identity", "## Education", "## Awards"]
        self.assertTrue(appear_in_order(skeleton, ["## Identity", "## Education", "## Certifications", "## Awards"]))
        self.assertFalse(appear_in_order(skeleton, ["## Identity", "## Schooling", "## Awards"]))
        self.assertFalse(appear_in_order(skeleton, ["## Identity", "## Awards"]))
        self.assertFalse(appear_in_order(skeleton, ["## Education", "## Identity", "## Awards"]))

    def test_full_file_skeletons_keep_the_framework_version_frontmatter(self):
        # The shipped files open with a `framework_version` marker that
        # check_framework_version.py requires and check_upstream_updates.py
        # reads. The skeletons started at the `#` title, so one /reset profile
        # left both files failing the version gate ("missing 'framework_version'
        # in frontmatter") and invisible to the fork update checker. The
        # instruction must say to keep the block, and the block must not be
        # hardcoded into the skeleton, where it would go stale on the next bump.
        import sys
        sys.path.insert(0, str(REPO / "tools"))
        from check_upstream_updates import get_framework_version_from_text

        for filename in ("01-candidate-profile.md", "02-behavioral-profile.md"):
            with self.subTest(file=filename):
                shipped = (SKILL_DIR / filename).read_text(encoding="utf-8")
                head = frontmatter(shipped)
                self.assertTrue(head, f"{filename} must ship with a frontmatter block")
                instruction, block = skeleton_step(self.reset, filename)
                self.assertIn("frontmatter", instruction, f"the {filename} step must say to keep the frontmatter")
                self.assertNotIn("framework_version", block, "the skeleton must not hardcode a version")
                # What the file looks like after the reset, applied as written.
                after = head + block.lstrip("\n")
                self.assertEqual(
                    get_framework_version_from_text(after),
                    get_framework_version_from_text(shipped),
                    f"a reset must leave {filename}'s framework_version readable and unchanged",
                )

    def test_candidate_skeleton_keeps_the_languages_table_under_identity(self):
        # 04-job-evaluation.md's Language Gate and job-scraper/search-queries.md
        # both read "your Languages table" from 01-candidate-profile.md. The
        # skeleton had `## Identity` with nothing under it, so after a reset the
        # gate and the scraper looked for a table the file no longer had. Both
        # the `### Languages` heading and the table header row are derived from
        # the shipped file; the placeholder data row is deliberately not kept.
        shipped = (SKILL_DIR / "01-candidate-profile.md").read_text(encoding="utf-8")
        start = shipped.index("### Languages")
        header_rows = [
            line for line in shipped[start:].splitlines()[1:]
            if line.startswith("|")
        ][:2]
        self.assertEqual(len(header_rows), 2, "the shipped Languages table must have a header and a separator row")
        block = skeleton_block(self.reset, "01-candidate-profile.md")
        identity = block[block.index("## Identity"): block.index("## Education")]
        self.assertIn("### Languages", identity, "the Languages table must sit under Identity, as shipped")
        for row in header_rows:
            with self.subTest(row=row):
                self.assertIn(row, identity)
        self.assertNotIn("[LANGUAGE]", block, "a reset must not reintroduce a placeholder data row")

    def test_partial_anchors_exist_in_the_files_they_target(self):
        # (reset.md anchor phrase, file, heading the replacement must re-emit)
        cases = (
            (
                r"locate the section that begins with the line `([^`]+)`",
                "05-cv-templates.md",
            ),
            (
                r"The entire `([^`]+)` section",
                "07-interview-prep.md",
            ),
        )
        for pattern, filename in cases:
            with self.subTest(file=filename):
                match = re.search(pattern, self.reset)
                self.assertIsNotNone(match, f"reset.md no longer states its anchor for {filename}")
                anchor = match.group(1)
                shipped = (SKILL_DIR / filename).read_text(encoding="utf-8")
                self.assertIn(
                    anchor,
                    shipped,
                    f"/reset anchors {filename} on {anchor!r}, which the shipped file does not contain - "
                    "the wipe would silently find nothing",
                )
                # The replacement block re-emits the anchor line so the heading survives the wipe.
                step = self.reset[match.start():]
                block_start = step.index("```markdown") + len("```markdown")
                block = step[block_start: step.index("```", block_start)]
                self.assertIn(anchor, block, f"the replacement for {filename} must keep the heading {anchor!r}")

if __name__ == "__main__":
    unittest.main()

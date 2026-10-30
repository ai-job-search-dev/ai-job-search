import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
LINTER_SCRIPT = REPO_ROOT / "tools" / "lint_skills.py"


def run_linter(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "tools" / "lint_skills.py")],
        capture_output=True,
        text=True,
    )


class LinterRepoFixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

        tools = self.root / "tools"
        tools.mkdir()
        shutil.copy(LINTER_SCRIPT, tools / "lint_skills.py")
        # The Python-test CI job does not install PyYAML; the separate lint job
        # does. These settings-focused tests only need a valid frontmatter map.
        # The stub parses simple "key: value" lines, enough for the flat
        # frontmatter these fixtures write, so the checks under test see the
        # actual file content instead of a canned mapping.
        (tools / "yaml.py").write_text(
            "class YAMLError(Exception):\n"
            "    pass\n\n"
            "def safe_load(text):\n"
            "    result = {}\n"
            "    for line in (text or '').splitlines():\n"
            "        if ':' in line:\n"
            "            key, _, value = line.partition(':')\n"
            "            result[key.strip()] = value.strip()\n"
            "    return result\n",
            encoding="utf-8",
        )

        command = self.root / ".claude" / "commands" / "setup.md"
        command.parent.mkdir(parents=True)
        command.write_text("# /setup - Test setup command\n", encoding="utf-8")

        skill = self.root / ".claude" / "skills" / "example" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            "---\nname: example\ndescription: Example skill\n---\n",
            encoding="utf-8",
        )

        self.settings = self.root / ".claude" / "settings.json"
        self.write_settings({"permissions": {"allow": []}})

    def write_settings(self, data):
        self.settings.write_text(json.dumps(data), encoding="utf-8")


class SettingsShapeTests(LinterRepoFixture):
    def test_valid_settings_pass(self):
        result = run_linter(self.root)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("lint_skills: OK", result.stdout)

    def test_invalid_json_fails_cleanly(self):
        self.settings.write_text("{not json", encoding="utf-8")

        result = run_linter(self.root)

        self.assertEqual(result.returncode, 1)
        self.assertIn(".claude/settings.json", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_non_object_root_fails_cleanly(self):
        for data in ([], "settings", 1, None):
            with self.subTest(data=data):
                self.write_settings(data)

                result = run_linter(self.root)

                self.assertEqual(result.returncode, 1)
                self.assertIn("top-level JSON value to be an object", result.stdout)
                self.assertNotIn("Traceback", result.stderr)

    def test_non_object_permissions_fails_cleanly(self):
        for permissions in ([], "permissions", 1, None):
            with self.subTest(permissions=permissions):
                self.write_settings({"permissions": permissions})

                result = run_linter(self.root)

                self.assertEqual(result.returncode, 1)
                self.assertIn("expected permissions to be an object", result.stdout)
                self.assertNotIn("Traceback", result.stderr)

    def test_non_list_allow_fails_cleanly(self):
        for allow in ({}, "Bash(bun run:*)", 1, None):
            with self.subTest(allow=allow):
                self.write_settings({"permissions": {"allow": allow}})

                result = run_linter(self.root)

                self.assertEqual(result.returncode, 1)
                self.assertIn("expected permissions.allow to be a list", result.stdout)
                self.assertNotIn("Traceback", result.stderr)
class NonUtf8FileTests(LinterRepoFixture):
    """A skill or command saved in a Windows code page (cp1252 é) used to end
    the run with a UnicodeDecodeError traceback before any report was printed;
    every other malformed input the linter meets becomes a finding."""

    def test_non_utf8_skill_is_a_finding_not_a_traceback(self):
        skill = self.root / ".claude" / "skills" / "example" / "SKILL.md"
        skill.write_bytes("---\nname: example\ndescription: Café skill\n---\n".encode("cp1252"))

        result = run_linter(self.root)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        # rel() prints native separators; Windows shows backslashes.
        self.assertIn(".claude/skills/example/SKILL.md: not valid UTF-8", result.stdout.replace("\\", "/"))
        self.assertNotIn("Traceback", result.stderr)

    def test_non_utf8_command_is_a_finding_not_a_traceback(self):
        command = self.root / ".claude" / "commands" / "setup.md"
        command.write_bytes("# /setup - Café setup\n".encode("cp1252"))

        result = run_linter(self.root)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        # rel() prints native separators; Windows shows backslashes.
        self.assertIn(".claude/commands/setup.md: not valid UTF-8", result.stdout.replace("\\", "/"))
        self.assertNotIn("Traceback", result.stderr)


class SkillAndCommandCheckTests(LinterRepoFixture):
    """check_skill()/check_command() are the linter's main job and were
    previously untested - only check_settings() had coverage, so deleting
    e.g. the missing-allowed-tools error survived the whole suite (review
    finding F23, 2026-08-19)."""

    def write_skill(self, frontmatter: str):
        skill = self.root / ".claude" / "skills" / "example" / "SKILL.md"
        skill.write_text(frontmatter, encoding="utf-8")

    def test_allowed_tools_referencing_a_missing_file_fails(self):
        self.write_skill(
            "---\n"
            "name: example\n"
            "description: Example skill\n"
            "allowed-tools: Bash(bun run .claude/skills/example/DOES_NOT_EXIST.ts *)\n"
            "---\n"
        )

        result = run_linter(self.root)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("allowed-tools references a missing file", result.stdout)
        self.assertIn("DOES_NOT_EXIST.ts", result.stdout)

    def test_allowed_tools_referencing_an_existing_file_passes(self):
        target = self.root / ".claude" / "skills" / "example" / "cli.ts"
        target.write_text("// present\n", encoding="utf-8")
        self.write_skill(
            "---\n"
            "name: example\n"
            "description: Example skill\n"
            "allowed-tools: Bash(bun run .claude/skills/example/cli.ts *)\n"
            "---\n"
        )

        result = run_linter(self.root)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_frontmatter_missing_description_fails(self):
        self.write_skill("---\nname: example\ndescription:\n---\n")

        result = run_linter(self.root)

        self.assertEqual(result.returncode, 1)
        self.assertIn("missing required key 'description'", result.stdout)

    def test_command_without_slash_title_fails(self):
        command = self.root / ".claude" / "commands" / "setup.md"
        command.write_text("# setup - missing the slash\n", encoding="utf-8")

        result = run_linter(self.root)

        self.assertEqual(result.returncode, 1)
        self.assertIn("must start with a '# /<name>' title", result.stdout)


if __name__ == "__main__":
    unittest.main()

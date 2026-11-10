"""The weekly release workflow can't run offline, so pin its safety properties.

The version itself comes from commitizen (`cz bump --get-next`, .cz.toml).
"""
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "release.yml"


class ReleaseWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_release_runs_only_on_upstream(self):
        self.assertIn("if: github.repository == 'ai-job-search-dev/ai-job-search'", self.text)

    def test_write_access_is_granted_to_the_job_not_the_workflow(self):
        top_level = self.text.split("\njobs:", 1)[0]
        self.assertIn("permissions:\n  contents: read", top_level)
        self.assertIn("contents: write", self.text.split("\njobs:", 1)[1])

    def test_actions_are_sha_pinned(self):
        for line in self.text.splitlines():
            stripped = line.strip()
            if stripped.startswith(("- uses:", "uses:")):
                ref = stripped.split("uses:", 1)[1].strip()
                self.assertRegex(ref.split("@", 1)[1].split()[0], r"^[0-9a-f]{40}$", f"action not SHA-pinned: {ref}")


if __name__ == "__main__":
    unittest.main()

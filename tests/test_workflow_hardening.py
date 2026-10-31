"""Pin the workflow settings zizmor checks, without needing zizmor or PyYAML.

- Every `actions/checkout` sets `persist-credentials: false`. Otherwise the job
  token stays in `.git/config` for every later step, including the CLI code a
  pull request brings (`artipacked`).
- A matrix value that comes from repository content - `cli-checks` discovers
  `matrix.tool` from directory names, which a pull request controls - never
  appears expanded inside a `run:` script (`template-injection`).
"""
import unittest
from pathlib import Path

WORKFLOWS = sorted((Path(__file__).resolve().parent.parent / ".github" / "workflows").glob("*.yml"))


def indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def key_indent(line: str) -> int:
    """Column of a step's keys: two past the dash on a `- key:` line."""
    return indent(line) + (2 if line.lstrip().startswith("- ") else 0)


class WorkflowHardeningTests(unittest.TestCase):
    def test_workflows_found(self):
        self.assertTrue(WORKFLOWS)

    def test_every_checkout_drops_its_credentials(self):
        for path in WORKFLOWS:
            lines = path.read_text(encoding="utf-8").splitlines()
            for i, line in enumerate(lines):
                if "uses: actions/checkout@" not in line:
                    continue
                keys = key_indent(line)
                block = []
                for following in lines[i + 1 :]:
                    if following.strip() and indent(following) < keys:
                        break
                    block.append(following.strip())
                with self.subTest(workflow=path.name, line=i + 1):
                    self.assertIn("persist-credentials: false", block)

    def test_discovered_matrix_values_stay_out_of_run_scripts(self):
        for path in WORKFLOWS:
            lines = path.read_text(encoding="utf-8").splitlines()
            run_keys = None  # indent of the active `run:` key, None outside one
            for i, line in enumerate(lines, 1):
                if run_keys is not None and line.strip() and indent(line) <= run_keys:
                    run_keys = None
                stripped = line.strip()
                if run_keys is None and stripped.startswith(("run:", "- run:")):
                    run_keys = key_indent(line)
                    script = stripped.split("run:", 1)[1]
                else:
                    script = line if run_keys is not None else ""
                with self.subTest(workflow=path.name, line=i):
                    self.assertNotIn("${{ matrix.tool }}", script)

if __name__ == "__main__":
    unittest.main()

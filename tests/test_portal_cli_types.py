"""Portal CLIs using @bunli/core must pin @types/node directly.

@bunli/core pulls in image-q, which pins @types/node 16.9.1 exactly. Without
a direct devDependency, bun 1.4.4 hoists that version and the web globals
(fetch, Response, URL, Headers) vanish from typecheck.
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class PortalCliTypesTests(unittest.TestCase):
    def test_bunli_clis_pin_types_node(self):
        manifests = sorted((ROOT / ".agents" / "skills").glob("*/cli/package.json"))
        checked = 0
        for path in manifests:
            data = json.loads(path.read_text(encoding="utf-8"))
            if "@bunli/core" not in data.get("dependencies", {}):
                continue
            checked += 1
            version = data.get("devDependencies", {}).get("@types/node")
            with self.subTest(cli=path.parent.parent.name):
                self.assertIsNotNone(version, "missing @types/node devDependency")
                self.assertRegex(version, r"^\d+(\.\d+)*$")
                # image-q pins 16.9.1, which lacks fetch/Response/URL/Headers.
                self.assertGreaterEqual(int(version.split(".")[0]), 18)
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()

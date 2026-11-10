"""Real Bun install against an offline npm registry: dependency hooks stay off.

The fixture has the same ordinary npm dependency shape as esbuild, whose
postinstall Bun 1.2 runs by default. It writes only a local marker. No public
registry, credentials or lifecycle-script runner is mocked.
"""
import base64
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
import threading
import unittest

ROOT = Path(__file__).resolve().parent.parent
BUN = os.environ.get("TEST_BUN") or shutil.which("bun")


@unittest.skipUnless(BUN, "requires Bun for the offline package-manager integration")
class DependencyHooks(unittest.TestCase):
    def test_installing_dependency_does_not_execute_its_hook(self):
        package = {"name": "esbuild", "version": "0.25.0", "scripts": {"postinstall": "bun run hook.js"}}
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as tar:
            for name, data in {
                "package.json": json.dumps(package).encode(),
                "hook.js": b'await Bun.write("hook-ran.txt", "ran");',
            }.items():
                entry = tarfile.TarInfo("package/" + name)
                entry.size = len(data)
                tar.addfile(entry, io.BytesIO(data))
        blob = archive.getvalue()

        class Registry(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.endswith(".tgz"):
                    body = blob
                else:
                    metadata = {**package, "dist": {
                        "tarball": f"http://127.0.0.1:{self.server.server_port}/esbuild.tgz",
                        "integrity": "sha512-" + base64.b64encode(hashlib.sha512(blob).digest()).decode(),
                    }}
                    body = json.dumps({"name": "esbuild", "dist-tags": {"latest": "0.25.0"},
                                       "versions": {"0.25.0": metadata}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Registry)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with TemporaryDirectory() as directory:
                root = Path(directory)
                tools = root / "tools"
                tools.mkdir()
                for name in ("install_portal_clis.py", "security_guards.py"):
                    shutil.copyfile(ROOT / "tools" / name, tools / name)
                cli = root / ".agents/skills/example-search/cli"
                cli.mkdir(parents=True)
                (cli / "package.json").write_text(json.dumps({
                    "name": "example-search", "private": True, "dependencies": {"esbuild": "0.25.0"},
                }), encoding="utf-8")
                # The registry goes in through the environment: a .npmrc beside
                # the manifest is one of the things the installer now refuses.
                env = {"PATH": str(Path(BUN).absolute().parent) + os.pathsep + os.defpath,
                       "HOME": str(root), "TMPDIR": str(root), "TEMP": str(root), "TMP": str(root),
                       "BUN_INSTALL_CACHE_DIR": str(root / "cache"),
                       "NPM_CONFIG_REGISTRY": f"http://127.0.0.1:{server.server_port}/"}
                if "SYSTEMROOT" in os.environ:
                    env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
                proc = subprocess.run([sys.executable, str(tools / "install_portal_clis.py")],
                                      env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                dependency = cli / "node_modules/esbuild"
                self.assertTrue((dependency / "hook.js").is_file(), "the dependency was actually installed")
                self.assertFalse((dependency / "hook-ran.txt").exists(), "the dependency install hook ran")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()

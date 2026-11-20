"""Run SETUP.md's TinyTeX PATH snippet under bash and zsh against a fake HOME.

The snippet is pasted into the user's interactive shell, which is zsh on
macOS. zsh aborts a command whose glob matches nothing, so a lookup that
globs both install locations came back empty there, and the snippet then
persisted `export PATH=":$PATH"` - the current directory first on PATH in
every future shell. bash's failglob aborts on an unmatched glob the same
way, so that variant keeps the regression guarded on runners without zsh.
The installer download is stripped; everything after it runs as written.
"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SETUP = REPO / "SETUP.md"
INSTALL_LINES = re.compile(r"^\s*(curl |sh .*install-bin-unix\.sh)")


def tinytex_snippet():
    text = SETUP.read_text(encoding="utf-8")
    section = text.split("#### macOS and Linux: TinyTeX", 1)[1]
    block = re.search(r"```bash\n(.*?)```", section, re.DOTALL).group(1)
    return "\n".join(line for line in block.splitlines() if not INSTALL_LINES.match(line))


@unittest.skipIf(os.name == "nt", "the snippet targets macOS and Linux; Windows uses MiKTeX")
class TestTinyTeXPathSnippet(unittest.TestCase):
    def run_snippet(self, shell, layout, *shell_opts, existing=(), fake_os=None):
        home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home)
        for name in existing:
            (home / name).touch()
        path = os.environ["PATH"]
        if fake_os:
            fake_bin = home / "fakebin"
            fake_bin.mkdir()
            uname = fake_bin / "uname"
            uname.write_text(f"#!/bin/sh\necho {fake_os}\n")
            uname.chmod(0o755)
            path = f"{fake_bin}:{path}"
        bin_dir = None
        if layout:
            bin_dir = home / layout
            bin_dir.mkdir(parents=True)
            stub = bin_dir / "lualatex"
            stub.write_text("#!/bin/sh\necho stub lualatex\n")
            stub.chmod(0o755)
        env = {"HOME": str(home), "SHELL": shell, "PATH": path}
        result = subprocess.run(
            [shell, *shell_opts, "-c", tinytex_snippet()], env=env, cwd=home, capture_output=True, text=True
        )
        rc_text = "".join(p.read_text() for p in home.glob(".*") if p.is_file())
        return bin_dir, result, rc_text, home

    def assert_persists_bin_dir(self, shell, *shell_opts):
        for layout in ("Library/TinyTeX/bin/universal-darwin", ".TinyTeX/bin/x86_64-linux"):
            with self.subTest(shell=shell, opts=shell_opts, layout=layout):
                bin_dir, result, rc_text, _ = self.run_snippet(shell, layout, *shell_opts)
                self.assertIn(f'export PATH="{bin_dir}:$PATH"', rc_text, result.stderr)
                self.assertIn("stub lualatex", result.stdout, result.stderr)

    def assert_writes_nothing_without_install(self, shell, *shell_opts):
        _, result, rc_text, _ = self.run_snippet(shell, None, *shell_opts)
        self.assertNotIn("PATH", rc_text, "a failed install must not persist an empty PATH entry")
        self.assertIn("not found", result.stderr)
        self.assertNotEqual(result.returncode, 0, "a failed install must not look like success")

    def test_bash_persists_the_tinytex_bin_dir(self):
        self.assert_persists_bin_dir(shutil.which("bash") or self.skipTest("bash not installed"))

    def test_bash_writes_nothing_when_tinytex_is_missing(self):
        self.assert_writes_nothing_without_install(shutil.which("bash") or self.skipTest("bash not installed"))

    def test_failglob_bash_persists_the_tinytex_bin_dir(self):
        self.assert_persists_bin_dir(shutil.which("bash") or self.skipTest("bash not installed"), "-O", "failglob")

    def test_bash_appends_to_the_startup_file_bash_reads(self):
        bash = shutil.which("bash") or self.skipTest("bash not installed")
        cases = (
            ("Linux", (), ".bashrc"),
            ("Darwin", (), ".profile"),
            ("Darwin", (".bash_profile",), ".bash_profile"),
            ("Darwin", (".bash_login",), ".bash_login"),
            ("Darwin", (".bash_login", ".bash_profile"), ".bash_profile"),
        )
        for fake_os, existing, expected in cases:
            with self.subTest(os=fake_os, existing=existing):
                _, result, _, home = self.run_snippet(
                    bash, "Library/TinyTeX/bin/universal-darwin", existing=existing, fake_os=fake_os
                )
                written = [p.name for p in home.glob(".*") if p.is_file() and "PATH" in p.read_text()]
                self.assertEqual(written, [expected], result.stderr)

    def test_zsh_persists_the_tinytex_bin_dir(self):
        self.assert_persists_bin_dir(shutil.which("zsh") or self.skipTest("zsh not installed"))

    def test_zsh_writes_nothing_when_tinytex_is_missing(self):
        self.assert_writes_nothing_without_install(shutil.which("zsh") or self.skipTest("zsh not installed"))


if __name__ == "__main__":
    unittest.main()

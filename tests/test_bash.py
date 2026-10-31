"""A Bash launcher on PATH is not necessarily a usable shell (Windows WSL)."""
from pathlib import Path
import subprocess
import unittest
from unittest import mock

try:
    from tests import _bash
except ImportError:
    import _bash


class BashDiscoveryTests(unittest.TestCase):
    def test_working_path_shell_is_used(self):
        with mock.patch.object(_bash, "WINDOWS", False), \
             mock.patch.object(_bash.shutil, "which", return_value="/bin/bash"), \
             mock.patch.object(_bash.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            self.assertEqual(_bash.find_bash(), "/bin/bash")
        self.assertEqual(run.call_args.args[0], ["/bin/bash", "-c", 'x=ok; test "$x" = ok'])

    def test_launcher_that_expands_variables_falls_back_to_git_bash(self):
        git = Path(__file__).resolve().parent / "Git" / "cmd" / "git.exe"
        fallback = str(git.parent.parent / "bin" / "bash.exe")

        def probe(args, **kwargs):
            script = args[2]
            if args[0] == "wsl-bash":
                # The launcher can exit successfully, but expands $x before
                # Bash assigns it, so the reset command cannot work.
                script = script.replace("$x", "")
            return subprocess.CompletedProcess(args, 0 if script in (
                "exit 0", 'x=ok; test "$x" = ok',
            ) else 1)

        with mock.patch.object(_bash, "WINDOWS", True), \
             mock.patch.object(_bash.shutil, "which", side_effect=["wsl-bash", str(git)]), \
             mock.patch.object(_bash.subprocess, "run", side_effect=probe):
            self.assertEqual(_bash.find_bash(), fallback)

    def test_missing_shell_needs_no_probe(self):
        with mock.patch.object(_bash, "WINDOWS", False), \
             mock.patch.object(_bash.shutil, "which", return_value=None), \
             mock.patch.object(_bash.subprocess, "run") as run:
            self.assertIsNone(_bash.find_bash())
        run.assert_not_called()

    def test_unusable_path_shell_falls_back_to_git_bash_on_windows(self):
        git = Path(__file__).resolve().parent / "Git" / "cmd" / "git.exe"
        fallback = str(git.parent.parent / "bin" / "bash.exe")
        with mock.patch.object(_bash, "WINDOWS", True), \
             mock.patch.object(_bash.shutil, "which", side_effect=["wsl-bash", str(git)]), \
             mock.patch.object(_bash.subprocess, "run", side_effect=[
                 mock.Mock(returncode=1), mock.Mock(returncode=0),
             ]) as run:
            self.assertEqual(_bash.find_bash(), fallback)
        self.assertEqual([call.args[0][0] for call in run.call_args_list], ["wsl-bash", fallback])

    def test_failed_or_hanging_shell_is_unavailable(self):
        for error in (OSError("cannot start"), subprocess.TimeoutExpired("bash", 5)):
            with self.subTest(error=type(error).__name__), \
                 mock.patch.object(_bash, "WINDOWS", False), \
                 mock.patch.object(_bash.shutil, "which", return_value="bash"), \
                 mock.patch.object(_bash.subprocess, "run", side_effect=error):
                self.assertIsNone(_bash.find_bash())

    def test_nonzero_exit_without_a_fallback_is_unavailable(self):
        with mock.patch.object(_bash, "WINDOWS", False), \
             mock.patch.object(_bash.shutil, "which", return_value="bash"), \
             mock.patch.object(_bash.subprocess, "run", return_value=mock.Mock(returncode=1)):
            self.assertIsNone(_bash.find_bash())

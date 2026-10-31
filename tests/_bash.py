"""Find a usable Bash for tests that replay the documented reset commands."""
import os
from pathlib import Path
import shutil
import subprocess

WINDOWS = os.name == "nt"


def find_bash():
    candidates = [shutil.which("bash")]
    if WINDOWS:
        # A Windows PATH may find the WSL launcher even without a usable WSL
        # installation. Git for Windows also ships Bash beside its cmd/ entry
        # points, but does not normally add that bin/ directory to PATH.
        git = shutil.which("git")
        if git:
            candidates.append(str(Path(git).resolve().parent.parent / "bin" / "bash.exe"))
    for candidate in dict.fromkeys(candidates):
        if not candidate:
            continue
        try:
            result = subprocess.run(
                [candidate, "-c", 'x=ok; test "$x" = ok'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            return candidate
    return None

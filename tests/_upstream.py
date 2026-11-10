"""Shared fork-detection for tests that only apply to the upstream template.

Several guards (placeholder integrity, template placeholders) assert on the
pristine upstream files, so they must skip on personalized forks. In CI the
``GITHUB_REPOSITORY`` variable is authoritative; in a local run it is unset,
so fall back to the checkout's ``origin`` remote instead of assuming
upstream.
"""
import os
import subprocess
from pathlib import Path

UPSTREAM = "ai-job-search-dev/ai-job-search"

REPO = Path(__file__).resolve().parent.parent

_HTTPS_PREFIX = "https://github.com/"
_SSH_PREFIX = "git@github.com:"
_SSH_URL_PREFIX = "ssh://git@github.com/"


def _is_upstream_url(url: str) -> bool:
    """Whether a git remote URL points at the upstream template repo.

    Matches the https, scp-style ssh and ``ssh://`` forms with an optional
    ``.git`` suffix;
    anything else (a fork URL, some other host) is not upstream.
    """
    lowered = url.strip().lower()
    slug = UPSTREAM.lower()
    for prefix in (_HTTPS_PREFIX, _SSH_PREFIX, _SSH_URL_PREFIX):
        if lowered.startswith(prefix):
            rest = lowered[len(prefix):].rstrip("/")
            if rest.endswith(".git"):
                rest = rest[: -len(".git")]
            return rest == slug
    return False


def is_upstream_checkout() -> bool:
    """Whether this checkout is the upstream template rather than a fork.

    ``GITHUB_REPOSITORY`` decides when set (CI). Otherwise the ``origin``
    remote is inspected. When nothing can be determined (git missing,
    timeout, non-zero exit), upstream is assumed: the guards then run
    exactly as they do today.
    """
    env_repo = os.environ.get("GITHUB_REPOSITORY")
    if env_repo:
        return env_repo == UPSTREAM
    try:
        origin = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    if origin.returncode != 0:
        return True
    return _is_upstream_url(origin.stdout)

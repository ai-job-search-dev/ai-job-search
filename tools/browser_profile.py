#!/usr/bin/env python3
"""Dedicated Chrome Browser Profile and Debug Session Manager.

Manages the isolated user profile at ~/.config/ai-job-search/chrome-profile
for persistent sessions across LinkedIn and ATS platforms, with remote debugging on port 9222.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

CHROME_PROFILE_DIR = Path.home() / ".config" / "ai-job-search" / "chrome-profile"
DEFAULT_CDP_PORT = 9222
MAC_CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def get_chrome_executable() -> str:
    """Detect Google Chrome binary path."""
    if Path(MAC_CHROME_BIN).exists():
        return MAC_CHROME_BIN
    # Fallback to PATH search
    from shutil import which
    for candidate in ["google-chrome", "google-chrome-stable", "chromium", "chrome"]:
        p = which(candidate)
        if p:
            return p
    raise FileNotFoundError("Google Chrome binary not found on system.")


def launch_dedicated_chrome(port: int = DEFAULT_CDP_PORT, url: str = "https://www.linkedin.com") -> subprocess.Popen:
    """Launch dedicated Chrome instance with remote debugging enabled."""
    CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    chrome_bin = get_chrome_executable()

    cmd = [
        chrome_bin,
        f"--user-data-dir={CHROME_PROFILE_DIR}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]

    print(f"Launching dedicated Chrome on port {port}...")
    print(f"Profile Directory: {CHROME_PROFILE_DIR}")
    print(f"Target URL:        {url}")
    print("\nNote: Keep this window open if you want agent scripts or browser-use to attach via CDP.\n")

    return subprocess.Popen(cmd)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Launch dedicated Chrome session for ai-job-search")
    parser.add_argument("--port", type=int, default=DEFAULT_CDP_PORT, help="CDP debug port (default: 9222)")
    parser.add_argument("--url", type=str, default="https://www.linkedin.com", help="Initial URL to open")
    args = parser.parse_args()

    proc = launch_dedicated_chrome(port=args.port, url=args.url)
    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\nClosing Chrome session.")
        proc.terminate()

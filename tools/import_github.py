#!/usr/bin/env python3
"""Import public GitHub repositories into the master profile's project list.

Uses the unauthenticated public API (60 requests/hour) or GITHUB_TOKEN when it is set and
valid. Forks and archived repos are skipped. Technologies come from the repository's
language and topics only, so nothing is claimed that GitHub cannot show.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.net import get_with_retry
from tools.profile_manager import DEFAULT_PROFILE_FILE, load_profile, save_profile
from tools.profile_schema import ProjectEntry

API = "https://api.github.com"


def fetch_repos(user: str, token: Optional[str] = None, timeout: int = 20) -> List[Dict[str, Any]]:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = get_with_retry(
        f"{API}/users/{user}/repos", params={"per_page": 100, "sort": "pushed"},
        headers=headers, timeout=timeout,
    )
    if resp.status_code == 401 and token:
        return fetch_repos(user, None, timeout)
    resp.raise_for_status()
    return resp.json()


def repo_to_project(repo: Dict[str, Any]) -> ProjectEntry:
    techs = [t for t in [repo.get("language"), *(repo.get("topics") or [])] if t]
    return ProjectEntry(
        name=repo["name"],
        description=(repo.get("description") or "").strip() or repo["name"],
        repo_url=repo.get("html_url"),
        technologies=list(dict.fromkeys(techs)),
    )


def select_repos(repos: List[Dict[str, Any]], include_empty: bool = False) -> List[Dict[str, Any]]:
    chosen = [r for r in repos if not r.get("fork") and not r.get("archived") and not r.get("private")]
    if not include_empty:
        chosen = [r for r in chosen if r.get("description") or r.get("language")]
    return chosen


def merge_projects(profile: Any, repos: List[Dict[str, Any]]) -> List[str]:
    known_urls = {(p.repo_url or "").casefold() for p in profile.projects}
    known_names = {p.name.casefold() for p in profile.projects}
    added: List[str] = []
    for repo in repos:
        project = repo_to_project(repo)
        if (project.repo_url or "").casefold() in known_urls or project.name.casefold() in known_names:
            continue
        profile.projects.append(project)
        added.append(project.name)
    return added


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Add public GitHub repos to the master profile")
    parser.add_argument("user", help="GitHub username")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE_FILE)
    parser.add_argument("--include-empty", action="store_true", help="Also import repos with no description or language")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        repos = select_repos(fetch_repos(args.user, os.environ.get("GITHUB_TOKEN")), args.include_empty)
    except requests.RequestException as exc:
        print(f"error: could not read GitHub repos: {exc}", file=sys.stderr)
        return 2
    profile = load_profile(args.profile)
    added = merge_projects(profile, repos)
    for name in added:
        print(f"  + project: {name}")
    if not added:
        print("nothing new to import")
    if not args.dry_run and added:
        print(f"saved: {save_profile(profile, args.profile)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

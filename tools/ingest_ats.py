#!/usr/bin/env python3
"""Direct ATS Public API Ingestion for Target Companies.

Queries public JSON endpoints for Greenhouse, Lever, and Ashby job boards,
sanitizes descriptions using posting_guard, and stores them in job_search.db.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import requests
import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

CONFIG_PATH = (
    ROOT_DIR / "config" / "companies.yaml"
    if (ROOT_DIR / "config" / "companies.yaml").exists()
    else ROOT_DIR / "config" / "target_companies.yaml"
)

from tools.db import backup_db, check_company_cooldown, flagged_verdict, get_db, init_db, upsert_job
from tools.net import SourceError, get_with_retry
from tools.posting_guard import guard
from tools.logger import get_logger, METRICS

logger = get_logger("ingest_ats")

REQ_TIMEOUT = 15
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

_HTML_TAGS = re.compile(r"<[^>]+>")


def strip_html(markup: str) -> str:
    """Simple clean text extractor from HTML."""
    if not markup:
        return ""
    text = _HTML_TAGS.sub(" ", markup)
    return re.sub(r"\s+", " ", text).strip()


def fetch_greenhouse_jobs(slug: str) -> List[Dict[str, Any]]:
    """Fetch jobs from public Greenhouse board API."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    resp = get_with_retry(url, headers=HEADERS, timeout=REQ_TIMEOUT)
    if resp.status_code != 200:
        raise SourceError(f"greenhouse:{slug}", resp.status_code)
    data = resp.json()
    jobs = []
    for item in data.get("jobs", []):
        raw_content = html.unescape(item.get("content", "") or "")  # Greenhouse sends the HTML entity-escaped
        location = (
            item.get("location", {}).get("name", "")
            if isinstance(item.get("location"), dict)
            else str(item.get("location") or "")
        )
        is_remote = "remote" in location.lower() or "remote" in item.get("title", "").lower()
        jobs.append({
            "title": item.get("title", ""),
            "url": item.get("absolute_url", ""),
            "location": location,
            "is_remote": is_remote,
            "raw_description": raw_content,
            "description_format": "html",
            "portal": f"greenhouse:{slug}",
        })
    return jobs


def fetch_lever_jobs(slug: str) -> List[Dict[str, Any]]:
    """Fetch jobs from public Lever postings API."""
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    resp = get_with_retry(url, headers=HEADERS, timeout=REQ_TIMEOUT)
    if resp.status_code != 200:
        raise SourceError(f"lever:{slug}", resp.status_code)
    data = resp.json()
    jobs = []
    for item in data:
        plain = item.get("descriptionPlain")
        raw_desc = plain or item.get("description", "")
        desc_format = "text" if plain else "html"
        cats = item.get("categories", {})
        location = cats.get("location", "")
        workplace_type = cats.get("workplaceType", "").lower()
        is_remote = workplace_type == "remote" or "remote" in location.lower() or "remote" in item.get("text", "").lower()
        jobs.append({
            "title": item.get("text", ""),
            "url": item.get("hostedUrl", ""),
            "location": location,
            "is_remote": is_remote,
            "raw_description": raw_desc,
            "description_format": desc_format,
            "portal": f"lever:{slug}",
        })
    return jobs


def fetch_ashby_jobs(slug: str) -> List[Dict[str, Any]]:
    """Fetch jobs from public Ashby job board API."""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    resp = get_with_retry(url, headers=HEADERS, timeout=REQ_TIMEOUT)
    if resp.status_code != 200:
        raise SourceError(f"ashby:{slug}", resp.status_code)
    data = resp.json()
    jobs = []
    for item in data.get("jobs", []):
        raw_desc = item.get("descriptionHtml", "") or ""
        location = item.get("location", "")
        is_remote = item.get("isRemote", False) or "remote" in location.lower()
        jobs.append({
            "title": item.get("title", ""),
            "url": item.get("jobUrl", ""),
            "location": location,
            "is_remote": is_remote,
            "raw_description": raw_desc,
            "description_format": "html",
            "portal": f"ashby:{slug}",
        })
    return jobs


def run_ats_ingestion(config_file: Optional[Path] = None, db_path: Optional[Path] = None) -> Dict[str, int]:
    """Execute ingestion across all target companies declared in config."""
    cfg_path = config_file or CONFIG_PATH
    if not cfg_path.exists():
        logger.error(f"Configuration file not found: {cfg_path}")
        return {"total": 0, "new": 0, "refreshed": 0}

    with open(cfg_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    companies = config.get("companies", [])
    init_db(db_path)
    backup_db(db_path, min_interval_minutes=60)
    stats = {"total": 0, "new": 0, "refreshed": 0, "companies_queried": 0, "cooldown_skipped": 0, "companies_failed": 0}
    failed: list = []
    with get_db(db_path) as conn:
        for c in companies:
            if not c.get("enabled", True):
                continue
            name = c.get("name")
            provider = c.get("ats_provider", "").lower()
            slug = c.get("slug")

            # Check 30-day company cooldown
            in_cooldown, reason = check_company_cooldown(conn, name)
            if in_cooldown:
                logger.info(f"Skipping {name}: {reason}")
                stats["cooldown_skipped"] += 1
                METRICS.increment("cooldown_skipped")
                continue

            logger.info(f"Querying {name} via {provider} ({slug})...")
            postings = []
            try:
                if provider == "greenhouse":
                    postings = fetch_greenhouse_jobs(slug)
                elif provider == "lever":
                    postings = fetch_lever_jobs(slug)
                elif provider == "ashby":
                    postings = fetch_ashby_jobs(slug)
                else:
                    logger.warning(f"Unknown ATS provider '{provider}' for {name}")
                    continue
                time.sleep(0.5)  # Friendly rate-limiting
            except Exception as e:
                logger.error(f"Failed fetching {name}: {e}")
                stats["companies_failed"] += 1
                failed.append(f"{name}: {e}")
                continue
            stats["companies_queried"] += 1
            for p in postings:
                stats["total"] += 1
                METRICS.increment("ingested_total")

                # Sanitize using posting_guard logic
                raw_desc = p.get("raw_description", "")
                is_html = p.get("description_format") == "html"
                cleaned_desc, findings = guard(raw_desc, mode="html" if is_html else "text")
                if "⚠" in findings.get("verdict", ""):
                    logger.warning(f"Security Alert on {name} - {p['title']}", verdict=findings["verdict"])
                    METRICS.increment("sanitized_flagged")
                else:
                    METRICS.increment("sanitized_clean")

                job_id, is_new = upsert_job(
                    conn=conn,
                    title=p["title"],
                    company=name,
                    url=p["url"],
                    portal=p["portal"],
                    location=p.get("location", ""),
                    is_remote=p.get("is_remote", False),
                    # HTML is never stored: the ranker falls back to raw_description, and hidden markup must not reach it
                    raw_description=cleaned_desc if is_html else raw_desc,
                    cleaned_description=cleaned_desc,
                    guard_verdict=flagged_verdict(findings),
                )
                if is_new:
                    stats["new"] += 1
                else:
                    stats["refreshed"] += 1
                    METRICS.increment("duplicates_skipped")
            conn.commit()
    if failed:
        logger.warning(f"{len(failed)} source(s) failed: " + " | ".join(failed))
    logger.info("ATS Ingestion Run Complete", **stats)
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest jobs from direct ATS APIs")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to companies.yaml")
    args = parser.parse_args()
    run_ats_ingestion(config_file=args.config)
    METRICS.print_summary()

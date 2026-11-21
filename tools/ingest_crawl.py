#!/usr/bin/env python3
"""Niche Quantum Portals & Custom Careers Crawling Engine.

Fetches career listings from specialized quantum computing websites
(e.g., Unitary Fund Job Board, Quantum Jobs), converts HTML to Markdown,
sanitizes text with posting_guard, and persists records in SQLite.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
import requests
import yaml
from bs4 import BeautifulSoup
from markdownify import markdownify as md

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

CONFIG_PATH = ROOT_DIR / "config" / "portals.yaml"

from tools.db import backup_db, check_company_cooldown, flagged_verdict, get_db, init_db, upsert_job
from tools.net import get_with_retry
from tools.posting_guard import guard
from tools.logger import get_logger, METRICS

logger = get_logger("ingest_crawl")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def crawl_unitary_fund(url: str = "https://unitary.fund/jobs/") -> List[Dict[str, Any]]:
    """Specialized crawler for the Unitary Fund Open-Source Quantum Job Board."""
    logger.info(f"Crawling Unitary Fund jobs from {url}")
    try:
        resp = get_with_retry(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"Unitary Fund returned HTTP {resp.status_code}")
            return []
    except Exception as e:
        logger.error(f"Failed to fetch Unitary Fund: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    jobs = []

    # Unitary Fund lists jobs inside article or list containers
    job_cards = soup.select(".job-listing, article, .job, li")
    for card in job_cards:
        link_elem = card.select_one("a[href]")
        if not link_elem:
            continue
        href = link_elem["href"]
        if not href.startswith("http"):
            href = f"https://unitary.fund{href}" if href.startswith("/") else f"https://unitary.fund/jobs/{href}"

        title_text = link_elem.get_text(strip=True)
        if not title_text or len(title_text) < 4 or any(w in title_text.lower() for w in ["home", "about", "blog", "privacy", "contact"]):
            continue

        raw_html = str(card)
        markdown_desc = md(raw_html, strip=["script", "style"])
        if len(markdown_desc) < 30:
            continue

        jobs.append({
            "title": title_text,
            "company": "Unitary Fund Community",
            "url": href,
            "portal": "crawl4ai:unitary_fund",
            "location": "Remote / Hybrid",
            "is_remote": True,
            "raw_description": markdown_desc,
        })

    return jobs


def run_niche_crawling(config_file: Optional[Path] = None, db_path: Optional[Path] = None) -> Dict[str, int]:
    """Execute crawlers for all enabled niche portals in config."""
    cfg_path = config_file or CONFIG_PATH
    if not cfg_path.exists():
        logger.error(f"Configuration file not found: {cfg_path}")
        return {"total": 0, "new": 0, "refreshed": 0}

    with open(cfg_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    niche_portals = config.get("niche_portals", [])
    init_db(db_path)
    backup_db(db_path, min_interval_minutes=60)
    stats = {"total": 0, "new": 0, "refreshed": 0, "portals_queried": 0}

    with get_db(db_path) as conn:
        for p in niche_portals:
            if not p.get("enabled", True):
                continue
            name = p.get("name")
            parser_type = p.get("parser")
            url = p.get("url")

            stats["portals_queried"] += 1
            postings = []

            if parser_type == "unitary_fund":
                postings = crawl_unitary_fund(url)
            elif parser_type == "generic_page":
                logger.info(f"Generic markdown crawl for {name} ({url})")
                try:
                    resp = get_with_retry(url, headers=HEADERS, timeout=15)
                    if resp.status_code == 200:
                        soup = BeautifulSoup(resp.text, "html.parser")
                        postings.append({
                            "title": soup.title.string.strip() if soup.title else name,
                            "company": name,
                            "url": url,
                            "portal": f"crawl4ai:{name.lower().replace(' ', '_')}",
                            "location": "Remote",
                            "is_remote": True,
                            "raw_description": md(str(soup.body or soup)),
                        })
                except Exception as e:
                    logger.error(f"Failed generic crawl on {url}: {e}")
                    continue
            else:
                logger.warning(f"No parser '{parser_type}' for {name}; skipping (use unitary_fund or generic_page)")
                continue

            for post in postings:
                stats["total"] += 1
                METRICS.increment("ingested_total")

                raw_desc = post.get("raw_description", "")
                cleaned_desc, findings = guard(raw_desc, mode="text")
                if "⚠" in findings.get("verdict", ""):
                    logger.warning(f"Security Alert on {post['company']} - {post['title']}", verdict=findings["verdict"])
                    METRICS.increment("sanitized_flagged")
                else:
                    METRICS.increment("sanitized_clean")

                job_id, is_new = upsert_job(
                    conn=conn,
                    title=post["title"],
                    company=post["company"],
                    url=post["url"],
                    portal=post["portal"],
                    location=post.get("location", ""),
                    is_remote=post.get("is_remote", True),
                    raw_description=raw_desc,
                    cleaned_description=cleaned_desc,
                    guard_verdict=flagged_verdict(findings),
                )

                if is_new:
                    stats["new"] += 1
                else:
                    stats["refreshed"] += 1
                    METRICS.increment("duplicates_skipped")

    logger.info("Niche Crawling Complete", **stats)
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crawl niche quantum portals into Markdown")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to portals.yaml")
    args = parser.parse_args()
    run_niche_crawling(config_file=args.config)
    METRICS.print_summary()

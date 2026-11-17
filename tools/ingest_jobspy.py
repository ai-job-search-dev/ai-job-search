#!/usr/bin/env python3
"""JobSpy Generalist Aggregator Ingestion (LinkedIn, Indeed, Glassdoor, Google).

Consumes configurations from config/portals.yaml, queries jobspy, sanitizes
descriptions via posting_guard, and persists records in SQLite under status NEW.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

CONFIG_PATH = ROOT_DIR / "config" / "portals.yaml"

from tools.db import get_db, init_db, upsert_job, check_company_cooldown
from tools.posting_guard import guard
from tools.logger import get_logger, METRICS

logger = get_logger("ingest_jobspy")

try:
    from jobspy import scrape_jobs
    JOBSPY_AVAILABLE = True
except ImportError:
    JOBSPY_AVAILABLE = False


def _clean(value: Any) -> Any:
    """Map pandas NaN/NaT/None to None so they are not stringified or treated as truthy."""
    if value is None:
        return None
    try:
        if value != value:
            return None
    except Exception:
        pass
    return value


def _to_float(value: Any) -> Optional[float]:
    value = _clean(value)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def run_jobspy_ingestion(
    site_names: Optional[List[str]] = None,
    search_term: Optional[str] = None,
    location: Optional[str] = None,
    results_wanted: Optional[int] = None,
    hours_old: Optional[int] = None,
    is_remote: Optional[bool] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, int]:
    """Execute JobSpy queries based on config or CLI arguments."""
    if not JOBSPY_AVAILABLE:
        logger.error("python-jobspy is not installed in the current environment.")
        return {"total": 0, "new": 0, "refreshed": 0}

    # Load config defaults
    config = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f).get("jobspy", {})

    sites = site_names or config.get("sites", ["linkedin", "indeed"])
    terms = [search_term] if search_term else config.get("search_terms", ["Quantum Software Engineer"])
    loc = location or (config.get("locations", ["Remote"])[0] if config.get("locations") else "Remote")
    limit = results_wanted or config.get("results_wanted", 20)
    hours = hours_old or config.get("hours_old", 72)
    remote_flag = is_remote if is_remote is not None else config.get("is_remote", True)
    country_indeed = config.get("country_indeed", "USA")

    init_db(db_path)
    stats = {"total": 0, "new": 0, "refreshed": 0, "cooldown_skipped": 0}

    with get_db(db_path) as conn:
        for term in terms:
            logger.info(
                f"Querying JobSpy for '{term}'",
                sites=sites,
                location=loc,
                results_wanted=limit,
                hours_old=hours,
            )
            try:
                jobs_df = scrape_jobs(
                    site_name=sites,
                    search_term=term,
                    location=loc,
                    results_wanted=limit,
                    hours_old=hours,
                    country_indeed=country_indeed,
                    is_remote=remote_flag,
                    description_format="markdown",
                )
            except Exception as e:
                logger.error(f"JobSpy scrape error for term '{term}': {e}")
                continue

            if jobs_df is None or jobs_df.empty:
                logger.info(f"No results returned for '{term}'")
                continue

            for _, row in jobs_df.iterrows():
                stats["total"] += 1
                METRICS.increment("ingested_total")

                company = str(_clean(row.get("company")) or "Unknown Company").strip()
                title = str(_clean(row.get("title")) or "Unknown Role").strip()
                url = str(_clean(row.get("job_url")) or "").strip()
                site = str(_clean(row.get("site")) or "jobspy").lower()
                job_location = str(_clean(row.get("location")) or loc).strip()
                is_rem = bool(_clean(row.get("is_remote")) or "remote" in job_location.lower())

                # Check 30-day cooldown
                in_cooldown, reason = check_company_cooldown(conn, company)
                if in_cooldown:
                    logger.info(f"Skipping job from {company}: {reason}")
                    stats["cooldown_skipped"] += 1
                    METRICS.increment("cooldown_skipped")
                    continue

                raw_desc = str(_clean(row.get("description")) or "").strip()
                cleaned_desc, findings = guard(raw_desc, mode="text")
                if "⚠" in findings.get("verdict", ""):
                    logger.warning(f"Security Alert on {company} - {title}", verdict=findings["verdict"])
                    METRICS.increment("sanitized_flagged")
                else:
                    METRICS.increment("sanitized_clean")

                salary_min = _to_float(row.get("min_amount"))
                salary_max = _to_float(row.get("max_amount"))
                currency = str(_clean(row.get("currency")) or "USD") if (salary_min or salary_max) else None

                job_id, is_new = upsert_job(
                    conn=conn,
                    title=title,
                    company=company,
                    url=url,
                    portal=f"jobspy:{site}",
                    location=job_location,
                    is_remote=is_rem,
                    raw_description=raw_desc,
                    cleaned_description=cleaned_desc,
                    salary_min=salary_min,
                    salary_max=salary_max,
                    currency=currency,
                )

                if is_new:
                    stats["new"] += 1
                else:
                    stats["refreshed"] += 1
                    METRICS.increment("duplicates_skipped")

    logger.info("JobSpy Ingestion Complete", **stats)
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest jobs using python-jobspy")
    parser.add_argument("--term", type=str, default=None, help="Search query string")
    parser.add_argument("--site", type=str, nargs="+", default=None, help="Sites to scrape (linkedin, indeed, glassdoor, google)")
    parser.add_argument("--limit", type=int, default=None, help="Max results wanted")
    parser.add_argument("--location", type=str, default=None, help="Target location (e.g. Remote, USA)")
    args = parser.parse_args()

    run_jobspy_ingestion(
        site_names=args.site,
        search_term=args.term,
        location=args.location,
        results_wanted=args.limit,
    )
    METRICS.print_summary()

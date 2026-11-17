#!/usr/bin/env python3
"""JobSpy generalist aggregator ingestion (LinkedIn, Indeed, Glassdoor, Google).

Reads the `jobspy:` section of config/portals.yaml, runs every search term in every region,
keeps only remote postings when `remote_only` is set, sanitizes descriptions with
posting_guard and stores new jobs in SQLite with status NEW.

Remote handling differs per site: Indeed, Glassdoor and Google have a native remote filter;
LinkedIn ignores it, so " remote" is added to the term and results are filtered afterwards.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

CONFIG_PATH = ROOT_DIR / "config" / "portals.yaml"

from tools.db import backup_db, check_company_cooldown, flagged_verdict, get_db, init_db, upsert_job
from tools.logger import METRICS, get_logger
from tools.posting_guard import guard
from tools.preferences import load_preferences

logger = get_logger("ingest_jobspy")

try:
    from jobspy import scrape_jobs

    JOBSPY_AVAILABLE = True
except ImportError:
    JOBSPY_AVAILABLE = False

DEFAULT_REGIONS = [{"name": "Worldwide remote", "location": "Remote", "country_indeed": "usa"}]
REMOTE_SIGNAL = re.compile(r"\bremote\b|work from home|\bwfh\b|\banywhere\b|telecommut", re.IGNORECASE)
HYBRID_SIGNAL = re.compile(r"\bhybrid\b|\bon-?site\b|in[- ]office", re.IGNORECASE)


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


def is_remote_posting(title: str, location: str, description: str, flagged_remote: bool) -> bool:
    """True when the posting is remote and not described as hybrid or on-site."""
    head = f"{title} {location}"
    if HYBRID_SIGNAL.search(head):
        return False
    if flagged_remote or REMOTE_SIGNAL.search(head):
        return True
    return bool(re.search(r"fully remote|100% remote|remote[- ]first|remote position|work from home", description, re.I))


class _SiteErrorCounter(logging.Handler):
    """JobSpy logs scraper failures and returns an empty frame; count them so we can retry."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        self.count += 1


def search_with_retry(scraper: Any, kwargs: Dict[str, Any], sleep: Any, delay: float, attempts: int = 2):
    """Call the scraper, retrying once when it failed and returned nothing. Returns (frame, errors)."""
    counter = _SiteErrorCounter()
    jobspy_logger = logging.getLogger("jobspy")
    jobspy_logger.addHandler(counter)
    frame = None
    try:
        for attempt in range(attempts):
            before = counter.count
            try:
                frame = scraper(**kwargs)
            except Exception as exc:
                logger.error(f"JobSpy raised for {kwargs.get('site_name')}: {exc}")
                frame = None
                counter.count += 1
            failed = counter.count > before
            if not failed or (frame is not None and not frame.empty):
                break
            if attempt + 1 < attempts:
                sleep(delay * 2)
        sleep(delay)
    finally:
        jobspy_logger.removeHandler(counter)
    return frame, counter.count


def is_relevant(title: str, description: str, keywords: List[str]) -> bool:
    """True when no keywords are configured or any appears in the title or description."""
    if not keywords:
        return True
    text = f"{title} {description}".lower()
    return any(k.lower() in text for k in keywords)


def load_config(config_path: Path = CONFIG_PATH) -> Dict[str, Any]:
    if config_path.exists():
        return (yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}).get("jobspy", {})
    return {}


def build_queries(
    config: Dict[str, Any],
    term: Optional[str] = None,
    region_names: Optional[List[str]] = None,
    location: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Cross search terms with regions. A CLI location builds an ad-hoc region."""
    terms = [term] if term else config.get("search_terms", ["Quantum Software Engineer"])
    regions = config.get("regions") or DEFAULT_REGIONS
    if location:
        known = next((r for r in regions if r["location"].casefold() == location.casefold()), None)
        regions = [known or {"name": location, "location": location, "country_indeed": config.get("country_indeed", "usa")}]
    elif region_names:
        wanted = {n.casefold() for n in region_names}
        regions = [r for r in regions if r["name"].casefold() in wanted or r["location"].casefold() in wanted]
    return [{"term": t, "region": r} for r in regions for t in terms]


def run_jobspy_ingestion(
    site_names: Optional[List[str]] = None,
    search_term: Optional[str] = None,
    location: Optional[str] = None,
    results_wanted: Optional[int] = None,
    hours_old: Optional[int] = None,
    is_remote: Optional[bool] = None,
    db_path: Optional[Path] = None,
    region_names: Optional[List[str]] = None,
    max_queries: Optional[int] = None,
    config_path: Path = CONFIG_PATH,
    scraper: Any = None,
    sleep: Any = time.sleep,
) -> Dict[str, int]:
    """Run the configured searches. `scraper` and `sleep` are injectable for tests."""
    scraper = scraper or (scrape_jobs if JOBSPY_AVAILABLE else None)
    if scraper is None:
        logger.error("python-jobspy is not installed in the current environment.")
        return {"total": 0, "new": 0, "refreshed": 0}

    config = load_config(config_path)
    prefs = load_preferences()
    sites = [s.lower() for s in (site_names or config.get("sites", ["linkedin", "indeed"]))]
    limit = results_wanted or config.get("results_wanted", 20)
    hours = hours_old or config.get("hours_old", 72)
    remote_only = is_remote if is_remote is not None else config.get("remote_only", prefs.remote_only)
    delay = float(config.get("delay_seconds", 4))
    fetch_linkedin = bool(config.get("linkedin_fetch_description", True))
    keywords = [str(k) for k in config.get("require_keywords", [])]

    queries = build_queries(config, search_term, region_names, location)
    if max_queries:
        queries = queries[: int(max_queries)]
    native_sites = [s for s in sites if s != "linkedin"]
    has_linkedin = "linkedin" in sites

    init_db(db_path)
    backup_db(db_path, min_interval_minutes=60)
    stats = {"total": 0, "new": 0, "refreshed": 0, "cooldown_skipped": 0, "not_remote_skipped": 0,
             "queries": len(queries), "site_errors": 0, "irrelevant_skipped": 0}

    with get_db(db_path) as conn:
        for number, query in enumerate(queries, 1):
            term, region = query["term"], query["region"]
            calls = []
            if native_sites:
                calls.append((native_sites, term, True))
            if has_linkedin:
                calls.append((["linkedin"], f"{term} remote" if remote_only else term, False))

            for call_sites, call_term, native_remote in calls:
                logger.info(
                    f"[{number}/{len(queries)}] '{call_term}' in {region['name']} via {','.join(call_sites)}",
                    hours_old=hours, results_wanted=limit,
                )
                jobs_df, errors = search_with_retry(
                    scraper,
                    dict(
                        site_name=call_sites,
                        search_term=call_term,
                        location=region["location"],
                        results_wanted=limit,
                        hours_old=hours,
                        country_indeed=region.get("country_indeed", "usa"),
                        is_remote=bool(remote_only and native_remote),
                        description_format="markdown",
                        fetch_description=fetch_linkedin,
                        verbose=0,
                    ),
                    sleep, delay,
                )
                stats["site_errors"] += errors
                fetched = 0 if jobs_df is None else len(jobs_df)
                new_before = stats["new"]
                if jobs_df is None or jobs_df.empty:
                    logger.info("  -> 0 rows", errors=errors)
                    continue

                for _, row in jobs_df.iterrows():
                    company = str(_clean(row.get("company")) or "Unknown Company").strip()
                    title = str(_clean(row.get("title")) or "Unknown Role").strip()
                    url = str(_clean(row.get("job_url")) or "").strip()
                    site = str(_clean(row.get("site")) or "jobspy").lower()
                    job_location = str(_clean(row.get("location")) or region["location"]).strip()
                    raw_desc = str(_clean(row.get("description")) or "").strip()
                    flagged = bool(_clean(row.get("is_remote")))
                    if not url:
                        continue

                    if not is_relevant(title, raw_desc, keywords):
                        stats["irrelevant_skipped"] += 1
                        continue
                    remote_ok = is_remote_posting(title, job_location, raw_desc, flagged)
                    if remote_only and not remote_ok:
                        stats["not_remote_skipped"] += 1
                        continue

                    stats["total"] += 1
                    METRICS.increment("ingested_total")
                    in_cooldown, reason = check_company_cooldown(conn, company)
                    if in_cooldown:
                        logger.info(f"Skipping job from {company}: {reason}")
                        stats["cooldown_skipped"] += 1
                        METRICS.increment("cooldown_skipped")
                        continue

                    cleaned_desc, findings = guard(raw_desc, mode="text")
                    if "⚠" in findings.get("verdict", ""):
                        logger.warning(f"Security Alert on {company} - {title}", verdict=findings["verdict"])
                        METRICS.increment("sanitized_flagged")
                    else:
                        METRICS.increment("sanitized_clean")

                    salary_min = _to_float(row.get("min_amount"))
                    salary_max = _to_float(row.get("max_amount"))
                    currency = str(_clean(row.get("currency")) or "USD") if (salary_min or salary_max) else None

                    _, is_new = upsert_job(
                        conn=conn, title=title, company=company, url=url, portal=f"jobspy:{site}",
                        location=job_location, is_remote=remote_ok, raw_description=raw_desc,
                        cleaned_description=cleaned_desc, salary_min=salary_min, salary_max=salary_max,
                        currency=currency, guard_verdict=flagged_verdict(findings),
                    )
                    if is_new:
                        stats["new"] += 1
                    else:
                        stats["refreshed"] += 1
                        METRICS.increment("duplicates_skipped")
                conn.commit()
                logger.info(f"  -> {fetched} rows, {stats['new'] - new_before} new", errors=errors)

    logger.info("JobSpy Ingestion Complete", **stats)
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest remote jobs using python-jobspy")
    parser.add_argument("--term", type=str, default=None, help="Single search term (overrides config)")
    parser.add_argument("--site", type=str, nargs="+", default=None, help="linkedin indeed glassdoor google")
    parser.add_argument("--limit", type=int, default=None, help="Max results per query")
    parser.add_argument("--location", type=str, default=None, help="One location instead of the configured regions")
    parser.add_argument("--region", type=str, nargs="+", default=None, help="Only these configured regions (by name)")
    parser.add_argument("--hours-old", type=int, default=None)
    parser.add_argument("--max-queries", type=int, default=None, help="Stop after this many term/region pairs")
    parser.add_argument("--include-onsite", action="store_true", help="Do not restrict to remote postings")
    args = parser.parse_args()

    run_jobspy_ingestion(
        site_names=args.site, search_term=args.term, location=args.location, results_wanted=args.limit,
        hours_old=args.hours_old, is_remote=False if args.include_onsite else None,
        region_names=args.region, max_queries=args.max_queries,
    )
    METRICS.print_summary()

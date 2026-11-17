#!/usr/bin/env python3
"""One command for the automatic part of the job search: ingest, rank, and a digest of what is new.

    python tools/pipeline.py run                 # ingest ATS + JobSpy, rank, write a digest
    python tools/pipeline.py run --dry-run       # show what would happen and the LLM cost estimate
    python tools/pipeline.py status              # last runs, pipeline counts, LLM spend, backups
    python tools/pipeline.py digest --hours 48   # re-print shortlisted jobs from the last 48 hours

Tailoring and applying stay manual on purpose: the digest ends with the exact commands to run.
A run holds a lock so two runs never write at once, a failing step does not stop the others,
and every run is recorded in the database.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.db import (
    SCHEMA_VERSION,
    STATUS_SHORTLISTED,
    backup_db,
    get_db,
    init_db,
    mark_stale_jobs,
    resolve_db_path,
)
from tools.llm import DEFAULT_MODEL, estimate_cost, llm_configured, load_pricing, preferences_hash, price_for, total_spend
from tools.logger import METRICS, get_logger, safe_print
from tools.preferences import load_preferences
from tools.ranker import MIN_DESCRIPTION_CHARS
logger = get_logger("pipeline")

DEFAULT_SOURCES = ("ats", "jobspy")
ALL_SOURCES = ("ats", "jobspy", "crawl")
LOCK_STALE_SECONDS = 6 * 3600
REPORTS_DIR = ROOT_DIR / "reports"


class LockHeld(RuntimeError):
    """Another pipeline run is in progress."""


def _pid_alive(pid: int) -> Optional[bool]:
    """True/False on POSIX. None where liveness cannot be tested safely (os.kill would terminate on Windows)."""
    if os.name == "nt":
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class RunLock:
    """A lock file next to the database. A lock left by a dead process or older than six hours is taken over."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.path = resolve_db_path(db_path).parent / ".pipeline.lock"
        self._owned = False

    def _read(self) -> Dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _is_stale(self) -> bool:
        info = self._read()
        if not info:
            return True
        if time.time() - float(info.get("started", 0)) > LOCK_STALE_SECONDS:
            return True
        return _pid_alive(int(info.get("pid", 0))) is False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if self._is_stale():
                    try:
                        self.path.unlink()
                    except FileNotFoundError:
                        pass
                    continue
                info = self._read()
                raise LockHeld(f"another run is in progress (pid {info.get('pid')}, started {info.get('started_at')})")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"pid": os.getpid(), "started": time.time(), "started_at": datetime.now().isoformat(timespec="seconds")}, handle)
            self._owned = True
            return
        raise LockHeld("could not take the run lock")

    def release(self) -> None:
        if self._owned:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            self._owned = False

    def __enter__(self) -> "RunLock":
        self.acquire()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()


@dataclass
class Options:
    sources: List[str] = field(default_factory=lambda: list(DEFAULT_SOURCES))
    skip_ingest: bool = False
    limit: Optional[int] = None
    max_llm_calls: Optional[int] = None
    max_cost_usd: Optional[float] = None
    use_cache: bool = True
    refresh_stale: bool = False
    close_stale_days: Optional[int] = None
    jobspy_max_queries: Optional[int] = None


@dataclass
class StepResult:
    name: str
    status: str = "ok"
    seconds: float = 0.0
    detail: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class RunReport:
    run_id: Optional[int] = None
    status: str = "ok"
    steps: List[StepResult] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    usage: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, int] = field(default_factory=dict)
    digest: List[Dict[str, Any]] = field(default_factory=list)
    digest_path: Optional[Path] = None

    @property
    def exit_code(self) -> int:
        return 1 if any(s.status == "failed" for s in self.steps) else 0


def _run_step(report: RunReport, name: str, fn: Callable[[], Optional[Dict[str, Any]]], enabled: bool = True) -> Dict[str, Any]:
    if not enabled:
        report.steps.append(StepResult(name, "skipped"))
        return {}
    started = time.time()
    try:
        detail = fn() or {}
        report.steps.append(StepResult(name, "ok", round(time.time() - started, 1), detail))
        return detail
    except Exception as exc:
        message = f"{type(exc).__name__}: {str(exc)[:200]}"
        logger.error(f"Step '{name}' failed: {message}")
        report.steps.append(StepResult(name, "failed", round(time.time() - started, 1), error=message))
        return {}


def default_ingesters(opts: Options) -> Dict[str, Callable[[Optional[Path]], Dict[str, Any]]]:
    def ats(db: Optional[Path]) -> Dict[str, Any]:
        from tools.ingest_ats import run_ats_ingestion

        return run_ats_ingestion(db_path=db)

    def jobspy(db: Optional[Path]) -> Dict[str, Any]:
        from tools.ingest_jobspy import run_jobspy_ingestion

        return run_jobspy_ingestion(db_path=db, max_queries=opts.jobspy_max_queries)

    def crawl(db: Optional[Path]) -> Dict[str, Any]:
        from tools.ingest_crawl import run_niche_crawling

        return run_niche_crawling(db_path=db)

    return {"ats": ats, "jobspy": jobspy, "crawl": crawl}


def build_digest(conn: sqlite3.Connection, since: str, limit: int = 25) -> List[Dict[str, Any]]:
    """Shortlisted jobs whose latest evaluation was written at or after `since` (a SQLite UTC timestamp)."""
    rows = conn.execute(
        """
        SELECT j.id, co.name AS company, j.title, j.location, j.url, j.portal, e.overall_score AS score,
               e.eval_source AS source, e.raw_eval
        FROM jobs j
        JOIN companies co ON co.id = j.company_id
        JOIN evaluations e ON e.id = (SELECT MAX(id) FROM evaluations WHERE job_id = j.id)
        WHERE j.status = ? AND e.created_at >= ?
        ORDER BY e.overall_score DESC, j.id ASC LIMIT ?
        """,
        (STATUS_SHORTLISTED, since, limit),
    ).fetchall()
    out = []
    for r in rows:
        try:
            reasoning = json.loads(r["raw_eval"] or "{}").get("reasoning", "")
        except ValueError:
            reasoning = ""
        out.append({**{k: r[k] for k in ("id", "company", "title", "location", "url", "portal", "score", "source")}, "reasoning": reasoning})
    return out


def render_digest(digest: List[Dict[str, Any]], report: Optional[RunReport] = None, counts: Optional[Dict[str, int]] = None) -> str:
    lines = [f"# Job search digest ({datetime.now().strftime('%Y-%m-%d %H:%M')})", ""]
    if counts:
        lines += [f"New postings: {counts.get('new', 0)}. Evaluated: {counts.get('evaluated', 0)}. Shortlisted: {len(digest)}.", ""]
    if report:
        failed = [s for s in report.steps if s.status == "failed"]
        for step in failed:
            lines.append(f"- FAILED step `{step.name}`: {step.error}")
        for warning in report.warnings:
            lines.append(f"- Warning: {warning}")
        if report.usage.get("calls") or report.usage.get("cache_hits"):
            u = report.usage
            lines.append(f"- LLM: {u.get('calls', 0)} calls, {u.get('cache_hits', 0)} from cache, "
                         f"{u.get('prompt_tokens', 0) + u.get('completion_tokens', 0)} tokens, ${u.get('cost_usd', 0):.4f}")
        if failed or report.warnings or report.usage:
            lines.append("")
    if not digest:
        lines.append("No new shortlisted postings.")
        return "\n".join(lines) + "\n"
    for item in digest:
        lines.append(f"## [{item['score']}/10] {item['company']} - {item['title']}")
        lines.append(f"- Location: {item['location'] or 'n/a'} | source: {item['portal']} | scored by: {item['source'] or 'heuristic'}")
        lines.append(f"- Link: {item['url']}")
        if item["reasoning"]:
            lines.append(f"- Why: {item['reasoning'][:300]}")
        lines.append(f"- Next: `python tools/tailor.py --job-id {item['id']}`, then `python tools/browser_apply.py --job-id {item['id']}`")
        lines.append("")
    return "\n".join(lines)


def run_pipeline(
    opts: Optional[Options] = None,
    db_path: Optional[Path] = None,
    profile_path: Optional[Path] = None,
    llm_client: Any = None,
    ingesters: Optional[Dict[str, Callable[[Optional[Path]], Dict[str, Any]]]] = None,
    reports_dir: Optional[Path] = None,
) -> RunReport:
    from tools.ranker import run_ranker

    opts = opts or Options()
    ingesters = ingesters or default_ingesters(opts)
    report = RunReport()
    init_db(db_path)

    with RunLock(db_path):
        with get_db(db_path) as conn:
            started = conn.execute("SELECT datetime('now')").fetchone()[0]
            before = METRICS.to_dict()
            report.run_id = conn.execute("INSERT INTO pipeline_runs (status) VALUES ('running')").lastrowid
            conn.commit()
            new_before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

        _run_step(report, "backup", lambda: {"path": str(backup_db(db_path, min_interval_minutes=30) or "recent backup exists")})

        for name in ALL_SOURCES:
            wanted = name in opts.sources and not opts.skip_ingest
            detail = _run_step(report, f"ingest_{name}", lambda n=name: ingesters[n](db_path), enabled=wanted)
            if detail.get("companies_failed"):
                report.warnings.append(f"{detail['companies_failed']} ATS source(s) failed; see the log for the names")
            if detail.get("site_errors"):
                report.warnings.append(f"JobSpy reported {detail['site_errors']} site error(s)")

        def close_stale() -> Dict[str, Any]:
            with get_db(db_path) as c:
                return {"closed": mark_stale_jobs(c, opts.close_stale_days)}

        _run_step(report, "close_stale", close_stale, enabled=bool(opts.close_stale_days))

        usage: Dict[str, Any] = {}

        def rank() -> Dict[str, Any]:
            results = run_ranker(
                limit=opts.limit, profile_path=profile_path, llm_client=llm_client, max_llm_calls=opts.max_llm_calls,
                max_cost_usd=opts.max_cost_usd, use_cache=opts.use_cache, stale_only=opts.refresh_stale,
                usage_out=usage, db_path=db_path,
            )
            return {"evaluated": len(results), "shortlisted": sum(1 for r in results if r["status"] == STATUS_SHORTLISTED)}

        rank_detail = _run_step(report, "rank", rank)
        report.usage = usage
        if usage.get("skipped_by_budget"):
            report.warnings.append(f"LLM budget reached: {usage['skipped_by_budget']} posting(s) scored by the keyword heuristic instead")
        if usage.get("errors"):
            report.warnings.append(f"{usage['errors']} LLM call(s) failed and fell back to the heuristic")

        with get_db(db_path) as conn:
            report.digest = build_digest(conn, started)
            new_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] - new_before
            counts = {"new": new_jobs, "evaluated": rank_detail.get("evaluated", 0)}
            after = METRICS.to_dict()
            report.metrics = {k: after[k] - before.get(k, 0) for k in after if after[k] != before.get(k, 0)}
            report.status = "failed" if report.exit_code else ("partial" if report.warnings else "ok")
            conn.execute(
                "UPDATE pipeline_runs SET finished_at = datetime('now'), status = ?, steps = ?, metrics = ?, llm_cost_usd = ?, notes = ? WHERE id = ?",
                (report.status, json.dumps([s.__dict__ for s in report.steps], default=str), json.dumps(report.metrics),
                 float(usage.get("cost_usd", 0.0) or 0.0), "; ".join(report.warnings) or None, report.run_id),
            )

    folder = Path(reports_dir) if reports_dir else REPORTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    report.digest_path = folder / f"digest-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    report.digest_path.write_text(render_digest(report.digest, report, counts), encoding="utf-8")
    return report


def plan_run(opts: Options, db_path: Optional[Path] = None, profile_path: Optional[Path] = None) -> Dict[str, Any]:
    """What a run would do, without touching the network or writing anything."""
    path = resolve_db_path(db_path)
    prefs = load_preferences(profile_path)
    model = os.environ.get("AI_JOB_SEARCH_MODEL", DEFAULT_MODEL)
    pricing = load_pricing()
    plan: Dict[str, Any] = {
        "database": str(path),
        "database_exists": path.exists(),
        "ingest": [] if opts.skip_ingest else [s for s in ALL_SOURCES if s in opts.sources],
        "close_stale_days": opts.close_stale_days,
        "refresh_stale": opts.refresh_stale,
        "llm": {"configured": llm_configured(), "model": model, "price_known": price_for(model, pricing) is not None,
                "max_calls": opts.max_llm_calls, "max_cost_usd": opts.max_cost_usd},
        "locked": (path.parent / ".pipeline.lock").exists(),
    }
    pending = stale = 0
    if path.exists():
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('NEW', 'DISCOVERED')").fetchone()[0]
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(evaluations)")]
            if "prefs_hash" in cols:
                stale = conn.execute(
                    "SELECT COUNT(*) FROM jobs j JOIN evaluations e ON e.id = (SELECT MAX(id) FROM evaluations WHERE job_id = j.id) "
                    "WHERE j.status IN ('NEW', 'DISCOVERED', 'SHORTLISTED', 'IGNORED') AND (e.prefs_hash IS NULL OR e.prefs_hash != ?) "
                    "AND LENGTH(TRIM(COALESCE(NULLIF(j.cleaned_description, ''), j.raw_description, ''))) >= " + str(MIN_DESCRIPTION_CHARS),
                    (preferences_hash(prefs),),
                ).fetchone()[0]
            else:
                stale = conn.execute(
                    "SELECT COUNT(*) FROM jobs j WHERE j.status IN ('SHORTLISTED', 'IGNORED') "
                    "AND EXISTS (SELECT 1 FROM evaluations e WHERE e.job_id = j.id)"
                ).fetchone()[0]
        except sqlite3.DatabaseError:
            pass
        finally:
            conn.close()
    to_rank = pending + (stale if opts.refresh_stale else 0)
    plan["to_rank"] = {"pending_new": pending, "stale_evaluations": stale, "will_rank": to_rank}
    if plan["llm"]["configured"] and plan["llm"]["price_known"]:
        calls = min(to_rank, opts.max_llm_calls) if opts.max_llm_calls else to_rank
        plan["llm"]["estimated_cost_usd"] = round(estimate_cost(model, calls, pricing) or 0.0, 4)
        plan["llm"]["note"] = "upper bound: cached answers are free"
    return plan


def render_plan(plan: Dict[str, Any]) -> str:
    llm = plan["llm"]
    lines = ["DRY RUN: nothing was fetched or written.", f"Database:  {plan['database']} ({'found' if plan['database_exists'] else 'will be created'})",
             f"Ingest:    {', '.join(plan['ingest']) or 'skipped'}",
             f"To rank:   {plan['to_rank']['will_rank']} (new: {plan['to_rank']['pending_new']}, out-of-date evaluations: {plan['to_rank']['stale_evaluations']}"
             + ("" if plan["refresh_stale"] else "; re-scored only with --refresh-stale") + ")"]
    if llm["configured"]:
        price = f"estimated cost up to ${llm['estimated_cost_usd']}" if "estimated_cost_usd" in llm else "no price for this model, so no estimate and no cost cap"
        caps = [f"max {llm['max_calls']} calls" if llm["max_calls"] else "", f"max ${llm['max_cost_usd']}" if llm["max_cost_usd"] else ""]
        lines.append(f"LLM:       {llm['model']} ({price}; {', '.join(c for c in caps if c) or 'no caps set'})")
    else:
        lines.append("LLM:       not configured (OPENAI_API_KEY unset), the keyword heuristic will score everything")
    if plan["locked"]:
        lines.append("Lock:      a lock file exists; a real run would stop unless it is stale")
    return "\n".join(lines)


def render_status(db_path: Optional[Path] = None) -> str:
    path = resolve_db_path(db_path)
    if not path.exists():
        return f"No database at {path}. Run `python tools/pipeline.py run` first."
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    out: List[str] = []
    try:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        out.append(f"Database: {path} (schema v{version}{'' if version == SCHEMA_VERSION else f', code expects v{SCHEMA_VERSION}: run any command to migrate'})")
        counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")}
        out.append("Jobs:     " + (", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "none"))
        if version >= 3:
            spend = total_spend(conn)
            out.append(f"LLM:      {spend['calls']} calls ({spend['cache_hits']} cached), {spend['prompt_tokens'] + spend['completion_tokens']} tokens, ${spend['cost_usd']:.4f} total")
            out.append("Recent runs (UTC):")
            runs = conn.execute("SELECT id, started_at, status, llm_cost_usd, notes FROM pipeline_runs ORDER BY id DESC LIMIT 5").fetchall()
            out += [f"  #{r['id']} {r['started_at']}  {r['status']:<8} ${r['llm_cost_usd'] or 0:.4f}" + (f"  {r['notes']}" if r["notes"] else "") for r in runs] or ["  none yet"]
    finally:
        conn.close()
    backups = sorted((path.parent / "backups").glob(f"{path.stem}-*.db"))
    out.append(f"Backups:  {len(backups)}" + (f", newest {datetime.fromtimestamp(backups[-1].stat().st_mtime):%Y-%m-%d %H:%M}" if backups else ""))
    lock = path.parent / ".pipeline.lock"
    out.append("Lock:     " + ("held (a run is in progress or crashed)" if lock.exists() else "free"))
    return "\n".join(out)


def render_summary(report: RunReport) -> str:
    lines = ["", f"Run #{report.run_id}: {report.status.upper()}"]
    for step in report.steps:
        extra = f" - {step.error}" if step.error else (f" {json.dumps(step.detail, default=str)[:110]}" if step.detail else "")
        lines.append(f"  {step.name:<14} {step.status:<8} {step.seconds:>6.1f}s{extra}")
    lines += [f"  ! {w}" for w in report.warnings]
    if report.digest:
        lines.append(f"\n{len(report.digest)} new shortlisted:")
        lines += [f"  [{d['score']}/10] #{d['id']} {d['company']} - {d['title']}" for d in report.digest[:10]]
        lines.append("Next: python tools/tailor.py --job-id N   then   python tools/browser_apply.py --job-id N")
    else:
        lines.append("\nNo new shortlisted postings.")
    if report.digest_path:
        lines.append(f"Digest saved to {report.digest_path}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest, rank and summarise in one command")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run the pipeline")
    run.add_argument("--sources", nargs="+", choices=ALL_SOURCES, default=list(DEFAULT_SOURCES))
    run.add_argument("--skip-ingest", action="store_true", help="Only rank what is already in the database")
    run.add_argument("--limit", type=int, default=None, help="Max postings to rank")
    run.add_argument("--max-llm-calls", type=int, default=None)
    run.add_argument("--max-cost", type=float, default=None, help="Stop paying for LLM calls past this many USD")
    run.add_argument("--no-cache", action="store_true")
    run.add_argument("--refresh-stale", action="store_true", help="Also re-score evaluations made under older preferences")
    run.add_argument("--close-stale-days", type=int, default=None, help="Close postings no ingestion has seen for N days")
    run.add_argument("--jobspy-max-queries", type=int, default=None)
    run.add_argument("--dry-run", action="store_true")
    sub.add_parser("status", help="Show runs, counts, LLM spend and backups")
    digest = sub.add_parser("digest", help="Print shortlisted jobs evaluated recently")
    digest.add_argument("--hours", type=float, default=24.0)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "status":
        safe_print(render_status())
        return 0
    if args.command == "digest":
        init_db()
        with get_db() as conn:
            since = conn.execute("SELECT datetime('now', ?)", (f"-{args.hours} hours",)).fetchone()[0]
            safe_print(render_digest(build_digest(conn, since)))
        return 0

    opts = Options(
        sources=args.sources, skip_ingest=args.skip_ingest, limit=args.limit, max_llm_calls=args.max_llm_calls,
        max_cost_usd=args.max_cost, use_cache=not args.no_cache, refresh_stale=args.refresh_stale,
        close_stale_days=args.close_stale_days, jobspy_max_queries=args.jobspy_max_queries,
    )
    if args.dry_run:
        safe_print(render_plan(plan_run(opts)))
        return 0
    try:
        report = run_pipeline(opts)
    except LockHeld as exc:
        print(f"error: {exc}. If it crashed, delete the .pipeline.lock file beside the database.", file=sys.stderr)
        return 2
    safe_print(render_summary(report))
    METRICS.print_summary()
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())

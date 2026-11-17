#!/usr/bin/env python3
"""SQLite Database and State Engine for ai-job-search.

Provides transaction-safe storage, deduplication, company cooldown tracking,
and lifecycle state machine management for job opportunities.
"""

from __future__ import annotations

import json
import os
import sqlite3
import unicodedata
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

DEFAULT_DB_PATH = ROOT_DIR / "job_search.db"

# Canonical job statuses
STATUS_NEW = "NEW"
STATUS_DISCOVERED = "NEW"  # Backward-compatible alias
STATUS_SHORTLISTED = "SHORTLISTED"
STATUS_IGNORED = "IGNORED"
STATUS_TAILORED = "TAILORED"
STATUS_READY_TO_APPLY = "READY_TO_APPLY"
STATUS_APPLIED = "APPLIED"
STATUS_INTERVIEW = "INTERVIEW"
STATUS_REJECTED = "REJECTED"
STATUS_OFFER = "OFFER"
STATUS_ARCHIVED = "ARCHIVED"

VALID_STATUSES = {
    STATUS_NEW,
    "DISCOVERED",
    STATUS_SHORTLISTED,
    STATUS_IGNORED,
    STATUS_TAILORED,
    STATUS_READY_TO_APPLY,
    STATUS_APPLIED,
    STATUS_INTERVIEW,
    STATUS_REJECTED,
    STATUS_OFFER,
    STATUS_ARCHIVED,
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    normalized_name TEXT UNIQUE NOT NULL,
    domain TEXT,
    last_applied_at TIMESTAMP,
    cooldown_until TIMESTAMP,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_key TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    company_id INTEGER REFERENCES companies(id),
    location TEXT,
    is_remote BOOLEAN DEFAULT 0,
    url TEXT NOT NULL,
    portal TEXT NOT NULL,
    raw_description TEXT,
    cleaned_description TEXT,
    salary_min REAL,
    salary_max REAL,
    currency TEXT,
    status TEXT DEFAULT 'NEW',
    rejection_reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    overall_score REAL NOT NULL,
    software_fit_score REAL,
    quantum_fit_score REAL,
    phd_requirement_analysis TEXT,
    is_experimental_hardware BOOLEAN DEFAULT 0,
    veto_triggered BOOLEAN DEFAULT 0,
    veto_reason TEXT,
    recommended_channel TEXT,
    matched_skills TEXT,            -- JSON array
    missing_keywords TEXT,          -- JSON array
    tailoring_recommendations TEXT, -- JSON array
    strengths TEXT,                 -- JSON array (alias)
    gaps TEXT,                      -- JSON array (alias)
    raw_eval TEXT,                  -- Full raw JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    cv_path TEXT NOT NULL,
    cover_letter_path TEXT,
    outreach_message TEXT,
    ats_verified BOOLEAN DEFAULT 0,
    layout_verified BOOLEAN DEFAULT 0,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    submission_mode TEXT,
    stage TEXT DEFAULT 'SUBMITTED',
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_canonical_key ON jobs(canonical_key);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_company_id ON jobs(company_id);
CREATE INDEX IF NOT EXISTS idx_companies_normalized_name ON companies(normalized_name);
"""


def normalize_company_name(name: str) -> str:
    """Normalize company name for robust cooldown and deduplication comparison."""
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", name).casefold()
    ascii_text = text.encode("ascii", "ignore").decode("ascii")
    suffixes = [
        " inc.", " inc", " corp.", " corp", " llc", " ltd.", " ltd",
        " gmbh", " s.a.", " sa", " s.l.", " sl", " ag", " b.v.", " bv",
        " technologies", " technology", " tech", " labs", " lab", " quantum"
    ]
    for s in suffixes:
        if ascii_text.endswith(s):
            ascii_text = ascii_text[:-len(s)]
    return "".join(c for c in ascii_text if c.isalnum()).strip()


def get_db(db_path: Optional[Path | str] = None) -> sqlite3.Connection:
    """Get an open SQLite connection with Row factory and foreign keys enabled."""
    path = Path(db_path or os.environ.get("AI_JOB_SEARCH_DB") or DEFAULT_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def init_db(db_path: Optional[Path | str] = None) -> None:
    """Initialize SQLite database tables, indices, and run lightweight migrations."""
    with get_db(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        # Migrate existing evaluations table columns if absent
        cursor = conn.execute("PRAGMA table_info(evaluations)")
        columns = [row["name"] for row in cursor.fetchall()]
        if columns and "phd_requirement_analysis" not in columns:
            conn.execute("ALTER TABLE evaluations ADD COLUMN phd_requirement_analysis TEXT;")
        if columns and "is_experimental_hardware" not in columns:
            conn.execute("ALTER TABLE evaluations ADD COLUMN is_experimental_hardware BOOLEAN DEFAULT 0;")
        if columns and "matched_skills" not in columns:
            conn.execute("ALTER TABLE evaluations ADD COLUMN matched_skills TEXT;")
        if columns and "missing_keywords" not in columns:
            conn.execute("ALTER TABLE evaluations ADD COLUMN missing_keywords TEXT;")
        if columns and "tailoring_recommendations" not in columns:
            conn.execute("ALTER TABLE evaluations ADD COLUMN tailoring_recommendations TEXT;")


def get_or_create_company(
    conn: sqlite3.Connection, name: str, domain: Optional[str] = None
) -> int:
    """Get existing company id or insert new company."""
    norm_name = normalize_company_name(name)
    row = conn.execute(
        "SELECT id FROM companies WHERE normalized_name = ?", (norm_name,)
    ).fetchone()
    if row:
        return int(row["id"])

    cursor = conn.execute(
        "INSERT INTO companies (name, normalized_name, domain) VALUES (?, ?, ?)",
        (name.strip(), norm_name, domain.strip() if domain else None),
    )
    return cursor.lastrowid


def check_company_cooldown(
    conn: sqlite3.Connection, company_name: str, cooldown_days: int = 30
) -> Tuple[bool, Optional[str]]:
    """Check if company is under active cooldown period.

    Returns:
        (is_in_cooldown, reason_or_none)
    """
    norm_name = normalize_company_name(company_name)
    row = conn.execute(
        "SELECT name, last_applied_at, cooldown_until FROM companies WHERE normalized_name = ?",
        (norm_name,),
    ).fetchone()
    if not row or not row["cooldown_until"]:
        return False, None

    cooldown_until_str = row["cooldown_until"]
    try:
        cooldown_until = datetime.fromisoformat(cooldown_until_str)
        if cooldown_until.tzinfo is None:
            cooldown_until = cooldown_until.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if now < cooldown_until:
            days_left = (cooldown_until - now).days + 1
            return True, f"Company '{row['name']}' in cooldown until {cooldown_until.date()} ({days_left}d left)"
    except Exception:
        pass
    return False, None


def upsert_job(
    conn: sqlite3.Connection,
    title: str,
    company: str,
    url: str,
    portal: str,
    location: str = "",
    is_remote: bool = False,
    raw_description: str = "",
    cleaned_description: str = "",
    salary_min: Optional[float] = None,
    salary_max: Optional[float] = None,
    currency: Optional[str] = None,
    canonical_key: Optional[str] = None,
) -> Tuple[int, bool]:
    """Insert or update a job record using canonical deduplication.

    Returns:
        (job_id, is_new)
    """
    if not canonical_key:
        try:
            from tools.job_key import make_key
            canonical_key = make_key(company=company, title=title, url=url)
        except Exception:
            canonical_key = f"{normalize_company_name(company)}_{normalize_company_name(title)}"

    company_id = get_or_create_company(conn, company)

    existing = conn.execute(
        "SELECT id, status FROM jobs WHERE canonical_key = ?", (canonical_key,)
    ).fetchone()

    if existing:
        job_id = int(existing["id"])
        conn.execute(
            """
            UPDATE jobs SET
                title = ?,
                location = COALESCE(NULLIF(?, ''), location),
                is_remote = ?,
                url = ?,
                portal = ?,
                raw_description = COALESCE(NULLIF(?, ''), raw_description),
                cleaned_description = COALESCE(NULLIF(?, ''), cleaned_description),
                salary_min = COALESCE(?, salary_min),
                salary_max = COALESCE(?, salary_max),
                currency = COALESCE(?, currency),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                title,
                location,
                1 if is_remote else 0,
                url,
                portal,
                raw_description,
                cleaned_description,
                salary_min,
                salary_max,
                currency,
                job_id,
            ),
        )
        return job_id, False
    else:
        cursor = conn.execute(
            """
            INSERT INTO jobs (
                canonical_key, title, company_id, location, is_remote,
                url, portal, raw_description, cleaned_description,
                salary_min, salary_max, currency, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                canonical_key,
                title,
                company_id,
                location,
                1 if is_remote else 0,
                url,
                portal,
                raw_description,
                cleaned_description,
                salary_min,
                salary_max,
                currency,
                STATUS_NEW,
            ),
        )
        return cursor.lastrowid, True


def update_job_status(
    conn: sqlite3.Connection,
    job_id: int,
    new_status: str,
    rejection_reason: Optional[str] = None,
) -> None:
    """Transition a job to a new lifecycle state."""
    # Canonicalize DISCOVERED to NEW
    if new_status == "DISCOVERED":
        new_status = STATUS_NEW

    if new_status not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{new_status}'. Must be one of {VALID_STATUSES}")

    conn.execute(
        """
        UPDATE jobs
        SET status = ?, rejection_reason = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (new_status, rejection_reason, job_id),
    )


def record_evaluation(
    conn: sqlite3.Connection,
    job_id: int,
    overall_score: float,
    software_fit_score: Optional[float] = None,
    quantum_fit_score: Optional[float] = None,
    phd_requirement_analysis: Optional[str] = None,
    is_experimental_hardware: bool = False,
    veto_triggered: bool = False,
    veto_reason: Optional[str] = None,
    recommended_channel: str = "surgical_form_plus_dm",
    matched_skills: Optional[List[str]] = None,
    missing_keywords: Optional[List[str]] = None,
    tailoring_recommendations: Optional[List[str]] = None,
    strengths: Optional[List[str]] = None,
    gaps: Optional[List[str]] = None,
    raw_eval: Optional[Dict[str, Any]] = None,
    auto_update_status: bool = True,
) -> int:
    """Record evaluation scores and update job status (SHORTLISTED if score >= 7.5 and not vetoed)."""
    # Consolidate strengths/matched_skills
    final_matched = matched_skills or strengths or []
    final_missing = missing_keywords or gaps or []
    final_tailor = tailoring_recommendations or []

    cursor = conn.execute(
        """
        INSERT INTO evaluations (
            job_id, overall_score, software_fit_score, quantum_fit_score,
            phd_requirement_analysis, is_experimental_hardware,
            veto_triggered, veto_reason, recommended_channel,
            matched_skills, missing_keywords, tailoring_recommendations,
            strengths, gaps, raw_eval
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            overall_score,
            software_fit_score,
            quantum_fit_score,
            phd_requirement_analysis,
            1 if is_experimental_hardware else 0,
            1 if veto_triggered else 0,
            veto_reason,
            recommended_channel,
            json.dumps(final_matched, ensure_ascii=False),
            json.dumps(final_missing, ensure_ascii=False),
            json.dumps(final_tailor, ensure_ascii=False),
            json.dumps(final_matched, ensure_ascii=False),
            json.dumps(final_missing, ensure_ascii=False),
            json.dumps(raw_eval or {}, ensure_ascii=False),
        ),
    )

    if auto_update_status:
        # Check threshold: support 0-10 scale (threshold 7.5) or 0-100 scale (threshold 75.0)
        threshold = 7.5 if overall_score <= 10.0 else 75.0
        if veto_triggered:
            new_status = STATUS_IGNORED
            reason = veto_reason or "Veto triggered during evaluation"
        elif overall_score >= threshold:
            new_status = STATUS_SHORTLISTED
            reason = None
        else:
            new_status = STATUS_IGNORED
            reason = f"Score below threshold ({overall_score:.1f}/{10 if overall_score <= 10 else 100})"

        conn.execute(
            "UPDATE jobs SET status = ?, rejection_reason = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_status, reason, job_id),
        )

    return cursor.lastrowid


def get_pending_jobs(conn: sqlite3.Connection, limit: Optional[int] = None) -> List[sqlite3.Row]:
    """Retrieve jobs awaiting evaluation (status 'NEW' or legacy 'DISCOVERED')."""
    sql = "SELECT * FROM jobs WHERE status IN ('NEW', 'DISCOVERED') ORDER BY id ASC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def record_application(
    conn: sqlite3.Connection,
    job_id: int,
    cv_path: str,
    cover_letter_path: Optional[str] = None,
    outreach_message: Optional[str] = None,
    ats_verified: bool = False,
    layout_verified: bool = False,
    submission_mode: str = "browser_use_hitl",
    stage: str = "SUBMITTED",
    notes: Optional[str] = None,
    cooldown_days: int = 30,
) -> int:
    """Record an application attempt and trigger company cooldown."""
    now = datetime.now(timezone.utc)
    cooldown_until = now + timedelta(days=cooldown_days)

    cursor = conn.execute(
        """
        INSERT INTO applications (
            job_id, cv_path, cover_letter_path, outreach_message,
            ats_verified, layout_verified, applied_at, submission_mode, stage, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            cv_path,
            cover_letter_path,
            outreach_message,
            1 if ats_verified else 0,
            1 if layout_verified else 0,
            now.isoformat(),
            submission_mode,
            stage,
            notes,
        ),
    )

    conn.execute(
        "UPDATE jobs SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (STATUS_APPLIED, job_id),
    )
    _start_company_cooldown(conn, job_id, now, cooldown_until)

    return cursor.lastrowid


def _start_company_cooldown(
    conn: sqlite3.Connection, job_id: int, applied_at: datetime, cooldown_until: datetime
) -> None:
    job = conn.execute("SELECT company_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job and job["company_id"]:
        conn.execute(
            "UPDATE companies SET last_applied_at = ?, cooldown_until = ? WHERE id = ?",
            (applied_at.isoformat(), cooldown_until.isoformat(), job["company_id"]),
        )


def get_shortlisted_for_tailoring(
    conn: sqlite3.Connection, min_score: float = 7.5, limit: Optional[int] = None
) -> List[sqlite3.Row]:
    """SHORTLISTED, non-vetoed jobs with their latest evaluation, best score first."""
    sql = """
        SELECT j.*, c.name AS company_name, e.overall_score, e.matched_skills,
               e.missing_keywords, e.tailoring_recommendations
        FROM jobs j
        JOIN companies c ON c.id = j.company_id
        JOIN evaluations e ON e.id = (
            SELECT MAX(id) FROM evaluations WHERE job_id = j.id
        )
        WHERE j.status = ? AND e.veto_triggered = 0 AND e.overall_score >= ?
        ORDER BY e.overall_score DESC, j.id ASC
    """
    params: list = [STATUS_SHORTLISTED, min_score]
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    return conn.execute(sql, params).fetchall()


def record_tailored_documents(
    conn: sqlite3.Connection,
    job_id: int,
    cv_path: str,
    cover_letter_path: Optional[str] = None,
    ats_verified: bool = False,
    layout_verified: bool = False,
    notes: Optional[str] = None,
) -> int:
    """Store generated documents and move the job to TAILORED, or READY_TO_APPLY when both checks passed."""
    conn.execute(
        "DELETE FROM applications WHERE job_id = ? AND stage = 'READY_TO_APPLY'", (job_id,)
    )
    cursor = conn.execute(
        """
        INSERT INTO applications (
            job_id, cv_path, cover_letter_path, ats_verified, layout_verified,
            applied_at, submission_mode, stage, notes
        ) VALUES (?, ?, ?, ?, ?, NULL, NULL, 'READY_TO_APPLY', ?)
        """,
        (
            job_id,
            cv_path,
            cover_letter_path,
            1 if ats_verified else 0,
            1 if layout_verified else 0,
            notes,
        ),
    )
    ready = ats_verified and layout_verified
    update_job_status(conn, job_id, STATUS_READY_TO_APPLY if ready else STATUS_TAILORED)
    return cursor.lastrowid


def get_ready_application(conn: sqlite3.Connection, job_id: int) -> Optional[sqlite3.Row]:
    """Pending (not yet submitted) application row for a job, with job and company fields."""
    return conn.execute(
        """
        SELECT a.*, j.title, j.url, j.status AS job_status, c.name AS company_name
        FROM applications a
        JOIN jobs j ON j.id = a.job_id
        JOIN companies c ON c.id = j.company_id
        WHERE a.job_id = ? AND a.stage = 'READY_TO_APPLY'
        ORDER BY a.id DESC LIMIT 1
        """,
        (job_id,),
    ).fetchone()


def confirm_application(
    conn: sqlite3.Connection,
    job_id: int,
    submission_mode: str = "browser_use_hitl",
    cooldown_days: int = 30,
    notes: Optional[str] = None,
) -> None:
    """Record that the human submitted the application: APPLIED status plus company cooldown."""
    row = get_ready_application(conn, job_id)
    if row is None or row["job_status"] != STATUS_READY_TO_APPLY:
        raise ValueError(f"Job {job_id} has no READY_TO_APPLY application to confirm")
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        UPDATE applications
        SET stage = 'SUBMITTED', applied_at = ?, submission_mode = ?, notes = COALESCE(?, notes)
        WHERE id = ?
        """,
        (now.isoformat(), submission_mode, notes, row["id"]),
    )
    update_job_status(conn, job_id, STATUS_APPLIED)
    _start_company_cooldown(conn, job_id, now, now + timedelta(days=cooldown_days))


def get_pipeline_summary(conn: sqlite3.Connection) -> Dict[str, int]:
    """Get job count grouped by current status."""
    rows = conn.execute(
        "SELECT status, COUNT(*) as count FROM jobs GROUP BY status"
    ).fetchall()
    summary = {s: 0 for s in VALID_STATUSES}
    for r in rows:
        summary[r["status"]] = r["count"]
    return summary


if __name__ == "__main__":
    init_db()
    with get_db() as c:
        summary = get_pipeline_summary(c)
        print("ai-job-search SQLite initialized successfully at:", DEFAULT_DB_PATH)
        print("Pipeline Summary:", summary)

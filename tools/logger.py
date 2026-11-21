#!/usr/bin/env python3
"""Structured Logging and Funnel Observability for ai-job-search.

Provides JSON-formatted or ANSI-colored structured logging, with thread-safe
funnel metrics tracking across ingestion, evaluation, and application stages.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Log Levels
LOG_LEVEL_NAME = os.environ.get("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = os.environ.get("LOG_FORMAT", "pretty").lower()

LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}



def safe_print(text: str, stream: Any = None, flush: bool = True) -> None:
    """Print without crashing on consoles that cannot encode the text (Windows cp1252 and similar)."""
    stream = stream or sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        text = text.encode(encoding if encoding else "ascii", "replace").decode(encoding if encoding else "ascii")
    print(text, file=stream, flush=flush)


class FunnelMetrics:
    """Thread-safe funnel counter for observability across all pipeline layers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counters: Dict[str, int] = {
            "ingested_total": 0,
            "sanitized_clean": 0,
            "sanitized_flagged": 0,
            "cooldown_skipped": 0,
            "duplicates_skipped": 0,
            "evaluated_total": 0,
            "vetoed_hardware": 0,
            "vetoed_location": 0,
            "vetoed_role": 0,
            "vetoed_phd_strict": 0,
            "phd_boilerplate_tolerated": 0,
            "shortlisted_total": 0,
            "ignored_total": 0,
            "tailored_total": 0,
            "applied_total": 0,
            "errors_total": 0,
            "llm_calls": 0,
            "llm_cache_hits": 0,
            "llm_prompt_tokens": 0,
            "llm_completion_tokens": 0,
            "llm_cost_microusd": 0,
            "llm_errors": 0,
        }

    def increment(self, metric: str, amount: int = 1) -> None:
        with self._lock:
            if metric not in self.counters:
                self.counters[metric] = 0
            self.counters[metric] += amount

    def get(self, metric: str) -> int:
        with self._lock:
            return self.counters.get(metric, 0)

    def to_dict(self) -> Dict[str, int]:
        with self._lock:
            return dict(self.counters)

    def print_summary(self) -> None:
        """Render a readable summary table of the recruitment funnel."""
        data = self.to_dict()
        lines = ["", "=" * 65]
        lines.append("          AI-JOB-SEARCH PIPELINE FUNNEL SUMMARY")
        lines.append("=" * 65)
        lines.append(f"  - Total Opportunities Ingested:        {data['ingested_total']}")
        lines.append(f"  - Sanitized Clean:                     {data['sanitized_clean']}")
        if data['sanitized_flagged'] > 0:
            lines.append(f"  - Flagged by Security Guard:           {data['sanitized_flagged']}")
        lines.append(f"  - Duplicates / Already Seen:           {data['duplicates_skipped']}")
        lines.append(f"  - Company Cooldown Skipped (30d):      {data['cooldown_skipped']}")
        lines.append("-" * 65)
        lines.append(f"  - Opportunities Evaluated by Ranker:   {data['evaluated_total']}")
        lines.append(f"  - Vetoed - Experimental Hardware/Lab:  {data['vetoed_hardware']}")
        lines.append(f"  - Vetoed - Incompatible Location:      {data['vetoed_location']}")
        lines.append(f"  - Vetoed - Non-engineering/Management: {data['vetoed_role']}")
        lines.append(f"  - PhD Boilerplate Rescued (SWE Fit):   {data['phd_boilerplate_tolerated']}")
        lines.append(f"  - Vetoed - Strict Academic Research:   {data['vetoed_phd_strict']}")
        lines.append("-" * 65)
        lines.append(f"  * SHORTLISTED (>= threshold):           {data['shortlisted_total']}")
        lines.append(f"  x IGNORED (below threshold or vetoed): {data['ignored_total']}")
        lines.append(f"  + Documents Tailored:                  {data['tailored_total']}")
        lines.append(f"  > Postulations Applied (HITL):        {data['applied_total']}")
        if data['llm_calls'] or data['llm_cache_hits']:
            lines.append(f"  $ LLM: {data['llm_calls']} calls, {data['llm_cache_hits']} from cache, "
                         f"{data['llm_prompt_tokens'] + data['llm_completion_tokens']} tokens, ${data['llm_cost_microusd'] / 1e6:.4f}")
        if data['errors_total'] > 0:
            lines.append(f"  ! Pipeline Errors / Exceptions:        {data['errors_total']}")
        lines.append("=" * 65)
        safe_print("\n".join(lines))


METRICS = FunnelMetrics()


class StructuredLogger:
    """Configurable logger emitting formatted console strings or structured JSON."""

    def __init__(self, name: str = "ai_job_search") -> None:
        self.name = name
        self.level = LEVEL_MAP.get(LOG_LEVEL_NAME, logging.INFO)

    def _log(
        self, level_name: str, message: str, extra: Optional[Dict[str, Any]] = None
    ) -> None:
        lvl = LEVEL_MAP.get(level_name, logging.INFO)
        if lvl < self.level:
            return

        timestamp = datetime.now(timezone.utc).isoformat()
        extra_data = extra or {}

        if LOG_FORMAT == "json":
            entry = {
                "timestamp": timestamp,
                "level": level_name,
                "logger": self.name,
                "message": message,
                **extra_data,
            }
            safe_print(json.dumps(entry), sys.stderr if lvl >= logging.WARNING else sys.stdout)
        else:
            # Pretty console format with ANSI colors
            colors = {
                "DEBUG": "\033[90m",    # Gray
                "INFO": "\033[36m",     # Cyan
                "WARNING": "\033[33m",  # Yellow
                "ERROR": "\033[31m",    # Red
                "CRITICAL": "\033[35m", # Magenta
            }
            reset = "\033[0m"
            color = colors.get(level_name, "")
            extra_str = f" | {json.dumps(extra_data)}" if extra_data else ""
            out = f"{color}[{timestamp[:19]}] [{level_name:<7}] [{self.name}] {message}{extra_str}{reset}"
            safe_print(out, sys.stderr if lvl >= logging.WARNING else sys.stdout)

    def debug(self, message: str, **kwargs: Any) -> None:
        self._log("DEBUG", message, kwargs)

    def info(self, message: str, **kwargs: Any) -> None:
        self._log("INFO", message, kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        self._log("WARNING", message, kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        METRICS.increment("errors_total")
        self._log("ERROR", message, kwargs)


def get_logger(name: str = "ai_job_search") -> StructuredLogger:
    """Factory helper to obtain a named StructuredLogger."""
    return StructuredLogger(name)

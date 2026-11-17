#!/usr/bin/env python3
"""HTTP helper with retries and exponential backoff for the ingesters.

Transient failures (connection errors, timeouts, 429 and 5xx) are retried; client errors such as
404 come straight back so the caller can tell a bad slug from a flaky network.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

import requests

RETRY_STATUS = {429, 500, 502, 503, 504}


class SourceError(RuntimeError):
    """A job source answered with an error or could not be reached."""

    def __init__(self, source: str, status: Optional[int] = None, detail: str = "") -> None:
        self.source = source
        self.status = status
        self.detail = detail
        label = f"HTTP {status}" if status else "unreachable"
        hint = " (check the slug in the config)" if status == 404 else ""
        super().__init__(f"{source}: {label}{hint}{': ' + detail if detail else ''}")


def _retry_after(response: Any, cap: float) -> Optional[float]:
    value = (getattr(response, "headers", None) or {}).get("Retry-After")
    try:
        return min(cap, max(0.0, float(value)))
    except (TypeError, ValueError):
        return None


def get_with_retry(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    timeout: float = 15,
    attempts: int = 3,
    backoff: float = 1.0,
    max_wait: float = 30.0,
    get: Callable[..., Any] = requests.get,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """GET `url`, retrying transient failures. Returns the last response, or raises the last network error."""
    last_error: Optional[Exception] = None
    response: Any = None
    for attempt in range(1, attempts + 1):
        wait: Optional[float] = None
        try:
            response = get(url, headers=headers, params=params, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_error, response = exc, None
        else:
            last_error = None
            if response.status_code not in RETRY_STATUS:
                return response
            wait = _retry_after(response, max_wait)
        if attempt < attempts:
            sleep(wait if wait is not None else min(max_wait, backoff * 2 ** (attempt - 1)))
    if last_error is not None:
        raise last_error
    return response

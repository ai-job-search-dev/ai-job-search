#!/usr/bin/env python3
"""LLM access for the ranker: one gateway with token and cost accounting, a response cache and budget caps.

The client is any object shaped like an Instructor-patched OpenAI client, so the same code works with
OpenAI, an OpenAI-compatible server (set `OPENAI_BASE_URL`) or a client you build yourself and pass in.
Every call is recorded in `llm_calls`; identical requests are answered from `llm_cache` at no cost.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

PRICING_PATH = ROOT_DIR / "config" / "llm_pricing.yaml"
DEFAULT_MODEL = "gpt-4o-mini"
ESTIMATED_PROMPT_TOKENS = 1800
ESTIMATED_COMPLETION_TOKENS = 350


class BudgetExceeded(RuntimeError):
    """The configured call or cost cap was reached; the caller should fall back to the heuristic."""


def load_pricing(path: Optional[Path] = None) -> Dict[str, Dict[str, float]]:
    """Per-model USD prices per million tokens, from config/llm_pricing.yaml. Missing file gives no prices."""
    p = Path(path) if path else PRICING_PATH
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {str(k).lower(): v for k, v in (data.get("models") or {}).items()}


def price_for(model: str, pricing: Dict[str, Dict[str, float]]) -> Optional[Dict[str, float]]:
    """Exact model name first, then the longest listed name the model starts with (e.g. a dated snapshot)."""
    name = model.lower()
    if name in pricing:
        return pricing[name]
    prefixes = [k for k in pricing if name.startswith(k)]
    return pricing[max(prefixes, key=len)] if prefixes else None


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int, pricing: Dict[str, Dict[str, float]]) -> Optional[float]:
    rates = price_for(model, pricing)
    if not rates:
        return None
    return (prompt_tokens * float(rates["input"]) + completion_tokens * float(rates["output"])) / 1_000_000


def estimate_cost(model: str, jobs: int, pricing: Dict[str, Dict[str, float]]) -> Optional[float]:
    """Rough cost of ranking `jobs` postings, for a dry run. Real usage depends on posting length."""
    return cost_usd(model, ESTIMATED_PROMPT_TOKENS * jobs, ESTIMATED_COMPLETION_TOKENS * jobs, pricing)


def preferences_hash(prefs: Any) -> str:
    """Short stable hash of the preferences, stored with each evaluation to tell when it is out of date."""
    payload = json.dumps(prefs.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


@dataclass
class CallInfo:
    source: str = "llm"
    model: str = ""
    cache_hit: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: Optional[float] = 0.0


@dataclass
class Budget:
    max_calls: Optional[int] = None
    max_cost_usd: Optional[float] = None
    calls: int = 0
    cost_usd: float = 0.0

    def check(self, price_known: bool) -> None:
        if self.max_calls is not None and self.calls >= self.max_calls:
            raise BudgetExceeded(f"reached the limit of {self.max_calls} LLM calls")
        if self.max_cost_usd is not None:
            if not price_known:
                raise BudgetExceeded("a cost cap is set but this model has no price in config/llm_pricing.yaml")
            if self.cost_usd >= self.max_cost_usd:
                raise BudgetExceeded(f"reached the cost cap of ${self.max_cost_usd:.2f}")


@dataclass
class UsageSummary:
    calls: int = 0
    cache_hits: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    unpriced_calls: int = 0
    errors: int = 0
    skipped_by_budget: int = 0
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def make_client(api_key: Optional[str] = None) -> Any:
    """Instructor-patched OpenAI client. `OPENAI_BASE_URL` points it at any OpenAI-compatible server."""
    import instructor
    from openai import OpenAI

    return instructor.from_openai(OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY")))


def llm_configured() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


class LLMGateway:
    """Runs structured calls through `client`, caching answers and recording usage in the database."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        client: Any = None,
        model: Optional[str] = None,
        pricing: Optional[Dict[str, Dict[str, float]]] = None,
        budget: Optional[Budget] = None,
        use_cache: bool = True,
        prefs_hash: str = "",
    ) -> None:
        self.conn = conn
        self._client = client
        self.model = model or os.environ.get("AI_JOB_SEARCH_MODEL", DEFAULT_MODEL)
        self.pricing = load_pricing() if pricing is None else pricing
        self.budget = budget or Budget()
        self.use_cache = use_cache
        self.prefs_hash = prefs_hash
        self.summary = UsageSummary()

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = make_client()
        return self._client

    def _cache_key(self, messages: List[Dict[str, str]], response_model: Type[Any]) -> str:
        payload = json.dumps({"model": self.model, "schema": response_model.__name__, "messages": messages}, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def evaluate(
        self, messages: List[Dict[str, str]], response_model: Type[Any], job_id: Optional[int] = None
    ) -> Tuple[Any, CallInfo]:
        key = self._cache_key(messages, response_model)
        if self.use_cache:
            row = self.conn.execute("SELECT response_json FROM llm_cache WHERE cache_key = ?", (key,)).fetchone()
            if row:
                try:
                    parsed = response_model.model_validate_json(row[0])
                except Exception:
                    parsed = None
                if parsed is not None:
                    self.summary.cache_hits += 1
                    self._record(job_id, 0, 0, 0.0, cache_hit=True)
                    return parsed, CallInfo(model=self.model, cache_hit=True, cost_usd=0.0)

        try:
            self.budget.check(price_known=price_for(self.model, self.pricing) is not None)
        except BudgetExceeded:
            self.summary.skipped_by_budget += 1
            raise

        parsed, completion = self.client.chat.completions.create_with_completion(
            model=self.model, response_model=response_model, messages=messages, temperature=0.1, max_retries=2,
        )
        usage = getattr(completion, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        cost = cost_usd(self.model, prompt_tokens, completion_tokens, self.pricing)

        self.budget.calls += 1
        self.budget.cost_usd += cost or 0.0
        self.summary.calls += 1
        self.summary.prompt_tokens += prompt_tokens
        self.summary.completion_tokens += completion_tokens
        if cost is None:
            self.summary.unpriced_calls += 1
        else:
            self.summary.cost_usd += cost
        self._record(job_id, prompt_tokens, completion_tokens, cost)
        if self.use_cache:
            self.conn.execute(
                "INSERT OR REPLACE INTO llm_cache (cache_key, model, response_json, prompt_tokens, completion_tokens) VALUES (?, ?, ?, ?, ?)",
                (key, self.model, parsed.model_dump_json(), prompt_tokens, completion_tokens),
            )
        self.conn.commit()
        return parsed, CallInfo(model=self.model, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, cost_usd=cost)

    def _record(self, job_id: Optional[int], prompt: int, completion: int, cost: Optional[float], cache_hit: bool = False) -> None:
        self.conn.execute(
            "INSERT INTO llm_calls (job_id, model, prompt_tokens, completion_tokens, cost_usd, cache_hit, prefs_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (job_id, self.model, prompt, completion, cost, 1 if cache_hit else 0, self.prefs_hash),
        )


def total_spend(conn: sqlite3.Connection) -> Dict[str, Any]:
    """All-time LLM usage from the database."""
    row = conn.execute(
        "SELECT COUNT(*) AS calls, COALESCE(SUM(cache_hit), 0) AS hits, COALESCE(SUM(prompt_tokens), 0) AS p, "
        "COALESCE(SUM(completion_tokens), 0) AS c, COALESCE(SUM(cost_usd), 0) AS cost FROM llm_calls"
    ).fetchone()
    return {"calls": row["calls"], "cache_hits": row["hits"], "prompt_tokens": row["p"],
            "completion_tokens": row["c"], "cost_usd": round(row["cost"], 4)}

#!/usr/bin/env python3
"""Tests for the LLM gateway (accounting, cache, budget) and its use by the ranker, with a fake client."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tools import ranker
from tools.db import STATUS_SHORTLISTED, get_db, init_db, upsert_job
from tools.llm import (
    Budget,
    BudgetExceeded,
    LLMGateway,
    cost_usd,
    estimate_cost,
    load_pricing,
    preferences_hash,
    price_for,
    total_spend,
)
from tools.profile_schema import CareerPreferences
from tools.ranker_schema import QuantumJobEvaluation

PRICING = {"gpt-4o-mini": {"input": 0.15, "output": 0.60}}
DESC = (
    "Build the quantum SDK in Python and C++. Compiler passes, testing and CI/CD. "
    "Work with Qiskit on qubits and error mitigation for our platform."
)


def evaluation(score=9.0, **kw):
    base = dict(overall_score=score, software_fit_score=9.0, quantum_fit_score=9.0, is_software_role=True,
                is_experimental_hardware=False, phd_requirement_analysis="not_required", should_veto=False,
                veto_reason=None, matched_skills=["Python"], reasoning="fits")
    base.update(kw)
    return QuantumJobEvaluation(**base)


class FakeClient:
    """Shaped like an Instructor client: chat.completions.create_with_completion(...) -> (parsed, completion)."""

    def __init__(self, result=None, tokens=(1000, 200), error=None):
        self.calls = []
        self.result, self.tokens, self.error = result or evaluation(), tokens, error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create_with_completion=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        usage = SimpleNamespace(prompt_tokens=self.tokens[0], completion_tokens=self.tokens[1])
        return self.result.model_copy(), SimpleNamespace(usage=usage)


class PricingTests(unittest.TestCase):
    def test_cost_is_tokens_times_price_per_million(self):
        self.assertAlmostEqual(cost_usd("gpt-4o-mini", 1000, 200, PRICING), 0.00027)

    def test_dated_snapshots_use_the_base_model_price(self):
        self.assertIsNotNone(price_for("gpt-4o-mini-2024-07-18", PRICING))
        pricing = {**PRICING, "gpt-4o": {"input": 2.5, "output": 10.0}}
        self.assertEqual(price_for("gpt-4o-mini-2024-07-18", pricing), PRICING["gpt-4o-mini"])

    def test_unknown_model_has_no_price(self):
        self.assertIsNone(cost_usd("my-local-agent", 10, 10, PRICING))
        self.assertIsNone(estimate_cost("my-local-agent", 5, PRICING))

    def test_shipped_pricing_file_is_valid(self):
        pricing = load_pricing()
        self.assertIn("gpt-4o-mini", pricing)
        for rates in pricing.values():
            self.assertGreater(float(rates["input"]), 0)
            self.assertGreater(float(rates["output"]), 0)

    def test_preferences_hash_changes_only_when_preferences_change(self):
        a = CareerPreferences()
        self.assertEqual(preferences_hash(a), preferences_hash(CareerPreferences()))
        self.assertNotEqual(preferences_hash(a), preferences_hash(CareerPreferences(shortlist_threshold=6.0)))


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        init_db(self.db)
        self.messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]

    def tearDown(self):
        self.tmp.cleanup()

    def gateway(self, conn, client, **kw):
        return LLMGateway(conn, client=client, model="gpt-4o-mini", pricing=PRICING, **kw)

    def test_a_call_is_recorded_with_tokens_and_cost(self):
        client = FakeClient()
        with get_db(self.db) as conn:
            gw = self.gateway(conn, client, prefs_hash="abc")
            parsed, info = gw.evaluate(self.messages, QuantumJobEvaluation, job_id=7)
            self.assertEqual(parsed.overall_score, 9.0)
            self.assertFalse(info.cache_hit)
            row = conn.execute("SELECT * FROM llm_calls").fetchone()
        self.assertEqual((row["job_id"], row["prompt_tokens"], row["completion_tokens"], row["prefs_hash"]), (7, 1000, 200, "abc"))
        self.assertAlmostEqual(row["cost_usd"], 0.00027)
        self.assertEqual((gw.summary.calls, gw.summary.prompt_tokens), (1, 1000))
        self.assertAlmostEqual(gw.summary.cost_usd, 0.00027)

    def test_an_identical_request_is_served_from_the_cache_for_free(self):
        client = FakeClient()
        with get_db(self.db) as conn:
            gw = self.gateway(conn, client)
            gw.evaluate(self.messages, QuantumJobEvaluation)
            parsed, info = gw.evaluate(self.messages, QuantumJobEvaluation)
            self.assertTrue(info.cache_hit)
            self.assertEqual(info.cost_usd, 0.0)
            spend = total_spend(conn)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual((spend["calls"], spend["cache_hits"]), (2, 1))
        self.assertEqual(gw.summary.cache_hits, 1)

    def test_a_different_prompt_or_model_is_not_a_cache_hit(self):
        client = FakeClient()
        with get_db(self.db) as conn:
            gw = self.gateway(conn, client)
            gw.evaluate(self.messages, QuantumJobEvaluation)
            gw.evaluate([{"role": "user", "content": "other"}], QuantumJobEvaluation)
            other_model = LLMGateway(conn, client=client, model="gpt-4o", pricing=PRICING)
            other_model.evaluate(self.messages, QuantumJobEvaluation)
        self.assertEqual(len(client.calls), 3)

    def test_cache_can_be_disabled(self):
        client = FakeClient()
        with get_db(self.db) as conn:
            gw = self.gateway(conn, client, use_cache=False)
            gw.evaluate(self.messages, QuantumJobEvaluation)
            gw.evaluate(self.messages, QuantumJobEvaluation)
        self.assertEqual(len(client.calls), 2)

    def test_call_cap_stops_paid_calls_but_not_cached_ones(self):
        client = FakeClient()
        with get_db(self.db) as conn:
            gw = self.gateway(conn, client, budget=Budget(max_calls=1))
            gw.evaluate(self.messages, QuantumJobEvaluation)
            gw.evaluate(self.messages, QuantumJobEvaluation)
            with self.assertRaises(BudgetExceeded):
                gw.evaluate([{"role": "user", "content": "new"}], QuantumJobEvaluation)
        self.assertEqual((len(client.calls), gw.summary.skipped_by_budget), (1, 1))

    def test_cost_cap_stops_once_spent(self):
        client = FakeClient(tokens=(1_000_000, 0))
        with get_db(self.db) as conn:
            gw = self.gateway(conn, client, budget=Budget(max_cost_usd=0.10))
            gw.evaluate(self.messages, QuantumJobEvaluation)
            self.assertAlmostEqual(gw.summary.cost_usd, 0.15)
            with self.assertRaises(BudgetExceeded):
                gw.evaluate([{"role": "user", "content": "new"}], QuantumJobEvaluation)

    def test_cost_cap_cannot_be_enforced_for_an_unpriced_model(self):
        client = FakeClient()
        with get_db(self.db) as conn:
            gw = LLMGateway(conn, client=client, model="my-local-agent", pricing=PRICING, budget=Budget(max_cost_usd=1.0))
            with self.assertRaises(BudgetExceeded) as ctx:
                gw.evaluate(self.messages, QuantumJobEvaluation)
        self.assertIn("no price", str(ctx.exception))
        self.assertEqual(client.calls, [])

    def test_unpriced_model_still_counts_tokens(self):
        client = FakeClient()
        with get_db(self.db) as conn:
            gw = LLMGateway(conn, client=client, model="my-local-agent", pricing=PRICING)
            gw.evaluate(self.messages, QuantumJobEvaluation)
        self.assertEqual((gw.summary.unpriced_calls, gw.summary.prompt_tokens, gw.summary.cost_usd), (1, 1000, 0.0))


class RankerWithLlmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        init_db(self.db)
        self.env = mock.patch.dict("os.environ", {"OPENAI_API_KEY": "", "AI_JOB_SEARCH_MODEL": "gpt-4o-mini"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def add(self, company, description=DESC, **kw):
        with get_db(self.db) as conn:
            job_id, _ = upsert_job(conn, "Quantum Software Engineer", company, f"https://x/{company}", "ashby:x",
                                   "Remote", True, description, description, **kw)
        return job_id

    def run_ranker(self, client, **kw):
        kw.setdefault("prefs", CareerPreferences())
        return ranker.run_ranker(db_path=self.db, llm_client=client, **kw)

    def sources(self):
        with get_db(self.db) as conn:
            return [(r["eval_source"], r["eval_model"], r["prefs_hash"]) for r in
                    conn.execute("SELECT eval_source, eval_model, prefs_hash FROM evaluations ORDER BY id")]

    def test_llm_scores_are_used_and_tagged(self):
        self.add("Acme")
        client = FakeClient(evaluation(9.2))
        results = self.run_ranker(client)
        self.assertEqual(results[0]["score"], 9.2)
        self.assertEqual(results[0]["status"], STATUS_SHORTLISTED)
        source, model, phash = self.sources()[0]
        self.assertEqual((source, model), ("llm", "gpt-4o-mini"))
        self.assertEqual(phash, preferences_hash(CareerPreferences()))

    def test_the_prompt_contains_the_rules_and_the_posting(self):
        self.add("Acme")
        client = FakeClient()
        self.run_ranker(client)
        system, user = client.calls[0]["messages"]
        self.assertIn("Screening rules:", system["content"])
        self.assertIn("Acme", user["content"])

    def test_reevaluating_unchanged_postings_costs_nothing(self):
        self.add("Acme")
        client = FakeClient()
        self.run_ranker(client)
        usage = {}
        self.run_ranker(client, reevaluate=True, usage_out=usage)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual((usage["calls"], usage["cache_hits"]), (0, 1))

    def test_call_cap_leaves_the_rest_to_the_heuristic(self):
        for name in ("A", "B", "C"):
            self.add(name)
        client = FakeClient()
        usage = {}
        self.run_ranker(client, max_llm_calls=1, usage_out=usage)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(sorted(s[0] for s in self.sources()), ["heuristic", "heuristic", "llm"])
        self.assertEqual(usage["skipped_by_budget"], 2)

    def test_flagged_postings_never_reach_the_client(self):
        self.add("Evil", guard_verdict="posting_guard: \u26a0 instruction-like lines")
        self.add("Fine")
        client = FakeClient()
        self.run_ranker(client)
        self.assertEqual(len(client.calls), 1)
        self.assertIn("Fine", client.calls[0]["messages"][1]["content"])

    def test_a_failing_client_falls_back_to_the_heuristic_and_counts_the_error(self):
        self.add("Acme")
        usage = {}
        results = self.run_ranker(FakeClient(error=RuntimeError("503")), usage_out=usage)
        self.assertEqual(len(results), 1)
        self.assertEqual(self.sources()[0][0], "heuristic")
        self.assertEqual(usage["errors"], 1)

    def test_code_rules_still_apply_to_what_the_model_says(self):
        self.add("Acme", DESC + " Only U.S. citizens are eligible to apply.")
        results = self.run_ranker(FakeClient(evaluation(9.9)))
        self.assertEqual(results[0]["status"], "IGNORED")
        self.assertIn("citizenship", results[0]["veto_reason"].lower())

    def test_unpriced_model_with_a_cost_cap_uses_the_heuristic(self):
        self.add("Acme")
        client = FakeClient()
        with mock.patch.dict("os.environ", {"AI_JOB_SEARCH_MODEL": "my-local-agent"}):
            self.run_ranker(client, max_cost_usd=1.0)
        self.assertEqual(client.calls, [])
        self.assertEqual(self.sources()[0][0], "heuristic")

    def test_stale_only_rescores_just_the_evaluations_made_under_other_preferences(self):
        self.add("Acme")
        client = FakeClient()
        self.run_ranker(client)
        self.assertEqual(self.run_ranker(client, stale_only=True), [])
        changed = CareerPreferences(shortlist_threshold=6.0)
        results = self.run_ranker(client, stale_only=True, prefs=changed)
        self.assertEqual(len(results), 1)
        self.assertEqual(self.sources()[-1][2], preferences_hash(changed))

    def test_stale_only_ignores_jobs_that_have_no_usable_description(self):
        self.add("Acme")
        self.add("Empty", description="")
        client = FakeClient()
        self.run_ranker(client)
        changed = CareerPreferences(shortlist_threshold=6.0)
        results = self.run_ranker(client, stale_only=True, prefs=changed)
        self.assertEqual([r["company"] for r in results], ["Acme"])

    def test_without_a_client_or_key_the_heuristic_runs_and_no_gateway_is_built(self):
        self.add("Acme")
        self.run_ranker(None)
        self.assertEqual(self.sources()[0][0], "heuristic")
        with get_db(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()

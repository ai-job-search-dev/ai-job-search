#!/usr/bin/env python3
"""Tests for the retrying HTTP helper and the ATS fetchers' error reporting."""
import unittest
from unittest import mock

import requests

from tools import ingest_ats
from tools.net import SourceError, get_with_retry


class FakeResponse:
    def __init__(self, status, headers=None):
        self.status_code = status
        self.headers = headers or {}


class Sequence:
    """Callable that returns or raises the queued items in order."""

    def __init__(self, *items):
        self.items = list(items)
        self.calls = 0

    def __call__(self, url, **kwargs):
        self.calls += 1
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.sleeps = []

    def _get(self, *items, **kw):
        seq = Sequence(*items)
        resp = get_with_retry("https://x", get=seq, sleep=self.sleeps.append, **kw)
        return resp, seq

    def test_success_is_returned_immediately(self):
        resp, seq = self._get(FakeResponse(200))
        self.assertEqual((resp.status_code, seq.calls, self.sleeps), (200, 1, []))

    def test_server_errors_are_retried_with_exponential_backoff(self):
        resp, seq = self._get(FakeResponse(503), FakeResponse(500), FakeResponse(200))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(seq.calls, 3)
        self.assertEqual(self.sleeps, [1.0, 2.0])

    def test_client_errors_are_not_retried(self):
        resp, seq = self._get(FakeResponse(404))
        self.assertEqual((resp.status_code, seq.calls, self.sleeps), (404, 1, []))

    def test_connection_errors_are_retried_then_raised(self):
        seq = Sequence(requests.ConnectionError("a"), requests.Timeout("b"), requests.ConnectionError("c"))
        with self.assertRaises(requests.ConnectionError):
            get_with_retry("https://x", get=seq, sleep=self.sleeps.append)
        self.assertEqual(seq.calls, 3)

    def test_exhausted_retryable_status_returns_the_last_response(self):
        resp, seq = self._get(FakeResponse(502), FakeResponse(502), FakeResponse(502))
        self.assertEqual((resp.status_code, seq.calls), (502, 3))

    def test_retry_after_header_is_honoured_and_capped(self):
        self._get(FakeResponse(429, {"Retry-After": "7"}), FakeResponse(429, {"Retry-After": "9999"}), FakeResponse(200))
        self.assertEqual(self.sleeps, [7.0, 30.0])

    def test_recovers_after_a_network_error(self):
        resp, _ = self._get(requests.ConnectionError("x"), FakeResponse(200))
        self.assertEqual(resp.status_code, 200)


class SourceErrorTests(unittest.TestCase):
    def test_message_points_at_the_slug_for_404(self):
        self.assertIn("check the slug", str(SourceError("lever:acme", 404)))
        self.assertIn("HTTP 503", str(SourceError("lever:acme", 503)))

    def test_ats_fetchers_raise_instead_of_returning_an_empty_board(self):
        for fetch in (ingest_ats.fetch_greenhouse_jobs, ingest_ats.fetch_lever_jobs, ingest_ats.fetch_ashby_jobs):
            with mock.patch.object(ingest_ats, "get_with_retry", return_value=FakeResponse(404)):
                with self.assertRaises(SourceError) as ctx:
                    fetch("nope")
            self.assertEqual(ctx.exception.status, 404)


if __name__ == "__main__":
    unittest.main()

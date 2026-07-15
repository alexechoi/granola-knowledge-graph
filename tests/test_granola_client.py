"""Tests for Granola request pacing, pagination, and retries."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from granola_kg.granola_client import GranolaApiError, GranolaClient, RequestPacer
from granola_kg.granola_models import ListNotesQuery

NOTE = """{
  "id": "not_1", "object": "note", "title": "Planning",
  "owner": {"name": "Alex", "email": "alex@example.com"},
  "created_at": "2026-07-15T08:00:00Z",
  "updated_at": "2026-07-15T09:00:00Z"
}"""
EXPECTED_ATTEMPTS = 2
RETRY_AFTER_SECONDS = 3.0
FLOAT_TOLERANCE = 1e-9


class FakeTime:
    """Deterministic monotonic clock and sleeper."""

    def __init__(self) -> None:
        """Start at zero with no recorded delays."""
        self.now = 0.0
        self.delays: list[float] = []

    def clock(self) -> float:
        """Return the current fake time."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Record and advance a fake delay."""
        self.delays.append(seconds)
        self.now += seconds


def test_pacer_enforces_sustained_rate() -> None:
    """Consecutive calls should be spaced at five requests per second."""
    fake_time = FakeTime()
    pacer = RequestPacer(clock=fake_time.clock, sleeper=fake_time.sleep)

    pacer.wait()
    pacer.wait()

    assert len(fake_time.delays) == 1
    assert abs(fake_time.delays[0] - 0.2) < FLOAT_TOLERANCE


def test_iter_notes_follows_cursors_and_serializes_filters() -> None:
    """All pages should be yielded and date filters sent as UTC."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        cursor_values = request.url.params.get_list("cursor")
        cursor = cursor_values[0] if cursor_values else None
        if cursor is None:
            return httpx.Response(
                200,
                text=f'{{"notes":[{NOTE}],"hasMore":true,"cursor":"next"}}',
            )
        return httpx.Response(200, text='{"notes":[],"hasMore":false,"cursor":null}')

    http_client = httpx.Client(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    )
    client = GranolaClient(
        "grn_test",
        client=http_client,
        pacer=RequestPacer(requests_per_second=1_000_000),
    )

    notes = list(
        client.iter_notes(ListNotesQuery(updated_after=datetime(2026, 7, 15, 9, tzinfo=UTC)))
    )

    assert [note.id for note in notes] == ["not_1"]
    assert len(requests) == EXPECTED_ATTEMPTS
    assert requests[0].url.params["updated_after"] == "2026-07-15T09:00:00Z"
    assert requests[1].url.params["cursor"] == "next"
    http_client.close()


def test_retries_rate_limits_using_retry_after() -> None:
    """A 429 response should honor Retry-After before retrying."""
    attempts = 0
    fake_time = FakeTime()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "3"}, request=request)
        return httpx.Response(200, text='{"notes":[],"hasMore":false,"cursor":null}')

    http_client = httpx.Client(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    )
    client = GranolaClient(
        "grn_test",
        client=http_client,
        pacer=RequestPacer(clock=fake_time.clock, sleeper=fake_time.sleep),
        sleeper=fake_time.sleep,
    )

    assert list(client.iter_notes()) == []
    assert attempts == EXPECTED_ATTEMPTS
    assert fake_time.delays[0] == RETRY_AFTER_SECONDS
    http_client.close()


def test_rejects_stalled_cursor() -> None:
    """A repeated cursor should fail instead of looping forever."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='{"notes":[],"hasMore":true,"cursor":"same"}',
            request=request,
        )

    http_client = httpx.Client(
        base_url="https://example.test", transport=httpx.MockTransport(handler)
    )
    client = GranolaClient(
        "grn_test", client=http_client, pacer=RequestPacer(requests_per_second=1_000_000)
    )

    with pytest.raises(GranolaApiError, match="new cursor"):
        list(client.iter_notes(ListNotesQuery(cursor="same")))
    http_client.close()


def test_rejects_naive_filter_datetime() -> None:
    """Ambiguous local timestamps should not be sent to Granola."""
    client = GranolaClient("grn_test")
    with pytest.raises(ValueError, match="timezone-aware"):
        list(
            client.iter_notes(
                ListNotesQuery(
                    updated_after=datetime(2026, 7, 15)  # noqa: DTZ001 - intentionally naive.
                )
            )
        )
    client.close()

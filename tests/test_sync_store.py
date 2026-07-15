"""Tests for durable incremental synchronization state."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from granola_kg.database import fetch_object_row, initialize_database
from granola_kg.granola_models import NoteSummary, User
from granola_kg.sync_store import QueueCounts, SyncStore

BASE_TIME = datetime(2026, 7, 15, 9, tzinfo=UTC)
SECOND_ATTEMPT = 2
THIRD_ATTEMPT = 3


def note_summary(updated_at: datetime = BASE_TIME) -> NoteSummary:
    """Build a typed remote note summary."""
    return NoteSummary(
        id="not_1",
        object="note",
        title="Planning",
        owner=User(name="Alex", email="alex@example.com"),
        created_at=BASE_TIME - timedelta(hours=1),
        updated_at=updated_at,
    )


def test_discovers_only_new_or_updated_notes(tmp_path: Path) -> None:
    """Unchanged remote summaries should not reset completed queue jobs."""
    connection = initialize_database(tmp_path / "graph.db")
    store = SyncStore(connection)

    assert store.discover_note(note_summary()) is True
    claimed = store.claim_next()
    assert claimed is not None
    store.complete(claimed.note_id)
    assert store.discover_note(note_summary()) is False
    assert store.counts() == QueueCounts(complete=1)

    later = BASE_TIME + timedelta(minutes=5)
    assert store.discover_note(note_summary(later)) is True
    assert store.counts() == QueueCounts(pending=1)
    connection.close()


def test_watermark_advances_only_on_success(tmp_path: Path) -> None:
    """A failed discovery pass must preserve its previous safe watermark."""
    connection = initialize_database(tmp_path / "graph.db")
    store = SyncStore(connection)
    store.begin_sync()
    store.complete_sync(BASE_TIME)

    store.begin_sync()
    store.fail_sync("network unavailable")

    assert store.watermark() == BASE_TIME
    error = fetch_object_row(
        connection.execute("SELECT last_error FROM sync_state WHERE source_key = 'granola'")
    )
    assert error == ("network unavailable",)
    connection.close()


def test_claim_retry_recovery_and_completion(tmp_path: Path) -> None:
    """Claims should survive errors and interrupted jobs should be recoverable."""
    connection = initialize_database(tmp_path / "graph.db")
    store = SyncStore(connection)
    store.discover_note(note_summary())

    first = store.claim_next()
    assert first is not None
    assert first.attempts == 1
    store.fail(first.note_id, "model timeout")
    retry = store.claim_next()
    assert retry is not None
    assert retry.attempts == SECOND_ATTEMPT
    assert store.recover_interrupted() == 1
    recovered = store.claim_next()
    assert recovered is not None
    assert recovered.attempts == THIRD_ATTEMPT
    store.complete(recovered.note_id)
    assert store.claim_next() is None
    connection.close()


def test_reprocess_resets_completed_job(tmp_path: Path) -> None:
    """Explicit reprocessing should bypass remote update detection."""
    connection = initialize_database(tmp_path / "graph.db")
    store = SyncStore(connection)
    store.discover_note(note_summary())
    item = store.claim_next()
    assert item is not None
    store.complete(item.note_id)

    store.reprocess("not_1")

    assert store.counts() == QueueCounts(pending=1)
    with pytest.raises(ValueError, match="Unknown note"):
        store.reprocess("not_missing")
    connection.close()


def test_stops_retrying_at_attempt_limit(tmp_path: Path) -> None:
    """Permanently failing jobs should remain inspectable after the retry budget."""
    connection = initialize_database(tmp_path / "graph.db")
    store = SyncStore(connection)
    store.discover_note(note_summary())
    for _attempt in range(2):
        item = store.claim_next(max_attempts=2)
        assert item is not None
        store.fail(item.note_id, "still failing")

    assert store.claim_next(max_attempts=2) is None
    assert store.counts() == QueueCounts(failed=1)
    connection.close()

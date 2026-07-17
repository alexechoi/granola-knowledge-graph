"""Durable state for incremental discovery and note processing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from granola_kg.database import fetch_object_row, fetch_object_rows

if TYPE_CHECKING:
    import sqlite3

    from granola_kg.granola_models import NoteSummary

DEFAULT_SOURCE_KEY = "granola"
PAIR_ROW_SIZE = 2
QUEUE_ROW_SIZE = 4


@dataclass(frozen=True)
class QueueItem:
    """One claimed note-processing job."""

    note_id: str
    remote_updated_at: datetime
    attempts: int
    force_reprocess: bool


@dataclass(frozen=True)
class QueueCounts:
    """Processing queue counts by state."""

    pending: int = 0
    processing: int = 0
    complete: int = 0
    failed: int = 0


class SyncStore:
    """Manage discovery watermarks and a restart-safe SQLite queue."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Bind state operations to one configured connection."""
        self._connection = connection

    def begin_sync(self, source_key: str = DEFAULT_SOURCE_KEY) -> None:
        """Record the start of a discovery pass without moving its watermark."""
        self._connection.execute(
            """
            INSERT INTO sync_state(source_key, last_started_at, last_error)
            VALUES (?, CURRENT_TIMESTAMP, NULL)
            ON CONFLICT(source_key) DO UPDATE SET
                last_started_at = CURRENT_TIMESTAMP, last_error = NULL
            """,
            (source_key,),
        )
        self._connection.commit()

    def complete_sync(
        self, watermark: datetime | None, source_key: str = DEFAULT_SOURCE_KEY
    ) -> None:
        """Advance the watermark only after a complete discovery pass."""
        self._connection.execute(
            """
            INSERT INTO sync_state(source_key, watermark, last_completed_at, last_error)
            VALUES (?, ?, CURRENT_TIMESTAMP, NULL)
            ON CONFLICT(source_key) DO UPDATE SET
                watermark = COALESCE(excluded.watermark, sync_state.watermark),
                last_completed_at = CURRENT_TIMESTAMP,
                last_error = NULL
            """,
            (source_key, _iso(watermark)),
        )
        self._connection.commit()

    def fail_sync(self, error: str, source_key: str = DEFAULT_SOURCE_KEY) -> None:
        """Record a discovery error while preserving the previous watermark."""
        self._connection.execute(
            """
            INSERT INTO sync_state(source_key, last_error) VALUES (?, ?)
            ON CONFLICT(source_key) DO UPDATE SET last_error = excluded.last_error
            """,
            (source_key, error),
        )
        self._connection.commit()

    def watermark(self, source_key: str = DEFAULT_SOURCE_KEY) -> datetime | None:
        """Return the last fully discovered remote update timestamp."""
        row = fetch_object_row(
            self._connection.execute(
                "SELECT watermark FROM sync_state WHERE source_key = ?", (source_key,)
            )
        )
        if row is None or not row or row[0] is None:
            return None
        if not isinstance(row[0], str):
            msg = "Database returned an invalid sync watermark"
            raise TypeError(msg)
        return datetime.fromisoformat(row[0])

    def discover_note(self, note: NoteSummary) -> bool:
        """Upsert remote summary metadata and enqueue a new or changed note."""
        remote_updated_at = note.updated_at.isoformat()
        existing = fetch_object_row(
            self._connection.execute(
                "SELECT updated_at FROM source_notes WHERE note_id = ?", (note.id,)
            )
        )
        previous = existing[0] if existing is not None and existing else None
        if previous is not None and not isinstance(previous, str):
            msg = "Database returned an invalid note update timestamp"
            raise RuntimeError(msg)
        changed = previous is None or datetime.fromisoformat(previous) < note.updated_at
        self._connection.execute(
            """
            INSERT INTO source_notes(
                note_id, title, owner_name, owner_email, created_at, updated_at, visibility
            ) VALUES (?, ?, ?, ?, ?, ?, 'active')
            ON CONFLICT(note_id) DO UPDATE SET
                title = excluded.title,
                owner_name = excluded.owner_name,
                owner_email = excluded.owner_email,
                updated_at = excluded.updated_at,
                visibility = 'active'
            """,
            (
                note.id,
                note.title,
                note.owner.name,
                note.owner.email,
                note.created_at.isoformat(),
                remote_updated_at,
            ),
        )
        if changed:
            self._connection.execute(
                """
                INSERT INTO processing_queue(note_id, remote_updated_at, state, last_error)
                VALUES (?, ?, 'pending', NULL)
                ON CONFLICT(note_id) DO UPDATE SET
                    remote_updated_at = excluded.remote_updated_at,
                    state = 'pending',
                    attempts = 0,
                    force_reprocess = 0,
                    last_error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (note.id, remote_updated_at),
            )
        self._connection.commit()
        return changed

    def recover_interrupted(self) -> int:
        """Return jobs left processing by a crashed process to pending state."""
        cursor = self._connection.execute(
            """
            UPDATE processing_queue SET state = 'pending', updated_at = CURRENT_TIMESTAMP
            WHERE state = 'processing'
            """
        )
        self._connection.commit()
        return cursor.rowcount

    def claim_next(self, *, max_attempts: int = 5) -> QueueItem | None:
        """Atomically claim the oldest retryable job."""
        with self._connection:
            row = fetch_object_row(
                self._connection.execute(
                    """
                    SELECT note_id, remote_updated_at, attempts, force_reprocess
                    FROM processing_queue
                    WHERE state IN ('pending', 'failed') AND attempts < ?
                    ORDER BY updated_at, queued_at, note_id
                    LIMIT 1
                    """,
                    (max_attempts,),
                )
            )
            if row is None:
                return None
            note_id, remote_updated_at, attempts, force_reprocess = _queue_values(row)
            self._connection.execute(
                """
                UPDATE processing_queue
                SET state = 'processing', attempts = attempts + 1,
                    last_error = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE note_id = ?
                """,
                (note_id,),
            )
        return QueueItem(note_id, remote_updated_at, attempts + 1, force_reprocess)

    def complete(self, note_id: str) -> None:
        """Mark a claimed job complete."""
        self._set_job_state(note_id, "complete", None)

    def fail(self, note_id: str, error: str) -> None:
        """Retain a failed job for a later bounded retry."""
        self._set_job_state(note_id, "failed", error)

    def reprocess(self, note_id: str) -> None:
        """Queue a retained note regardless of its processed hash."""
        row = fetch_object_row(
            self._connection.execute(
                "SELECT updated_at FROM source_notes WHERE note_id = ?", (note_id,)
            )
        )
        if row is None or not row or not isinstance(row[0], str):
            msg = f"Unknown note: {note_id}"
            raise ValueError(msg)
        self._queue_for_reprocessing(note_id, row[0])
        self._connection.commit()

    def reprocess_all(self) -> int:
        """Queue every retained active note for ontology or prompt upgrades."""
        rows = fetch_object_rows(
            self._connection.execute(
                "SELECT note_id, updated_at FROM source_notes WHERE visibility = 'active'"
            )
        )
        queued = 0
        for row in rows:
            if len(row) == PAIR_ROW_SIZE and isinstance(row[0], str) and isinstance(row[1], str):
                self._queue_for_reprocessing(row[0], row[1])
                queued += 1
        self._connection.commit()
        return queued

    def counts(self) -> QueueCounts:
        """Return a complete queue-state summary."""
        values = {"pending": 0, "processing": 0, "complete": 0, "failed": 0}
        rows = fetch_object_rows(
            self._connection.execute("SELECT state, COUNT(*) FROM processing_queue GROUP BY state")
        )
        for row in rows:
            if len(row) == PAIR_ROW_SIZE and isinstance(row[0], str) and isinstance(row[1], int):
                values[row[0]] = row[1]
        return QueueCounts(
            pending=values["pending"],
            processing=values["processing"],
            complete=values["complete"],
            failed=values["failed"],
        )

    def _queue_for_reprocessing(self, note_id: str, updated_at: str) -> None:
        self._connection.execute(
            """
            INSERT INTO processing_queue(
                note_id, remote_updated_at, state, attempts, last_error, force_reprocess
            ) VALUES (?, ?, 'pending', 0, NULL, 1)
            ON CONFLICT(note_id) DO UPDATE SET
                state = 'pending', attempts = 0, last_error = NULL,
                force_reprocess = 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (note_id, updated_at),
        )

    def _set_job_state(self, note_id: str, state: str, error: str | None) -> None:
        cursor = self._connection.execute(
            """
            UPDATE processing_queue SET state = ?, last_error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE note_id = ? AND state = 'processing'
            """,
            (state, error, note_id),
        )
        if cursor.rowcount != 1:
            msg = f"Note is not currently claimed: {note_id}"
            raise RuntimeError(msg)
        self._connection.commit()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _queue_values(row: tuple[object, ...]) -> tuple[str, datetime, int, bool]:
    if (
        len(row) != QUEUE_ROW_SIZE
        or not isinstance(row[0], str)
        or not isinstance(row[1], str)
        or not isinstance(row[2], int)
        or not isinstance(row[3], int)
    ):
        msg = "Database returned an invalid queue item"
        raise RuntimeError(msg)
    return row[0], datetime.fromisoformat(row[1]), row[2], row[3] == 1

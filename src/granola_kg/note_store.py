"""Materialize Granola notes as deterministic local evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from granola_kg.database import fetch_object_rows

if TYPE_CHECKING:
    import sqlite3
    from datetime import datetime

    from granola_kg.granola_models import NoteDetail, TranscriptSegment


@dataclass(frozen=True)
class NoteMaterialization:
    """Outcome of writing one source note and its active evidence set."""

    note_id: str
    content_hash: str
    evidence_ids: tuple[str, ...]


class NoteStore:
    """Persist source metadata, evidence units, and their FTS projection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Bind the store to a caller-managed SQLite connection."""
        self._connection = connection

    def materialize(self, note: NoteDetail) -> NoteMaterialization:
        """Upsert a note and replace its active evidence projection."""
        raw_json = note.model_dump_json(by_alias=True)
        content_hash = _digest(raw_json)
        event = note.calendar_event
        folder_ids = [folder.id for folder in note.folder_membership]
        self._connection.execute(
            """
            INSERT INTO source_notes(
                note_id, title, owner_name, owner_email, web_url,
                calendar_event_id, scheduled_start_at, scheduled_end_at,
                created_at, updated_at, content_hash, folder_ids_json,
                raw_json, visibility
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
            ON CONFLICT(note_id) DO UPDATE SET
                title = excluded.title,
                owner_name = excluded.owner_name,
                owner_email = excluded.owner_email,
                web_url = excluded.web_url,
                calendar_event_id = excluded.calendar_event_id,
                scheduled_start_at = excluded.scheduled_start_at,
                scheduled_end_at = excluded.scheduled_end_at,
                updated_at = excluded.updated_at,
                content_hash = excluded.content_hash,
                folder_ids_json = excluded.folder_ids_json,
                raw_json = excluded.raw_json,
                visibility = 'active'
            """,
            (
                note.id,
                note.title,
                note.owner.name,
                note.owner.email,
                note.web_url,
                event.calendar_event_id if event is not None else None,
                _iso(event.scheduled_start_time) if event is not None else None,
                _iso(event.scheduled_end_time) if event is not None else None,
                _iso(note.created_at),
                _iso(note.updated_at),
                content_hash,
                json.dumps(folder_ids, separators=(",", ":")),
                raw_json,
            ),
        )
        self._connection.execute(
            "UPDATE evidence_units SET is_active = 0 WHERE note_id = ?", (note.id,)
        )
        evidence_ids: list[str] = []
        if note.summary_text.strip():
            evidence_ids.append(
                self._write_evidence(
                    note_id=note.id,
                    kind="summary",
                    index=0,
                    content=note.summary_text,
                    segment=None,
                )
            )
        for index, segment in enumerate(note.transcript or []):
            if segment.text.strip():
                evidence_ids.append(
                    self._write_evidence(
                        note_id=note.id,
                        kind="transcript",
                        index=index,
                        content=segment.text,
                        segment=segment,
                    )
                )
        self._rebuild_note_fts(note.id, note.title or "")
        return NoteMaterialization(
            note_id=note.id,
            content_hash=content_hash,
            evidence_ids=tuple(evidence_ids),
        )

    def hide_missing(self, active_note_ids: set[str]) -> int:
        """Hide retained notes absent from a complete remote reconciliation."""
        rows = fetch_object_rows(
            self._connection.execute("SELECT note_id FROM source_notes WHERE visibility = 'active'")
        )
        missing = [
            row[0]
            for row in rows
            if row and isinstance(row[0], str) and row[0] not in active_note_ids
        ]
        for note_id in missing:
            self._connection.execute(
                "UPDATE source_notes SET visibility = 'hidden' WHERE note_id = ?", (note_id,)
            )
            self._connection.execute(
                "DELETE FROM evidence_fts WHERE evidence_id IN "
                "(SELECT evidence_id FROM evidence_units WHERE note_id = ?)",
                (note_id,),
            )
            self._connection.execute("DELETE FROM note_fts WHERE note_id = ?", (note_id,))
        return len(missing)

    def _write_evidence(
        self,
        *,
        note_id: str,
        kind: str,
        index: int,
        content: str,
        segment: TranscriptSegment | None,
    ) -> str:
        normalized_content = content.strip()
        content_hash = _digest(normalized_content)
        evidence_id = "ev_" + _digest(f"{note_id}:{kind}:{index}:{content_hash}")[:32]
        speaker = segment.speaker if segment is not None else None
        self._connection.execute(
            """
            INSERT INTO evidence_units(
                evidence_id, note_id, unit_kind, unit_index, content,
                speaker_source, speaker_label, speaker_name, started_at,
                ended_at, content_hash, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(evidence_id) DO UPDATE SET is_active = 1
            """,
            (
                evidence_id,
                note_id,
                kind,
                index,
                normalized_content,
                speaker.source if speaker is not None else None,
                speaker.diarization_label if speaker is not None else None,
                speaker.name if speaker is not None else None,
                _iso(segment.start_time) if segment is not None else None,
                _iso(segment.end_time) if segment is not None else None,
                content_hash,
            ),
        )
        return evidence_id

    def _rebuild_note_fts(self, note_id: str, title: str) -> None:
        self._connection.execute(
            "DELETE FROM evidence_fts WHERE evidence_id IN "
            "(SELECT evidence_id FROM evidence_units WHERE note_id = ?)",
            (note_id,),
        )
        self._connection.execute(
            """
            INSERT INTO evidence_fts(evidence_id, title, content)
            SELECT evidence_id, '', content FROM evidence_units
            WHERE note_id = ? AND is_active = 1
            """,
            (note_id,),
        )
        self._connection.execute("DELETE FROM note_fts WHERE note_id = ?", (note_id,))
        self._connection.execute(
            """
            INSERT INTO note_fts(note_id, title, summary)
            SELECT ?, ?, COALESCE((
                SELECT content FROM evidence_units
                WHERE note_id = ? AND unit_kind = 'summary' AND is_active = 1
                ORDER BY unit_index LIMIT 1
            ), '')
            """,
            (note_id, title, note_id),
        )


def _digest(value: str) -> str:
    """Return a stable SHA-256 hex digest."""
    return hashlib.sha256(value.encode()).hexdigest()


def _iso(value: datetime | None) -> str | None:
    """Serialize an optional datetime."""
    if value is None:
        return None
    return value.isoformat()

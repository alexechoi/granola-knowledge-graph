"""Tests for deterministic note and evidence persistence."""

from pathlib import Path

from granola_kg.database import fetch_object_row, fetch_object_rows, initialize_database
from granola_kg.granola_models import NoteDetail
from granola_kg.note_store import NoteStore

NOTE_JSON = """{
  "id": "not_1", "object": "note", "title": "Launch planning",
  "owner": {"name": "Alex", "email": "alex@example.com"},
  "created_at": "2026-07-15T08:00:00Z", "updated_at": "2026-07-15T09:00:00Z",
  "web_url": "https://notes.example/not_1",
  "calendar_event": {
    "event_title": "Launch planning", "invitees": [],
    "organiser": "alex@example.com", "calendar_event_id": "cal_1",
    "scheduled_start_time": "2026-07-15T08:00:00Z",
    "scheduled_end_time": "2026-07-15T09:00:00Z"
  },
  "attendees": [{"name": "Alex", "email": "alex@example.com"}],
  "folder_membership": [{
    "id": "fol_1", "object": "folder", "name": "Team", "parent_folder_id": null
  }],
  "summary_text": "The team approved a Friday launch.",
  "summary_markdown": "## Launch",
  "transcript": [{
    "speaker": {"source": "microphone", "name": "Alex"},
    "text": "We will ship on Friday.",
    "start_time": "2026-07-15T08:10:00Z",
    "end_time": "2026-07-15T08:10:05Z"
  }]
}"""
EXPECTED_EVIDENCE_COUNT = 2


def test_materializes_metadata_evidence_and_fts(tmp_path: Path) -> None:
    """A note should create searchable summary and transcript evidence."""
    connection = initialize_database(tmp_path / "graph.db")
    store = NoteStore(connection)

    result = store.materialize(NoteDetail.model_validate_json(NOTE_JSON))
    connection.commit()

    rows = fetch_object_rows(
        connection.execute(
            "SELECT unit_kind, content, speaker_name FROM evidence_units "
            "WHERE note_id = ? AND is_active = 1 ORDER BY unit_kind",
            (result.note_id,),
        )
    )
    assert len(result.evidence_ids) == EXPECTED_EVIDENCE_COUNT
    assert rows == [
        ("summary", "The team approved a Friday launch.", None),
        ("transcript", "We will ship on Friday.", "Alex"),
    ]
    fts_rows = fetch_object_rows(
        connection.execute("SELECT evidence_id FROM evidence_fts WHERE evidence_fts MATCH 'Friday'")
    )
    assert len(fts_rows) == EXPECTED_EVIDENCE_COUNT
    connection.close()


def test_reingestion_is_stable_and_deactivates_changed_evidence(tmp_path: Path) -> None:
    """Unchanged input should reuse IDs while edited input retires old evidence."""
    connection = initialize_database(tmp_path / "graph.db")
    store = NoteStore(connection)
    original = NoteDetail.model_validate_json(NOTE_JSON)

    first = store.materialize(original)
    second = store.materialize(original)
    changed = NoteDetail.model_validate_json(
        NOTE_JSON.replace("We will ship on Friday.", "We will ship on Monday.")
    )
    third = store.materialize(changed)

    assert first.evidence_ids == second.evidence_ids
    assert third.evidence_ids[0] == first.evidence_ids[0]
    assert third.evidence_ids[1] != first.evidence_ids[1]
    rows = fetch_object_rows(
        connection.execute(
            "SELECT content, is_active FROM evidence_units "
            "WHERE unit_kind = 'transcript' ORDER BY content"
        )
    )
    assert rows == [("We will ship on Friday.", 0), ("We will ship on Monday.", 1)]
    connection.close()


def test_reconciliation_hides_but_retains_missing_notes(tmp_path: Path) -> None:
    """Missing remote notes should remain stored but disappear from search."""
    connection = initialize_database(tmp_path / "graph.db")
    store = NoteStore(connection)
    store.materialize(NoteDetail.model_validate_json(NOTE_JSON))

    hidden = store.hide_missing(set())

    visibility = fetch_object_row(
        connection.execute("SELECT visibility FROM source_notes WHERE note_id = 'not_1'")
    )
    evidence_count = fetch_object_row(connection.execute("SELECT COUNT(*) FROM evidence_units"))
    fts_count = fetch_object_row(connection.execute("SELECT COUNT(*) FROM evidence_fts"))
    assert hidden == 1
    assert visibility is not None
    assert visibility[0] == "hidden"
    assert evidence_count is not None
    assert evidence_count[0] == EXPECTED_EVIDENCE_COUNT
    assert fts_count is not None
    assert fts_count[0] == 0
    connection.close()

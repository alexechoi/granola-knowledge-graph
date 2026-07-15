"""Contract tests for Granola API response models."""

import pytest
from pydantic import ValidationError

from granola_kg.granola_models import ListNotesResponse, NoteDetail

MACOS_NOTE = r"""
{
  "id": "not_1d3tmYTlCICgjy",
  "object": "note",
  "title": "Quarterly yoghurt budget review",
  "owner": {"name": "Oat Benson", "email": "oat@granola.ai"},
  "created_at": "2026-01-27T15:30:00Z",
  "updated_at": "2026-01-27T16:45:00Z",
  "web_url": "https://notes.granola.ai/d/example",
  "calendar_event": {
    "event_title": "Quarterly yoghurt budget review",
    "invitees": [{"email": "raisin@granola.ai"}],
    "organiser": "oat@granola.ai",
    "calendar_event_id": "calendar_123",
    "scheduled_start_time": "2026-01-27T15:30:00Z",
    "scheduled_end_time": "2026-01-27T16:30:00Z"
  },
  "attendees": [
    {"name": "Oat Benson", "email": "oat@granola.ai"},
    {"name": "Raisin Patel", "email": "raisin@granola.ai"}
  ],
  "folder_membership": [
    {
      "id": "fol_4y6LduVdwSKC27",
      "object": "folder",
      "name": "Top secret recipes",
      "parent_folder_id": null
    }
  ],
  "summary_text": "The budget review was successful.",
  "summary_markdown": "## Budget review",
  "transcript": [
    {
      "speaker": {"source": "microphone", "name": "Oat Benson"},
      "text": "Greek yoghurt deserves us.",
      "start_time": "2026-01-27T15:30:00Z",
      "end_time": "2026-01-27T15:30:05Z"
    },
    {
      "speaker": {"source": "speaker"},
      "text": "Regular yoghurt gave up halfway.",
      "start_time": "2026-01-27T15:30:05Z",
      "end_time": "2026-01-27T15:30:10Z"
    }
  ]
}
"""

IOS_NOTE = r"""
{
  "id": "not_2d3tmYTlCICgjz",
  "object": "note",
  "title": null,
  "owner": {"name": null, "email": "owner@example.com"},
  "created_at": "2026-01-28T15:30:00Z",
  "updated_at": "2026-01-28T16:45:00Z",
  "web_url": "https://notes.granola.ai/d/mobile",
  "calendar_event": null,
  "attendees": [],
  "folder_membership": [],
  "summary_text": "Mobile meeting.",
  "summary_markdown": null,
  "transcript": [
    {
      "speaker": {"source": "microphone", "diarization_label": "Speaker A"},
      "text": "First speaker.",
      "start_time": "2026-01-28T15:30:00Z",
      "end_time": "2026-01-28T15:30:05Z"
    }
  ]
}
"""


def test_parses_desktop_note_shape() -> None:
    """Desktop sources and identified speakers should be retained."""
    note = NoteDetail.model_validate_json(MACOS_NOTE)
    assert note.calendar_event is not None
    assert note.calendar_event.invitees[0].email == "raisin@granola.ai"
    assert note.transcript is not None
    assert note.transcript[0].speaker.name == "Oat Benson"
    assert note.transcript[1].speaker.source == "speaker"


def test_parses_mobile_diarization_and_nullable_fields() -> None:
    """Mobile anonymous speakers and notes without events must validate."""
    note = NoteDetail.model_validate_json(IOS_NOTE)
    assert note.title is None
    assert note.calendar_event is None
    assert note.transcript is not None
    assert note.transcript[0].speaker.diarization_label == "Speaker A"


def test_parses_cursor_aliases() -> None:
    """Granola's camel-case pagination flag should map to a Python field."""
    page = ListNotesResponse.model_validate_json(
        '{"notes": [], "hasMore": true, "cursor": "next-page"}'
    )
    assert page.has_more is True
    assert page.cursor == "next-page"


def test_rejects_undocumented_response_fields() -> None:
    """Unexpected API changes should fail visibly at the boundary."""
    changed = MACOS_NOTE.replace('"object": "note",', '"object": "note", "surprise": 1,')
    with pytest.raises(ValidationError):
        NoteDetail.model_validate_json(changed)

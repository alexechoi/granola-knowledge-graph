"""Strict models for the public Granola API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 - Pydantic resolves this at runtime.

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    """Base model that rejects undocumented response fields."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, strict=True)


class User(ApiModel):
    """Granola user or attendee."""

    name: str | None
    email: str


class Folder(ApiModel):
    """Granola folder and its optional parent."""

    id: str
    object: str
    name: str
    parent_folder_id: str | None


class Speaker(ApiModel):
    """Transcript speaker metadata across desktop and mobile clients."""

    source: str
    diarization_label: str | None = None
    name: str | None = None


class TranscriptSegment(ApiModel):
    """One timestamped transcript segment."""

    speaker: Speaker
    text: str
    start_time: datetime
    end_time: datetime


class CalendarInvitee(ApiModel):
    """Calendar invitee returned without profile metadata."""

    email: str


class CalendarEvent(ApiModel):
    """Calendar event attached to a note."""

    event_title: str | None
    invitees: list[CalendarInvitee]
    organiser: str | None
    calendar_event_id: str | None
    scheduled_start_time: datetime | None
    scheduled_end_time: datetime | None


class NoteSummary(ApiModel):
    """List Notes representation of a meeting note."""

    id: str
    object: str
    title: str | None
    owner: User
    created_at: datetime
    updated_at: datetime


class NoteDetail(NoteSummary):
    """Complete meeting note with optional transcript."""

    web_url: str
    calendar_event: CalendarEvent | None
    attendees: list[User]
    folder_membership: list[Folder]
    summary_text: str
    summary_markdown: str | None
    transcript: list[TranscriptSegment] | None


class ListNotesResponse(ApiModel):
    """Cursor page returned by List Notes."""

    notes: list[NoteSummary]
    has_more: bool = Field(alias="hasMore")
    cursor: str | None


class ListFoldersResponse(ApiModel):
    """Cursor page returned by List Folders."""

    folders: list[Folder]
    has_more: bool = Field(alias="hasMore")
    cursor: str | None


@dataclass(frozen=True)
class ListNotesQuery:
    """Typed List Notes filters."""

    created_before: datetime | None = None
    created_after: datetime | None = None
    updated_after: datetime | None = None
    folder_id: str | None = None
    cursor: str | None = None
    page_size: int = 30

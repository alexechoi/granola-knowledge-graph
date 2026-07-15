"""Tests for incremental API and extraction orchestration."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from granola_kg.database import fetch_object_row, initialize_database
from granola_kg.extraction_models import (
    ExtractedEntity,
    ExtractedIdentifier,
    ExtractionResult,
    OntologyProposal,
)
from granola_kg.granola_models import ListNotesQuery, NoteDetail, NoteSummary, User
from granola_kg.sync_engine import SyncEngine
from granola_kg.sync_store import QueueCounts, SyncStore

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

BASE_TIME = datetime(2026, 7, 15, 9, tzinfo=UTC)


def summary(note_id: str, minute: int = 0) -> NoteSummary:
    """Build a remote summary at a stable update time."""
    return NoteSummary(
        id=note_id,
        object="note",
        title=f"Planning {note_id}",
        owner=User(name="Alex", email="alex@example.com"),
        created_at=BASE_TIME - timedelta(hours=1),
        updated_at=BASE_TIME + timedelta(minutes=minute),
    )


def detail(note_id: str) -> NoteDetail:
    """Build a complete summarized Granola note."""
    source = summary(note_id)
    return NoteDetail(
        id=source.id,
        object=source.object,
        title=source.title,
        owner=source.owner,
        created_at=source.created_at,
        updated_at=source.updated_at,
        web_url=f"https://notes.example/{note_id}",
        calendar_event=None,
        attendees=[],
        folder_membership=[],
        summary_text="The team confirmed the plan.",
        summary_markdown=None,
        transcript=None,
    )


class FakeGranola:
    """In-memory Granola gateway with query capture."""

    def __init__(self, summaries: list[NoteSummary]) -> None:
        """Store a mutable remote note set."""
        self.summaries = summaries
        self.queries: list[ListNotesQuery | None] = []

    def iter_notes(self, query: ListNotesQuery | None = None) -> Iterator[NoteSummary]:
        """Yield summaries honoring the incremental timestamp."""
        self.queries.append(query)
        for item in self.summaries:
            if (
                query is None
                or query.updated_after is None
                or item.updated_at > query.updated_after
            ):
                yield item

    def get_note(self, note_id: str, *, include_transcript: bool = True) -> NoteDetail:
        """Return a complete note for a known summary."""
        if not include_transcript:
            msg = "Tests require transcript-inclusive retrieval"
            raise ValueError(msg)
        return detail(note_id)


class FakeExtractor:
    """Structured extractor that cites the supplied evidence."""

    def __init__(self, *, fail: bool = False) -> None:
        """Optionally configure a deterministic provider failure."""
        self.fail = fail
        self.ontology_seen = False

    def extract(self, *, title: str, evidence_json: str, ontology_json: str) -> ExtractionResult:
        """Return a meeting entity using the evidence ID embedded in the prompt."""
        if self.fail:
            msg = "model unavailable"
            raise RuntimeError(msg)
        self.ontology_seen = '"type_key":"meeting"' in ontology_json
        match = re.search(r"ev_[a-f0-9]+", evidence_json)
        if match is None:
            msg = "Evidence prompt had no ID"
            raise RuntimeError(msg)
        return ExtractionResult(
            ontology=OntologyProposal(),
            entities=[
                ExtractedEntity(
                    local_id="meeting_1",
                    type_key="meeting",
                    name=title,
                    identifiers=[ExtractedIdentifier(field_key="note_id", value="not_1")],
                    evidence_ids=[match.group()],
                )
            ],
        )


def test_incremental_discovery_and_processing(tmp_path: Path) -> None:
    """A discovered note should be extracted once and advance the watermark."""
    connection = initialize_database(tmp_path / "graph.db")
    granola = FakeGranola([summary("not_1")])
    extractor = FakeExtractor()
    engine = SyncEngine(
        connection, granola, extractor, prompt_version="v1", model_name="test-model"
    )

    discovery = engine.discover()
    processing = engine.process()
    unchanged = engine.discover()

    assert discovery.enqueued == 1
    assert processing.completed == 1
    assert processing.failed == 0
    assert extractor.ontology_seen is True
    assert unchanged.enqueued == 0
    assert granola.queries[-1] is not None
    assert granola.queries[-1].updated_after == BASE_TIME
    assert SyncStore(connection).counts() == QueueCounts(complete=1)
    connection.close()


def test_processing_failure_is_retained_for_retry(tmp_path: Path) -> None:
    """A provider failure should not lose its queue job or source evidence."""
    connection = initialize_database(tmp_path / "graph.db")
    engine = SyncEngine(
        connection,
        FakeGranola([summary("not_1")]),
        FakeExtractor(fail=True),
        prompt_version="v1",
        model_name="test-model",
    )
    engine.discover()

    report = engine.process(limit=1)

    assert report.failed_note_ids == ("not_1",)
    assert SyncStore(connection).counts() == QueueCounts(failed=1)
    assert fetch_object_row(connection.execute("SELECT COUNT(*) FROM evidence_units")) == (1,)
    connection.close()


def test_full_reconciliation_hides_disappeared_notes(tmp_path: Path) -> None:
    """A complete listing should hide, not delete, notes absent remotely."""
    connection = initialize_database(tmp_path / "graph.db")
    granola = FakeGranola([summary("not_1"), summary("not_2", 1)])
    engine = SyncEngine(
        connection, granola, FakeExtractor(), prompt_version="v1", model_name="test-model"
    )
    engine.reconcile()
    granola.summaries = [summary("not_1")]

    report = engine.reconcile()

    hidden = fetch_object_row(
        connection.execute("SELECT visibility FROM source_notes WHERE note_id = 'not_2'")
    )
    assert report.hidden == 1
    assert hidden == ("hidden",)
    connection.close()

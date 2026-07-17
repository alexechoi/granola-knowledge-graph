"""Incremental Granola discovery and extraction orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from granola_kg.database import fetch_object_row
from granola_kg.extraction_applier import ExtractionApplier
from granola_kg.granola_models import ListNotesQuery
from granola_kg.graph_store import GraphStore
from granola_kg.note_store import NoteStore
from granola_kg.prompt_builder import PromptBuilder
from granola_kg.sync_store import SyncStore

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Iterator
    from datetime import datetime

    from granola_kg.granola_models import NoteDetail, NoteSummary
    from granola_kg.llm_client import ExtractionResponse


class GranolaGateway(Protocol):
    """Granola operations required by synchronization."""

    def iter_notes(self, query: ListNotesQuery | None = None) -> Iterator[NoteSummary]:
        """Yield remote note summaries."""
        ...

    def get_note(self, note_id: str, *, include_transcript: bool = True) -> NoteDetail:
        """Fetch a full note."""
        ...


class ExtractionProvider(Protocol):
    """Structured model operation required by processing."""

    def extract(self, *, title: str, evidence_json: str, ontology_json: str) -> ExtractionResponse:
        """Return one validated graph extraction."""
        ...


@dataclass(frozen=True)
class DiscoveryReport:
    """Outcome of a remote discovery or reconciliation pass."""

    discovered: int
    enqueued: int
    hidden: int
    watermark: datetime | None


@dataclass(frozen=True)
class ProcessingReport:
    """Outcome of draining the local processing queue."""

    completed: int
    skipped: int
    failed: int
    failed_note_ids: tuple[str, ...]


class SyncEngine:
    """Coordinate Granola, SQLite state, and structured extraction."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        granola: GranolaGateway,
        extractor: ExtractionProvider,
        *,
        prompt_version: str,
        model_name: str,
    ) -> None:
        """Bind remote adapters and processing provenance to local state."""
        self._connection = connection
        self._granola = granola
        self._extractor = extractor
        self._prompt_version = prompt_version
        self._model_name = model_name
        self._state = SyncStore(connection)
        self._notes = NoteStore(connection)
        self._prompts = PromptBuilder(connection)

    def discover(self, *, folder_id: str | None = None) -> DiscoveryReport:
        """Queue notes updated after the last fully completed discovery pass."""
        previous = self._state.watermark()
        return self._discover(
            ListNotesQuery(updated_after=previous, folder_id=folder_id), reconcile=False
        )

    def reconcile(self, *, folder_id: str | None = None) -> DiscoveryReport:
        """List the complete remote set and hide retained notes that disappeared."""
        return self._discover(ListNotesQuery(folder_id=folder_id), reconcile=True)

    def process(self, *, limit: int | None = None, max_attempts: int = 5) -> ProcessingReport:
        """Drain retryable jobs, retaining individual failures for later attempts."""
        self._state.recover_interrupted()
        completed = 0
        skipped = 0
        failed_ids: list[str] = []
        while limit is None or completed + skipped + len(failed_ids) < limit:
            item = self._state.claim_next(max_attempts=max_attempts)
            if item is None:
                break
            try:
                note = self._granola.get_note(item.note_id, include_transcript=True)
                with self._connection:
                    materialized = self._notes.materialize(note)
                    GraphStore(self._connection).ensure_seed_ontology()
                if not item.force_reprocess and self._already_processed(
                    item.note_id, materialized.content_hash
                ):
                    self._state.complete(item.note_id)
                    skipped += 1
                    continue
                title = note.title or "Untitled meeting"
                evidence_json = self._prompts.evidence_json(item.note_id, title)
                ontology_json = self._prompts.ontology_json(f"{title} {note.summary_text}")
                response = self._extractor.extract(
                    title=title,
                    evidence_json=evidence_json,
                    ontology_json=ontology_json,
                )
                ExtractionApplier(self._connection).apply(
                    note,
                    response.extraction,
                    prompt_version=self._prompt_version,
                    model_name=self._model_name,
                    usage=response.usage,
                )
                self._state.complete(item.note_id)
                completed += 1
            except Exception as error:  # noqa: BLE001 - one bad job must not stop the queue.
                self._state.fail(item.note_id, str(error))
                failed_ids.append(item.note_id)
        return ProcessingReport(completed, skipped, len(failed_ids), tuple(failed_ids))

    def _already_processed(self, note_id: str, content_hash: str) -> bool:
        row = fetch_object_row(
            self._connection.execute(
                "SELECT processed_hash FROM source_notes WHERE note_id = ?", (note_id,)
            )
        )
        return row is not None and len(row) == 1 and row[0] == content_hash

    def _discover(self, query: ListNotesQuery, *, reconcile: bool) -> DiscoveryReport:
        self._state.begin_sync()
        discovered = 0
        enqueued = 0
        hidden = 0
        active_ids: set[str] = set()
        latest = self._state.watermark()
        try:
            for note in self._granola.iter_notes(query):
                discovered += 1
                active_ids.add(note.id)
                if self._state.discover_note(note):
                    enqueued += 1
                if latest is None or note.updated_at > latest:
                    latest = note.updated_at
            if reconcile:
                with self._connection:
                    hidden = self._notes.hide_missing(active_ids)
            self._state.complete_sync(latest)
        except Exception as error:
            self._state.fail_sync(str(error))
            raise
        return DiscoveryReport(discovered, enqueued, hidden, latest)

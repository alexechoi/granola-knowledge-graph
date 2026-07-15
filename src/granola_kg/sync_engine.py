"""Incremental Granola discovery and extraction orchestration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from granola_kg.database import fetch_object_rows
from granola_kg.extraction_applier import ExtractionApplier
from granola_kg.granola_models import ListNotesQuery
from granola_kg.graph_store import GraphStore
from granola_kg.note_store import NoteStore
from granola_kg.sync_store import SyncStore

EVIDENCE_ROW_SIZE = 6
EVIDENCE_REQUIRED_TEXT_FIELDS = 3
ENTITY_ONTOLOGY_ROW_SIZE = 7
RELATION_ONTOLOGY_ROW_SIZE = 6

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Iterator
    from datetime import datetime

    from granola_kg.extraction_models import ExtractionResult
    from granola_kg.granola_models import NoteDetail, NoteSummary


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

    def extract(self, *, title: str, evidence_json: str, ontology_json: str) -> ExtractionResult:
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
        failed_ids: list[str] = []
        while limit is None or completed + len(failed_ids) < limit:
            item = self._state.claim_next(max_attempts=max_attempts)
            if item is None:
                break
            try:
                note = self._granola.get_note(item.note_id, include_transcript=True)
                with self._connection:
                    self._notes.materialize(note)
                    GraphStore(self._connection).ensure_seed_ontology()
                evidence_json = self._evidence_json(item.note_id)
                ontology_json = self._ontology_json()
                extraction = self._extractor.extract(
                    title=note.title or "Untitled meeting",
                    evidence_json=evidence_json,
                    ontology_json=ontology_json,
                )
                ExtractionApplier(self._connection).apply(
                    note,
                    extraction,
                    prompt_version=self._prompt_version,
                    model_name=self._model_name,
                )
                self._state.complete(item.note_id)
                completed += 1
            except Exception as error:  # noqa: BLE001 - one bad job must not stop the queue.
                self._state.fail(item.note_id, str(error))
                failed_ids.append(item.note_id)
        return ProcessingReport(completed, len(failed_ids), tuple(failed_ids))

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

    def _evidence_json(self, note_id: str) -> str:
        rows = fetch_object_rows(
            self._connection.execute(
                """
                SELECT evidence_id, unit_kind, content, speaker_name, started_at, ended_at
                FROM evidence_units
                WHERE note_id = ? AND is_active = 1
                ORDER BY unit_kind, unit_index
                """,
                (note_id,),
            )
        )
        evidence: list[dict[str, object]] = []
        for row in rows:
            if len(row) != EVIDENCE_ROW_SIZE or not all(
                isinstance(row[index], str) for index in range(EVIDENCE_REQUIRED_TEXT_FIELDS)
            ):
                msg = "Database returned invalid evidence prompt data"
                raise RuntimeError(msg)
            evidence.append(
                {
                    "evidence_id": row[0],
                    "kind": row[1],
                    "content": row[2],
                    "speaker_name": row[3],
                    "started_at": row[4],
                    "ended_at": row[5],
                }
            )
        return json.dumps(evidence, separators=(",", ":"))

    def _ontology_json(self) -> str:
        entity_rows = fetch_object_rows(
            self._connection.execute(
                """
                SELECT e.type_key, e.display_name, e.description, e.identity_scope,
                       f.field_key, f.data_type, f.is_identifier
                FROM entity_types AS e
                LEFT JOIN field_definitions AS f ON f.type_key = e.type_key
                ORDER BY e.type_key, f.field_key
                """
            )
        )
        relation_rows = fetch_object_rows(
            self._connection.execute(
                """
                SELECT relation_key, display_name, description,
                       source_type_key, target_type_key, is_directed
                FROM relation_types ORDER BY relation_key
                """
            )
        )
        snapshot: dict[str, object] = {
            "entity_type_fields": [_entity_ontology_record(row) for row in entity_rows],
            "relation_types": [_relation_ontology_record(row) for row in relation_rows],
        }
        return json.dumps(snapshot, separators=(",", ":"))


def _entity_ontology_record(row: tuple[object, ...]) -> dict[str, object]:
    """Convert a joined entity/field row into named prompt data."""
    if len(row) != ENTITY_ONTOLOGY_ROW_SIZE or not all(
        isinstance(row[index], str) for index in range(4)
    ):
        msg = "Database returned invalid entity ontology prompt data"
        raise RuntimeError(msg)
    return {
        "type_key": row[0],
        "display_name": row[1],
        "description": row[2],
        "identity_scope": row[3],
        "field_key": row[4],
        "field_data_type": row[5],
        "field_is_identifier": row[6],
    }


def _relation_ontology_record(row: tuple[object, ...]) -> dict[str, object]:
    """Convert a relation row into named prompt data."""
    if len(row) != RELATION_ONTOLOGY_ROW_SIZE or not all(
        isinstance(row[index], str) for index in range(5)
    ):
        msg = "Database returned invalid relation ontology prompt data"
        raise RuntimeError(msg)
    return {
        "relation_key": row[0],
        "display_name": row[1],
        "description": row[2],
        "source_type_key": row[3],
        "target_type_key": row[4],
        "is_directed": row[5],
    }

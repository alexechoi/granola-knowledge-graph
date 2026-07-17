"""Atomically apply structured meeting extractions to the local graph."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid4, uuid5

from granola_kg.database import fetch_object_row
from granola_kg.graph_models import (
    EntityCandidate,
    EntityTypeDefinition,
    FieldDefinition,
    IdentifierCandidate,
    IdentityScope,
    RelationTypeDefinition,
)
from granola_kg.graph_store import GraphStore
from granola_kg.note_store import NoteStore

if TYPE_CHECKING:
    import sqlite3

    from granola_kg.extraction_models import (
        ExtractedEntity,
        ExtractedProperty,
        ExtractionResult,
    )
    from granola_kg.granola_models import NoteDetail
    from granola_kg.llm_client import TokenUsage


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of one committed extraction application."""

    run_id: str
    note_id: str
    ontology_revision: int
    entity_ids: tuple[str, ...]


class ExtractionIntegrityError(RuntimeError):
    """Structured output references evidence or ontology that does not exist."""


class ExtractionApplier:
    """Coordinate source, ontology, and graph stores in one transaction."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Bind processing to one caller-owned database connection."""
        self._connection = connection
        self._notes = NoteStore(connection)
        self._graph = GraphStore(connection)

    def apply(
        self,
        note: NoteDetail,
        extraction: ExtractionResult,
        *,
        prompt_version: str,
        model_name: str,
        usage: TokenUsage | None = None,
    ) -> ApplyResult:
        """Validate and commit a complete graph replacement for one note."""
        run_id = str(uuid4())
        with self._connection:
            materialized = self._notes.materialize(note)
            self._graph.ensure_seed_ontology()
            self._validate_evidence(extraction, set(materialized.evidence_ids))
            revision, type_aliases = self._apply_ontology(note.id, extraction)
            self._retire_note_facts(note.id)
            entity_ids = self._apply_entities(
                note.id,
                note.title or "Untitled meeting",
                materialized.evidence_ids,
                extraction,
                type_aliases,
            )
            self._apply_relations(extraction, entity_ids)
            self._refresh_entity_indexes(note.id, set(entity_ids.values()))
            self._connection.execute(
                """
                INSERT INTO extraction_runs(
                    run_id, note_id, prompt_version, model_name, ontology_revision,
                    response_json, state, completed_at, input_tokens, output_tokens,
                    cached_input_tokens, reasoning_tokens
                ) VALUES (?, ?, ?, ?, ?, ?, 'complete', CURRENT_TIMESTAMP, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    note.id,
                    prompt_version,
                    model_name,
                    revision,
                    extraction.model_dump_json(),
                    usage.input_tokens if usage is not None else 0,
                    usage.output_tokens if usage is not None else 0,
                    usage.cached_input_tokens if usage is not None else 0,
                    usage.reasoning_tokens if usage is not None else 0,
                ),
            )
            self._connection.execute(
                """
                UPDATE source_notes
                SET processed_hash = content_hash, ontology_revision = ?,
                    processed_at = CURRENT_TIMESTAMP
                WHERE note_id = ?
                """,
                (revision, note.id),
            )
        return ApplyResult(run_id, note.id, revision, tuple(dict.fromkeys(entity_ids.values())))

    def _apply_ontology(
        self, note_id: str, extraction: ExtractionResult
    ) -> tuple[int, dict[str, str]]:
        proposal = extraction.ontology
        if not proposal.entity_types and not proposal.relation_types:
            return self._current_revision(), {}
        revision = self._graph.create_revision("automatic extraction", note_id)
        aliases: dict[str, str] = {}
        for entity_type in proposal.entity_types:
            fields = tuple(
                FieldDefinition(
                    key=field.key,
                    display_name=field.display_name,
                    description=field.description,
                    data_type=field.data_type,
                    is_identifier=field.is_identifier,
                )
                for field in entity_type.fields
            )
            canonical_key = self._graph.equivalent_type_key(entity_type.key)
            if canonical_key != entity_type.key:
                aliases[entity_type.key] = canonical_key
                self._graph.register_type_alias(entity_type.key, canonical_key, revision)
                self._graph.upsert_type_fields(canonical_key, fields, revision)
            else:
                self._graph.upsert_entity_type(
                    EntityTypeDefinition(
                        key=entity_type.key,
                        display_name=entity_type.display_name,
                        description=entity_type.description,
                        identity_scope=entity_type.identity_scope,
                        fields=fields,
                    ),
                    revision,
                )
        for relation in proposal.relation_types:
            self._graph.upsert_relation_type(
                RelationTypeDefinition(
                    key=relation.key,
                    display_name=relation.display_name,
                    description=relation.description,
                    source_type_key=aliases.get(
                        relation.source_type_key,
                        self._graph.equivalent_type_key(relation.source_type_key),
                    ),
                    target_type_key=aliases.get(
                        relation.target_type_key,
                        self._graph.equivalent_type_key(relation.target_type_key),
                    ),
                    is_directed=relation.is_directed,
                ),
                revision,
            )
        return revision, aliases

    def _apply_entities(
        self,
        note_id: str,
        note_title: str,
        evidence_ids: tuple[str, ...],
        extraction: ExtractionResult,
        type_aliases: dict[str, str],
    ) -> dict[str, str]:
        entity_ids: dict[str, str] = {}
        if evidence_ids:
            meeting = self._graph.resolve_entity(
                EntityCandidate(
                    "meeting",
                    note_title,
                    evidence_ids[0],
                    identifiers=(IdentifierCandidate("note_id", note_id),),
                )
            )
            entity_ids["_source_meeting"] = meeting.entity_id
        for extracted in extraction.entities:
            type_key = type_aliases.get(
                extracted.type_key, self._graph.equivalent_type_key(extracted.type_key)
            )
            self._validate_entity_fields(extracted, type_key)
            scope_note_id = (
                note_id if self._identity_scope(type_key) is IdentityScope.NOTE else None
            )
            name = note_title if type_key == "meeting" else extracted.name
            identifiers = (
                (IdentifierCandidate("note_id", note_id),)
                if type_key == "meeting"
                else tuple(
                    IdentifierCandidate(identifier.field_key, identifier.value)
                    for identifier in extracted.identifiers
                )
            )
            resolved_id = ""
            for evidence_id in extracted.evidence_ids:
                resolution = self._graph.resolve_entity(
                    EntityCandidate(
                        type_key=type_key,
                        name=name,
                        evidence_id=evidence_id,
                        scope_note_id=scope_note_id,
                        identifiers=identifiers,
                    )
                )
                if resolved_id and resolution.entity_id != resolved_id:
                    msg = f"Evidence for {extracted.local_id} resolved to different entities"
                    raise ExtractionIntegrityError(msg)
                resolved_id = resolution.entity_id
            entity_ids[extracted.local_id] = resolved_id
            for property_value in extracted.properties:
                self._write_property(resolved_id, property_value)
        return entity_ids

    def _apply_relations(self, extraction: ExtractionResult, entity_ids: dict[str, str]) -> None:
        for relation in extraction.relations:
            source_id = entity_ids[relation.source_local_id]
            target_id = entity_ids[relation.target_local_id]
            self._validate_relation(relation.relation_key, source_id, target_id)
            for evidence_id in relation.evidence_ids:
                stable_key = f"{source_id}:{relation.relation_key}:{target_id}:{evidence_id}"
                edge_id = str(uuid5(NAMESPACE_URL, stable_key))
                self._connection.execute(
                    """
                    INSERT INTO edges(
                        edge_id, source_entity_id, relation_key, target_entity_id,
                        confidence, source_evidence_id, is_active
                    ) VALUES (?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(source_entity_id, relation_key, target_entity_id,
                                source_evidence_id)
                    DO UPDATE SET confidence = excluded.confidence, is_active = 1
                    """,
                    (
                        edge_id,
                        source_id,
                        relation.relation_key,
                        target_id,
                        relation.confidence,
                        evidence_id,
                    ),
                )

    def _write_property(self, entity_id: str, property_value: ExtractedProperty) -> None:
        for evidence_id in property_value.evidence_ids:
            stable_key = f"{entity_id}:{property_value.field_key}:{evidence_id}"
            property_id = str(uuid5(NAMESPACE_URL, stable_key))
            value_json = json.dumps(property_value.value, separators=(",", ":"))
            self._connection.execute(
                """
                INSERT INTO entity_properties(
                    property_id, entity_id, field_key, value_json,
                    normalized_value, confidence, source_evidence_id, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(property_id) DO UPDATE SET
                    value_json = excluded.value_json,
                    normalized_value = excluded.normalized_value,
                    confidence = excluded.confidence,
                    is_active = 1
                """,
                (
                    property_id,
                    entity_id,
                    property_value.field_key,
                    value_json,
                    str(property_value.value).casefold(),
                    property_value.confidence,
                    evidence_id,
                ),
            )

    def _validate_entity_fields(self, entity: ExtractedEntity, type_key: str) -> None:
        for identifier in entity.identifiers:
            row = fetch_object_row(
                self._connection.execute(
                    """
                    SELECT is_identifier FROM field_definitions
                    WHERE type_key = ? AND field_key = ?
                    """,
                    (type_key, identifier.field_key),
                )
            )
            if row is None or not row or row[0] != 1:
                msg = f"Unknown identifier field {type_key}.{identifier.field_key}"
                raise ExtractionIntegrityError(msg)
        for property_value in entity.properties:
            row = fetch_object_row(
                self._connection.execute(
                    "SELECT 1 FROM field_definitions WHERE type_key = ? AND field_key = ?",
                    (type_key, property_value.field_key),
                )
            )
            if row is None:
                msg = f"Unknown property field {type_key}.{property_value.field_key}"
                raise ExtractionIntegrityError(msg)

    def _validate_relation(self, relation_key: str, source_id: str, target_id: str) -> None:
        row = fetch_object_row(
            self._connection.execute(
                """
                SELECT 1 FROM relation_types AS r
                JOIN entities AS source ON source.type_key = r.source_type_key
                JOIN entities AS target ON target.type_key = r.target_type_key
                WHERE r.relation_key = ? AND source.entity_id = ? AND target.entity_id = ?
                """,
                (relation_key, source_id, target_id),
            )
        )
        if row is None:
            msg = f"Relation {relation_key} has incompatible entity endpoints"
            raise ExtractionIntegrityError(msg)

    def _identity_scope(self, type_key: str) -> IdentityScope:
        row = fetch_object_row(
            self._connection.execute(
                "SELECT identity_scope FROM entity_types WHERE type_key = ?", (type_key,)
            )
        )
        if row is None or not row or not isinstance(row[0], str):
            msg = f"Unknown entity type: {type_key}"
            raise ExtractionIntegrityError(msg)
        return IdentityScope(row[0])

    def _current_revision(self) -> int:
        row = fetch_object_row(
            self._connection.execute("SELECT MAX(revision) FROM ontology_revisions")
        )
        if row is None or not row or not isinstance(row[0], int):
            msg = "Ontology has no current revision"
            raise ExtractionIntegrityError(msg)
        return row[0]

    def _retire_note_facts(self, note_id: str) -> None:
        evidence_query = "SELECT evidence_id FROM evidence_units WHERE note_id = ?"
        self._connection.execute(
            f"UPDATE entity_properties SET is_active = 0 "  # noqa: S608 - fixed SQL fragment.
            f"WHERE source_evidence_id IN ({evidence_query})",
            (note_id,),
        )
        self._connection.execute(
            f"UPDATE edges SET is_active = 0 "  # noqa: S608 - fixed SQL fragment.
            f"WHERE source_evidence_id IN ({evidence_query})",
            (note_id,),
        )

    def _refresh_entity_indexes(self, note_id: str, entity_ids: set[str]) -> None:
        self._connection.execute(
            "DELETE FROM entity_fts WHERE entity_id IN "
            "(SELECT entity_id FROM entities WHERE scope_note_id = ?)",
            (note_id,),
        )
        for entity_id in entity_ids:
            self._connection.execute("DELETE FROM entity_fts WHERE entity_id = ?", (entity_id,))
            self._connection.execute(
                """
                INSERT INTO entity_fts(entity_id, canonical_name, aliases, properties)
                SELECT e.entity_id, e.canonical_name,
                       COALESCE((SELECT group_concat(a.alias, ' ') FROM entity_aliases AS a
                                 WHERE a.entity_id = e.entity_id), ''),
                       COALESCE((SELECT group_concat(p.value_json, ' ')
                                 FROM entity_properties AS p
                                 WHERE p.entity_id = e.entity_id AND p.is_active = 1), '')
                FROM entities AS e WHERE e.entity_id = ? AND e.status = 'active'
                """,
                (entity_id,),
            )

    @staticmethod
    def _validate_evidence(extraction: ExtractionResult, available: set[str]) -> None:
        cited: set[str] = set()
        for entity in extraction.entities:
            cited.update(entity.evidence_ids)
            for property_value in entity.properties:
                cited.update(property_value.evidence_ids)
        for relation in extraction.relations:
            cited.update(relation.evidence_ids)
        missing = cited - available
        if missing:
            msg = f"Extraction cites unknown evidence: {', '.join(sorted(missing))}"
            raise ExtractionIntegrityError(msg)

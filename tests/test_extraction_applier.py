"""Tests for atomic extraction application."""

import sqlite3
from pathlib import Path

import pytest

from granola_kg.database import fetch_object_row, initialize_database
from granola_kg.extraction_applier import ExtractionApplier, ExtractionIntegrityError
from granola_kg.extraction_models import (
    ExtractedEntity,
    ExtractedIdentifier,
    ExtractedProperty,
    ExtractedRelation,
    ExtractionResult,
    OntologyProposal,
    ProposedEntityType,
    ProposedField,
    ProposedRelationType,
)
from granola_kg.granola_models import NoteDetail
from granola_kg.graph_models import FieldDataType, IdentityScope
from granola_kg.note_store import NoteStore
from granola_kg.query_store import QueryStore

NOTE_JSON = """{
  "id": "not_1", "object": "note", "title": "Launch planning",
  "owner": {"name": "Alex", "email": "alex@example.com"},
  "created_at": "2026-07-15T08:00:00Z", "updated_at": "2026-07-15T09:00:00Z",
  "web_url": "https://notes.example/not_1", "calendar_event": null,
  "attendees": [], "folder_membership": [],
  "summary_text": "The team approved a Friday launch.", "summary_markdown": null,
  "transcript": null
}"""
EXPECTED_DISCOVERY_REVISION = 2


def prepare_note(
    tmp_path: Path,
) -> tuple[sqlite3.Connection, NoteDetail, str]:
    """Create a database and discover its deterministic summary evidence ID."""
    connection = initialize_database(tmp_path / "graph.db")
    note = NoteDetail.model_validate_json(NOTE_JSON)
    evidence_id = NoteStore(connection).materialize(note).evidence_ids[0]
    connection.commit()
    return connection, note, evidence_id


def extraction(evidence_id: str) -> ExtractionResult:
    """Build a decision-as-primary-entity extraction."""
    return ExtractionResult(
        ontology=OntologyProposal(
            entity_types=[
                ProposedEntityType(
                    key="decision",
                    display_name="Decision",
                    description="A choice made during one meeting.",
                    identity_scope=IdentityScope.NOTE,
                    fields=[
                        ProposedField(
                            key="status",
                            display_name="Status",
                            description="Current decision status.",
                            data_type=FieldDataType.STRING,
                        )
                    ],
                )
            ],
            relation_types=[
                ProposedRelationType(
                    key="made_in",
                    display_name="Made in",
                    description="A decision was made in a meeting.",
                    source_type_key="decision",
                    target_type_key="meeting",
                )
            ],
        ),
        entities=[
            ExtractedEntity(
                local_id="meeting_1",
                type_key="meeting",
                name="Launch planning",
                identifiers=[ExtractedIdentifier(field_key="note_id", value="not_1")],
                evidence_ids=[evidence_id],
            ),
            ExtractedEntity(
                local_id="decision_1",
                type_key="decision",
                name="Launch on Friday",
                evidence_ids=[evidence_id],
                properties=[
                    ExtractedProperty(
                        field_key="status",
                        value="approved",
                        evidence_ids=[evidence_id],
                        confidence=0.95,
                    )
                ],
            ),
        ],
        relations=[
            ExtractedRelation(
                source_local_id="decision_1",
                relation_key="made_in",
                target_local_id="meeting_1",
                evidence_ids=[evidence_id],
                confidence=0.9,
            )
        ],
    )


def test_atomically_applies_ontology_entities_and_facts(tmp_path: Path) -> None:
    """A valid extraction should become a searchable, provenance-backed graph."""
    connection, note, evidence_id = prepare_note(tmp_path)
    applier = ExtractionApplier(connection)

    result = applier.apply(
        note, extraction(evidence_id), prompt_version="v1", model_name="test-model"
    )

    assert result.ontology_revision == EXPECTED_DISCOVERY_REVISION
    search_results = QueryStore(connection).search("approved", type_keys=("decision",))
    decisions = [result for result in search_results if result.type_key == "decision"]
    assert len(decisions) == 1
    property_count = fetch_object_row(
        connection.execute("SELECT COUNT(*) FROM entity_properties WHERE is_active = 1")
    )
    edge_count = fetch_object_row(
        connection.execute("SELECT COUNT(*) FROM edges WHERE is_active = 1")
    )
    run = fetch_object_row(
        connection.execute(
            "SELECT state, model_name FROM extraction_runs WHERE run_id = ?", (result.run_id,)
        )
    )
    assert property_count == (1,)
    assert edge_count == (1,)
    assert run == ("complete", "test-model")
    connection.close()


def test_reapplication_reuses_entities_and_facts(tmp_path: Path) -> None:
    """Reprocessing unchanged evidence should be idempotent at graph level."""
    connection, note, evidence_id = prepare_note(tmp_path)
    applier = ExtractionApplier(connection)
    extracted = extraction(evidence_id)

    first = applier.apply(note, extracted, prompt_version="v1", model_name="test-model")
    second = applier.apply(note, extracted, prompt_version="v1", model_name="test-model")

    assert second.entity_ids == first.entity_ids
    assert fetch_object_row(connection.execute("SELECT COUNT(*) FROM entities")) == (2,)
    assert fetch_object_row(connection.execute("SELECT COUNT(*) FROM edges")) == (1,)
    connection.close()


def test_meeting_identity_comes_from_source_note(tmp_path: Path) -> None:
    """Model-supplied meeting identifiers cannot override Granola metadata."""
    connection, note, evidence_id = prepare_note(tmp_path)
    extracted = extraction(evidence_id)
    extracted.entities[0].identifiers = [
        ExtractedIdentifier(field_key="note_id", value="model_hallucination")
    ]

    ExtractionApplier(connection).apply(
        note, extracted, prompt_version="v1", model_name="test-model"
    )

    identifiers = connection.execute(
        "SELECT raw_value FROM entity_identifiers WHERE field_key = 'note_id'"
    ).fetchall()
    assert identifiers == [("not_1",)]
    connection.close()


def test_source_meeting_exists_when_model_omits_it(tmp_path: Path) -> None:
    """Meeting creation should not depend on model output."""
    connection, note, evidence_id = prepare_note(tmp_path)
    extracted = extraction(evidence_id)
    extracted.entities = [extracted.entities[1]]
    extracted.relations = []

    ExtractionApplier(connection).apply(
        note, extracted, prompt_version="v1", model_name="test-model"
    )

    meetings = connection.execute(
        "SELECT canonical_name FROM entities WHERE type_key = 'meeting'"
    ).fetchall()
    assert meetings == [("Launch planning",)]
    connection.close()


def test_invalid_evidence_rolls_back_every_graph_change(tmp_path: Path) -> None:
    """Unknown citations should prevent partial ontology or entity writes."""
    connection = initialize_database(tmp_path / "graph.db")
    note = NoteDetail.model_validate_json(NOTE_JSON)
    invalid = extraction("ev_missing")

    with pytest.raises(ExtractionIntegrityError, match="unknown evidence"):
        ExtractionApplier(connection).apply(
            note, invalid, prompt_version="v1", model_name="test-model"
        )

    assert fetch_object_row(connection.execute("SELECT COUNT(*) FROM source_notes")) == (0,)
    assert fetch_object_row(connection.execute("SELECT COUNT(*) FROM entity_types")) == (0,)
    assert fetch_object_row(connection.execute("SELECT COUNT(*) FROM entities")) == (0,)
    connection.close()

"""Tests for bounded local extraction context."""

from pathlib import Path

from granola_kg.database import initialize_database
from granola_kg.graph_models import EntityTypeDefinition, IdentityScope
from granola_kg.graph_store import GraphStore
from granola_kg.prompt_builder import PromptBuilder

EVIDENCE_BUDGET = 30
ONTOLOGY_LIMIT = 3


def test_summary_precedes_relevant_transcript_within_budget(tmp_path: Path) -> None:
    """Local selection should spend its bounded context on useful grounding."""
    connection = initialize_database(tmp_path / "graph.db")
    connection.execute(
        """
        INSERT INTO source_notes(note_id, owner_email, created_at, updated_at)
        VALUES ('not_1', 'owner@example.com', '2026-01-01', '2026-01-01')
        """
    )
    rows = (
        ("ev_summary", "summary", 0, "Zenning launch"),
        ("ev_weather", "transcript", 0, "Weather discussion is unrelated"),
        ("ev_zenning", "transcript", 1, "Zenning customer"),
    )
    connection.executemany(
        """
        INSERT INTO evidence_units(
            evidence_id, note_id, unit_kind, unit_index, content, content_hash
        ) VALUES (?, 'not_1', ?, ?, ?, ?)
        """,
        (
            (evidence_id, kind, index, content, evidence_id)
            for evidence_id, kind, index, content in rows
        ),
    )

    encoded = PromptBuilder(connection, evidence_char_budget=EVIDENCE_BUDGET).evidence_json(
        "not_1", "Zenning"
    )
    assert encoded.index("ev_summary") < encoded.index("ev_zenning")
    assert "Weather discussion" not in encoded
    assert '"content":"Zenning customer"' in encoded
    connection.close()


def test_ontology_keeps_seeds_and_relevant_types_under_limit(tmp_path: Path) -> None:
    """Growing ontology size must not cause growing prompt context."""
    connection = initialize_database(tmp_path / "graph.db")
    graph = GraphStore(connection)
    graph.ensure_seed_ontology()
    revision = graph.create_revision("test prompt relevance")
    for key in ("company", "decision", "project", "topic"):
        graph.upsert_entity_type(
            EntityTypeDefinition(key, key.title(), f"A {key} entity.", IdentityScope.GLOBAL),
            revision,
        )

    encoded = PromptBuilder(connection, ontology_row_limit=ONTOLOGY_LIMIT).ontology_json(
        "Zenning company strategy"
    )
    for type_key in ("meeting", "person", "company"):
        assert f'"type_key":"{type_key}"' in encoded
    assert encoded.count('"type_key"') == ONTOLOGY_LIMIT
    connection.close()

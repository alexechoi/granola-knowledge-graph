"""Tests for local graph search and traversal."""

import sqlite3
from pathlib import Path

from granola_kg.database import initialize_database
from granola_kg.graph_models import (
    EntityCandidate,
    EntityTypeDefinition,
    IdentifierCandidate,
    IdentityScope,
)
from granola_kg.graph_store import GraphStore
from granola_kg.query_models import SearchResultKind
from granola_kg.query_store import QueryStore, compile_fts_query


def make_graph(tmp_path: Path) -> tuple[GraphStore, QueryStore, sqlite3.Connection]:
    """Create a small searchable person-to-meeting graph."""
    connection = initialize_database(tmp_path / "graph.db")
    graph = GraphStore(connection)
    graph.ensure_seed_ontology()
    connection.execute(
        """
        INSERT INTO source_notes(
            note_id, title, owner_email, web_url, created_at, updated_at
        ) VALUES (
            'not_budget', 'Quarterly budget', 'owner@example.com',
            'https://notes.example/budget', '2026-01-01T00:00:00Z', '2026-01-01T01:00:00Z'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO evidence_units(
            evidence_id, note_id, unit_kind, unit_index, content, content_hash,
            speaker_name, started_at, ended_at
        ) VALUES (
            'ev_budget', 'not_budget', 'summary', 0,
            'Alex approved the quarterly yoghurt budget.', 'hash-budget',
            'Alex Choi', '2026-01-01T00:10:00Z', '2026-01-01T00:11:00Z'
        )
        """
    )
    person = graph.resolve_entity(
        EntityCandidate(
            "person",
            "Alex Choi",
            "ev_budget",
            identifiers=(IdentifierCandidate("email", "alex@example.com"),),
        )
    )
    meeting = graph.resolve_entity(
        EntityCandidate(
            "meeting",
            "Quarterly budget",
            "ev_budget",
            identifiers=(IdentifierCandidate("note_id", "not_budget"),),
        )
    )
    connection.execute(
        """
        INSERT INTO entity_properties(
            property_id, entity_id, field_key, value_json, confidence, source_evidence_id
        ) VALUES ('prop_role', ?, 'role', '"Approver"', 0.95, 'ev_budget')
        """,
        (person.entity_id,),
    )
    connection.execute(
        """
        INSERT INTO edges(
            edge_id, source_entity_id, relation_key, target_entity_id,
            confidence, source_evidence_id
        ) VALUES ('edge_attended', ?, 'attended', ?, 1.0, 'ev_budget')
        """,
        (person.entity_id, meeting.entity_id),
    )
    queries = QueryStore(connection)
    queries.index_evidence("ev_budget", "", "Alex approved the quarterly yoghurt budget")
    queries.index_note("not_budget", "Quarterly budget", "Alex approved the budget")
    queries.index_entity(person.entity_id, "Alex Choi", "Alex", "Approver")
    queries.index_entity(meeting.entity_id, "Quarterly budget")
    return graph, queries, connection


def test_compiles_user_text_as_literal_tokens() -> None:
    """FTS control characters should not become query syntax."""
    assert compile_fts_query('budget: "Q3"') == '"budget:" AND """Q3"""'


def test_searches_evidence_and_entities(tmp_path: Path) -> None:
    """Unified search should rank both graph object families."""
    graph, queries, _connection = make_graph(tmp_path)
    person = graph.resolve_entity(
        EntityCandidate(
            "person",
            "Alex Choi",
            "ev_budget",
            identifiers=(IdentifierCandidate("email", "alex@example.com"),),
        )
    )
    results = queries.search("Alex")
    assert {result.kind for result in results} == {
        SearchResultKind.ENTITY,
        SearchResultKind.NOTE,
        SearchResultKind.EVIDENCE,
    }
    detail = queries.get_entity(person.entity_id)
    assert detail is not None
    assert detail.identifiers == (("email", "alex@example.com"),)
    assert detail.properties[0].citation.note_id == "not_budget"


def test_title_match_returns_note_without_false_evidence(tmp_path: Path) -> None:
    """A meeting title must not be repeated as a hit for every evidence unit."""
    _graph, queries, connection = make_graph(tmp_path)
    connection.execute("UPDATE source_notes SET title = 'Zenning' WHERE note_id = 'not_budget'")
    queries.index_note("not_budget", "Zenning", "Alex approved the budget")

    results = queries.search("Zenning")

    assert [result.kind for result in results] == [SearchResultKind.NOTE]
    assert results[0].note_id == "not_budget"


def test_same_label_entities_have_distinct_typed_groups(tmp_path: Path) -> None:
    """Equal labels in legitimate graph roles must remain separate facets."""
    graph, queries, _connection = make_graph(tmp_path)
    revision = graph.create_revision("add Zenning facets")
    for type_key in ("company", "product"):
        graph.upsert_entity_type(
            EntityTypeDefinition(
                type_key,
                type_key.title(),
                f"A {type_key}.",
                IdentityScope.GLOBAL,
            ),
            revision,
        )
        entity = graph.resolve_entity(EntityCandidate(type_key, "Zenning", "ev_budget"))
        queries.index_entity(entity.entity_id, "Zenning")

    results = queries.search("Zenning")
    facets = {result.type_key: result.group_key for result in results}

    assert facets == {
        "company": "entity:company:zenning",
        "product": "entity:product:zenning",
    }


def test_traverses_relationships_in_both_directions(tmp_path: Path) -> None:
    """Traversal should expose direction and stop at configured depth."""
    graph, queries, _connection = make_graph(tmp_path)
    person = graph.resolve_entity(
        EntityCandidate(
            "person",
            "Alex Choi",
            "ev_budget",
            identifiers=(IdentifierCandidate("email", "alex@example.com"),),
        )
    )
    neighbors = queries.traverse(person.entity_id, max_depth=99)
    assert len(neighbors) == 1
    assert neighbors[0].canonical_name == "Quarterly budget"
    assert neighbors[0].direction == "outgoing"
    assert neighbors[0].depth == 1

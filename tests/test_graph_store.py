"""Tests for ontology persistence and canonical entity resolution."""

import sqlite3
from pathlib import Path

import pytest

from granola_kg.database import initialize_database
from granola_kg.graph_models import (
    EntityCandidate,
    EntityTypeDefinition,
    IdentifierCandidate,
    IdentityScope,
)
from granola_kg.graph_store import GraphStore, IdentityConflictError


def add_evidence(
    connection: sqlite3.Connection, path: Path, note_id: str, evidence_id: str
) -> None:
    """Add the minimum source evidence required by resolution tests."""
    connection.execute(
        """
        INSERT INTO source_notes(note_id, owner_email, created_at, updated_at)
        VALUES (?, 'owner@example.com', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
        """,
        (note_id,),
    )
    connection.execute(
        """
        INSERT INTO evidence_units(
            evidence_id, note_id, unit_kind, unit_index, content, content_hash
        ) VALUES (?, ?, 'summary', 0, ?, ?)
        """,
        (evidence_id, note_id, path.name, evidence_id),
    )


def make_store(tmp_path: Path) -> tuple[GraphStore, sqlite3.Connection]:
    """Create a seeded graph store."""
    connection = initialize_database(tmp_path / "graph.db")
    store = GraphStore(connection)
    store.ensure_seed_ontology()
    return store, connection


def test_resolves_global_person_by_email(tmp_path: Path) -> None:
    """Different names with one email should resolve to one person."""
    store, connection = make_store(tmp_path)
    add_evidence(connection, tmp_path, "not_first", "ev_first")
    add_evidence(connection, tmp_path, "not_second", "ev_second")
    first = store.resolve_entity(
        EntityCandidate(
            type_key="person",
            name="Alex Choi",
            evidence_id="ev_first",
            identifiers=(IdentifierCandidate("email", "Alex@Example.com"),),
        )
    )
    second = store.resolve_entity(
        EntityCandidate(
            type_key="person",
            name="Alex",
            evidence_id="ev_second",
            identifiers=(IdentifierCandidate("email", "alex@example.com"),),
        )
    )
    assert first.created is True
    assert second.created is False
    assert second.match_source == "exact_identifier"
    assert first.entity_id == second.entity_id


def test_keeps_note_scoped_decisions_separate(tmp_path: Path) -> None:
    """An occurrence-like entity should only deduplicate inside its note."""
    store, connection = make_store(tmp_path)
    add_evidence(connection, tmp_path, "not_first", "ev_first")
    add_evidence(connection, tmp_path, "not_second", "ev_second")
    revision = store.create_revision("discover decision")
    store.upsert_entity_type(
        EntityTypeDefinition(
            key="decision",
            display_name="Decision",
            description="A decision made in one meeting.",
            identity_scope=IdentityScope.NOTE,
        ),
        revision,
    )
    first = store.resolve_entity(
        EntityCandidate("decision", "Launch in September", "ev_first", "not_first")
    )
    repeated = store.resolve_entity(
        EntityCandidate("decision", "Launch in September", "ev_first", "not_first")
    )
    second = store.resolve_entity(
        EntityCandidate("decision", "Launch in September", "ev_second", "not_second")
    )
    assert repeated.entity_id == first.entity_id
    assert second.entity_id != first.entity_id


def test_rejects_conflicting_identifiers(tmp_path: Path) -> None:
    """One candidate must not silently combine two canonical people."""
    store, connection = make_store(tmp_path)
    add_evidence(connection, tmp_path, "not_people", "ev_people")
    for name, email in (("Alex", "alex@example.com"), ("Sam", "sam@example.com")):
        store.resolve_entity(
            EntityCandidate(
                "person",
                name,
                "ev_people",
                identifiers=(IdentifierCandidate("email", email),),
            )
        )
    with pytest.raises(IdentityConflictError):
        store.resolve_entity(
            EntityCandidate(
                "person",
                "Conflicted Person",
                "ev_people",
                identifiers=(
                    IdentifierCandidate("email", "alex@example.com"),
                    IdentifierCandidate("email", "sam@example.com"),
                ),
            )
        )

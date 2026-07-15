"""Tests for the local graph explorer HTTP surface."""

from http import HTTPStatus
from pathlib import Path
from threading import Thread

import httpx
import pytest

from granola_kg.database import initialize_database
from granola_kg.explorer_server import create_explorer_server
from granola_kg.graph_store import GraphStore

INVALID_PORT = 70000


def test_rejects_an_invalid_port(tmp_path: Path) -> None:
    """Explorer setup should reject ports outside the TCP range."""
    with pytest.raises(ValueError, match="port"):
        create_explorer_server(tmp_path / "graph.db", INVALID_PORT)


def test_serves_interactive_graph_and_entity_evidence(tmp_path: Path) -> None:
    """The local API and UI should expose graph structure with cited detail."""
    database = tmp_path / "graph.db"
    _seed_graph(database)
    server = create_explorer_server(database, 0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        html = httpx.get(base_url, timeout=2)
        overview = httpx.get(f"{base_url}/api/graph", timeout=2)
        entity = httpx.get(f"{base_url}/api/entity?id=person_test", timeout=2)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert html.status_code == HTTPStatus.OK
    assert "Granola Graph Explorer" in html.text
    assert overview.json()["nodes"] == [
        {"id": "meeting_test", "name": "Test Meeting", "type": "meeting"},
        {"id": "person_test", "name": "Test Person", "type": "person"},
    ]
    assert overview.json()["edges"][0]["type"] == "attended"
    assert entity.json()["properties"][0]["evidence"]["excerpt"] == "Synthetic evidence"


def test_reports_a_missing_entity(tmp_path: Path) -> None:
    """Unknown entity requests should fail clearly."""
    database = tmp_path / "graph.db"
    _seed_graph(database)
    server = create_explorer_server(database, 0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = httpx.get(
            f"http://127.0.0.1:{server.server_port}/api/entity?id=missing", timeout=2
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert response.status_code == HTTPStatus.NOT_FOUND


def _seed_graph(database: Path) -> None:
    connection = initialize_database(database)
    GraphStore(connection).ensure_seed_ontology()
    connection.execute(
        """
        INSERT INTO source_notes(note_id, title, owner_email, created_at, updated_at)
        VALUES ('not_test', 'Test Note', 'owner@example.com', '2026-01-01', '2026-01-01')
        """
    )
    connection.execute(
        """
        INSERT INTO evidence_units(
            evidence_id, note_id, unit_kind, unit_index, content, content_hash
        ) VALUES ('ev_test', 'not_test', 'summary', 0, 'Synthetic evidence', 'hash')
        """
    )
    connection.execute(
        """
        INSERT INTO entities(
            entity_id, type_key, canonical_name, normalized_name, identity_scope
        ) VALUES
          ('meeting_test', 'meeting', 'Test Meeting', 'test meeting', 'global'),
          ('person_test', 'person', 'Test Person', 'test person', 'global')
        """
    )
    connection.execute(
        """
        INSERT INTO entity_properties(
            property_id, entity_id, field_key, value_json, source_evidence_id
        ) VALUES ('prop_test', 'person_test', 'role', '"Engineer"', 'ev_test')
        """
    )
    connection.execute(
        """
        INSERT INTO edges(
            edge_id, source_entity_id, relation_key, target_entity_id, source_evidence_id
        ) VALUES ('edge_test', 'person_test', 'attended', 'meeting_test', 'ev_test')
        """
    )
    connection.commit()
    connection.close()

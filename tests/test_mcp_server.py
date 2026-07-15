"""Tests for structured local MCP query tools."""

import asyncio
from pathlib import Path

from granola_kg.database import initialize_database
from granola_kg.graph_store import GraphStore
from granola_kg.mcp_server import LocalGraphTools, create_server

EXPECTED_TOOL_COUNT = 6


def test_local_tools_report_seeded_ontology_and_status(tmp_path: Path) -> None:
    """MCP operations should open the configured local database per call."""
    database = tmp_path / "graph.db"
    connection = initialize_database(database)
    GraphStore(connection).ensure_seed_ontology()
    connection.commit()
    connection.close()
    tools = LocalGraphTools(database)

    ontology = tools.list_entity_types()
    status = tools.ingestion_status()
    search = tools.search_knowledge("nothing")

    assert [item.type_key for item in ontology.entity_types] == ["meeting", "person"]
    assert status.watermark is None
    assert status.queue.pending == 0
    assert search.results == []


def test_server_registers_structured_read_only_tools(tmp_path: Path) -> None:
    """The stdio server should advertise each stable structured operation."""
    server = create_server(tmp_path / "graph.db")

    registered = asyncio.run(server.list_tools())
    names = {tool.name for tool in registered}

    assert len(registered) == EXPECTED_TOOL_COUNT
    assert names == {
        "search_knowledge",
        "get_entity",
        "get_evidence",
        "traverse_graph",
        "list_entity_types",
        "ingestion_status",
    }
    assert all(tool.outputSchema is not None for tool in registered)

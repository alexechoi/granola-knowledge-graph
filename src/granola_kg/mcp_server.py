"""Stdio MCP server for querying the local knowledge graph."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from granola_kg.config import load_settings
from granola_kg.database import fetch_object_rows, initialize_database
from granola_kg.mcp_models import (
    CitationResponse,
    EntityResponse,
    EntityTypeResponse,
    IngestionStatusResponse,
    NeighborResponse,
    OntologyResponse,
    PropertyResponse,
    QueueStatusResponse,
    SearchItem,
    SearchResponse,
    TraverseResponse,
)
from granola_kg.query_store import QueryStore
from granola_kg.sync_store import SyncStore

if TYPE_CHECKING:
    from pathlib import Path

ONTOLOGY_ROW_SIZE = 4


class LocalGraphTools:
    """Structured read-only operations over one local SQLite graph."""

    def __init__(self, database_path: Path) -> None:
        """Bind tools to a local database path without holding global connections."""
        self._database_path = database_path

    def search_knowledge(
        self, query: str, type_keys: list[str] | None = None, limit: int = 20
    ) -> SearchResponse:
        """Search meeting evidence and canonical entities using literal text tokens."""
        connection = initialize_database(self._database_path)
        try:
            results = QueryStore(connection).search(
                query, type_keys=tuple(type_keys or []), limit=limit
            )
            return SearchResponse(
                results=[
                    SearchItem(
                        kind=item.kind.value,
                        object_id=item.object_id,
                        title=item.title,
                        snippet=item.snippet,
                        score=item.score,
                        type_key=item.type_key,
                        note_id=item.note_id,
                    )
                    for item in results
                ]
            )
        finally:
            connection.close()

    def get_entity(self, entity_id: str) -> EntityResponse:
        """Get a canonical entity, identifiers, properties, and Granola citations."""
        connection = initialize_database(self._database_path)
        try:
            detail = QueryStore(connection).get_entity(entity_id)
            if detail is None:
                msg = f"Unknown entity: {entity_id}"
                raise ValueError(msg)
            return EntityResponse(
                entity_id=detail.entity_id,
                type_key=detail.type_key,
                canonical_name=detail.canonical_name,
                aliases=list(detail.aliases),
                identifiers=[list(item) for item in detail.identifiers],
                properties=[
                    PropertyResponse(
                        field_key=item.field_key,
                        value_json=item.value_json,
                        confidence=item.confidence,
                        citation=CitationResponse(
                            evidence_id=item.citation.evidence_id,
                            note_id=item.citation.note_id,
                            note_title=item.citation.note_title,
                            web_url=item.citation.web_url,
                            excerpt=item.citation.excerpt,
                            speaker_name=item.citation.speaker_name,
                            started_at=item.citation.started_at,
                            ended_at=item.citation.ended_at,
                        ),
                    )
                    for item in detail.properties
                ],
            )
        finally:
            connection.close()

    def traverse_graph(
        self, entity_id: str, max_depth: int = 2, limit: int = 100
    ) -> TraverseResponse:
        """Traverse active evidence-backed relations up to three hops."""
        connection = initialize_database(self._database_path)
        try:
            neighbors = QueryStore(connection).traverse(entity_id, max_depth=max_depth, limit=limit)
            return TraverseResponse(
                neighbors=[
                    NeighborResponse(
                        entity_id=item.entity_id,
                        canonical_name=item.canonical_name,
                        type_key=item.type_key,
                        relation_key=item.relation_key,
                        direction=item.direction,
                        depth=item.depth,
                        evidence_id=item.evidence_id,
                    )
                    for item in neighbors
                ]
            )
        finally:
            connection.close()

    def list_entity_types(self) -> OntologyResponse:
        """List the seeded and automatically generated primary entity types."""
        connection = initialize_database(self._database_path)
        try:
            rows = fetch_object_rows(
                connection.execute(
                    """
                    SELECT type_key, display_name, description, identity_scope
                    FROM entity_types ORDER BY type_key
                    """
                )
            )
            return OntologyResponse(entity_types=[_entity_type(row) for row in rows])
        finally:
            connection.close()

    def ingestion_status(self) -> IngestionStatusResponse:
        """Show the safe Granola watermark and durable processing queue counts."""
        connection = initialize_database(self._database_path)
        try:
            state = SyncStore(connection)
            counts = state.counts()
            watermark = state.watermark()
            return IngestionStatusResponse(
                watermark=watermark.isoformat() if watermark is not None else None,
                queue=QueueStatusResponse(
                    pending=counts.pending,
                    processing=counts.processing,
                    complete=counts.complete,
                    failed=counts.failed,
                ),
            )
        finally:
            connection.close()


def create_server(database_path: Path | None = None) -> FastMCP[None]:
    """Create a read-only stdio MCP server for one local database."""
    path = database_path or load_settings().database_path
    service = LocalGraphTools(path)
    server: FastMCP[None] = FastMCP(
        "granola-knowledge-graph",
        instructions=(
            "Query the local Granola knowledge graph. Search first, then inspect entities "
            "or traverse evidence-backed relationships. Use the CLI to ingest new meetings."
        ),
    )
    server.add_tool(service.search_knowledge, structured_output=True)
    server.add_tool(service.get_entity, structured_output=True)
    server.add_tool(service.traverse_graph, structured_output=True)
    server.add_tool(service.list_entity_types, structured_output=True)
    server.add_tool(service.ingestion_status, structured_output=True)
    return server


def main() -> None:
    """Run the MCP server over standard input and output."""
    create_server().run(transport="stdio")


def _entity_type(row: tuple[object, ...]) -> EntityTypeResponse:
    if len(row) != ONTOLOGY_ROW_SIZE:
        msg = "Database returned invalid ontology data"
        raise RuntimeError(msg)
    return EntityTypeResponse(
        type_key=_text(row[0]),
        display_name=_text(row[1]),
        description=_text(row[2]),
        identity_scope=_text(row[3]),
    )


def _text(value: object) -> str:
    if not isinstance(value, str):
        msg = "Database returned non-text ontology data"
        raise TypeError(msg)
    return value

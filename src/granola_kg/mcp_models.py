"""Structured MCP tool responses for the local graph."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class McpModel(BaseModel):
    """Strict base for MCP structured content."""

    model_config = ConfigDict(extra="forbid", strict=True)


class SearchItem(McpModel):
    """One evidence or entity search match."""

    kind: str
    object_id: str
    title: str
    snippet: str
    score: float
    group_key: str
    type_key: str | None
    note_id: str | None


class SearchResponse(McpModel):
    """Ranked local graph search results."""

    results: list[SearchItem]


class CitationResponse(McpModel):
    """Granola evidence supporting one graph assertion."""

    evidence_id: str
    note_id: str
    note_title: str | None
    web_url: str | None
    excerpt: str
    speaker_name: str | None
    started_at: str | None
    ended_at: str | None


class PropertyResponse(McpModel):
    """One evidence-backed entity property."""

    field_key: str
    value_json: str
    confidence: float | None
    citation: CitationResponse


class EntityResponse(McpModel):
    """Canonical entity detail with provenance."""

    entity_id: str
    type_key: str
    canonical_name: str
    aliases: list[str]
    identifiers: list[list[str]]
    properties: list[PropertyResponse]


class NeighborResponse(McpModel):
    """One graph traversal result."""

    entity_id: str
    canonical_name: str
    type_key: str
    relation_key: str
    direction: str
    depth: int
    evidence_id: str


class TraverseResponse(McpModel):
    """Bounded relationship traversal results."""

    neighbors: list[NeighborResponse]


class EntityTypeResponse(McpModel):
    """One generated or seeded ontology entity type."""

    type_key: str
    display_name: str
    description: str
    identity_scope: str


class OntologyResponse(McpModel):
    """Available primary entity types."""

    entity_types: list[EntityTypeResponse]


class QueueStatusResponse(McpModel):
    """Durable ingestion queue counts."""

    pending: int
    processing: int
    complete: int
    failed: int


class IngestionStatusResponse(McpModel):
    """Local ingestion watermark and queue state."""

    watermark: str | None
    queue: QueueStatusResponse

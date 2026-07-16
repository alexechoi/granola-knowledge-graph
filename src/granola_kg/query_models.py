"""Typed graph query results."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SearchResultKind(StrEnum):
    """Searchable graph object families."""

    ENTITY = "entity"
    NOTE = "note"
    EVIDENCE = "evidence"


@dataclass(frozen=True)
class SearchResult:
    """One ranked full-text result."""

    kind: SearchResultKind
    object_id: str
    title: str
    snippet: str
    score: float
    type_key: str | None = None
    note_id: str | None = None


@dataclass(frozen=True)
class EvidenceCitation:
    """Granola provenance for a graph assertion."""

    evidence_id: str
    note_id: str
    note_title: str | None
    web_url: str | None
    excerpt: str
    speaker_name: str | None
    started_at: str | None
    ended_at: str | None


@dataclass(frozen=True)
class EntityProperty:
    """One active evidence-backed property value."""

    field_key: str
    value_json: str
    confidence: float | None
    citation: EvidenceCitation


@dataclass(frozen=True)
class EntityDetail:
    """Canonical entity with aliases, identifiers, and properties."""

    entity_id: str
    type_key: str
    canonical_name: str
    aliases: tuple[str, ...]
    identifiers: tuple[tuple[str, str], ...]
    properties: tuple[EntityProperty, ...]


@dataclass(frozen=True)
class GraphNeighbor:
    """One reachable entity relationship."""

    entity_id: str
    canonical_name: str
    type_key: str
    relation_key: str
    direction: str
    depth: int
    evidence_id: str

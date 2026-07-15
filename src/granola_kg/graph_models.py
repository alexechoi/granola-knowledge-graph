"""Typed inputs and outputs for the local knowledge graph."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class IdentityScope(StrEnum):
    """Entity identity boundary."""

    GLOBAL = "global"
    NOTE = "note"


class FieldDataType(StrEnum):
    """Supported generated ontology field types."""

    STRING = "string"
    TEXT = "text"
    URL = "url"
    EMAIL = "email"
    PHONE = "phone"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"


@dataclass(frozen=True)
class FieldDefinition:
    """One field in an entity type."""

    key: str
    display_name: str
    description: str
    data_type: FieldDataType
    is_identifier: bool = False


@dataclass(frozen=True)
class EntityTypeDefinition:
    """A generated or seeded entity type."""

    key: str
    display_name: str
    description: str
    identity_scope: IdentityScope
    fields: tuple[FieldDefinition, ...] = ()


@dataclass(frozen=True)
class RelationTypeDefinition:
    """A generated relationship type."""

    key: str
    display_name: str
    description: str
    source_type_key: str
    target_type_key: str
    is_directed: bool = True


@dataclass(frozen=True)
class IdentifierCandidate:
    """A candidate unique identifier backed by evidence."""

    field_key: str
    value: str


@dataclass(frozen=True)
class EntityCandidate:
    """An extracted entity awaiting canonical resolution."""

    type_key: str
    name: str
    evidence_id: str
    scope_note_id: str | None = None
    identifiers: tuple[IdentifierCandidate, ...] = ()


@dataclass(frozen=True)
class EntityResolution:
    """Result of canonical entity resolution."""

    entity_id: str
    type_key: str
    canonical_name: str
    created: bool
    match_source: str


@dataclass(frozen=True)
class EntityRecord:
    """Stored canonical entity fields."""

    entity_id: str
    type_key: str
    canonical_name: str
    identity_scope: IdentityScope
    scope_note_id: str | None
    status: str

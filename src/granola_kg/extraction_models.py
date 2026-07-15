"""Strict contracts for automatic ontology and graph extraction."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from granola_kg.graph_models import (  # noqa: TC001 - Pydantic resolves enums at runtime.
    FieldDataType,
    IdentityScope,
)

KEY_PATTERN = r"^[a-z][a-z0-9_]*$"


class ExtractionModel(BaseModel):
    """Base extraction contract with no implicit coercion or extra fields."""

    model_config = ConfigDict(extra="forbid", strict=True)


class ProposedField(ExtractionModel):
    """Field proposed for a new or existing entity type."""

    key: str = Field(pattern=KEY_PATTERN)
    display_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    data_type: FieldDataType
    is_identifier: bool = False


class ProposedEntityType(ExtractionModel):
    """Additive entity type proposal derived from meeting evidence."""

    key: str = Field(pattern=KEY_PATTERN)
    display_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    identity_scope: IdentityScope
    fields: list[ProposedField] = Field(default_factory=list)


class ProposedRelationType(ExtractionModel):
    """Additive relation type proposal derived from meeting evidence."""

    key: str = Field(pattern=KEY_PATTERN)
    display_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    source_type_key: str = Field(pattern=KEY_PATTERN)
    target_type_key: str = Field(pattern=KEY_PATTERN)
    is_directed: bool = True


class OntologyProposal(ExtractionModel):
    """Only the ontology additions needed to represent a meeting."""

    entity_types: list[ProposedEntityType] = Field(default_factory=list)
    relation_types: list[ProposedRelationType] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_keys(self) -> OntologyProposal:
        """Reject duplicate type and relation proposals."""
        entity_keys = [definition.key for definition in self.entity_types]
        relation_keys = [definition.key for definition in self.relation_types]
        if len(entity_keys) != len(set(entity_keys)):
            msg = "ontology proposal contains duplicate entity type keys"
            raise ValueError(msg)
        if len(relation_keys) != len(set(relation_keys)):
            msg = "ontology proposal contains duplicate relation type keys"
            raise ValueError(msg)
        return self


class ExtractedIdentifier(ExtractionModel):
    """Stable identifier asserted for an extracted entity."""

    field_key: str = Field(pattern=KEY_PATTERN)
    value: str = Field(min_length=1)


class ExtractedProperty(ExtractionModel):
    """Evidence-backed scalar property on an extracted entity."""

    field_key: str = Field(pattern=KEY_PATTERN)
    value: str | float | bool
    evidence_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class ExtractedEntity(ExtractionModel):
    """Primary entity extracted from meeting evidence."""

    local_id: str = Field(pattern=KEY_PATTERN)
    type_key: str = Field(pattern=KEY_PATTERN)
    name: str = Field(min_length=1)
    identifiers: list[ExtractedIdentifier] = Field(default_factory=list)
    evidence_ids: list[str] = Field(min_length=1)
    properties: list[ExtractedProperty] = Field(default_factory=list)


class ExtractedRelation(ExtractionModel):
    """Evidence-backed edge between two locally referenced entities."""

    source_local_id: str = Field(pattern=KEY_PATTERN)
    relation_key: str = Field(pattern=KEY_PATTERN)
    target_local_id: str = Field(pattern=KEY_PATTERN)
    evidence_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class ExtractionResult(ExtractionModel):
    """Complete structured extraction response for one meeting."""

    ontology: OntologyProposal = Field(default_factory=OntologyProposal)
    entities: list[ExtractedEntity]
    relations: list[ExtractedRelation] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_local_references(self) -> ExtractionResult:
        """Require unique entity references and valid relationship endpoints."""
        local_ids = [entity.local_id for entity in self.entities]
        if len(local_ids) != len(set(local_ids)):
            msg = "extraction contains duplicate entity local IDs"
            raise ValueError(msg)
        known = set(local_ids)
        for relation in self.relations:
            if relation.source_local_id not in known or relation.target_local_id not in known:
                msg = "relation endpoint does not reference an extracted entity"
                raise ValueError(msg)
        return self


def extraction_instructions() -> str:
    """Return stable extraction policy shared by all compatible providers."""
    return """You build an additive knowledge graph from one Granola meeting.
Return one JSON object matching the requested extraction contract.

Rules:
- Extract primary entities, not isolated attributes or prose fragments.
- Treat decisions, action items, projects, organizations, products, topics, and events as
  entities when they have their own identity or relationships.
- Reuse an existing ontology key whenever it fits. Propose only missing types, fields, and
  relations. Never rename, delete, or redefine existing ontology.
- Use global identity for reusable things such as people, organizations, and projects. Use
  note identity for meeting-specific decisions, action items, and occurrences.
- Add identifiers only when the evidence explicitly gives a stable value such as an email,
  URL, external ID, or exact handle. Never invent identifiers.
- Every entity, property, and relation must cite supplied evidence IDs. Do not cite IDs that
  are absent from the input.
- Prefer conservative, reusable snake_case ontology keys. Avoid near-duplicate types.
- Include the meeting itself as a meeting entity and connect relevant entities to it.
- Confidence is a number from 0 to 1. Omit unsupported claims.
"""

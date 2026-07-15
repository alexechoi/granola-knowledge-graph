"""Provider-neutral JSON Schema for structured graph extraction."""

from __future__ import annotations

from granola_kg.extraction_models import KEY_PATTERN
from granola_kg.graph_models import FieldDataType, IdentityScope

JsonSchema = dict[str, object]


def extraction_json_schema() -> JsonSchema:
    """Return the strict schema accepted by OpenAI-compatible providers."""
    key = _string(pattern=KEY_PATTERN)
    nonempty = _string(min_length=1)
    evidence_ids = _array(nonempty, min_items=1)
    confidence: JsonSchema = {"type": "number", "minimum": 0, "maximum": 1}

    proposed_field = _object(
        {
            "key": key,
            "display_name": nonempty,
            "description": nonempty,
            "data_type": _enum([item.value for item in FieldDataType]),
            "is_identifier": {"type": "boolean"},
        }
    )
    entity_type = _object(
        {
            "key": key,
            "display_name": nonempty,
            "description": nonempty,
            "identity_scope": _enum([item.value for item in IdentityScope]),
            "fields": _array(proposed_field),
        }
    )
    relation_type = _object(
        {
            "key": key,
            "display_name": nonempty,
            "description": nonempty,
            "source_type_key": key,
            "target_type_key": key,
            "is_directed": {"type": "boolean"},
        }
    )
    identifier = _object({"field_key": key, "value": nonempty})
    extracted_property = _object(
        {
            "field_key": key,
            "value": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "number"},
                    {"type": "boolean"},
                ]
            },
            "evidence_ids": evidence_ids,
            "confidence": confidence,
        }
    )
    entity = _object(
        {
            "local_id": key,
            "type_key": key,
            "name": nonempty,
            "identifiers": _array(identifier),
            "evidence_ids": evidence_ids,
            "properties": _array(extracted_property),
        }
    )
    relation = _object(
        {
            "source_local_id": key,
            "relation_key": key,
            "target_local_id": key,
            "evidence_ids": evidence_ids,
            "confidence": confidence,
        }
    )
    ontology = _object(
        {
            "entity_types": _array(entity_type),
            "relation_types": _array(relation_type),
        }
    )
    return _object(
        {
            "ontology": ontology,
            "entities": _array(entity),
            "relations": _array(relation),
        }
    )


def _object(properties: JsonSchema) -> JsonSchema:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _array(items: JsonSchema, *, min_items: int | None = None) -> JsonSchema:
    schema: JsonSchema = {"type": "array", "items": items}
    if min_items is not None:
        schema["minItems"] = min_items
    return schema


def _string(*, pattern: str | None = None, min_length: int | None = None) -> JsonSchema:
    schema: JsonSchema = {"type": "string"}
    if pattern is not None:
        schema["pattern"] = pattern
    if min_length is not None:
        schema["minLength"] = min_length
    return schema


def _enum(values: list[str]) -> JsonSchema:
    return {"type": "string", "enum": values}

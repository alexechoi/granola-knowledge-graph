"""Tests for the provider-neutral extraction JSON Schema."""

from granola_kg.extraction_schema import extraction_json_schema

EXPECTED_OBJECT_SCHEMAS = 9


def test_schema_is_strict_at_every_object_boundary() -> None:
    """Object definitions should reject invented fields from model responses."""
    schema_text = str(extraction_json_schema())

    assert schema_text.count("'additionalProperties': False") == EXPECTED_OBJECT_SCHEMAS
    assert "source_type_key" in schema_text
    assert "relation_key" in schema_text


def test_schema_requires_array_shapes_for_repeated_values() -> None:
    """Identifiers, properties, and ontology definitions should be arrays."""
    schema_text = str(extraction_json_schema())

    assert "'identifiers': {'type': 'array'" in schema_text
    assert "'properties': {'type': 'array'" in schema_text
    assert "'entity_types': {'type': 'array'" in schema_text

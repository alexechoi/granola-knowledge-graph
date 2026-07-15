"""Tests for automatic ontology and extraction contracts."""

import pytest
from pydantic import ValidationError

from granola_kg.extraction_models import ExtractionResult

VALID_RESULT = """{
  "ontology": {
    "entity_types": [{
      "key": "decision", "display_name": "Decision",
      "description": "A choice made in a meeting", "identity_scope": "note",
      "fields": []
    }],
    "relation_types": [{
      "key": "made_in", "display_name": "Made in",
      "description": "A decision was made in a meeting",
      "source_type_key": "decision", "target_type_key": "meeting",
      "is_directed": true
    }]
  },
  "entities": [
    {
      "local_id": "meeting_1", "type_key": "meeting", "name": "Planning",
      "identifiers": [{"field_key": "note_id", "value": "not_1"}],
      "evidence_ids": ["ev_1"], "properties": []
    },
    {
      "local_id": "decision_1", "type_key": "decision", "name": "Ship Friday",
      "identifiers": [], "evidence_ids": ["ev_1"],
      "properties": [{
        "field_key": "status", "value": "approved",
        "evidence_ids": ["ev_1"], "confidence": 0.9
      }]
    }
  ],
  "relations": [{
    "source_local_id": "decision_1", "relation_key": "made_in",
    "target_local_id": "meeting_1", "evidence_ids": ["ev_1"],
    "confidence": 0.95
  }]
}"""


def test_validates_additive_ontology_and_primary_entities() -> None:
    """A complete decision-as-entity extraction should validate."""
    result = ExtractionResult.model_validate_json(VALID_RESULT)

    assert result.ontology.entity_types[0].identity_scope.value == "note"
    assert result.entities[1].properties[0].value == "approved"
    assert result.relations[0].source_local_id == "decision_1"


def test_rejects_unknown_relation_endpoint() -> None:
    """Relations cannot refer to entities absent from the same response."""
    invalid = VALID_RESULT.replace('"target_local_id": "meeting_1"', '"target_local_id": "missing"')

    with pytest.raises(ValidationError, match="endpoint"):
        ExtractionResult.model_validate_json(invalid)


def test_rejects_duplicate_ontology_keys() -> None:
    """One response cannot define the same ontology key twice."""
    duplicate_type = """{
      "key": "decision", "display_name": "Decision copy",
      "description": "Duplicate", "identity_scope": "note", "fields": []
    }"""
    invalid = VALID_RESULT.replace(
        '    }],\n    "relation_types"',
        f'    }},\n    {duplicate_type}],\n    "relation_types"',
        1,
    )

    with pytest.raises(ValidationError, match="duplicate entity"):
        ExtractionResult.model_validate_json(invalid)


def test_rejects_non_snake_case_keys() -> None:
    """Generated ontology keys must remain stable and portable."""
    invalid = VALID_RESULT.replace('"key": "decision"', '"key": "Decision Item"', 1)

    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        ExtractionResult.model_validate_json(invalid)

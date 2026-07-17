"""Build locally retrieved, size-bounded extraction context."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from granola_kg.database import fetch_object_rows

if TYPE_CHECKING:
    import sqlite3

DEFAULT_EVIDENCE_CHAR_BUDGET = 12_000
DEFAULT_ONTOLOGY_ROW_LIMIT = 48
EVIDENCE_ROW_SIZE = 7
ENTITY_ROW_SIZE = 7
RELATION_ROW_SIZE = 6
SEED_TYPES = frozenset({"meeting", "person"})
MIN_TOKEN_LENGTH = 3


class PromptBuilder:
    """Select summary-first evidence and compact relevant ontology."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        evidence_char_budget: int = DEFAULT_EVIDENCE_CHAR_BUDGET,
        ontology_row_limit: int = DEFAULT_ONTOLOGY_ROW_LIMIT,
    ) -> None:
        """Configure strict local prompt bounds."""
        if evidence_char_budget < 1 or ontology_row_limit < 1:
            msg = "Prompt builder budgets must be positive"
            raise ValueError(msg)
        self._connection = connection
        self._evidence_char_budget = evidence_char_budget
        self._ontology_row_limit = ontology_row_limit

    def evidence_json(self, note_id: str, title: str) -> str:
        """Return summary evidence followed by locally ranked transcript units."""
        rows = fetch_object_rows(
            self._connection.execute(
                """
                SELECT evidence_id, unit_kind, unit_index, content,
                       speaker_name, started_at, ended_at
                FROM evidence_units
                WHERE note_id = ? AND is_active = 1
                ORDER BY unit_kind, unit_index
                """,
                (note_id,),
            )
        )
        valid = [_evidence_row(row) for row in rows]
        summaries = [row for row in valid if row[1] == "summary"]
        transcripts = [row for row in valid if row[1] == "transcript"]
        context_tokens = _tokens(" ".join([title, *(row[3] for row in summaries)]))
        transcripts.sort(key=lambda row: (-len(context_tokens & _tokens(row[3])), row[2], row[0]))
        remaining = self._evidence_char_budget
        evidence: list[dict[str, object]] = []
        for row in (*summaries, *transcripts):
            if remaining == 0:
                break
            content = row[3][:remaining]
            remaining -= len(content)
            evidence.append(
                {
                    "evidence_id": row[0],
                    "kind": row[1],
                    "content": content,
                    "speaker_name": row[4],
                    "started_at": row[5],
                    "ended_at": row[6],
                }
            )
        return json.dumps(evidence, separators=(",", ":"))

    def ontology_json(self, context: str) -> str:
        """Return seed and lexically relevant ontology rows under a fixed limit."""
        entity_rows = fetch_object_rows(
            self._connection.execute(
                """
                SELECT e.type_key, e.display_name, e.description, e.identity_scope,
                       f.field_key, f.data_type, f.is_identifier
                FROM entity_types AS e
                LEFT JOIN field_definitions AS f ON f.type_key = e.type_key
                ORDER BY e.type_key, f.field_key
                """
            )
        )
        context_tokens = _tokens(context)
        entities = [_entity_row(row) for row in entity_rows]
        entities.sort(
            key=lambda row: (
                row[0] not in SEED_TYPES,
                -len(context_tokens & _tokens(" ".join(row[:3]))),
                row[0],
                row[4] or "",
            )
        )
        selected = entities[: self._ontology_row_limit]
        selected_types = {row[0] for row in selected}
        relation_rows = fetch_object_rows(
            self._connection.execute(
                """
                SELECT relation_key, display_name, description,
                       source_type_key, target_type_key, is_directed
                FROM relation_types ORDER BY relation_key
                """
            )
        )
        relations = [
            _relation_record(row)
            for row in relation_rows
            if len(row) == RELATION_ROW_SIZE
            and row[3] in selected_types
            and row[4] in selected_types
        ][: self._ontology_row_limit]
        snapshot: dict[str, object] = {
            "entity_type_fields": [_entity_record(row) for row in selected],
            "relation_types": relations,
        }
        return json.dumps(snapshot, separators=(",", ":"))


def _tokens(value: str) -> set[str]:
    normalized = "".join(
        character if character.isalnum() or character == "_" else " "
        for character in value.casefold()
    )
    return {token for token in normalized.split() if len(token) >= MIN_TOKEN_LENGTH}


def _evidence_row(row: tuple[object, ...]) -> tuple[str, str, int, str, object, object, object]:
    if (
        len(row) != EVIDENCE_ROW_SIZE
        or not isinstance(row[0], str)
        or not isinstance(row[1], str)
        or not isinstance(row[2], int)
        or not isinstance(row[3], str)
    ):
        msg = "Database returned invalid evidence prompt data"
        raise TypeError(msg)
    return row[0], row[1], row[2], row[3], row[4], row[5], row[6]


def _entity_row(
    row: tuple[object, ...],
) -> tuple[str, str, str, str, str | None, str | None, object]:
    if len(row) != ENTITY_ROW_SIZE or not all(isinstance(row[index], str) for index in range(4)):
        msg = "Database returned invalid entity ontology prompt data"
        raise RuntimeError(msg)
    field_key = row[4] if isinstance(row[4], str) else None
    data_type = row[5] if isinstance(row[5], str) else None
    return (
        _string(row[0], "entity type key"),
        _string(row[1], "entity type name"),
        _string(row[2], "entity type description"),
        _string(row[3], "identity scope"),
        field_key,
        data_type,
        row[6],
    )


def _entity_record(
    row: tuple[str, str, str, str, str | None, str | None, object],
) -> dict[str, object]:
    return {
        "type_key": row[0],
        "display_name": row[1],
        "description": row[2],
        "identity_scope": row[3],
        "field_key": row[4],
        "field_data_type": row[5],
        "field_is_identifier": row[6],
    }


def _relation_record(row: tuple[object, ...]) -> dict[str, object]:
    if len(row) != RELATION_ROW_SIZE or not all(isinstance(row[index], str) for index in range(5)):
        msg = "Database returned invalid relation ontology prompt data"
        raise RuntimeError(msg)
    return {
        "relation_key": row[0],
        "display_name": row[1],
        "description": row[2],
        "source_type_key": row[3],
        "target_type_key": row[4],
        "is_directed": row[5],
    }


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        msg = f"Database returned invalid {label}"
        raise TypeError(msg)
    return value

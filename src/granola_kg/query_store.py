"""Read-side repository for full-text and graph traversal queries."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from granola_kg.database import fetch_object_row, fetch_object_rows
from granola_kg.query_models import (
    EntityDetail,
    EntityProperty,
    EvidenceCitation,
    GraphNeighbor,
    SearchResult,
    SearchResultKind,
)

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Sequence

MAX_TRAVERSAL_DEPTH = 3
MAX_QUERY_LIMIT = 100
ENTITY_DETAIL_ROW_SIZE = 3
PROPERTY_ROW_SIZE = 11
TRAVERSAL_ROW_SIZE = 7
IDENTIFIER_ROW_SIZE = 2
EVIDENCE_SEARCH_ROW_SIZE = 5
ENTITY_SEARCH_ROW_SIZE = 4
NOTE_SEARCH_ROW_SIZE = 4
RANK_FUSION_OFFSET = 60


def _text(value: object, label: str) -> str:
    if not isinstance(value, str):
        msg = f"Database returned invalid {label}"
        raise TypeError(msg)
    return value


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_number(value: object) -> float | None:
    return value if isinstance(value, int | float) else None


def _number(value: object, label: str) -> float:
    result = _optional_number(value)
    if result is None:
        msg = f"Database returned invalid {label}"
        raise TypeError(msg)
    return result


def _integer(value: object, label: str) -> int:
    if not isinstance(value, int):
        msg = f"Database returned invalid {label}"
        raise TypeError(msg)
    return value


def compile_fts_query(query: str) -> str:
    """Compile user text into a literal-token FTS5 AND query."""
    tokens = query.split()
    return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


class QueryStore:
    """Search and traverse the active local graph."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Bind queries to one configured SQLite connection."""
        self._connection = connection

    def index_evidence(self, evidence_id: str, title: str, content: str) -> None:
        """Replace one evidence document in the FTS index."""
        self._connection.execute("DELETE FROM evidence_fts WHERE evidence_id = ?", (evidence_id,))
        self._connection.execute(
            "INSERT INTO evidence_fts(evidence_id, title, content) VALUES (?, ?, ?)",
            (evidence_id, title, content),
        )

    def index_note(self, note_id: str, title: str, summary: str) -> None:
        """Replace one source note in the FTS index."""
        self._connection.execute("DELETE FROM note_fts WHERE note_id = ?", (note_id,))
        self._connection.execute(
            "INSERT INTO note_fts(note_id, title, summary) VALUES (?, ?, ?)",
            (note_id, title, summary),
        )

    def index_entity(
        self,
        entity_id: str,
        canonical_name: str,
        aliases: str = "",
        properties: str = "",
    ) -> None:
        """Replace one canonical entity in the FTS index."""
        self._connection.execute("DELETE FROM entity_fts WHERE entity_id = ?", (entity_id,))
        self._connection.execute(
            """
            INSERT INTO entity_fts(entity_id, canonical_name, aliases, properties)
            VALUES (?, ?, ?, ?)
            """,
            (entity_id, canonical_name, aliases, properties),
        )

    def search(
        self,
        query: str,
        *,
        type_keys: Sequence[str] = (),
        limit: int = 20,
    ) -> tuple[SearchResult, ...]:
        """Search active notes, evidence, and canonical entities by literal tokens."""
        compiled = compile_fts_query(query)
        if not compiled:
            return ()
        bounded_limit = max(1, min(limit, MAX_QUERY_LIMIT))
        evidence = self._search_evidence(f"content : ({compiled})", bounded_limit)
        notes = self._search_notes(compiled, bounded_limit)
        entities = self._search_entities(compiled, type_keys, bounded_limit)
        return self._fuse_results(query, (entities, notes, evidence), bounded_limit)

    def get_entity(self, entity_id: str) -> EntityDetail | None:
        """Load canonical entity metadata and evidence-backed properties."""
        entity_row = fetch_object_row(
            self._connection.execute(
                """
                SELECT entity_id, type_key, canonical_name
                FROM entities WHERE entity_id = ? AND status = 'active'
                """,
                (entity_id,),
            )
        )
        if entity_row is None or len(entity_row) != ENTITY_DETAIL_ROW_SIZE:
            return None
        alias_rows = fetch_object_rows(
            self._connection.execute(
                "SELECT alias FROM entity_aliases WHERE entity_id = ? ORDER BY normalized_alias",
                (entity_id,),
            )
        )
        identifier_rows = fetch_object_rows(
            self._connection.execute(
                """
                SELECT field_key, raw_value FROM entity_identifiers
                WHERE entity_id = ? ORDER BY field_key, normalized_value
                """,
                (entity_id,),
            )
        )
        properties = tuple(
            self._property_from_row(row)
            for row in fetch_object_rows(
                self._connection.execute(
                    """
                    SELECT p.field_key, p.value_json, p.confidence,
                           ev.evidence_id, n.note_id, n.title, n.web_url,
                           ev.content, ev.speaker_name, ev.started_at, ev.ended_at
                    FROM entity_properties AS p
                    JOIN evidence_units AS ev ON ev.evidence_id = p.source_evidence_id
                    JOIN source_notes AS n ON n.note_id = ev.note_id
                    WHERE p.entity_id = ? AND p.is_active = 1
                      AND ev.is_active = 1 AND n.visibility = 'active'
                    ORDER BY p.field_key, p.created_at DESC
                    """,
                    (entity_id,),
                )
            )
        )
        return EntityDetail(
            entity_id=_text(entity_row[0], "entity ID"),
            type_key=_text(entity_row[1], "entity type"),
            canonical_name=_text(entity_row[2], "canonical name"),
            aliases=tuple(_text(row[0], "alias") for row in alias_rows if row),
            identifiers=tuple(
                (_text(row[0], "identifier field"), _text(row[1], "identifier value"))
                for row in identifier_rows
                if len(row) == IDENTIFIER_ROW_SIZE
            ),
            properties=properties,
        )

    def traverse(
        self,
        entity_id: str,
        *,
        max_depth: int = 2,
        limit: int = 100,
    ) -> tuple[GraphNeighbor, ...]:
        """Traverse active relationships in either direction without cycles."""
        depth = max(1, min(max_depth, MAX_TRAVERSAL_DEPTH))
        bounded_limit = max(1, min(limit, MAX_QUERY_LIMIT))
        rows = fetch_object_rows(
            self._connection.execute(
                """
                WITH RECURSIVE walk(
                    entity_id, relation_key, direction, depth, evidence_id, path
                ) AS (
                    SELECT e.target_entity_id, e.relation_key, 'outgoing', 1,
                           e.source_evidence_id, '|' || ? || '|' || e.target_entity_id || '|'
                    FROM edges AS e
                    WHERE e.source_entity_id = ? AND e.is_active = 1
                    UNION ALL
                    SELECT e.source_entity_id, e.relation_key, 'incoming', 1,
                           e.source_evidence_id, '|' || ? || '|' || e.source_entity_id || '|'
                    FROM edges AS e
                    WHERE e.target_entity_id = ? AND e.is_active = 1
                    UNION ALL
                    SELECT CASE WHEN e.source_entity_id = w.entity_id
                                THEN e.target_entity_id ELSE e.source_entity_id END,
                           e.relation_key,
                           CASE WHEN e.source_entity_id = w.entity_id
                                THEN 'outgoing' ELSE 'incoming' END,
                           w.depth + 1, e.source_evidence_id,
                           w.path || CASE WHEN e.source_entity_id = w.entity_id
                                         THEN e.target_entity_id ELSE e.source_entity_id END || '|'
                    FROM walk AS w
                    JOIN edges AS e ON e.source_entity_id = w.entity_id
                                        OR e.target_entity_id = w.entity_id
                    WHERE w.depth < ? AND e.is_active = 1
                      AND instr(w.path, '|' || CASE WHEN e.source_entity_id = w.entity_id
                                THEN e.target_entity_id ELSE e.source_entity_id END || '|') = 0
                )
                SELECT w.entity_id, en.canonical_name, en.type_key,
                       w.relation_key, w.direction, MIN(w.depth), w.evidence_id
                FROM walk AS w
                JOIN entities AS en ON en.entity_id = w.entity_id AND en.status = 'active'
                GROUP BY w.entity_id, w.relation_key, w.direction
                ORDER BY MIN(w.depth), en.canonical_name
                LIMIT ?
                """,
                (entity_id, entity_id, entity_id, entity_id, depth, bounded_limit),
            )
        )
        return tuple(self._neighbor_from_row(row) for row in rows)

    def _search_evidence(self, compiled: str, limit: int) -> tuple[SearchResult, ...]:
        rows = fetch_object_rows(
            self._connection.execute(
                """
                SELECT f.evidence_id, n.title, snippet(evidence_fts, 2, '[', ']', '…', 24),
                       bm25(evidence_fts), n.note_id
                FROM evidence_fts AS f
                JOIN evidence_units AS ev ON ev.evidence_id = f.evidence_id
                JOIN source_notes AS n ON n.note_id = ev.note_id
                WHERE evidence_fts MATCH ? AND ev.is_active = 1 AND n.visibility = 'active'
                ORDER BY bm25(evidence_fts) LIMIT ?
                """,
                (compiled, limit),
            )
        )
        return tuple(
            SearchResult(
                kind=SearchResultKind.EVIDENCE,
                object_id=_text(row[0], "evidence ID"),
                title=_optional_text(row[1]) or "Untitled meeting",
                snippet=_text(row[2], "evidence snippet"),
                score=_number(row[3], "evidence score"),
                note_id=_text(row[4], "note ID"),
            )
            for row in rows
            if len(row) == EVIDENCE_SEARCH_ROW_SIZE
        )

    def _search_notes(self, compiled: str, limit: int) -> tuple[SearchResult, ...]:
        rows = fetch_object_rows(
            self._connection.execute(
                """
                SELECT f.note_id, COALESCE(n.title, 'Untitled meeting'),
                       COALESCE(NULLIF(snippet(note_fts, 2, '[', ']', '…', 24), ''),
                                n.title, 'Untitled meeting'),
                       bm25(note_fts)
                FROM note_fts AS f
                JOIN source_notes AS n ON n.note_id = f.note_id
                WHERE note_fts MATCH ? AND n.visibility = 'active'
                ORDER BY bm25(note_fts) LIMIT ?
                """,
                (compiled, limit),
            )
        )
        return tuple(
            SearchResult(
                kind=SearchResultKind.NOTE,
                object_id=_text(row[0], "note ID"),
                title=_text(row[1], "note title"),
                snippet=_text(row[2], "note snippet"),
                score=_number(row[3], "note score"),
                note_id=_text(row[0], "note ID"),
            )
            for row in rows
            if len(row) == NOTE_SEARCH_ROW_SIZE
        )

    def _search_entities(
        self, compiled: str, type_keys: Sequence[str], limit: int
    ) -> tuple[SearchResult, ...]:
        type_filter = json.dumps(type_keys)
        rows = fetch_object_rows(
            self._connection.execute(
                """
                SELECT f.entity_id, e.canonical_name, e.type_key, bm25(entity_fts)
                FROM entity_fts AS f
                JOIN entities AS e ON e.entity_id = f.entity_id
                WHERE entity_fts MATCH ? AND e.status = 'active'
                  AND (json_array_length(?) = 0
                       OR e.type_key IN (SELECT value FROM json_each(?)))
                ORDER BY bm25(entity_fts) LIMIT ?
                """,
                (compiled, type_filter, type_filter, limit),
            )
        )
        return tuple(
            SearchResult(
                kind=SearchResultKind.ENTITY,
                object_id=_text(row[0], "entity ID"),
                title=_text(row[1], "entity name"),
                snippet=_text(row[1], "entity name"),
                score=_number(row[3], "entity score"),
                type_key=_text(row[2], "entity type"),
            )
            for row in rows
            if len(row) == ENTITY_SEARCH_ROW_SIZE
        )

    @staticmethod
    def _fuse_results(
        query: str,
        streams: tuple[tuple[SearchResult, ...], ...],
        limit: int,
    ) -> tuple[SearchResult, ...]:
        normalized_query = " ".join(query.casefold().split())
        ranked: list[SearchResult] = []
        for stream in streams:
            for rank, result in enumerate(stream, start=1):
                exact_boost = 1.0 if _normalized(result.title) == normalized_query else 0.0
                score = exact_boost + 1.0 / (RANK_FUSION_OFFSET + rank)
                ranked.append(
                    SearchResult(
                        kind=result.kind,
                        object_id=result.object_id,
                        title=result.title,
                        snippet=result.snippet,
                        score=score,
                        type_key=result.type_key,
                        note_id=result.note_id,
                    )
                )
        ranked.sort(key=lambda result: (-result.score, result.kind.value, result.object_id))
        return tuple(ranked[:limit])

    @staticmethod
    def _property_from_row(row: tuple[object, ...]) -> EntityProperty:
        if len(row) != PROPERTY_ROW_SIZE:
            msg = "Database returned an invalid property row"
            raise RuntimeError(msg)
        return EntityProperty(
            field_key=_text(row[0], "property field"),
            value_json=_text(row[1], "property value"),
            confidence=_optional_number(row[2]),
            citation=EvidenceCitation(
                evidence_id=_text(row[3], "evidence ID"),
                note_id=_text(row[4], "note ID"),
                note_title=_optional_text(row[5]),
                web_url=_optional_text(row[6]),
                excerpt=_text(row[7], "evidence excerpt"),
                speaker_name=_optional_text(row[8]),
                started_at=_optional_text(row[9]),
                ended_at=_optional_text(row[10]),
            ),
        )

    @staticmethod
    def _neighbor_from_row(row: tuple[object, ...]) -> GraphNeighbor:
        if len(row) != TRAVERSAL_ROW_SIZE:
            msg = "Database returned an invalid traversal row"
            raise RuntimeError(msg)
        return GraphNeighbor(
            entity_id=_text(row[0], "neighbor ID"),
            canonical_name=_text(row[1], "neighbor name"),
            type_key=_text(row[2], "neighbor type"),
            relation_key=_text(row[3], "relation type"),
            direction=_text(row[4], "relationship direction"),
            depth=_integer(row[5], "traversal depth"),
            evidence_id=_text(row[6], "relationship evidence"),
        )


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())

"""Write-side repository for ontology and canonical entities."""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid4, uuid5

from granola_kg.database import fetch_object_row
from granola_kg.graph_models import (
    EntityCandidate,
    EntityRecord,
    EntityResolution,
    EntityTypeDefinition,
    FieldDataType,
    FieldDefinition,
    IdentityScope,
    RelationTypeDefinition,
)

if TYPE_CHECKING:
    import sqlite3

ENTITY_ROW_SIZE = 6
NAME_MATCH_ROW_SIZE = 2


class IdentityConflictError(RuntimeError):
    """Raised when candidate identifiers point to different entities."""


def normalize_text(value: str) -> str:
    """Normalize human text for deterministic matching."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        msg = f"Database returned invalid {label}"
        raise RuntimeError(msg)
    return value


class GraphStore:
    """Persist ontology definitions and resolve canonical entities."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Bind the repository to one configured SQLite connection."""
        self._connection = connection

    def ensure_seed_ontology(self) -> None:
        """Install universal Meeting and Person ontology definitions."""
        self._connection.execute(
            "INSERT OR IGNORE INTO ontology_revisions(revision, reason) VALUES (1, 'seed')"
        )
        self.upsert_entity_type(
            EntityTypeDefinition(
                key="meeting",
                display_name="Meeting",
                description="A meeting imported from Granola.",
                identity_scope=IdentityScope.GLOBAL,
                fields=(
                    FieldDefinition(
                        key="note_id",
                        display_name="Granola note ID",
                        description="Stable Granola API note identifier.",
                        data_type=FieldDataType.STRING,
                        is_identifier=True,
                    ),
                ),
            ),
            revision=1,
            is_seed=True,
        )
        self.upsert_entity_type(
            EntityTypeDefinition(
                key="person",
                display_name="Person",
                description="A meeting participant or mentioned person.",
                identity_scope=IdentityScope.GLOBAL,
                fields=(
                    FieldDefinition(
                        key="email",
                        display_name="Email",
                        description="Email address used as a stable person identifier.",
                        data_type=FieldDataType.EMAIL,
                        is_identifier=True,
                    ),
                ),
            ),
            revision=1,
            is_seed=True,
        )
        self.upsert_relation_type(
            RelationTypeDefinition(
                key="attended",
                display_name="Attended",
                description="A person attended a meeting.",
                source_type_key="person",
                target_type_key="meeting",
            ),
            revision=1,
        )

    def create_revision(self, reason: str, source_note_id: str | None = None) -> int:
        """Create and return the next ontology revision."""
        row = fetch_object_row(
            self._connection.execute("SELECT COALESCE(MAX(revision), 0) FROM ontology_revisions")
        )
        if row is None or not row or not isinstance(row[0], int):
            msg = "Database returned an invalid ontology revision"
            raise RuntimeError(msg)
        revision = row[0] + 1
        self._connection.execute(
            "INSERT INTO ontology_revisions(revision, reason, source_note_id) VALUES (?, ?, ?)",
            (revision, reason, source_note_id),
        )
        return revision

    def upsert_entity_type(
        self,
        definition: EntityTypeDefinition,
        revision: int,
        *,
        is_seed: bool = False,
    ) -> None:
        """Add a type or refresh its descriptive metadata without changing identity."""
        self._connection.execute(
            """
            INSERT INTO entity_types(
                type_key, display_name, description, identity_scope,
                created_revision, updated_revision, is_seed
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(type_key) DO UPDATE SET
                display_name = excluded.display_name,
                description = excluded.description,
                updated_revision = excluded.updated_revision
            """,
            (
                definition.key,
                definition.display_name,
                definition.description,
                definition.identity_scope.value,
                revision,
                revision,
                int(is_seed),
            ),
        )
        for field in definition.fields:
            self._connection.execute(
                """
                INSERT INTO field_definitions(
                    type_key, field_key, display_name, description,
                    data_type, is_identifier, created_revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(type_key, field_key) DO UPDATE SET
                    display_name = excluded.display_name,
                    description = excluded.description
                """,
                (
                    definition.key,
                    field.key,
                    field.display_name,
                    field.description,
                    field.data_type.value,
                    int(field.is_identifier),
                    revision,
                ),
            )

    def upsert_relation_type(self, definition: RelationTypeDefinition, revision: int) -> None:
        """Add a relationship type without destructively changing its endpoints."""
        self._connection.execute(
            """
            INSERT INTO relation_types(
                relation_key, display_name, description, source_type_key,
                target_type_key, is_directed, created_revision
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(relation_key) DO UPDATE SET
                display_name = excluded.display_name,
                description = excluded.description
            """,
            (
                definition.key,
                definition.display_name,
                definition.description,
                definition.source_type_key,
                definition.target_type_key,
                int(definition.is_directed),
                revision,
            ),
        )

    def resolve_entity(self, candidate: EntityCandidate) -> EntityResolution:
        """Resolve by identifiers, then unambiguous scoped normalized name."""
        scope = self._identity_scope(candidate.type_key)
        self._validate_scope(candidate, scope)
        identifier_match = self._identifier_match(candidate)
        if identifier_match is not None:
            return self._attach(candidate, identifier_match, "exact_identifier")
        stable_meeting_id = self._stable_meeting_id(candidate)
        name_match = None if stable_meeting_id is not None else self._name_match(candidate, scope)
        if name_match is not None:
            return self._attach(candidate, name_match, "exact_name")
        alias_match = None if stable_meeting_id is not None else self._alias_match(candidate, scope)
        if alias_match is not None:
            return self._attach(candidate, alias_match, "exact_alias")
        entity_id = stable_meeting_id or str(uuid4())
        self._connection.execute(
            """
            INSERT INTO entities(
                entity_id, type_key, canonical_name, normalized_name,
                identity_scope, scope_note_id
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                entity_id,
                candidate.type_key,
                candidate.name.strip(),
                normalize_text(candidate.name),
                scope.value,
                candidate.scope_note_id,
            ),
        )
        resolution = self._attach(candidate, entity_id, "created")
        return EntityResolution(
            entity_id=resolution.entity_id,
            type_key=resolution.type_key,
            canonical_name=resolution.canonical_name,
            created=True,
            match_source=resolution.match_source,
        )

    def get_entity(self, entity_id: str) -> EntityRecord | None:
        """Load one canonical entity."""
        row = fetch_object_row(
            self._connection.execute(
                """
                SELECT entity_id, type_key, canonical_name, identity_scope, scope_note_id, status
                FROM entities WHERE entity_id = ?
                """,
                (entity_id,),
            )
        )
        if row is None or len(row) != ENTITY_ROW_SIZE:
            return None
        scope_note_id = row[4] if isinstance(row[4], str) else None
        return EntityRecord(
            entity_id=_required_text(row[0], "entity ID"),
            type_key=_required_text(row[1], "entity type"),
            canonical_name=_required_text(row[2], "canonical name"),
            identity_scope=IdentityScope(_required_text(row[3], "identity scope")),
            scope_note_id=scope_note_id,
            status=_required_text(row[5], "entity status"),
        )

    def _identity_scope(self, type_key: str) -> IdentityScope:
        row = fetch_object_row(
            self._connection.execute(
                "SELECT identity_scope FROM entity_types WHERE type_key = ?", (type_key,)
            )
        )
        if row is None or not row:
            msg = f"Unknown entity type: {type_key}"
            raise ValueError(msg)
        return IdentityScope(_required_text(row[0], "identity scope"))

    @staticmethod
    def _validate_scope(candidate: EntityCandidate, scope: IdentityScope) -> None:
        if scope is IdentityScope.NOTE and not candidate.scope_note_id:
            msg = f"Note-scoped entity {candidate.type_key} requires scope_note_id"
            raise ValueError(msg)
        if scope is IdentityScope.GLOBAL and candidate.scope_note_id is not None:
            msg = f"Global entity {candidate.type_key} cannot have scope_note_id"
            raise ValueError(msg)

    def _identifier_match(self, candidate: EntityCandidate) -> str | None:
        matches: set[str] = set()
        for identifier in candidate.identifiers:
            row = fetch_object_row(
                self._connection.execute(
                    """
                    SELECT e.entity_id
                    FROM entity_identifiers AS i
                    JOIN entities AS e ON e.entity_id = i.entity_id
                    WHERE e.type_key = ? AND e.status = 'active'
                      AND i.field_key = ? AND i.normalized_value = ?
                    LIMIT 1
                    """,
                    (candidate.type_key, identifier.field_key, normalize_text(identifier.value)),
                )
            )
            if row is not None and row:
                matches.add(_required_text(row[0], "matched entity ID"))
        if len(matches) > 1:
            msg = f"Identifiers for {candidate.name} resolve to different entities"
            raise IdentityConflictError(msg)
        return next(iter(matches), None)

    def _name_match(self, candidate: EntityCandidate, scope: IdentityScope) -> str | None:
        row = fetch_object_row(
            self._connection.execute(
                """
                SELECT COUNT(*), MIN(entity_id)
                FROM entities
                WHERE type_key = ? AND normalized_name = ? AND status = 'active'
                  AND ((? = 'global' AND scope_note_id IS NULL) OR scope_note_id = ?)
                """,
                (
                    candidate.type_key,
                    normalize_text(candidate.name),
                    scope.value,
                    candidate.scope_note_id,
                ),
            )
        )
        if row is None or len(row) != NAME_MATCH_ROW_SIZE or row[0] != 1:
            return None
        return _required_text(row[1], "matched entity ID")

    def _alias_match(self, candidate: EntityCandidate, scope: IdentityScope) -> str | None:
        row = fetch_object_row(
            self._connection.execute(
                """
                SELECT COUNT(DISTINCT e.entity_id), MIN(e.entity_id)
                FROM entity_aliases AS a
                JOIN entities AS e ON e.entity_id = a.entity_id
                WHERE e.type_key = ? AND a.normalized_alias = ? AND e.status = 'active'
                  AND ((? = 'global' AND e.scope_note_id IS NULL) OR e.scope_note_id = ?)
                """,
                (
                    candidate.type_key,
                    normalize_text(candidate.name),
                    scope.value,
                    candidate.scope_note_id,
                ),
            )
        )
        if row is None or len(row) != NAME_MATCH_ROW_SIZE or row[0] != 1:
            return None
        return _required_text(row[1], "alias-matched entity ID")

    @staticmethod
    def _stable_meeting_id(candidate: EntityCandidate) -> str | None:
        if candidate.type_key != "meeting":
            return None
        for identifier in candidate.identifiers:
            if identifier.field_key == "note_id":
                return str(uuid5(NAMESPACE_URL, f"granola-note:{identifier.value.strip()}"))
        return None

    def _attach(self, candidate: EntityCandidate, entity_id: str, source: str) -> EntityResolution:
        self._connection.execute(
            """
            INSERT OR IGNORE INTO entity_aliases(
                entity_id, alias, normalized_alias, source_evidence_id
            ) VALUES (?, ?, ?, ?)
            """,
            (
                entity_id,
                candidate.name.strip(),
                normalize_text(candidate.name),
                candidate.evidence_id,
            ),
        )
        for identifier in candidate.identifiers:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO entity_identifiers(
                    entity_id, field_key, raw_value, normalized_value, source_evidence_id
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    entity_id,
                    identifier.field_key,
                    identifier.value.strip(),
                    normalize_text(identifier.value),
                    candidate.evidence_id,
                ),
            )
        entity = self.get_entity(entity_id)
        if entity is None:
            msg = f"Resolved entity disappeared: {entity_id}"
            raise RuntimeError(msg)
        return EntityResolution(
            entity_id=entity.entity_id,
            type_key=entity.type_key,
            canonical_name=entity.canonical_name,
            created=False,
            match_source=source,
        )

"""Tests for local SQLite initialization."""

from pathlib import Path

from granola_kg.database import (
    initialize_database,
    migrate_database,
    packaged_migrations,
    schema_version,
    table_exists,
)


def test_initializes_complete_schema(tmp_path: Path) -> None:
    """A fresh database should contain graph, queue, and FTS tables."""
    connection = initialize_database(tmp_path / "graph.db")
    try:
        assert schema_version(connection) == 1
        for table_name in (
            "source_notes",
            "evidence_units",
            "entity_types",
            "entities",
            "edges",
            "processing_queue",
            "evidence_fts",
            "entity_fts",
        ):
            assert table_exists(connection, table_name)
    finally:
        connection.close()


def test_migration_is_idempotent(tmp_path: Path) -> None:
    """Reopening a current database should not replay migrations."""
    path = tmp_path / "graph.db"
    connection = initialize_database(path)
    try:
        assert migrate_database(connection) == 1
        assert schema_version(connection) == 1
    finally:
        connection.close()


def test_packaged_migrations_are_ordered() -> None:
    """Migration discovery should be deterministic."""
    migrations = packaged_migrations()
    assert [migration.version for migration in migrations] == [1]
    assert migrations[0].name == "initial"

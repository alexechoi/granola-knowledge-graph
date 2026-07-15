"""SQLite connection and migration management."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from importlib import resources
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path


class ObjectCursor(Protocol):
    """Narrow the untyped DB-API row boundary to runtime-checked objects."""

    def fetchone(self) -> tuple[object, ...] | None:
        """Return one database row."""
        ...


class ObjectRowsCursor(Protocol):
    """Typed boundary for DB-API multi-row results."""

    def fetchall(self) -> list[tuple[object, ...]]:
        """Return all database rows."""
        ...


@dataclass(frozen=True)
class Migration:
    """One packaged database migration."""

    version: int
    name: str
    sql: str


def connect_database(path: Path) -> sqlite3.Connection:
    """Open and configure a local SQLite database."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def schema_version(connection: sqlite3.Connection) -> int:
    """Return the active SQLite schema version."""
    row = fetch_object_row(connection.execute("PRAGMA user_version"))
    if row is None or not row or not isinstance(row[0], int):
        msg = "SQLite did not return a valid schema version"
        raise RuntimeError(msg)
    return row[0]


def packaged_migrations() -> tuple[Migration, ...]:
    """Load packaged SQL migrations in version order."""
    migration_root = resources.files("granola_kg.migrations")
    migrations: list[Migration] = []
    for item in migration_root.iterdir():
        filename = item.name
        if not filename.endswith(".sql"):
            continue
        version_text, separator, name = filename.removesuffix(".sql").partition("_")
        if not separator or not version_text.isdigit():
            msg = f"Invalid migration filename: {item.name}"
            raise RuntimeError(msg)
        migrations.append(
            Migration(version=int(version_text), name=name, sql=item.read_text(encoding="utf-8"))
        )
    return tuple(sorted(migrations, key=lambda migration: migration.version))


def migrate_database(connection: sqlite3.Connection) -> int:
    """Apply pending packaged migrations in a transaction per version."""
    current = schema_version(connection)
    for migration in packaged_migrations():
        if migration.version <= current:
            continue
        try:
            connection.executescript(f"BEGIN IMMEDIATE;\n{migration.sql}\nCOMMIT;")
        except sqlite3.Error:
            connection.rollback()
            raise
        current = migration.version
    return current


def initialize_database(path: Path) -> sqlite3.Connection:
    """Open a database and bring it to the latest schema."""
    connection = connect_database(path)
    migrate_database(connection)
    return connection


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    """Return whether a table or virtual table exists."""
    row = fetch_object_row(
        connection.execute(
            "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE name = ?)",
            (table_name,),
        )
    )
    return row is not None and bool(row) and row[0] == 1


def fetch_object_row(cursor: ObjectCursor) -> tuple[object, ...] | None:
    """Read one row through a runtime-checked object boundary."""
    return cursor.fetchone()


def fetch_object_rows(cursor: ObjectRowsCursor) -> list[tuple[object, ...]]:
    """Read rows through a runtime-checked object boundary."""
    return cursor.fetchall()

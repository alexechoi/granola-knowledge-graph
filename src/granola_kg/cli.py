"""Command-line entrypoint for Granola Knowledge Graph."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from granola_kg.config import RuntimeSettings, load_settings
from granola_kg.database import initialize_database
from granola_kg.explorer_server import DEFAULT_EXPLORER_PORT, run_explorer
from granola_kg.granola_client import GranolaClient
from granola_kg.installer import install_skill
from granola_kg.llm_client import StructuredLlmClient
from granola_kg.query_store import QueryStore
from granola_kg.sync_engine import SyncEngine
from granola_kg.sync_store import SyncStore
from granola_kg.version import __version__

if TYPE_CHECKING:
    from collections.abc import Sequence

    from granola_kg.query_models import EntityDetail, EntityProperty, GraphNeighbor, SearchResult


class CliNamespace(argparse.Namespace):
    """Statically typed argparse destination."""

    command: str | None
    database_path: Path | None
    reconcile: bool
    folder_id: str | None
    limit: int | None
    max_attempts: int
    note_id: str | None
    all_notes: bool
    query: str
    type_keys: list[str]
    entity_id: str
    depth: int
    skills_directory: Path | None
    force: bool
    port: int
    no_open: bool

    def __init__(self) -> None:
        """Initialize defaults used by commands that omit specific destinations."""
        super().__init__()
        self.command = None
        self.database_path = None
        self.reconcile = False
        self.folder_id = None
        self.limit = None
        self.max_attempts = 5
        self.note_id = None
        self.all_notes = False
        self.query = ""
        self.type_keys = []
        self.entity_id = ""
        self.depth = 2
        self.skills_directory = None
        self.force = False
        self.port = DEFAULT_EXPLORER_PORT
        self.no_open = False


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level command parser."""
    parser = argparse.ArgumentParser(
        prog="granola-kg",
        description="Build and query a local knowledge graph from Granola meetings.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--db", dest="database_path", type=Path, help="SQLite database path")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("init", help="Initialize the local database")

    install = commands.add_parser("install", help="Install the packaged assistant skill")
    install.add_argument("--skills-dir", dest="skills_directory", type=Path)
    install.add_argument("--force", action="store_true")

    sync = commands.add_parser("sync", help="Discover and process changed Granola notes")
    sync.add_argument("--reconcile", action="store_true", help="Also hide disappeared notes")
    sync.add_argument("--folder-id", help="Limit discovery to one Granola folder")
    sync.add_argument("--limit", type=int, help="Maximum queued notes to process")
    sync.add_argument("--max-attempts", type=int, default=5)

    process = commands.add_parser("process", help="Process already queued notes")
    process.add_argument("--limit", type=int, help="Maximum queued notes to process")
    process.add_argument("--max-attempts", type=int, default=5)

    reprocess = commands.add_parser("reprocess", help="Queue retained notes again")
    reprocess.add_argument("note_id", nargs="?")
    reprocess.add_argument("--all", dest="all_notes", action="store_true")

    commands.add_parser("status", help="Show queue and watermark state")

    explore = commands.add_parser("explore", help="Open the local graph explorer")
    explore.add_argument("--port", type=int, default=DEFAULT_EXPLORER_PORT)
    explore.add_argument("--no-open", action="store_true", help="Do not open a browser")

    search = commands.add_parser("search", help="Search evidence and entities")
    search.add_argument("query")
    search.add_argument("--type", dest="type_keys", action="append", default=[])
    search.add_argument("--limit", type=int, default=20)

    entity = commands.add_parser("entity", help="Show an entity with citations")
    entity.add_argument("entity_id")

    traverse = commands.add_parser("traverse", help="Traverse local graph relationships")
    traverse.add_argument("entity_id")
    traverse.add_argument("--depth", type=int, default=2)
    traverse.add_argument("--limit", type=int, default=100)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface."""
    parser = build_parser()
    args = parser.parse_args(argv, namespace=CliNamespace())
    if args.command is None:
        parser.print_help()
        return 0
    settings = load_settings(args.database_path)
    try:
        return _dispatch(args, settings)
    except (OSError, RuntimeError, ValueError) as error:
        sys.stderr.write(f"granola-kg: {error}\n")
        return 2


def _dispatch(args: CliNamespace, settings: RuntimeSettings) -> int:
    if args.command in {"install", "init", "explore"}:
        return _dispatch_setup(args, settings)
    if args.command in {"sync", "process"}:
        return _run_remote(args, settings, discover=args.command == "sync")
    connection = initialize_database(settings.database_path)
    try:
        if args.command == "status":
            state = SyncStore(connection)
            counts = state.counts()
            watermark = state.watermark()
            _print_json(
                {
                    "watermark": watermark.isoformat() if watermark is not None else None,
                    "queue": {
                        "pending": counts.pending,
                        "processing": counts.processing,
                        "complete": counts.complete,
                        "failed": counts.failed,
                    },
                }
            )
        elif args.command == "reprocess":
            _reprocess(args, SyncStore(connection))
        elif args.command == "search":
            results = QueryStore(connection).search(
                args.query, type_keys=tuple(args.type_keys), limit=_positive(args.limit, "limit")
            )
            _print_json({"results": [_search_json(result) for result in results]})
        elif args.command == "entity":
            detail = QueryStore(connection).get_entity(args.entity_id)
            if detail is None:
                msg = f"Unknown entity: {args.entity_id}"
                raise ValueError(msg)
            _print_json(_entity_json(detail))
        elif args.command == "traverse":
            neighbors = QueryStore(connection).traverse(
                args.entity_id,
                max_depth=_positive(args.depth, "depth"),
                limit=_positive(args.limit, "limit"),
            )
            _print_json({"neighbors": [_neighbor_json(item) for item in neighbors]})
        else:
            msg = f"Unknown command: {args.command}"
            raise ValueError(msg)
    finally:
        connection.close()
    return 0


def _dispatch_setup(args: CliNamespace, settings: RuntimeSettings) -> int:
    """Run installation, initialization, and explorer setup commands."""
    if args.command == "install":
        destination = install_skill(args.skills_directory, force=args.force)
        _print_json(
            {
                "skill": str(destination),
                "mcp_command": "granola-kg-mcp",
                "installed": True,
            }
        )
        return 0
    if args.command == "init":
        connection = initialize_database(settings.database_path)
        connection.close()
        _print_json({"database": str(settings.database_path), "initialized": True})
        return 0
    if args.command == "explore":
        run_explorer(settings.database_path, args.port, open_browser=not args.no_open)
    return 0


def _run_remote(args: CliNamespace, settings: RuntimeSettings, *, discover: bool) -> int:
    granola_key, llm_config = settings.require_remote()
    limit = _optional_positive(args.limit, "limit")
    max_attempts = _positive(args.max_attempts, "max-attempts")
    connection = initialize_database(settings.database_path)
    granola = GranolaClient(granola_key)
    extractor = StructuredLlmClient(llm_config)
    try:
        engine = SyncEngine(
            connection,
            granola,
            extractor,
            prompt_version=settings.prompt_version,
            model_name=llm_config.model,
        )
        discovery_json: dict[str, object] | None = None
        if discover:
            report = (
                engine.reconcile(folder_id=args.folder_id)
                if args.reconcile
                else engine.discover(folder_id=args.folder_id)
            )
            discovery_json = {
                "discovered": report.discovered,
                "enqueued": report.enqueued,
                "hidden": report.hidden,
                "watermark": report.watermark.isoformat() if report.watermark else None,
            }
        processed = engine.process(limit=limit, max_attempts=max_attempts)
        _print_json(
            {
                "discovery": discovery_json,
                "processing": {
                    "completed": processed.completed,
                    "failed": processed.failed,
                    "failed_note_ids": list(processed.failed_note_ids),
                },
            }
        )
    finally:
        extractor.close()
        granola.close()
        connection.close()
    return 0


def _reprocess(args: CliNamespace, state: SyncStore) -> None:
    if args.all_notes == (args.note_id is not None):
        msg = "Provide exactly one note ID or --all"
        raise ValueError(msg)
    if args.all_notes:
        _print_json({"queued": state.reprocess_all()})
    elif args.note_id is not None:
        state.reprocess(args.note_id)
        _print_json({"queued": 1, "note_id": args.note_id})


def _search_json(result: SearchResult) -> dict[str, object]:
    return {
        "kind": result.kind.value,
        "id": result.object_id,
        "title": result.title,
        "snippet": result.snippet,
        "score": result.score,
        "type_key": result.type_key,
        "note_id": result.note_id,
    }


def _entity_json(detail: EntityDetail) -> dict[str, object]:
    return {
        "entity_id": detail.entity_id,
        "type_key": detail.type_key,
        "canonical_name": detail.canonical_name,
        "aliases": list(detail.aliases),
        "identifiers": [list(item) for item in detail.identifiers],
        "properties": [_property_json(item) for item in detail.properties],
    }


def _property_json(item: EntityProperty) -> dict[str, object]:
    return {
        "field_key": item.field_key,
        "value_json": item.value_json,
        "confidence": item.confidence,
        "citation": {
            "evidence_id": item.citation.evidence_id,
            "note_id": item.citation.note_id,
            "note_title": item.citation.note_title,
            "web_url": item.citation.web_url,
            "excerpt": item.citation.excerpt,
            "speaker_name": item.citation.speaker_name,
            "started_at": item.citation.started_at,
            "ended_at": item.citation.ended_at,
        },
    }


def _neighbor_json(item: GraphNeighbor) -> dict[str, object]:
    return {
        "entity_id": item.entity_id,
        "canonical_name": item.canonical_name,
        "type_key": item.type_key,
        "relation_key": item.relation_key,
        "direction": item.direction,
        "depth": item.depth,
        "evidence_id": item.evidence_id,
    }


def _positive(value: int | None, label: str) -> int:
    if value is None or value <= 0:
        msg = f"{label} must be positive"
        raise ValueError(msg)
    return value


def _optional_positive(value: int | None, label: str) -> int | None:
    return _positive(value, label) if value is not None else None


def _print_json(value: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(value, indent=2, sort_keys=True) + "\n")

"""Local-only HTTP server for browsing the knowledge graph."""

from __future__ import annotations

import json
import sqlite3
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from granola_kg.database import fetch_object_rows, initialize_database
from granola_kg.query_store import QueryStore

DEFAULT_EXPLORER_PORT = 8765
MAX_OVERVIEW_NODES = 300
MAX_PORT = 65535
NODE_ROW_SIZE = 3
EDGE_ROW_SIZE = 5

if TYPE_CHECKING:
    from pathlib import Path


class ExplorerHttpServer(ThreadingHTTPServer):
    """HTTP server carrying the configured graph database path."""

    database_path: Path

    def __init__(self, database_path: Path, port: int) -> None:
        """Bind exclusively to loopback and retain the database path."""
        super().__init__(("127.0.0.1", port), _bound_handler(database_path))
        self.database_path = database_path


class ExplorerRequestHandler(BaseHTTPRequestHandler):
    """Serve the explorer application and its read-only JSON API."""

    database_path: Path

    def do_GET(self) -> None:
        """Route one read-only explorer request."""
        parsed = urlsplit(self.path)
        try:
            if parsed.path == "/":
                self._send_html()
            elif parsed.path == "/api/graph":
                self._send_json(_graph_overview(self.database_path))
            elif parsed.path == "/api/entity":
                entity_ids = parse_qs(parsed.query).get("id", [])
                if len(entity_ids) != 1:
                    self._send_error(HTTPStatus.BAD_REQUEST, "Provide one entity id")
                else:
                    self._send_entity(entity_ids[0])
            else:
                self._send_error(HTTPStatus.NOT_FOUND, "Not found")
        except (OSError, RuntimeError, sqlite3.Error, ValueError):
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Explorer request failed")

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Keep the local explorer quiet during normal navigation."""

    def _send_entity(self, entity_id: str) -> None:
        connection = initialize_database(self.database_path)
        try:
            detail = QueryStore(connection).get_entity(entity_id)
        finally:
            connection.close()
        if detail is None:
            self._send_error(HTTPStatus.NOT_FOUND, "Unknown entity")
            return
        self._send_json(
            {
                "id": detail.entity_id,
                "name": detail.canonical_name,
                "type": detail.type_key,
                "aliases": list(detail.aliases),
                "properties": [
                    {
                        "field": item.field_key,
                        "value": item.value_json,
                        "confidence": item.confidence,
                        "evidence": {
                            "excerpt": item.citation.excerpt,
                            "note_title": item.citation.note_title,
                            "speaker": item.citation.speaker_name,
                        },
                    }
                    for item in detail.properties
                ],
            }
        )

    def _send_html(self) -> None:
        html = resources.files("granola_kg").joinpath("explorer.html").read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def _send_json(self, value: dict[str, object]) -> None:
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        body = json.dumps({"error": message}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def create_explorer_server(
    database_path: Path, port: int = DEFAULT_EXPLORER_PORT
) -> ExplorerHttpServer:
    """Create a loopback explorer server without starting it."""
    if not 0 <= port <= MAX_PORT:
        msg = "port must be between 0 and 65535"
        raise ValueError(msg)
    return ExplorerHttpServer(database_path, port)


def _bound_handler(database_path: Path) -> type[ExplorerRequestHandler]:
    """Create an isolated request handler bound to one database."""

    class BoundExplorerRequestHandler(ExplorerRequestHandler):
        pass

    BoundExplorerRequestHandler.database_path = database_path
    return BoundExplorerRequestHandler


def run_explorer(database_path: Path, port: int, *, open_browser: bool = True) -> None:
    """Serve the graph until interrupted and optionally open a browser."""
    server = create_explorer_server(database_path, port)
    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}"
    print(f"Granola graph explorer: {url}")  # noqa: T201 - interactive CLI status.
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _graph_overview(database_path: Path) -> dict[str, object]:
    connection = initialize_database(database_path)
    try:
        node_rows = fetch_object_rows(
            connection.execute(
                """
                SELECT entity_id, canonical_name, type_key FROM entities
                WHERE status = 'active' ORDER BY updated_at DESC, entity_id LIMIT ?
                """,
                (MAX_OVERVIEW_NODES,),
            )
        )
        node_ids = {row[0] for row in node_rows if row and isinstance(row[0], str)}
        edge_rows = fetch_object_rows(
            connection.execute(
                """
                SELECT edge_id, source_entity_id, target_entity_id, relation_key,
                       source_evidence_id FROM edges WHERE is_active = 1
                """
            )
        )
    finally:
        connection.close()
    nodes = [
        {"id": _text(row, 0), "name": _text(row, 1), "type": _text(row, 2)}
        for row in node_rows
        if len(row) == NODE_ROW_SIZE
    ]
    edges = [
        {
            "id": _text(row, 0),
            "source": _text(row, 1),
            "target": _text(row, 2),
            "type": _text(row, 3),
            "evidence_id": _text(row, 4),
        }
        for row in edge_rows
        if len(row) == EDGE_ROW_SIZE and row[1] in node_ids and row[2] in node_ids
    ]
    return {"nodes": nodes, "edges": edges, "truncated": len(nodes) == MAX_OVERVIEW_NODES}


def _text(row: tuple[object, ...], index: int) -> str:
    value = row[index]
    if not isinstance(value, str):
        msg = "Database returned invalid explorer data"
        raise TypeError(msg)
    return value

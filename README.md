# Granola Knowledge Graph

An unofficial, local-first knowledge graph for meeting notes retrieved through the Granola API.

The project is under active development. The initial release will provide incremental imports,
automatic ontology generation, a local SQLite graph, and a stdio MCP server.

## Development

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run pytest
uv run python scripts/check_constraints.py
```

This project is not affiliated with or endorsed by Granola.

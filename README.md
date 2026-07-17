# Granola Knowledge Graph

An unofficial, local-first knowledge graph for Granola meeting notes. It incrementally imports
notes through the Granola API, uses a structured LLM to discover primary entities and evolve an
additive ontology, stores evidence-backed graph data in SQLite, and exposes it through a CLI,
local graph explorer, and read-only MCP server.

The project is deliberately small: one Python package, one SQLite file, no hosted database, and
no application account of its own.

## What it does

- Imports only Granola notes that have both an AI summary and transcript.
- Models primary entities such as people, meetings, projects, organizations, decisions, action
  items, products, topics, and events instead of turning every attribute into a node.
- Generates new entity types, fields, and relation types from meeting evidence as needed.
- Reuses stable global identities while keeping meeting-specific occurrences note-scoped.
- Attaches source evidence to entity properties and relationships.
- Tracks remote update watermarks and processes a durable queue without full reingestion.
- Runs a visual graph explorer on loopback only.
- Provides structured local MCP tools for agents and assistants.

## Installation

Python 3.11 or newer is required. Install directly from GitHub with
[uv](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/alexechoi/granola-knowledge-graph.git
```

For development:

```bash
git clone https://github.com/alexechoi/granola-knowledge-graph.git
cd granola-knowledge-graph
uv sync
```

Once a release is published to PyPI, the package can be installed with:

```bash
uv tool install granola-knowledge-graph
# or: pipx install granola-knowledge-graph
```

Both approaches install `granola-kg` and `granola-kg-mcp`.

## Quick start with Gemini

Create a Granola API key in **Settings → Connectors → API keys** and a Gemini API key in
[Google AI Studio](https://aistudio.google.com/apikey). Keep both in your shell environment:

```bash
export GRANOLA_API_KEY="grn_YOUR_KEY"
export GEMINI_API_KEY="YOUR_GEMINI_KEY"

granola-kg sync --limit 10
granola-kg status
granola-kg explore
```

`GEMINI_API_KEY` automatically selects Google's OpenAI-compatible endpoint and the default
Gemini Flash model. `--limit` bounds how many queued notes are processed in that invocation;
discovery still records the accessible remote note set for incremental processing.

The explorer opens at `http://127.0.0.1:8765`. It shows the 300 most recently updated entities,
their evidence-backed relationships, entity filtering, and cited property detail. Use a custom
port or suppress automatic browser launch with:

```bash
granola-kg explore --port 9000 --no-open
```

## Model providers

Gemini is the simplest configuration:

```bash
export GEMINI_API_KEY="YOUR_GEMINI_KEY"
```

OpenAI requires an explicit extraction model:

```bash
export OPENAI_API_KEY="YOUR_OPENAI_KEY"
export GRANOLA_KG_LLM_MODEL="YOUR_STRUCTURED_OUTPUT_MODEL"
```

Any OpenAI-compatible local or hosted endpoint can be selected explicitly:

```bash
export GRANOLA_KG_LLM_PROVIDER="custom"
export GRANOLA_KG_LLM_MODEL="YOUR_MODEL"
export GRANOLA_KG_LLM_BASE_URL="http://127.0.0.1:11434/v1"
export GRANOLA_KG_LLM_API_KEY="OPTIONAL_PROVIDER_KEY"
```

Supported provider values are `gemini`, `openai`, and `custom`. Explicit
`GRANOLA_KG_LLM_MODEL`, `GRANOLA_KG_LLM_BASE_URL`, and `GRANOLA_KG_LLM_API_KEY` values override
provider defaults. The extractor sends a strict JSON schema, so a compatible provider must
support structured chat-completion responses.

Set `GRANOLA_KG_MAX_INPUT_TOKENS` to change the preflight prompt budget (default `6500`). The
client uses the provider-documented four-characters-per-token estimate and rejects oversized
requests before sending them. Provider-reported input, output, cached, and reasoning tokens are
stored with each extraction run.

## Incremental ingestion

The first sync discovers all accessible summarized notes and queues them by Granola `not_` ID.
Processing fetches transcripts only for claimed queue items, materializes their evidence, runs
structured extraction, and applies the graph update transactionally.

Extraction context is summary-first and locally bounded. The summary is always included first;
transcript units are ranked against the title and summary, then added only within a fixed character
budget. Seed and relevant ontology rows are likewise capped, so prompt size does not grow with the
number of retained meetings or generated types.

Later syncs request notes updated after the last safely completed discovery watermark. If a remote
timestamp changes without changing the fetched content, processing completes the job without an
LLM call. Failed and interrupted jobs remain retryable, while explicit `reprocess` commands bypass
the content-hash skip.

Useful workflows:

```bash
# Discover changes and process every retryable job
granola-kg sync

# Discover changes but process at most five queued notes
granola-kg sync --limit 5

# Process jobs already in the local queue without remote discovery
granola-kg process --limit 5

# Perform a full remote listing and hide notes no longer accessible
granola-kg sync --reconcile

# Re-extract one retained note or every retained note
granola-kg reprocess not_EXAMPLE
granola-kg reprocess --all
granola-kg process
```

Use `reprocess` after changing the extraction prompt version or model. Processing refetches the
selected Granola notes and replaces their active graph assertions without a destructive database
rebuild.

## Automatic ontology

The graph starts with universal `meeting` and `person` entity types plus an `attended` relation.
For each meeting, the model receives the current ontology and may propose only additive changes.
The application layer validates those proposals before committing them.

Identity follows two scopes:

- `global`: reusable concepts such as people, organizations, products, and projects. Stable
  identifiers are preferred; otherwise normalized names resolve only when unambiguous.
- `note`: occurrences whose identity belongs to one meeting, such as decisions and action items.

Every property and edge carries an evidence ID pointing to an active summary or transcript unit.
Ontology proposals cannot silently rename or redefine an existing key, and a failed extraction
does not partially update the graph. Common semantic equivalents such as `organization`,
`organisation`, `business`, and `company` are recorded as type aliases and reuse one canonical
type instead of fragmenting the graph.

## Querying from the CLI

```bash
granola-kg search "launch decision"
granola-kg search "Alex" --type person --limit 10
granola-kg entity ENTITY_ID
granola-kg traverse ENTITY_ID --depth 2
```

Results are JSON so they can be piped into scripts. Entity properties include Granola note
metadata and evidence excerpts.

## Local MCP server

Configure any stdio MCP client to run:

```text
granola-kg-mcp
```

Set `GRANOLA_KG_DB` in the MCP process environment when using a non-default database. The server
offers six structured, read-only tools:

| Tool | Purpose |
| --- | --- |
| `search_knowledge` | Search entity names and retained meeting evidence |
| `get_entity` | Get canonical identity, properties, and citations |
| `get_evidence` | Resolve an evidence ID to its meeting excerpt |
| `traverse_graph` | Follow evidence-backed relations up to three hops |
| `list_entity_types` | Inspect seeded and automatically generated ontology types |
| `ingestion_status` | Check the watermark and durable queue counts |

The package also includes an assistant skill that teaches agents to search first and cite graph
evidence. Install it into the default Codex skills directory with:

```bash
granola-kg install
```

Use `granola-kg install --skills-dir PATH` for another client, and `--force` to replace a prior
installation.

## Local storage and privacy

The default database is:

```text
~/.local/share/granola-kg/graph.db
```

Set `GRANOLA_KG_DB` or pass `--db PATH` to choose another location. The database contains meeting
summaries, transcript evidence, extracted entities, and citations, so protect it like the source
notes. API keys are read from environment variables and are never written by the application.

Extraction sends the selected note's title, retained evidence, and current ontology to the model
provider you configure. Granola data otherwise remains in the local SQLite database. The MCP
server is stdio-only and the explorer binds only to `127.0.0.1`.

## Architecture

```text
Granola API ── discovery/watermark ──> durable SQLite queue
                                            │
                                            ▼
                                     note + transcript
                                            │
                                            ▼
structured LLM <── current ontology ── extraction contract
                                            │
                                            ▼
                            transactional graph + evidence
                               │          │          │
                               ▼          ▼          ▼
                              CLI     explorer     MCP server
```

SQLite holds the source-note registry, evidence units, generated ontology, canonical entities,
properties, edges, full-text indexes, extraction provenance, sync watermark, and processing
queue. No external graph database is required.

## Development

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
uv run pytest
uv run python scripts/check_constraints.py
uv build
```

The project enforces strict typing, rejects `Any`, unknown types, and type casts, limits Python
files to 1,000 lines, and keeps feature PRs below 600 changed lines.

## Project status

This is an alpha release. Back up the local database before experimenting with new versions.
Granola API access depends on workspace plan and key scopes; see the
[official Granola API documentation](https://docs.granola.ai/introduction).

Licensed under Apache-2.0. This project is not affiliated with or endorsed by Granola.

---
name: granola-kg
description: Query a local Granola meeting knowledge graph through MCP for notes, people, projects, decisions, action items, topics, and their evidence-backed relationships. Use when answering questions from meeting history, finding supporting transcripts or summaries, tracing related entities, inspecting the generated ontology, or checking whether local Granola ingestion is current.
---

# Granola Knowledge Graph

Use the local MCP tools to answer from retained Granola meeting evidence. Treat the graph as a
source index with provenance, not as permission to invent missing facts.

## Query workflow

1. Call `ingestion_status` when freshness matters. If jobs are pending or failed, disclose that
   the local graph may be incomplete. Ask the user to run `granola-kg sync` when needed.
2. Call `list_entity_types` when the relevant ontology keys are unclear. The ontology evolves
   automatically, so do not assume only seeded types exist.
3. Call `search_knowledge` with concrete names and terms. Add `type_keys` only when narrowing
   entity results helps; evidence results remain available.
4. Call `get_entity` for identifiers, properties, aliases, and citations.
5. Call `traverse_graph` to follow relationships. Keep `max_depth` low unless the user asks for
   broader exploration.
6. Call `get_evidence` for every traversal evidence ID used in the answer.

## Evidence rules

- Ground factual claims in active evidence returned by the tools.
- Prefer entity property citations because they include the note, excerpt, speaker, timestamps,
  and Granola URL.
- For relationship claims, resolve the traversal's `evidence_id` with `get_evidence`.
- Cite the meeting title and web URL when available. Include a short excerpt only when it helps.
- Distinguish explicit graph facts from your synthesis across meetings.
- State when search finds no support or when ingestion status suggests incomplete coverage.

## Common tasks

- Decisions: search for the subject, inspect decision-like entities, then traverse to projects,
  people, meetings, and action items.
- Action items: search by owner or project, inspect status and due-date properties, and cite the
  meeting evidence.
- People and organizations: search identifiers or aliases, then traverse shared projects and
  meetings.
- Themes across meetings: gather multiple evidence results, inspect the connected entities, and
  label the cross-meeting conclusion as synthesis.

Do not run remote ingestion through MCP. Use the explicit `granola-kg sync`,
`granola-kg sync --reconcile`, or `granola-kg reprocess` CLI workflows so mutations remain
visible and intentional.

CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) STRICT;

CREATE TABLE source_notes (
    note_id TEXT PRIMARY KEY,
    title TEXT,
    owner_name TEXT,
    owner_email TEXT NOT NULL,
    web_url TEXT,
    calendar_event_id TEXT,
    scheduled_start_at TEXT,
    scheduled_end_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    content_hash TEXT,
    processed_hash TEXT,
    ontology_revision INTEGER,
    folder_ids_json TEXT NOT NULL DEFAULT '[]',
    raw_json TEXT,
    visibility TEXT NOT NULL DEFAULT 'active'
        CHECK (visibility IN ('active', 'hidden')),
    discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at TEXT,
    FOREIGN KEY (ontology_revision) REFERENCES ontology_revisions(revision)
) STRICT;

CREATE TABLE evidence_units (
    evidence_id TEXT PRIMARY KEY,
    note_id TEXT NOT NULL,
    unit_kind TEXT NOT NULL CHECK (unit_kind IN ('summary', 'transcript')),
    unit_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    speaker_source TEXT,
    speaker_label TEXT,
    speaker_name TEXT,
    started_at TEXT,
    ended_at TEXT,
    content_hash TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (note_id) REFERENCES source_notes(note_id) ON DELETE CASCADE,
    UNIQUE (note_id, unit_kind, unit_index, content_hash)
) STRICT;

CREATE TABLE ontology_revisions (
    revision INTEGER PRIMARY KEY,
    reason TEXT NOT NULL,
    source_note_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_note_id) REFERENCES source_notes(note_id) ON DELETE SET NULL
) STRICT;

CREATE TABLE entity_types (
    type_key TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    description TEXT NOT NULL,
    identity_scope TEXT NOT NULL CHECK (identity_scope IN ('global', 'note')),
    created_revision INTEGER NOT NULL,
    updated_revision INTEGER NOT NULL,
    is_seed INTEGER NOT NULL DEFAULT 0 CHECK (is_seed IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (created_revision) REFERENCES ontology_revisions(revision),
    FOREIGN KEY (updated_revision) REFERENCES ontology_revisions(revision)
) STRICT;

CREATE TABLE field_definitions (
    type_key TEXT NOT NULL,
    field_key TEXT NOT NULL,
    display_name TEXT NOT NULL,
    description TEXT NOT NULL,
    data_type TEXT NOT NULL
        CHECK (data_type IN ('string', 'text', 'url', 'email', 'phone',
                             'number', 'boolean', 'date', 'datetime')),
    is_identifier INTEGER NOT NULL DEFAULT 0 CHECK (is_identifier IN (0, 1)),
    created_revision INTEGER NOT NULL,
    PRIMARY KEY (type_key, field_key),
    FOREIGN KEY (type_key) REFERENCES entity_types(type_key) ON DELETE CASCADE,
    FOREIGN KEY (created_revision) REFERENCES ontology_revisions(revision)
) STRICT;

CREATE TABLE relation_types (
    relation_key TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    description TEXT NOT NULL,
    source_type_key TEXT NOT NULL,
    target_type_key TEXT NOT NULL,
    is_directed INTEGER NOT NULL DEFAULT 1 CHECK (is_directed IN (0, 1)),
    created_revision INTEGER NOT NULL,
    FOREIGN KEY (source_type_key) REFERENCES entity_types(type_key),
    FOREIGN KEY (target_type_key) REFERENCES entity_types(type_key),
    FOREIGN KEY (created_revision) REFERENCES ontology_revisions(revision)
) STRICT;

CREATE TABLE entities (
    entity_id TEXT PRIMARY KEY,
    type_key TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    identity_scope TEXT NOT NULL CHECK (identity_scope IN ('global', 'note')),
    scope_note_id TEXT,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'merged')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (type_key) REFERENCES entity_types(type_key),
    FOREIGN KEY (scope_note_id) REFERENCES source_notes(note_id) ON DELETE CASCADE,
    CHECK ((identity_scope = 'global' AND scope_note_id IS NULL)
        OR (identity_scope = 'note' AND scope_note_id IS NOT NULL))
) STRICT;

CREATE TABLE entity_aliases (
    entity_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    source_evidence_id TEXT,
    PRIMARY KEY (entity_id, normalized_alias),
    FOREIGN KEY (entity_id) REFERENCES entities(entity_id) ON DELETE CASCADE,
    FOREIGN KEY (source_evidence_id) REFERENCES evidence_units(evidence_id)
) STRICT;

CREATE TABLE entity_identifiers (
    entity_id TEXT NOT NULL,
    field_key TEXT NOT NULL,
    raw_value TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    source_evidence_id TEXT NOT NULL,
    PRIMARY KEY (entity_id, field_key, normalized_value, source_evidence_id),
    FOREIGN KEY (entity_id) REFERENCES entities(entity_id) ON DELETE CASCADE,
    FOREIGN KEY (source_evidence_id) REFERENCES evidence_units(evidence_id)
) STRICT;

CREATE TABLE entity_properties (
    property_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    field_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    normalized_value TEXT,
    confidence REAL,
    source_evidence_id TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (entity_id) REFERENCES entities(entity_id) ON DELETE CASCADE,
    FOREIGN KEY (source_evidence_id) REFERENCES evidence_units(evidence_id)
) STRICT;

CREATE TABLE edges (
    edge_id TEXT PRIMARY KEY,
    source_entity_id TEXT NOT NULL,
    relation_key TEXT NOT NULL,
    target_entity_id TEXT NOT NULL,
    confidence REAL,
    source_evidence_id TEXT NOT NULL,
    is_inferred INTEGER NOT NULL DEFAULT 0 CHECK (is_inferred IN (0, 1)),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_entity_id) REFERENCES entities(entity_id) ON DELETE CASCADE,
    FOREIGN KEY (target_entity_id) REFERENCES entities(entity_id) ON DELETE CASCADE,
    FOREIGN KEY (relation_key) REFERENCES relation_types(relation_key),
    FOREIGN KEY (source_evidence_id) REFERENCES evidence_units(evidence_id),
    UNIQUE (source_entity_id, relation_key, target_entity_id, source_evidence_id)
) STRICT;

CREATE TABLE entity_redirects (
    merged_entity_id TEXT PRIMARY KEY,
    survivor_entity_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (merged_entity_id) REFERENCES entities(entity_id),
    FOREIGN KEY (survivor_entity_id) REFERENCES entities(entity_id),
    CHECK (merged_entity_id <> survivor_entity_id)
) STRICT;

CREATE TABLE sync_state (
    source_key TEXT PRIMARY KEY,
    folder_id TEXT,
    watermark TEXT,
    last_started_at TEXT,
    last_completed_at TEXT,
    last_error TEXT
) STRICT;

CREATE TABLE processing_queue (
    note_id TEXT PRIMARY KEY,
    remote_updated_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'processing', 'complete', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    queued_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (note_id) REFERENCES source_notes(note_id) ON DELETE CASCADE
) STRICT;

CREATE TABLE extraction_runs (
    run_id TEXT PRIMARY KEY,
    note_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model_name TEXT NOT NULL,
    ontology_revision INTEGER NOT NULL,
    response_json TEXT,
    state TEXT NOT NULL CHECK (state IN ('running', 'complete', 'failed')),
    error TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    FOREIGN KEY (note_id) REFERENCES source_notes(note_id) ON DELETE CASCADE,
    FOREIGN KEY (ontology_revision) REFERENCES ontology_revisions(revision)
) STRICT;

CREATE VIRTUAL TABLE evidence_fts USING fts5(
    evidence_id UNINDEXED,
    title,
    content,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE VIRTUAL TABLE entity_fts USING fts5(
    entity_id UNINDEXED,
    canonical_name,
    aliases,
    properties,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE INDEX idx_evidence_note_active ON evidence_units(note_id, is_active);
CREATE INDEX idx_entities_type_name ON entities(type_key, normalized_name, status);
CREATE INDEX idx_entities_note_scope ON entities(scope_note_id, type_key, normalized_name);
CREATE INDEX idx_identifiers_lookup ON entity_identifiers(field_key, normalized_value);
CREATE INDEX idx_properties_entity_active ON entity_properties(entity_id, is_active);
CREATE INDEX idx_edges_source_active ON edges(source_entity_id, is_active);
CREATE INDEX idx_edges_target_active ON edges(target_entity_id, is_active);
CREATE INDEX idx_queue_state_updated ON processing_queue(state, updated_at);

INSERT INTO schema_migrations(version, name) VALUES (1, 'initial');
PRAGMA user_version = 1;

CREATE TABLE entity_type_aliases (
    alias_key TEXT PRIMARY KEY,
    canonical_type_key TEXT NOT NULL,
    created_revision INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (canonical_type_key) REFERENCES entity_types(type_key),
    FOREIGN KEY (created_revision) REFERENCES ontology_revisions(revision),
    CHECK (alias_key <> canonical_type_key)
) STRICT;

INSERT INTO schema_migrations(version, name) VALUES (4, 'entity_type_aliases');
PRAGMA user_version = 4;

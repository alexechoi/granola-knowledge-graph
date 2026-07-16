CREATE VIRTUAL TABLE note_fts USING fts5(
    note_id UNINDEXED,
    title,
    summary,
    tokenize = 'unicode61 remove_diacritics 2'
);

INSERT INTO note_fts(note_id, title, summary)
SELECT n.note_id, COALESCE(n.title, ''), COALESCE((
    SELECT ev.content
    FROM evidence_units AS ev
    WHERE ev.note_id = n.note_id
      AND ev.unit_kind = 'summary'
      AND ev.is_active = 1
    ORDER BY ev.unit_index
    LIMIT 1
), '')
FROM source_notes AS n
WHERE n.visibility = 'active';

INSERT INTO schema_migrations(version, name) VALUES (2, 'note_search');
PRAGMA user_version = 2;

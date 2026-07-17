ALTER TABLE processing_queue
ADD COLUMN force_reprocess INTEGER NOT NULL DEFAULT 0 CHECK (force_reprocess IN (0, 1));

ALTER TABLE extraction_runs
ADD COLUMN input_tokens INTEGER NOT NULL DEFAULT 0 CHECK (input_tokens >= 0);

ALTER TABLE extraction_runs
ADD COLUMN output_tokens INTEGER NOT NULL DEFAULT 0 CHECK (output_tokens >= 0);

ALTER TABLE extraction_runs
ADD COLUMN cached_input_tokens INTEGER NOT NULL DEFAULT 0 CHECK (cached_input_tokens >= 0);

ALTER TABLE extraction_runs
ADD COLUMN reasoning_tokens INTEGER NOT NULL DEFAULT 0 CHECK (reasoning_tokens >= 0);

INSERT INTO schema_migrations(version, name) VALUES (3, 'extraction_telemetry');
PRAGMA user_version = 3;

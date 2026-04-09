-- Add API key columns referenced by the agent worker's api_key_loader but
-- never previously migrated. Without these, the worker's SELECT col1, col2,...
-- query fails with PG 42703 ("column does not exist") and ALL api_keys come
-- back empty -- which then trips the cerebras_api_key validator and aborts
-- every job. (Discovered when v2.11.0 added Semrush + Ahrefs abilities.)
--
-- All columns are nullable text and idempotent so this can be applied to
-- databases that already have a subset of them.

ALTER TABLE clients ADD COLUMN IF NOT EXISTS semrush_api_key TEXT;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS ahrefs_api_key TEXT;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS descript_api_key TEXT;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS speechify_api_key TEXT;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS replicate_api_key TEXT;

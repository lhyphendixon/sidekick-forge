-- Add inworld_api_key column to clients table for the new Inworld TTS provider.
-- The agent worker's api_key_loader.py SELECTs each known *_api_key column;
-- without this column, the loader (which uses SELECT *) returns no value and
-- the Inworld TTS branch in entrypoint.py raises ConfigurationError.
--
-- Idempotent: safe to apply on any environment regardless of current state.

ALTER TABLE clients ADD COLUMN IF NOT EXISTS inworld_api_key TEXT;

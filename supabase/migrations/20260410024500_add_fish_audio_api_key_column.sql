-- Add fish_audio_api_key column to clients table for the Fish Audio TTS provider.
-- The agent worker's api_key_loader.py reads each known *_api_key column via
-- SELECT *; without this column the loader returns no value and the Fish Audio
-- TTS branch in entrypoint.py raises ConfigurationError.
--
-- Idempotent: safe to apply on any environment regardless of current state.

ALTER TABLE clients ADD COLUMN IF NOT EXISTS fish_audio_api_key TEXT;

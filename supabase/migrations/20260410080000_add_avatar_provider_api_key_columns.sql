-- Add API key columns for avatar/video providers (Bithuman, Beyond Presence, HeyGen LiveAvatar).
-- These columns were missing, causing the admin UI to silently drop entered keys.
-- Uses ADD COLUMN IF NOT EXISTS for idempotency.

ALTER TABLE clients ADD COLUMN IF NOT EXISTS bithuman_api_secret TEXT;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS bey_api_key TEXT;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS liveavatar_api_key TEXT;

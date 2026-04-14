-- Lore MCP API Keys — static bearer tokens for external MCP clients.
--
-- Each key belongs to a user. When an external MCP client sends
--     Authorization: Bearer slf_lore_<token>
-- the Lore MCP server hashes the token, looks it up, and resolves the user's
-- home client + Supabase target server-side. This means the user_id,
-- target_url, and target_key parameters are removed from the tool schema
-- entirely — external clients cannot impersonate other users or point the
-- MCP at arbitrary Supabase instances.
--
-- This table lives only on the platform Supabase (never on dedicated
-- client instances). Dedicated clients don't need their own API key store —
-- the Lore MCP always resolves auth against the platform.

CREATE TABLE IF NOT EXISTS lore_api_keys (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL,
    name          TEXT NOT NULL DEFAULT 'Unnamed key',
    key_hash      TEXT NOT NULL UNIQUE,
    prefix        TEXT NOT NULL,  -- First ~12 chars of the raw token, for display only
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at  TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS lore_api_keys_user_id_idx ON lore_api_keys (user_id);
CREATE INDEX IF NOT EXISTS lore_api_keys_key_hash_idx ON lore_api_keys (key_hash);
CREATE INDEX IF NOT EXISTS lore_api_keys_active_idx ON lore_api_keys (user_id) WHERE revoked_at IS NULL;

-- RLS: users can see their own keys, service role has full access
ALTER TABLE lore_api_keys ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lore_api_keys_select_own ON lore_api_keys;
CREATE POLICY lore_api_keys_select_own ON lore_api_keys
    FOR SELECT
    USING (auth.uid() = user_id);

DROP POLICY IF EXISTS lore_api_keys_insert_own ON lore_api_keys;
CREATE POLICY lore_api_keys_insert_own ON lore_api_keys
    FOR INSERT
    WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS lore_api_keys_update_own ON lore_api_keys;
CREATE POLICY lore_api_keys_update_own ON lore_api_keys
    FOR UPDATE
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

GRANT SELECT, INSERT, UPDATE ON lore_api_keys TO authenticated;

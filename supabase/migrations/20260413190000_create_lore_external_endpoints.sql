-- Lore external endpoints — users who've exported their Lore as a self-host
-- package and want Sidekick Forge agents to read/write against their own
-- server instead of the platform-hosted Supabase tables.
--
-- The platform stores the user's self-host base URL + Bearer token. When
-- the agent worker or voice interview resolves LoreContext, it checks this
-- table first; if an enabled row exists, all reads/writes proxy to the
-- user's self-host URL via HTTP instead of touching lore_files directly.
--
-- The token is stored in plaintext for now — same security model as the
-- supabase_service_role_key stored alongside each client record. Revisit if
-- we introduce encryption-at-rest for the clients table.

CREATE TABLE IF NOT EXISTS lore_external_endpoints (
    user_id            UUID PRIMARY KEY,              -- one self-host URL per user
    base_url           TEXT NOT NULL,                  -- e.g. https://my-lore.up.railway.app
    auth_token         TEXT NOT NULL,                  -- Bearer token the user set (LORE_AUTH_TOKEN)
    enabled            BOOLEAN NOT NULL DEFAULT TRUE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_tested_at     TIMESTAMPTZ,
    last_tested_status TEXT                            -- 'ok' | 'error:<detail>'
);

CREATE INDEX IF NOT EXISTS lore_external_endpoints_enabled_idx
    ON lore_external_endpoints (user_id) WHERE enabled = TRUE;

ALTER TABLE lore_external_endpoints ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lore_external_endpoints_select_own ON lore_external_endpoints;
CREATE POLICY lore_external_endpoints_select_own ON lore_external_endpoints
    FOR SELECT
    USING (auth.uid() = user_id);

GRANT SELECT ON lore_external_endpoints TO authenticated;

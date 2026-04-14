-- Lore OAuth 2.1 shim — RFC 7591 dynamic client registration + authorization
-- code grant with PKCE. Lets Claude.ai web and other compliant MCP clients
-- connect to the Lore MCP via a browser-based consent flow instead of
-- requiring users to copy/paste API keys.
--
-- The issued access tokens are opaque strings stored here. When the MCP
-- middleware sees an Authorization: Bearer <token>, it first checks
-- lore_api_keys (static keys for power users), then lore_oauth_tokens
-- (OAuth-issued tokens). Either path resolves to a user_id and the same
-- home-Supabase routing logic.

-- ---------------------------------------------------------------------------
-- 1. Registered OAuth clients (RFC 7591 dynamic client registration)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lore_oauth_clients (
    client_id          TEXT PRIMARY KEY,          -- random, no client secret for public clients
    client_name        TEXT,
    redirect_uris      TEXT[] NOT NULL,           -- allowed redirect targets
    grant_types        TEXT[] NOT NULL DEFAULT ARRAY['authorization_code', 'refresh_token'],
    response_types     TEXT[] NOT NULL DEFAULT ARRAY['code'],
    token_endpoint_auth_method TEXT DEFAULT 'none', -- public client (PKCE only)
    scope              TEXT DEFAULT 'lore:read lore:write',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at       TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- 2. Authorization codes — short-lived (10 min) bridge between
--    /authorize and /token endpoints
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lore_oauth_authorization_codes (
    code                  TEXT PRIMARY KEY,
    client_id             TEXT NOT NULL REFERENCES lore_oauth_clients(client_id) ON DELETE CASCADE,
    user_id               UUID NOT NULL,           -- who approved the consent
    redirect_uri          TEXT NOT NULL,
    scope                 TEXT NOT NULL,
    code_challenge        TEXT NOT NULL,           -- PKCE — required
    code_challenge_method TEXT NOT NULL DEFAULT 'S256',
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at            TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '10 minutes'),
    used_at               TIMESTAMPTZ              -- single-use guarantee
);

CREATE INDEX IF NOT EXISTS lore_oauth_codes_expires_idx ON lore_oauth_authorization_codes (expires_at);

-- ---------------------------------------------------------------------------
-- 3. Access + refresh tokens issued by /token
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lore_oauth_tokens (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    access_token_hash  TEXT NOT NULL UNIQUE,        -- SHA-256 of the opaque access token
    refresh_token_hash TEXT UNIQUE,                 -- SHA-256 of the refresh token (nullable)
    client_id          TEXT NOT NULL REFERENCES lore_oauth_clients(client_id) ON DELETE CASCADE,
    user_id            UUID NOT NULL,               -- whose Lore this token grants access to
    scope              TEXT NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at         TIMESTAMPTZ NOT NULL,        -- access token expiry (1 hour)
    refresh_expires_at TIMESTAMPTZ,                 -- refresh token expiry (30 days)
    revoked_at         TIMESTAMPTZ,
    last_used_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS lore_oauth_tokens_access_hash_idx ON lore_oauth_tokens (access_token_hash);
CREATE INDEX IF NOT EXISTS lore_oauth_tokens_refresh_hash_idx ON lore_oauth_tokens (refresh_token_hash) WHERE refresh_token_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS lore_oauth_tokens_user_active_idx ON lore_oauth_tokens (user_id) WHERE revoked_at IS NULL;

-- ---------------------------------------------------------------------------
-- 4. RLS — users can list/revoke their own OAuth tokens via the admin UI,
--    but all writes happen via service role inside the OAuth server
-- ---------------------------------------------------------------------------
ALTER TABLE lore_oauth_clients               ENABLE ROW LEVEL SECURITY;
ALTER TABLE lore_oauth_authorization_codes   ENABLE ROW LEVEL SECURITY;
ALTER TABLE lore_oauth_tokens                ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lore_oauth_tokens_select_own ON lore_oauth_tokens;
CREATE POLICY lore_oauth_tokens_select_own ON lore_oauth_tokens
    FOR SELECT
    USING (auth.uid() = user_id);

DROP POLICY IF EXISTS lore_oauth_tokens_update_own ON lore_oauth_tokens;
CREATE POLICY lore_oauth_tokens_update_own ON lore_oauth_tokens
    FOR UPDATE
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

GRANT SELECT, UPDATE ON lore_oauth_tokens TO authenticated;

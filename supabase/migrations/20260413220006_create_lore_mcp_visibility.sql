-- Lore MCP Visibility — per-user toggles controlling which astrology and
-- personality nodes are exposed to the Lore MCP.
--
-- One row per user; `flags` is a JSONB object keyed by node:
--   { "birth_chart": true, "human_design": true, "mbti": true, "big5": true }
--
-- Semantics: missing key OR missing row = enabled (opt-out default). A key
-- set to `false` hides that node from MCP tool reads and from the Lore
-- summary injection. The node's raw row in lore_astrology / lore_personality
-- is NOT deleted — only its visibility to agent reads is suppressed.

CREATE TABLE IF NOT EXISTS lore_mcp_visibility (
    user_id    UUID PRIMARY KEY,
    flags      JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION lore_mcp_visibility_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS lore_mcp_visibility_updated_at ON lore_mcp_visibility;
CREATE TRIGGER lore_mcp_visibility_updated_at
    BEFORE UPDATE ON lore_mcp_visibility
    FOR EACH ROW
    EXECUTE FUNCTION lore_mcp_visibility_set_updated_at();

ALTER TABLE lore_mcp_visibility ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lore_mcp_visibility_select_own ON lore_mcp_visibility;
CREATE POLICY lore_mcp_visibility_select_own ON lore_mcp_visibility
    FOR SELECT
    USING (auth.uid() = user_id);

DROP POLICY IF EXISTS lore_mcp_visibility_upsert_own ON lore_mcp_visibility;
CREATE POLICY lore_mcp_visibility_upsert_own ON lore_mcp_visibility
    FOR ALL
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON lore_mcp_visibility TO authenticated;

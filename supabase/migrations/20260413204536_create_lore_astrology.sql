-- Lore Astrology — Birth Chart + Human Design storage
--
-- Stores the raw birth chart JSON (astrology-api.io /api/v3/charts/natal) and
-- Human Design bodygraph JSON (astrology-api.io /api/v3/human-design/bodygraph)
-- for each user, alongside an LLM-generated narrative analysis of the HD chart.
--
-- One row per user. Exposed via the Lore MCP tools get_birth_chart and
-- get_human_design so agents can reason over astrological context. A matching
-- migration must be applied to every dedicated client Supabase.
--
-- All statements are idempotent.

CREATE TABLE IF NOT EXISTS lore_astrology (
    user_id                 UUID PRIMARY KEY,
    full_name               TEXT,
    birth_date              DATE NOT NULL,
    birth_time              TIME NOT NULL,
    birth_place             TEXT NOT NULL,
    city                    TEXT,
    country_code            TEXT,
    sun_sign                TEXT,
    hd_type                 TEXT,
    hd_strategy             TEXT,
    hd_authority            TEXT,
    hd_profile              TEXT,
    chart_json              JSONB,
    human_design_json       JSONB,
    human_design_analysis   TEXT,
    analysis_model          TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS lore_astrology_updated_at_idx ON lore_astrology (updated_at DESC);

CREATE OR REPLACE FUNCTION lore_astrology_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS lore_astrology_updated_at ON lore_astrology;
CREATE TRIGGER lore_astrology_updated_at
    BEFORE UPDATE ON lore_astrology
    FOR EACH ROW
    EXECUTE FUNCTION lore_astrology_set_updated_at();

ALTER TABLE lore_astrology ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lore_astrology_select_own ON lore_astrology;
CREATE POLICY lore_astrology_select_own ON lore_astrology
    FOR SELECT
    USING (auth.uid() = user_id);

DROP POLICY IF EXISTS lore_astrology_upsert_own ON lore_astrology;
CREATE POLICY lore_astrology_upsert_own ON lore_astrology
    FOR ALL
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON lore_astrology TO authenticated;

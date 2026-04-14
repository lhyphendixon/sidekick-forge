-- Lore — Personal Context Layer (Usersense 2.0)
--
-- Replaces the legacy user_overviews system with a per-user, per-category
-- markdown context store. Lore belongs to the user, not the sidekick — any
-- sidekick in any session always loads the latest Lore for the current user
-- from the user's home client Supabase.
--
-- This migration creates the three tables on the shared platform Supabase
-- (for Adventurer-tier users and platform-owned content). An equivalent
-- migration will be applied to every Champion/Paragon dedicated Supabase
-- via scripts/apply_lore_schema_to_dedicated.py.
--
-- Tables:
--   - lore_files         (one row per (user_id, category))
--   - lore_summary       (one row per user — compressed summary for prompt injection)
--   - lore_categories    (canonical category registry, ten rows)
--
-- All statements are idempotent.

-- ----------------------------------------------------------------------------
-- 1. lore_categories — canonical registry of the ten Lore categories
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lore_categories (
    category TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);

INSERT INTO lore_categories (category, description, sort_order) VALUES
    ('identity',                    'Name, role, org, philosophy, personal context',                            1),
    ('roles_and_responsibilities',  'Day-to-day work, outputs, decisions, who you serve',                       2),
    ('current_projects',            'Active workstreams, status, priority, KPIs, definition of done',           3),
    ('team_and_relationships',      'Key people, roles, what each relationship requires',                       4),
    ('tools_and_systems',           'Stack, architecture patterns, constraints, design systems',                5),
    ('communication_style',         'Tone, formatting, editing preferences, voice matching notes',              6),
    ('goals_and_priorities',        'Week / quarter / year / career optimization targets',                      7),
    ('preferences_and_constraints', 'Always/never rules, tool preferences, hard constraints',                   8),
    ('domain_knowledge',            'Expertise areas, frameworks used, what NOT to explain',                    9),
    ('decision_log',                'Past decisions and reasoning — how you think',                             10)
ON CONFLICT (category) DO UPDATE
    SET description = EXCLUDED.description,
        sort_order  = EXCLUDED.sort_order;

-- ----------------------------------------------------------------------------
-- 2. lore_files — per-user, per-category markdown content
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lore_files (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL,
    category   TEXT NOT NULL REFERENCES lore_categories(category) ON UPDATE CASCADE,
    content    TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT lore_files_user_category_unique UNIQUE (user_id, category)
);

CREATE INDEX IF NOT EXISTS lore_files_user_id_idx ON lore_files (user_id);
CREATE INDEX IF NOT EXISTS lore_files_updated_at_idx ON lore_files (updated_at DESC);

-- updated_at trigger
CREATE OR REPLACE FUNCTION lore_files_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS lore_files_updated_at ON lore_files;
CREATE TRIGGER lore_files_updated_at
    BEFORE UPDATE ON lore_files
    FOR EACH ROW
    EXECUTE FUNCTION lore_files_set_updated_at();

-- ----------------------------------------------------------------------------
-- 3. lore_summary — compressed per-user summary for system prompt injection
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lore_summary (
    user_id    UUID PRIMARY KEY,
    content    TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION lore_summary_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS lore_summary_updated_at ON lore_summary;
CREATE TRIGGER lore_summary_updated_at
    BEFORE UPDATE ON lore_summary
    FOR EACH ROW
    EXECUTE FUNCTION lore_summary_set_updated_at();

-- ----------------------------------------------------------------------------
-- 4. Row Level Security
-- ----------------------------------------------------------------------------
-- Users can read/write their own Lore rows.
-- Service role bypasses RLS entirely.
-- lore_categories is public-read (static reference data).

ALTER TABLE lore_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE lore_summary ENABLE ROW LEVEL SECURITY;
ALTER TABLE lore_categories ENABLE ROW LEVEL SECURITY;

-- lore_files policies
DROP POLICY IF EXISTS lore_files_select_own ON lore_files;
CREATE POLICY lore_files_select_own ON lore_files
    FOR SELECT
    USING (auth.uid() = user_id);

DROP POLICY IF EXISTS lore_files_insert_own ON lore_files;
CREATE POLICY lore_files_insert_own ON lore_files
    FOR INSERT
    WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS lore_files_update_own ON lore_files;
CREATE POLICY lore_files_update_own ON lore_files
    FOR UPDATE
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS lore_files_delete_own ON lore_files;
CREATE POLICY lore_files_delete_own ON lore_files
    FOR DELETE
    USING (auth.uid() = user_id);

-- lore_summary policies
DROP POLICY IF EXISTS lore_summary_select_own ON lore_summary;
CREATE POLICY lore_summary_select_own ON lore_summary
    FOR SELECT
    USING (auth.uid() = user_id);

DROP POLICY IF EXISTS lore_summary_upsert_own ON lore_summary;
CREATE POLICY lore_summary_upsert_own ON lore_summary
    FOR ALL
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- lore_categories — public read
DROP POLICY IF EXISTS lore_categories_read_all ON lore_categories;
CREATE POLICY lore_categories_read_all ON lore_categories
    FOR SELECT
    USING (true);

-- ----------------------------------------------------------------------------
-- 5. Grants for anon / authenticated roles (service role already has full)
-- ----------------------------------------------------------------------------
GRANT SELECT                         ON lore_categories TO anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON lore_files      TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON lore_summary    TO authenticated;

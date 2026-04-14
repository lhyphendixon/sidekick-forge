-- Lore Depth Scores — LLM-graded quality rubric per node.
--
-- Caches the result of running an LLM rubric against each "lore node":
-- the 10 lore categories plus birth_chart, human_design, mbti, and big5.
--
-- content_hash holds a stable hash of the underlying content at grading time
-- so the next depth-score read can detect stale grades and trigger a regrade.
--
-- All statements are idempotent. Must be applied to every client Supabase.

CREATE TABLE IF NOT EXISTS lore_depth_scores (
    user_id       UUID NOT NULL,
    node_key      TEXT NOT NULL,
    score         SMALLINT NOT NULL DEFAULT 0,
    level         TEXT NOT NULL DEFAULT 'not_captured',
    detail        TEXT,
    content_hash  TEXT,
    graded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, node_key),
    CONSTRAINT lore_depth_scores_score_range CHECK (score BETWEEN 0 AND 3)
);

CREATE INDEX IF NOT EXISTS lore_depth_scores_user_idx ON lore_depth_scores (user_id);

ALTER TABLE lore_depth_scores ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lore_depth_scores_select_own ON lore_depth_scores;
CREATE POLICY lore_depth_scores_select_own ON lore_depth_scores
    FOR SELECT
    USING (auth.uid() = user_id);

DROP POLICY IF EXISTS lore_depth_scores_upsert_own ON lore_depth_scores;
CREATE POLICY lore_depth_scores_upsert_own ON lore_depth_scores
    FOR ALL
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON lore_depth_scores TO authenticated;

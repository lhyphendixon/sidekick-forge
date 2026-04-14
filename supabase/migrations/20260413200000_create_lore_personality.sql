-- Lore personality — Myers-Briggs + Big Five personality traits.
--
-- Separate table per user (one row, user_id PK). Both profiles live together
-- because they answer the same question ("what is this person like?") and
-- the AI-analysis path generates them from the same source material (the
-- user's existing lore categories), so coupling their storage keeps the
-- read path to one query when the sidebar renders.
--
-- Scores are stored as smallint 0–100 rather than float to keep the display
-- lossless (no 0.72352... drift) and make the sidebar render trivial.
-- `*_source` tracks whether the user typed the value themselves or the LLM
-- inferred it — the UI surfaces this so AI-inferred values are labelled.

CREATE TABLE IF NOT EXISTS lore_personality (
    user_id                  UUID PRIMARY KEY,

    -- Myers-Briggs
    mbti_type                TEXT,                    -- e.g. "INTJ", always 4 letters
    mbti_summary             TEXT,                    -- short narrative
    mbti_source              TEXT,                    -- 'manual' | 'ai_analysis'
    mbti_updated_at          TIMESTAMPTZ,

    -- Big Five (OCEAN) — percentile-ish scores 0–100
    big5_openness            SMALLINT,
    big5_conscientiousness   SMALLINT,
    big5_extraversion        SMALLINT,
    big5_agreeableness       SMALLINT,
    big5_neuroticism         SMALLINT,
    big5_summary             TEXT,
    big5_source              TEXT,                    -- 'manual' | 'ai_analysis'
    big5_updated_at          TIMESTAMPTZ,

    analysis_model           TEXT,                    -- LLM that produced ai_analysis values
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT lore_personality_big5_range CHECK (
        (big5_openness          IS NULL OR big5_openness          BETWEEN 0 AND 100) AND
        (big5_conscientiousness IS NULL OR big5_conscientiousness BETWEEN 0 AND 100) AND
        (big5_extraversion      IS NULL OR big5_extraversion      BETWEEN 0 AND 100) AND
        (big5_agreeableness     IS NULL OR big5_agreeableness     BETWEEN 0 AND 100) AND
        (big5_neuroticism       IS NULL OR big5_neuroticism       BETWEEN 0 AND 100)
    )
);

ALTER TABLE lore_personality ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS lore_personality_select_own ON lore_personality;
CREATE POLICY lore_personality_select_own ON lore_personality
    FOR SELECT
    USING (auth.uid() = user_id);

GRANT SELECT ON lore_personality TO authenticated;

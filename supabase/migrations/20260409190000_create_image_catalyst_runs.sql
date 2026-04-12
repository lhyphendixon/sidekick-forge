-- Image Catalyst persistence layer.
--
-- The Image Catalyst ability (app/services/image_catalyst_service.py) calls
-- four RPCs and reads from a table that were never migrated:
--   - image_catalyst_runs            (table)
--   - create_image_catalyst_run      (RPC)
--   - update_image_catalyst_status   (RPC)
--   - save_image_catalyst_result     (RPC)
--   - increment_agent_image_cost     (RPC)
-- and two columns on agent_usage:
--   - image_generation_cost
--   - image_generation_count
--
-- Without these the widget call to /api/v1/image-catalyst/start runs without
-- recording state (PGRST202 in the FastAPI logs), so /status/{run_id} and
-- /costs return empty results. This migration adds them. All statements are
-- idempotent.

-- ----------------------------------------------------------------------------
-- 1. agent_usage columns for image cost/count tracking
-- ----------------------------------------------------------------------------
ALTER TABLE agent_usage
    ADD COLUMN IF NOT EXISTS image_generation_cost NUMERIC(12, 6) NOT NULL DEFAULT 0;

ALTER TABLE agent_usage
    ADD COLUMN IF NOT EXISTS image_generation_count INTEGER NOT NULL DEFAULT 0;

-- ----------------------------------------------------------------------------
-- 2. image_catalyst_runs table
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS image_catalyst_runs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id           UUID NOT NULL,
    agent_id            UUID NOT NULL,
    user_id             UUID,
    conversation_id     UUID,
    session_id          TEXT,
    mode                TEXT NOT NULL,
    prompt              TEXT NOT NULL,
    enriched_prompt     TEXT,
    model_air           TEXT,
    width               INTEGER,
    height              INTEGER,
    seed                BIGINT,
    task_uuid           TEXT,
    output_image_url    TEXT,
    generation_time_ms  NUMERIC,
    cost                NUMERIC(12, 6),
    status              TEXT NOT NULL DEFAULT 'pending',
    error               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_image_catalyst_runs_client_created
    ON image_catalyst_runs (client_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_image_catalyst_runs_agent_created
    ON image_catalyst_runs (agent_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_image_catalyst_runs_status
    ON image_catalyst_runs (status);

-- ----------------------------------------------------------------------------
-- 3. RPC: create_image_catalyst_run
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION create_image_catalyst_run(
    p_client_id        UUID,
    p_agent_id         UUID,
    p_user_id          UUID,
    p_conversation_id  UUID,
    p_session_id       TEXT,
    p_mode             TEXT,
    p_prompt           TEXT,
    p_enriched_prompt  TEXT,
    p_model_air        TEXT,
    p_width            INTEGER,
    p_height           INTEGER
) RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_id UUID;
BEGIN
    INSERT INTO image_catalyst_runs (
        client_id, agent_id, user_id, conversation_id, session_id,
        mode, prompt, enriched_prompt, model_air, width, height,
        status, started_at
    ) VALUES (
        p_client_id, p_agent_id, p_user_id, p_conversation_id, p_session_id,
        p_mode, p_prompt, p_enriched_prompt, p_model_air, p_width, p_height,
        'pending', now()
    )
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$$;

-- ----------------------------------------------------------------------------
-- 4. RPC: update_image_catalyst_status
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION update_image_catalyst_status(
    p_run_id  UUID,
    p_status  TEXT,
    p_error   TEXT DEFAULT NULL
) RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    UPDATE image_catalyst_runs
       SET status       = p_status,
           error        = COALESCE(p_error, error),
           completed_at = CASE
                             WHEN p_status IN ('complete', 'failed')
                                 THEN now()
                             ELSE completed_at
                          END
     WHERE id = p_run_id;

    RETURN FOUND;
END;
$$;

-- ----------------------------------------------------------------------------
-- 5. RPC: save_image_catalyst_result
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION save_image_catalyst_result(
    p_run_id              UUID,
    p_output_image_url    TEXT,
    p_seed                BIGINT,
    p_task_uuid           TEXT,
    p_generation_time_ms  NUMERIC,
    p_cost                NUMERIC
) RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    UPDATE image_catalyst_runs
       SET output_image_url   = p_output_image_url,
           seed               = p_seed,
           task_uuid          = p_task_uuid,
           generation_time_ms = p_generation_time_ms,
           cost               = p_cost,
           status             = 'complete',
           completed_at       = now()
     WHERE id = p_run_id;

    RETURN FOUND;
END;
$$;

-- ----------------------------------------------------------------------------
-- 6. RPC: increment_agent_image_cost
--
-- Atomically bumps image_generation_cost / image_generation_count on the
-- agent_usage row for the current billing period (month). Creates the row
-- if it does not exist. We avoid ON CONFLICT because we cannot assume a
-- unique constraint on (client_id, agent_id, period_start) -- the function
-- locks instead via SELECT ... FOR UPDATE.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION increment_agent_image_cost(
    p_client_id  UUID,
    p_agent_id   UUID,
    p_cost       NUMERIC,
    p_count      INTEGER DEFAULT 1
) RETURNS TABLE (new_cost NUMERIC, new_count INTEGER)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_period   DATE := date_trunc('month', CURRENT_DATE)::date;
    v_existing UUID;
BEGIN
    SELECT id INTO v_existing
      FROM agent_usage
     WHERE client_id    = p_client_id
       AND agent_id     = p_agent_id
       AND period_start = v_period
     FOR UPDATE;

    IF v_existing IS NULL THEN
        INSERT INTO agent_usage (
            client_id, agent_id, period_start,
            image_generation_cost, image_generation_count
        ) VALUES (
            p_client_id, p_agent_id, v_period,
            COALESCE(p_cost, 0), COALESCE(p_count, 0)
        )
        RETURNING image_generation_cost, image_generation_count
            INTO new_cost, new_count;
    ELSE
        UPDATE agent_usage
           SET image_generation_cost  = COALESCE(image_generation_cost, 0) + COALESCE(p_cost, 0),
               image_generation_count = COALESCE(image_generation_count, 0) + COALESCE(p_count, 0),
               updated_at             = now()
         WHERE id = v_existing
        RETURNING image_generation_cost, image_generation_count
            INTO new_cost, new_count;
    END IF;

    RETURN NEXT;
END;
$$;

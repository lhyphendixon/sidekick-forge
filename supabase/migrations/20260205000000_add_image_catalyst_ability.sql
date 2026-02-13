-- Migration: Add Image Catalyst ability for AI image generation
-- Supports two modes: Thumbnail/Promotional (GPT Image 1.5) and General (FLUX.2 Dev)
-- Includes per-client and per-agent cost tracking via Runware API

-- ============================================================================
-- CREATE IMAGE_CATALYST_RUNS TABLE
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.image_catalyst_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL,
    agent_id UUID NOT NULL,
    user_id UUID,
    conversation_id UUID,
    session_id UUID,

    -- Generation mode: 'thumbnail' (GPT Image 1.5) or 'general' (FLUX.2 Dev)
    mode TEXT NOT NULL CHECK (mode IN ('thumbnail', 'general')),

    -- Input
    prompt TEXT NOT NULL,
    enriched_prompt TEXT,  -- Full prompt sent to Runware (includes brand style guide context)
    reference_image_url TEXT,
    reference_image_path TEXT,

    -- Model configuration snapshot
    model_air TEXT NOT NULL,
    width INTEGER DEFAULT 1024,
    height INTEGER DEFAULT 1024,
    quality TEXT,
    steps INTEGER,
    cfg_scale FLOAT,
    strength FLOAT,

    -- Output
    output_image_url TEXT,
    seed BIGINT,
    task_uuid TEXT,
    generation_time_ms FLOAT,

    -- Cost tracking (dollar amount from Runware API)
    cost NUMERIC(10,6) DEFAULT 0,

    -- Status tracking
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'uploading', 'generating', 'complete', 'failed')),
    error TEXT,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- Indexes for image_catalyst_runs
CREATE INDEX IF NOT EXISTS idx_image_catalyst_runs_client ON public.image_catalyst_runs(client_id);
CREATE INDEX IF NOT EXISTS idx_image_catalyst_runs_agent ON public.image_catalyst_runs(agent_id);
CREATE INDEX IF NOT EXISTS idx_image_catalyst_runs_status ON public.image_catalyst_runs(status);
CREATE INDEX IF NOT EXISTS idx_image_catalyst_runs_created ON public.image_catalyst_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_image_catalyst_runs_session ON public.image_catalyst_runs(session_id);

-- RLS policies for image_catalyst_runs
ALTER TABLE public.image_catalyst_runs ENABLE ROW LEVEL SECURITY;

-- Service role has full access
DROP POLICY IF EXISTS "Service role full access to image_catalyst_runs" ON public.image_catalyst_runs;
CREATE POLICY "Service role full access to image_catalyst_runs"
    ON public.image_catalyst_runs
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Authenticated users can view their own runs
DROP POLICY IF EXISTS "Users can view own image_catalyst_runs" ON public.image_catalyst_runs;
CREATE POLICY "Users can view own image_catalyst_runs"
    ON public.image_catalyst_runs
    FOR SELECT
    TO authenticated
    USING (user_id = auth.uid());

-- ============================================================================
-- ADD COST TRACKING COLUMNS TO USAGE TABLES
-- ============================================================================

ALTER TABLE public.agent_usage
    ADD COLUMN IF NOT EXISTS image_generation_cost NUMERIC(10,6) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS image_generation_count INTEGER DEFAULT 0;

ALTER TABLE public.client_usage
    ADD COLUMN IF NOT EXISTS image_generation_cost NUMERIC(10,6) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS image_generation_count INTEGER DEFAULT 0;

-- ============================================================================
-- ATOMIC INCREMENT FOR IMAGE GENERATION COST
-- ============================================================================

CREATE OR REPLACE FUNCTION public.increment_agent_image_cost(
    p_client_id UUID,
    p_agent_id UUID,
    p_cost NUMERIC,
    p_count INTEGER DEFAULT 1
)
RETURNS TABLE (
    new_cost NUMERIC,
    new_count INTEGER
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_period_start DATE;
    v_new_cost NUMERIC;
    v_new_count INTEGER;
BEGIN
    -- Get current billing period (first day of month)
    v_period_start := date_trunc('month', CURRENT_DATE)::DATE;

    -- Get or create usage record with default limits from tier
    -- Cast client_tier ENUM to TEXT for comparison with tier_quotas.tier (VARCHAR)
    INSERT INTO public.agent_usage (
        client_id,
        agent_id,
        period_start,
        voice_seconds_used,
        voice_seconds_limit,
        text_messages_used,
        text_messages_limit,
        embedding_chunks_used,
        embedding_chunks_limit,
        image_generation_cost,
        image_generation_count
    )
    SELECT
        p_client_id,
        p_agent_id,
        v_period_start,
        0,
        COALESCE(tq.voice_seconds_per_month, 6000),
        0,
        COALESCE(tq.text_messages_per_month, 1000),
        0,
        COALESCE(tq.embedding_chunks_per_month, 10000),
        0,
        0
    FROM public.clients c
    LEFT JOIN public.tier_quotas tq ON tq.tier = COALESCE(c.tier::TEXT, 'adventurer')
    WHERE c.id = p_client_id
    ON CONFLICT (client_id, agent_id, period_start) DO NOTHING;

    -- Atomically increment cost and count
    UPDATE public.agent_usage
    SET
        image_generation_cost = image_generation_cost + p_cost,
        image_generation_count = image_generation_count + p_count,
        updated_at = NOW()
    WHERE client_id = p_client_id
      AND agent_id = p_agent_id
      AND period_start = v_period_start
    RETURNING
        image_generation_cost,
        image_generation_count
    INTO v_new_cost, v_new_count;

    RETURN QUERY SELECT v_new_cost, v_new_count;
END;
$$;

GRANT EXECUTE ON FUNCTION public.increment_agent_image_cost(UUID, UUID, NUMERIC, INTEGER)
    TO authenticated, service_role;

COMMENT ON FUNCTION public.increment_agent_image_cost IS 'Atomically increment image generation cost and count for an agent';

-- ============================================================================
-- INSERT IMAGE CATALYST AS A GLOBAL TOOL
-- ============================================================================

INSERT INTO public.tools (
    id,
    name,
    slug,
    description,
    type,
    scope,
    client_id,
    icon_url,
    config,
    enabled,
    execution_phase,
    created_at,
    updated_at
) VALUES (
    gen_random_uuid(),
    'IMAGE CATALYST',
    'image-catalyst',
    'Generate images using AI. Choose Thumbnail/Promotional mode for polished marketing images (GPT Image 1.5) or General mode for creative imagery (FLUX.2 Dev). Upload reference images for guided generation.',
    'image_catalyst',
    'global',
    NULL,
    '/static/images/abilities/image-catalyst.svg',
    '{
        "modes": {
            "thumbnail": {
                "model_air": "openai:4@1",
                "label": "Thumbnail / Promotional",
                "description": "Polished marketing images using GPT Image 1.5",
                "dimensions": ["1024x1024", "1536x1024", "1024x1536"],
                "quality_tiers": ["low", "medium", "high"],
                "supports_reference_images": true
            },
            "general": {
                "model_air": "runware:400@1",
                "label": "General Images",
                "description": "Creative and general imagery using FLUX.2 Dev",
                "default_steps": 28,
                "default_cfg_scale": 3.5,
                "supports_seed_image": true
            }
        },
        "max_reference_image_size_mb": 10,
        "supported_reference_formats": ["png", "jpg", "jpeg", "webp"]
    }'::jsonb,
    true,
    'active',
    NOW(),
    NOW()
) ON CONFLICT (slug) WHERE scope = 'global' AND client_id IS NULL DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    config = EXCLUDED.config,
    icon_url = EXCLUDED.icon_url,
    execution_phase = EXCLUDED.execution_phase,
    updated_at = NOW();

-- ============================================================================
-- HELPER FUNCTIONS FOR IMAGE CATALYST
-- ============================================================================

-- Create a new image catalyst run
CREATE OR REPLACE FUNCTION create_image_catalyst_run(
    p_client_id UUID,
    p_agent_id UUID,
    p_user_id UUID DEFAULT NULL,
    p_conversation_id UUID DEFAULT NULL,
    p_session_id UUID DEFAULT NULL,
    p_mode TEXT DEFAULT 'general',
    p_prompt TEXT DEFAULT '',
    p_enriched_prompt TEXT DEFAULT NULL,
    p_model_air TEXT DEFAULT 'runware:400@1',
    p_width INTEGER DEFAULT 1024,
    p_height INTEGER DEFAULT 1024
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_run_id UUID;
BEGIN
    INSERT INTO public.image_catalyst_runs (
        client_id,
        agent_id,
        user_id,
        conversation_id,
        session_id,
        mode,
        prompt,
        enriched_prompt,
        model_air,
        width,
        height,
        status
    ) VALUES (
        p_client_id,
        p_agent_id,
        p_user_id,
        p_conversation_id,
        p_session_id,
        p_mode,
        p_prompt,
        p_enriched_prompt,
        p_model_air,
        p_width,
        p_height,
        'pending'
    ) RETURNING id INTO v_run_id;

    RETURN v_run_id;
END;
$$;

-- Update image catalyst run status
CREATE OR REPLACE FUNCTION update_image_catalyst_status(
    p_run_id UUID,
    p_status TEXT,
    p_error TEXT DEFAULT NULL
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    UPDATE public.image_catalyst_runs
    SET
        status = p_status,
        error = p_error,
        updated_at = NOW(),
        completed_at = CASE WHEN p_status IN ('complete', 'failed') THEN NOW() ELSE completed_at END
    WHERE id = p_run_id;

    RETURN FOUND;
END;
$$;

-- Save image catalyst generation result
CREATE OR REPLACE FUNCTION save_image_catalyst_result(
    p_run_id UUID,
    p_output_image_url TEXT,
    p_seed BIGINT DEFAULT NULL,
    p_task_uuid TEXT DEFAULT NULL,
    p_generation_time_ms FLOAT DEFAULT NULL,
    p_cost NUMERIC DEFAULT 0
)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    UPDATE public.image_catalyst_runs
    SET
        output_image_url = p_output_image_url,
        seed = p_seed,
        task_uuid = p_task_uuid,
        generation_time_ms = p_generation_time_ms,
        cost = p_cost,
        status = 'complete',
        updated_at = NOW(),
        completed_at = NOW()
    WHERE id = p_run_id;

    RETURN FOUND;
END;
$$;

-- ============================================================================
-- COMMENTS
-- ============================================================================

COMMENT ON TABLE public.image_catalyst_runs IS 'Tracks Image Catalyst AI image generation runs with cost tracking';
COMMENT ON COLUMN public.image_catalyst_runs.mode IS 'Generation mode: thumbnail (GPT Image 1.5) or general (FLUX.2 Dev)';
COMMENT ON COLUMN public.image_catalyst_runs.model_air IS 'Runware AIR model identifier used for generation';
COMMENT ON COLUMN public.image_catalyst_runs.cost IS 'Dollar amount charged by Runware for this generation';
COMMENT ON COLUMN public.image_catalyst_runs.reference_image_url IS 'URL of user-uploaded reference image for guided generation';
COMMENT ON COLUMN public.image_catalyst_runs.enriched_prompt IS 'Full prompt sent to Runware API including appended brand style guide context';

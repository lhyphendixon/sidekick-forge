-- Migration: Fix atomic increment functions for usage tracking
-- Fixes type mismatch between client_tier ENUM and VARCHAR in tier_quotas
-- Date: 2026-02-03

-- Drop and recreate with proper type casting
DROP FUNCTION IF EXISTS public.increment_agent_voice_seconds(UUID, UUID, INTEGER);
DROP FUNCTION IF EXISTS public.increment_agent_text_messages(UUID, UUID, INTEGER);
DROP FUNCTION IF EXISTS public.increment_agent_embedding_chunks(UUID, UUID, INTEGER);

-- ============================================================================
-- Atomic increment for agent voice usage (with type casting fix)
-- ============================================================================
CREATE OR REPLACE FUNCTION public.increment_agent_voice_seconds(
    p_client_id UUID,
    p_agent_id UUID,
    p_seconds INTEGER
)
RETURNS TABLE (
    new_used INTEGER,
    limit_value INTEGER,
    is_exceeded BOOLEAN
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_period_start DATE;
    v_voice_limit INTEGER;
    v_new_used INTEGER;
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
        embedding_chunks_limit
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
        COALESCE(tq.embedding_chunks_per_month, 10000)
    FROM public.clients c
    LEFT JOIN public.tier_quotas tq ON tq.tier = COALESCE(c.tier::TEXT, 'adventurer')
    WHERE c.id = p_client_id
    ON CONFLICT (client_id, agent_id, period_start) DO NOTHING;

    -- Atomically increment and return new values
    UPDATE public.agent_usage
    SET
        voice_seconds_used = voice_seconds_used + p_seconds,
        updated_at = NOW()
    WHERE client_id = p_client_id
      AND agent_id = p_agent_id
      AND period_start = v_period_start
    RETURNING
        voice_seconds_used,
        voice_seconds_limit
    INTO v_new_used, v_voice_limit;

    RETURN QUERY SELECT
        v_new_used,
        v_voice_limit,
        (v_voice_limit > 0 AND v_new_used >= v_voice_limit);
END;
$$;

-- ============================================================================
-- Atomic increment for agent text usage
-- ============================================================================
CREATE OR REPLACE FUNCTION public.increment_agent_text_messages(
    p_client_id UUID,
    p_agent_id UUID,
    p_count INTEGER DEFAULT 1
)
RETURNS TABLE (
    new_used INTEGER,
    limit_value INTEGER,
    is_exceeded BOOLEAN
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_period_start DATE;
    v_text_limit INTEGER;
    v_new_used INTEGER;
BEGIN
    v_period_start := date_trunc('month', CURRENT_DATE)::DATE;

    INSERT INTO public.agent_usage (
        client_id,
        agent_id,
        period_start,
        voice_seconds_used,
        voice_seconds_limit,
        text_messages_used,
        text_messages_limit,
        embedding_chunks_used,
        embedding_chunks_limit
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
        COALESCE(tq.embedding_chunks_per_month, 10000)
    FROM public.clients c
    LEFT JOIN public.tier_quotas tq ON tq.tier = COALESCE(c.tier::TEXT, 'adventurer')
    WHERE c.id = p_client_id
    ON CONFLICT (client_id, agent_id, period_start) DO NOTHING;

    UPDATE public.agent_usage
    SET
        text_messages_used = text_messages_used + p_count,
        updated_at = NOW()
    WHERE client_id = p_client_id
      AND agent_id = p_agent_id
      AND period_start = v_period_start
    RETURNING
        text_messages_used,
        text_messages_limit
    INTO v_new_used, v_text_limit;

    RETURN QUERY SELECT
        v_new_used,
        v_text_limit,
        (v_text_limit > 0 AND v_new_used >= v_text_limit);
END;
$$;

-- ============================================================================
-- Atomic increment for agent embedding usage
-- ============================================================================
CREATE OR REPLACE FUNCTION public.increment_agent_embedding_chunks(
    p_client_id UUID,
    p_agent_id UUID,
    p_chunks INTEGER
)
RETURNS TABLE (
    new_used INTEGER,
    limit_value INTEGER,
    is_exceeded BOOLEAN
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_period_start DATE;
    v_embed_limit INTEGER;
    v_new_used INTEGER;
BEGIN
    v_period_start := date_trunc('month', CURRENT_DATE)::DATE;

    INSERT INTO public.agent_usage (
        client_id,
        agent_id,
        period_start,
        voice_seconds_used,
        voice_seconds_limit,
        text_messages_used,
        text_messages_limit,
        embedding_chunks_used,
        embedding_chunks_limit
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
        COALESCE(tq.embedding_chunks_per_month, 10000)
    FROM public.clients c
    LEFT JOIN public.tier_quotas tq ON tq.tier = COALESCE(c.tier::TEXT, 'adventurer')
    WHERE c.id = p_client_id
    ON CONFLICT (client_id, agent_id, period_start) DO NOTHING;

    UPDATE public.agent_usage
    SET
        embedding_chunks_used = embedding_chunks_used + p_chunks,
        updated_at = NOW()
    WHERE client_id = p_client_id
      AND agent_id = p_agent_id
      AND period_start = v_period_start
    RETURNING
        embedding_chunks_used,
        embedding_chunks_limit
    INTO v_new_used, v_embed_limit;

    RETURN QUERY SELECT
        v_new_used,
        v_embed_limit,
        (v_embed_limit > 0 AND v_new_used >= v_embed_limit);
END;
$$;

-- Grant execute permissions
GRANT EXECUTE ON FUNCTION public.increment_agent_voice_seconds(UUID, UUID, INTEGER) TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.increment_agent_text_messages(UUID, UUID, INTEGER) TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.increment_agent_embedding_chunks(UUID, UUID, INTEGER) TO authenticated, service_role;

COMMENT ON FUNCTION public.increment_agent_voice_seconds IS 'Atomically increment voice seconds for an agent, returns new total and limit';
COMMENT ON FUNCTION public.increment_agent_text_messages IS 'Atomically increment text messages for an agent, returns new total and limit';
COMMENT ON FUNCTION public.increment_agent_embedding_chunks IS 'Atomically increment embedding chunks for an agent, returns new total and limit';

-- Migration: Expand agent_usage with model-level token / character / audio metrics
-- Date: 2026-04-12
-- Purpose: Capture the per-model usage data emitted by LiveKit's session.usage
--          (LLMModelUsage / TTSModelUsage / STTModelUsage) so the admin agent
--          detail page can show real numbers and we can support cost reporting
--          later. The existing voice_seconds_used / text_messages_used columns
--          remain authoritative for quota enforcement.

ALTER TABLE public.agent_usage
    ADD COLUMN IF NOT EXISTS llm_input_tokens BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS llm_output_tokens BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS llm_cached_input_tokens BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS tts_characters BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS tts_audio_seconds NUMERIC(14, 3) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS stt_audio_seconds NUMERIC(14, 3) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS session_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_session_at TIMESTAMPTZ;

-- ============================================================================
-- Atomic roll-up RPC: applied once per session by the agent worker shutdown
-- callback. Reads cumulative session.usage and adds the deltas to the current
-- billing period's row, creating it if missing.
-- ============================================================================
DROP FUNCTION IF EXISTS public.record_agent_session_usage(
    UUID, UUID, INTEGER, BIGINT, BIGINT, BIGINT, BIGINT, NUMERIC, NUMERIC
);

CREATE OR REPLACE FUNCTION public.record_agent_session_usage(
    p_client_id UUID,
    p_agent_id UUID,
    p_voice_seconds INTEGER,
    p_llm_input_tokens BIGINT,
    p_llm_output_tokens BIGINT,
    p_llm_cached_input_tokens BIGINT,
    p_tts_characters BIGINT,
    p_tts_audio_seconds NUMERIC,
    p_stt_audio_seconds NUMERIC
)
RETURNS TABLE (
    voice_seconds_used INTEGER,
    voice_seconds_limit INTEGER
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_period_start DATE;
    v_voice_used INTEGER;
    v_voice_limit INTEGER;
BEGIN
    v_period_start := date_trunc('month', CURRENT_DATE)::DATE;

    -- Ensure the row exists for this period (mirrors the pattern used by
    -- increment_agent_voice_seconds so we pick up tier-based defaults).
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
        voice_seconds_used        = voice_seconds_used        + GREATEST(p_voice_seconds, 0),
        llm_input_tokens          = llm_input_tokens          + GREATEST(p_llm_input_tokens, 0),
        llm_output_tokens         = llm_output_tokens         + GREATEST(p_llm_output_tokens, 0),
        llm_cached_input_tokens   = llm_cached_input_tokens   + GREATEST(p_llm_cached_input_tokens, 0),
        tts_characters            = tts_characters            + GREATEST(p_tts_characters, 0),
        tts_audio_seconds         = tts_audio_seconds         + GREATEST(p_tts_audio_seconds, 0),
        stt_audio_seconds         = stt_audio_seconds         + GREATEST(p_stt_audio_seconds, 0),
        session_count             = session_count + 1,
        last_session_at           = NOW(),
        updated_at                = NOW()
    WHERE client_id = p_client_id
      AND agent_id = p_agent_id
      AND period_start = v_period_start
    RETURNING
        agent_usage.voice_seconds_used,
        agent_usage.voice_seconds_limit
    INTO v_voice_used, v_voice_limit;

    RETURN QUERY SELECT v_voice_used, v_voice_limit;
END;
$$;

GRANT EXECUTE ON FUNCTION public.record_agent_session_usage(
    UUID, UUID, INTEGER, BIGINT, BIGINT, BIGINT, BIGINT, NUMERIC, NUMERIC
) TO authenticated, service_role;

COMMENT ON FUNCTION public.record_agent_session_usage IS
    'Roll up one LiveKit session.usage snapshot into agent_usage. Called once '
    'from the agent worker shutdown callback. Increments voice seconds + '
    'per-model token/character/audio counters atomically and bumps session_count.';

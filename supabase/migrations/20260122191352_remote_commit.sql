

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;


COMMENT ON SCHEMA "public" IS 'standard public schema';



CREATE EXTENSION IF NOT EXISTS "pg_graphql" WITH SCHEMA "graphql";






CREATE EXTENSION IF NOT EXISTS "pg_stat_statements" WITH SCHEMA "extensions";






CREATE EXTENSION IF NOT EXISTS "pgcrypto" WITH SCHEMA "extensions";






CREATE EXTENSION IF NOT EXISTS "supabase_vault" WITH SCHEMA "vault";






CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA "extensions";






CREATE EXTENSION IF NOT EXISTS "vector" WITH SCHEMA "public";






CREATE TYPE "public"."client_tier" AS ENUM (
    'adventurer',
    'champion',
    'paragon'
);


ALTER TYPE "public"."client_tier" OWNER TO "postgres";


CREATE TYPE "public"."hosting_type" AS ENUM (
    'shared',
    'dedicated'
);


ALTER TYPE "public"."hosting_type" OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."check_client_quota"("p_client_id" "uuid", "p_quota_type" character varying) RETURNS TABLE("is_within_quota" boolean, "used" integer, "quota_limit" integer, "remaining" integer, "percent_used" numeric)
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
    v_usage RECORD;
BEGIN
    SELECT * INTO v_usage FROM public.get_client_aggregated_usage(p_client_id);

    IF p_quota_type = 'voice' THEN
        RETURN QUERY SELECT
            (v_usage.voice_limit = 0 OR v_usage.total_voice_seconds < v_usage.voice_limit),
            v_usage.total_voice_seconds,
            v_usage.voice_limit,
            GREATEST(0, v_usage.voice_limit - v_usage.total_voice_seconds),
            v_usage.voice_percent_used;
    ELSIF p_quota_type = 'text' THEN
        RETURN QUERY SELECT
            (v_usage.text_limit = 0 OR v_usage.total_text_messages < v_usage.text_limit),
            v_usage.total_text_messages,
            v_usage.text_limit,
            GREATEST(0, v_usage.text_limit - v_usage.total_text_messages),
            v_usage.text_percent_used;
    ELSIF p_quota_type = 'embedding' THEN
        RETURN QUERY SELECT
            (v_usage.embedding_limit = 0 OR v_usage.total_embedding_chunks < v_usage.embedding_limit),
            v_usage.total_embedding_chunks,
            v_usage.embedding_limit,
            GREATEST(0, v_usage.embedding_limit - v_usage.total_embedding_chunks),
            v_usage.embedding_percent_used;
    END IF;
END;
$$;


ALTER FUNCTION "public"."check_client_quota"("p_client_id" "uuid", "p_quota_type" character varying) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."check_quota"("p_client_id" "uuid", "p_quota_type" character varying) RETURNS TABLE("used" integer, "quota_limit" integer, "remaining" integer, "percent_used" numeric)
    LANGUAGE "plpgsql"
    AS $$
DECLARE
    v_usage_id UUID;
BEGIN
    v_usage_id := get_or_create_usage_record(p_client_id);

    IF p_quota_type = 'voice' THEN
        RETURN QUERY
        SELECT
            voice_seconds_used,
            voice_seconds_limit,
            GREATEST(0, voice_seconds_limit - voice_seconds_used),
            CASE WHEN voice_seconds_limit > 0
                 THEN ROUND((voice_seconds_used::NUMERIC / voice_seconds_limit) * 100, 1)
                 ELSE 0
            END
        FROM client_usage WHERE id = v_usage_id;
    ELSIF p_quota_type = 'text' THEN
        RETURN QUERY
        SELECT
            text_messages_used,
            text_messages_limit,
            GREATEST(0, text_messages_limit - text_messages_used),
            CASE WHEN text_messages_limit > 0
                 THEN ROUND((text_messages_used::NUMERIC / text_messages_limit) * 100, 1)
                 ELSE 0
            END
        FROM client_usage WHERE id = v_usage_id;
    ELSIF p_quota_type = 'embedding' THEN
        RETURN QUERY
        SELECT
            embedding_chunks_used,
            embedding_chunks_limit,
            GREATEST(0, embedding_chunks_limit - embedding_chunks_used),
            CASE WHEN embedding_chunks_limit > 0
                 THEN ROUND((embedding_chunks_used::NUMERIC / embedding_chunks_limit) * 100, 1)
                 ELSE 0
            END
        FROM client_usage WHERE id = v_usage_id;
    END IF;
END;
$$;


ALTER FUNCTION "public"."check_quota"("p_client_id" "uuid", "p_quota_type" character varying) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."claim_next_documentsense_job"() RETURNS TABLE("id" "uuid", "client_id" "uuid", "document_id" bigint, "document_title" "text", "job_type" "text", "chunks_total" integer)
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
  v_job_id UUID;
BEGIN
  -- Claim the oldest pending job
  UPDATE public.documentsense_learning_jobs j
  SET
    status = 'in_progress',
    started_at = NOW(),
    progress_message = 'Starting intelligence extraction...'
  WHERE j.id = (
    SELECT j2.id
    FROM public.documentsense_learning_jobs j2
    WHERE j2.status IN ('pending', 'queued')
    ORDER BY j2.created_at ASC
    LIMIT 1
    FOR UPDATE SKIP LOCKED
  )
  RETURNING j.id INTO v_job_id;

  IF v_job_id IS NULL THEN
    RETURN;
  END IF;

  RETURN QUERY
  SELECT
    j.id,
    j.client_id,
    j.document_id,
    j.document_title,
    j.job_type,
    j.chunks_total
  FROM public.documentsense_learning_jobs j
  WHERE j.id = v_job_id;
END;
$$;


ALTER FUNCTION "public"."claim_next_documentsense_job"() OWNER TO "postgres";


COMMENT ON FUNCTION "public"."claim_next_documentsense_job"() IS 'Claim and start processing the next pending extraction job';



CREATE OR REPLACE FUNCTION "public"."claim_next_learning_job"() RETURNS TABLE("id" "uuid", "client_id" "uuid", "user_id" "uuid", "user_email" "text", "job_type" "text", "agent_ids" "uuid"[], "conversations_total" integer)
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
  v_job_id UUID;
BEGIN
  -- Claim the oldest pending job
  UPDATE public.usersense_learning_jobs j
  SET
    status = 'in_progress',
    started_at = NOW(),
    progress_message = 'Starting learning process...'
  WHERE j.id = (
    SELECT j2.id
    FROM public.usersense_learning_jobs j2
    WHERE j2.status IN ('pending', 'queued')
    ORDER BY j2.created_at ASC
    LIMIT 1
    FOR UPDATE SKIP LOCKED
  )
  RETURNING j.id INTO v_job_id;

  IF v_job_id IS NULL THEN
    RETURN;
  END IF;

  RETURN QUERY
  SELECT
    j.id,
    j.client_id,
    j.user_id,
    j.user_email,
    j.job_type,
    j.agent_ids,
    j.conversations_total
  FROM public.usersense_learning_jobs j
  WHERE j.id = v_job_id;
END;
$$;


ALTER FUNCTION "public"."claim_next_learning_job"() OWNER TO "postgres";


COMMENT ON FUNCTION "public"."claim_next_learning_job"() IS 'Claim and start processing the next pending learning job';



CREATE OR REPLACE FUNCTION "public"."cleanup_expired_pending_checkouts"() RETURNS integer
    LANGUAGE "plpgsql"
    AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    UPDATE pending_checkouts
    SET status = 'expired'
    WHERE status = 'pending'
    AND expires_at < NOW();

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$;


ALTER FUNCTION "public"."cleanup_expired_pending_checkouts"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."complete_documentsense_job"("p_job_id" "uuid", "p_success" boolean, "p_result_summary" "text" DEFAULT NULL::"text", "p_error_message" "text" DEFAULT NULL::"text") RETURNS boolean
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
BEGIN
  UPDATE public.documentsense_learning_jobs
  SET
    status = CASE WHEN p_success THEN 'completed' ELSE 'failed' END,
    progress_percent = CASE WHEN p_success THEN 100 ELSE progress_percent END,
    progress_message = CASE WHEN p_success THEN 'Extraction complete' ELSE 'Extraction failed' END,
    result_summary = p_result_summary,
    error_message = p_error_message,
    completed_at = NOW()
  WHERE id = p_job_id;

  RETURN FOUND;
END;
$$;


ALTER FUNCTION "public"."complete_documentsense_job"("p_job_id" "uuid", "p_success" boolean, "p_result_summary" "text", "p_error_message" "text") OWNER TO "postgres";


COMMENT ON FUNCTION "public"."complete_documentsense_job"("p_job_id" "uuid", "p_success" boolean, "p_result_summary" "text", "p_error_message" "text") IS 'Mark an extraction job as completed or failed';



CREATE OR REPLACE FUNCTION "public"."complete_learning_job"("p_job_id" "uuid", "p_success" boolean, "p_result_summary" "text" DEFAULT NULL::"text", "p_error_message" "text" DEFAULT NULL::"text") RETURNS boolean
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
BEGIN
  UPDATE public.usersense_learning_jobs
  SET
    status = CASE WHEN p_success THEN 'completed' ELSE 'failed' END,
    progress_percent = CASE WHEN p_success THEN 100 ELSE progress_percent END,
    progress_message = CASE WHEN p_success THEN 'Learning complete' ELSE 'Learning failed' END,
    result_summary = p_result_summary,
    error_message = p_error_message,
    completed_at = NOW()
  WHERE id = p_job_id;

  RETURN FOUND;
END;
$$;


ALTER FUNCTION "public"."complete_learning_job"("p_job_id" "uuid", "p_success" boolean, "p_result_summary" "text", "p_error_message" "text") OWNER TO "postgres";


COMMENT ON FUNCTION "public"."complete_learning_job"("p_job_id" "uuid", "p_success" boolean, "p_result_summary" "text", "p_error_message" "text") IS 'Mark a learning job as completed or failed';



CREATE OR REPLACE FUNCTION "public"."create_content_catalyst_run"("p_client_id" "uuid", "p_agent_id" "uuid", "p_user_id" "uuid", "p_conversation_id" "uuid", "p_session_id" "uuid", "p_source_type" "text", "p_source_content" "text", "p_target_word_count" integer, "p_style_prompt" "text", "p_use_perplexity" boolean, "p_use_knowledge_base" boolean) RETURNS "uuid"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
    v_run_id UUID;
BEGIN
    INSERT INTO public.content_catalyst_runs (
        client_id, agent_id, user_id, conversation_id, session_id,
        source_type, source_content, target_word_count, style_prompt,
        use_perplexity, use_knowledge_base, current_phase, status, created_at
    ) VALUES (
        p_client_id, p_agent_id, p_user_id, p_conversation_id, p_session_id,
        p_source_type, p_source_content, COALESCE(p_target_word_count, 1500),
        p_style_prompt, COALESCE(p_use_perplexity, true),
        COALESCE(p_use_knowledge_base, true), 'input', 'pending', NOW()
    )
    RETURNING id INTO v_run_id;
    RETURN v_run_id;
END;
$$;


ALTER FUNCTION "public"."create_content_catalyst_run"("p_client_id" "uuid", "p_agent_id" "uuid", "p_user_id" "uuid", "p_conversation_id" "uuid", "p_session_id" "uuid", "p_source_type" "text", "p_source_content" "text", "p_target_word_count" integer, "p_style_prompt" "text", "p_use_perplexity" boolean, "p_use_knowledge_base" boolean) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."get_client_aggregated_usage"("p_client_id" "uuid", "p_period_start" "date" DEFAULT NULL::"date") RETURNS TABLE("client_id" "uuid", "period_start" "date", "total_voice_seconds" integer, "total_text_messages" integer, "total_embedding_chunks" integer, "voice_limit" integer, "text_limit" integer, "embedding_limit" integer, "voice_percent_used" numeric, "text_percent_used" numeric, "embedding_percent_used" numeric, "agent_count" integer)
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
    v_period DATE;
    v_tier VARCHAR(50);
    v_quota RECORD;
BEGIN
    -- Default to current month if not specified
    v_period := COALESCE(p_period_start, DATE_TRUNC('month', CURRENT_DATE)::DATE);

    -- Get client's tier for limits
    SELECT c.tier INTO v_tier FROM clients c WHERE c.id = p_client_id;

    -- Get tier quotas (these are CLIENT-level limits, not per-agent)
    SELECT * INTO v_quota FROM tier_quotas WHERE tier = COALESCE(v_tier, 'adventurer');

    RETURN QUERY
    SELECT
        p_client_id as client_id,
        v_period as period_start,
        COALESCE(SUM(au.voice_seconds_used), 0)::INTEGER as total_voice_seconds,
        COALESCE(SUM(au.text_messages_used), 0)::INTEGER as total_text_messages,
        COALESCE(SUM(au.embedding_chunks_used), 0)::INTEGER as total_embedding_chunks,
        COALESCE(v_quota.voice_seconds_per_month, 6000)::INTEGER as voice_limit,
        COALESCE(v_quota.text_messages_per_month, 1000)::INTEGER as text_limit,
        COALESCE(v_quota.embedding_chunks_per_month, 10000)::INTEGER as embedding_limit,
        CASE
            WHEN COALESCE(v_quota.voice_seconds_per_month, 6000) > 0
            THEN ROUND((COALESCE(SUM(au.voice_seconds_used), 0)::NUMERIC / v_quota.voice_seconds_per_month) * 100, 1)
            ELSE 0
        END as voice_percent_used,
        CASE
            WHEN COALESCE(v_quota.text_messages_per_month, 1000) > 0
            THEN ROUND((COALESCE(SUM(au.text_messages_used), 0)::NUMERIC / v_quota.text_messages_per_month) * 100, 1)
            ELSE 0
        END as text_percent_used,
        CASE
            WHEN COALESCE(v_quota.embedding_chunks_per_month, 10000) > 0
            THEN ROUND((COALESCE(SUM(au.embedding_chunks_used), 0)::NUMERIC / v_quota.embedding_chunks_per_month) * 100, 1)
            ELSE 0
        END as embedding_percent_used,
        COUNT(DISTINCT au.agent_id)::INTEGER as agent_count
    FROM public.agent_usage au
    WHERE au.client_id = p_client_id
    AND au.period_start = v_period;
END;
$$;


ALTER FUNCTION "public"."get_client_aggregated_usage"("p_client_id" "uuid", "p_period_start" "date") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."get_client_documentsense_status"("p_client_id" "uuid") RETURNS "jsonb"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
  v_in_progress JSONB;
  v_pending INTEGER;
  v_completed INTEGER;
  v_failed INTEGER;
BEGIN
  -- Get counts by status
  SELECT
    COUNT(*) FILTER (WHERE status = 'pending' OR status = 'queued'),
    COUNT(*) FILTER (WHERE status = 'completed'),
    COUNT(*) FILTER (WHERE status = 'failed')
  INTO v_pending, v_completed, v_failed
  FROM public.documentsense_learning_jobs
  WHERE client_id = p_client_id;

  -- Get details of in-progress jobs
  SELECT jsonb_agg(jsonb_build_object(
    'id', id,
    'document_id', document_id,
    'document_title', document_title,
    'progress_percent', progress_percent,
    'progress_message', progress_message,
    'chunks_processed', chunks_processed,
    'chunks_total', chunks_total,
    'started_at', started_at
  ))
  INTO v_in_progress
  FROM public.documentsense_learning_jobs
  WHERE client_id = p_client_id AND status = 'in_progress';

  RETURN jsonb_build_object(
    'pending', COALESCE(v_pending, 0),
    'in_progress', COALESCE(v_in_progress, '[]'::jsonb),
    'completed', COALESCE(v_completed, 0),
    'failed', COALESCE(v_failed, 0),
    'has_active_jobs', (v_pending > 0 OR jsonb_array_length(COALESCE(v_in_progress, '[]'::jsonb)) > 0)
  );
END;
$$;


ALTER FUNCTION "public"."get_client_documentsense_status"("p_client_id" "uuid") OWNER TO "postgres";


COMMENT ON FUNCTION "public"."get_client_documentsense_status"("p_client_id" "uuid") IS 'Get DocumentSense extraction status summary for a client (for admin UI)';



CREATE OR REPLACE FUNCTION "public"."get_client_learning_status"("p_client_id" "uuid") RETURNS "jsonb"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
  v_in_progress JSONB;
  v_pending INTEGER;
  v_completed INTEGER;
  v_failed INTEGER;
BEGIN
  -- Get counts by status
  SELECT
    COUNT(*) FILTER (WHERE status = 'pending' OR status = 'queued'),
    COUNT(*) FILTER (WHERE status = 'completed'),
    COUNT(*) FILTER (WHERE status = 'failed')
  INTO v_pending, v_completed, v_failed
  FROM public.usersense_learning_jobs
  WHERE client_id = p_client_id;

  -- Get details of in-progress jobs
  SELECT jsonb_agg(jsonb_build_object(
    'id', id,
    'user_email', user_email,
    'progress_percent', progress_percent,
    'progress_message', progress_message,
    'conversations_processed', conversations_processed,
    'conversations_total', conversations_total,
    'started_at', started_at
  ))
  INTO v_in_progress
  FROM public.usersense_learning_jobs
  WHERE client_id = p_client_id AND status = 'in_progress';

  RETURN jsonb_build_object(
    'pending', COALESCE(v_pending, 0),
    'in_progress', COALESCE(v_in_progress, '[]'::jsonb),
    'completed', COALESCE(v_completed, 0),
    'failed', COALESCE(v_failed, 0),
    'has_active_jobs', (v_pending > 0 OR jsonb_array_length(COALESCE(v_in_progress, '[]'::jsonb)) > 0)
  );
END;
$$;


ALTER FUNCTION "public"."get_client_learning_status"("p_client_id" "uuid") OWNER TO "postgres";


COMMENT ON FUNCTION "public"."get_client_learning_status"("p_client_id" "uuid") IS 'Get learning status summary for a client (for admin UI)';



CREATE OR REPLACE FUNCTION "public"."get_client_usage_summary"("p_client_id" "uuid", "p_period_start" "date") RETURNS TABLE("total_voice_seconds" integer, "total_text_messages" integer, "total_embedding_chunks" integer, "voice_limit" integer, "text_limit" integer, "embedding_limit" integer)
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
BEGIN
    RETURN QUERY
    SELECT
        COALESCE(SUM(voice_seconds_used), 0)::INTEGER as total_voice_seconds,
        COALESCE(SUM(text_messages_used), 0)::INTEGER as total_text_messages,
        COALESCE(SUM(embedding_chunks_used), 0)::INTEGER as total_embedding_chunks,
        MAX(voice_seconds_limit)::INTEGER as voice_limit,
        MAX(text_messages_limit)::INTEGER as text_limit,
        MAX(embedding_chunks_limit)::INTEGER as embedding_limit
    FROM public.agent_usage
    WHERE client_id = p_client_id
    AND period_start = p_period_start;
END;
$$;


ALTER FUNCTION "public"."get_client_usage_summary"("p_client_id" "uuid", "p_period_start" "date") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."get_conversations_for_learning"("p_user_id" "uuid", "p_agent_ids" "uuid"[], "p_limit" integer DEFAULT 50) RETURNS TABLE("conversation_id" "uuid", "agent_id" "uuid", "message_count" bigint, "first_message" timestamp with time zone, "last_message" timestamp with time zone)
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
BEGIN
  RETURN QUERY
  SELECT ct.conversation_id, ct.agent_id, COUNT(*) as message_count, MIN(ct.created_at) as first_message, MAX(ct.created_at) as last_message
  FROM public.conversation_transcripts ct
  WHERE ct.user_id = p_user_id AND (p_agent_ids IS NULL OR ct.agent_id = ANY(p_agent_ids))
  GROUP BY ct.conversation_id, ct.agent_id
  HAVING COUNT(*) >= 3
  ORDER BY MAX(ct.created_at) DESC
  LIMIT p_limit;
END;
$$;


ALTER FUNCTION "public"."get_conversations_for_learning"("p_user_id" "uuid", "p_agent_ids" "uuid"[], "p_limit" integer) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."get_document_intelligence"("p_client_id" "uuid", "p_document_id" bigint) RETURNS "jsonb"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
  v_result JSONB;
BEGIN
  SELECT jsonb_build_object(
    'id', id,
    'document_id', document_id,
    'client_id', client_id,
    'document_title', document_title,
    'intelligence', intelligence,
    'version', version,
    'extraction_model', extraction_model,
    'extraction_timestamp', extraction_timestamp,
    'chunks_analyzed', chunks_analyzed,
    'updated_at', updated_at
  ) INTO v_result
  FROM public.document_intelligence
  WHERE document_id = p_document_id AND client_id = p_client_id;

  IF v_result IS NULL THEN
    RETURN jsonb_build_object(
      'exists', false,
      'intelligence', jsonb_build_object(
        'summary', '',
        'key_quotes', '[]'::jsonb,
        'themes', '[]'::jsonb,
        'entities', jsonb_build_object(
          'people', '[]'::jsonb,
          'organizations', '[]'::jsonb,
          'locations', '[]'::jsonb,
          'dates', '[]'::jsonb,
          'concepts', '[]'::jsonb
        ),
        'questions_answered', '[]'::jsonb,
        'document_type_inferred', null
      )
    );
  END IF;

  RETURN v_result || jsonb_build_object('exists', true);
END;
$$;


ALTER FUNCTION "public"."get_document_intelligence"("p_client_id" "uuid", "p_document_id" bigint) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."get_or_create_usage_record"("p_client_id" "uuid") RETURNS "uuid"
    LANGUAGE "plpgsql"
    AS $$
DECLARE
    v_usage_id UUID;
    v_period_start DATE;
    v_tier VARCHAR(50);
    v_quota RECORD;
BEGIN
    -- Get first day of current month
    v_period_start := DATE_TRUNC('month', CURRENT_DATE)::DATE;

    -- Try to get existing record
    SELECT id INTO v_usage_id
    FROM client_usage
    WHERE client_id = p_client_id AND period_start = v_period_start;

    -- If not found, create one with tier-appropriate limits
    IF v_usage_id IS NULL THEN
        -- Get client's tier
        SELECT tier INTO v_tier FROM clients WHERE id = p_client_id;

        -- Get quota for tier
        SELECT * INTO v_quota FROM tier_quotas WHERE tier = COALESCE(v_tier, 'adventurer');

        INSERT INTO client_usage (
            client_id,
            period_start,
            voice_seconds_limit,
            text_messages_limit,
            embedding_chunks_limit
        )
        VALUES (
            p_client_id,
            v_period_start,
            COALESCE(v_quota.voice_seconds_per_month, 6000),
            COALESCE(v_quota.text_messages_per_month, 1000),
            COALESCE(v_quota.embedding_chunks_per_month, 10000)
        )
        RETURNING id INTO v_usage_id;
    END IF;

    RETURN v_usage_id;
END;
$$;


ALTER FUNCTION "public"."get_or_create_usage_record"("p_client_id" "uuid") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."get_pending_ambient_runs"("p_limit" integer DEFAULT 10) RETURNS TABLE("id" "uuid", "ability_id" "uuid", "ability_slug" "text", "ability_type" "text", "ability_config" "jsonb", "trigger_config" "jsonb", "client_id" "uuid", "user_id" "uuid", "conversation_id" "uuid", "session_id" "uuid", "trigger_type" "text", "input_context" "jsonb", "notification_message" "text", "created_at" timestamp with time zone)
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
BEGIN
    RETURN QUERY
    SELECT
        r.id,
        r.ability_id,
        t.slug as ability_slug,
        t.type as ability_type,
        t.config as ability_config,
        t.trigger_config,
        r.client_id,
        r.user_id,
        r.conversation_id,
        r.session_id,
        r.trigger_type,
        r.input_context,
        r.notification_message,
        r.created_at
    FROM public.ambient_ability_runs r
    JOIN public.tools t ON t.id = r.ability_id
    WHERE r.status = 'pending'
      AND t.enabled = true
      -- Check if delay_seconds has passed
      AND (
          t.trigger_config->>'delay_seconds' IS NULL
          OR r.created_at + (COALESCE((t.trigger_config->>'delay_seconds')::int, 0) * INTERVAL '1 second') <= NOW()
      )
    ORDER BY r.created_at ASC
    LIMIT p_limit
    FOR UPDATE OF r SKIP LOCKED;
END;
$$;


ALTER FUNCTION "public"."get_pending_ambient_runs"("p_limit" integer) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."get_user_ambient_notifications"("p_user_id" "uuid", "p_client_id" "uuid") RETURNS TABLE("id" "uuid", "ability_slug" "text", "notification_message" "text", "output_result" "jsonb", "completed_at" timestamp with time zone)
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
BEGIN
    RETURN QUERY
    SELECT
        r.id,
        t.slug as ability_slug,
        r.notification_message,
        r.output_result,
        r.completed_at
    FROM public.ambient_ability_runs r
    JOIN public.tools t ON t.id = r.ability_id
    WHERE r.user_id = p_user_id
      AND r.client_id = p_client_id
      AND r.status = 'completed'
      AND r.notification_shown = FALSE
      AND r.notification_message IS NOT NULL
    ORDER BY r.completed_at DESC
    LIMIT 5;
END;
$$;


ALTER FUNCTION "public"."get_user_ambient_notifications"("p_user_id" "uuid", "p_client_id" "uuid") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."get_user_overview"("p_user_id" "uuid", "p_client_id" "uuid") RETURNS "jsonb"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
  v_result JSONB;
BEGIN
  SELECT jsonb_build_object(
    'id', id,
    'user_id', user_id,
    'client_id', client_id,
    'overview', overview,
    'sidekick_insights', COALESCE(sidekick_insights, '{}'::jsonb),
    'learning_status', COALESCE(learning_status, 'none'),
    'learning_progress', COALESCE(learning_progress, 0),
    'conversations_analyzed', COALESCE(conversations_analyzed, 0),
    'version', version,
    'schema_version', COALESCE(schema_version, 1),
    'last_updated_at', updated_at,
    'last_updated_by_agent', last_updated_by_agent,
    'last_updated_reason', last_updated_reason
  ) INTO v_result
  FROM public.user_overviews
  WHERE user_id = p_user_id AND client_id = p_client_id;

  IF v_result IS NULL THEN
    RETURN jsonb_build_object(
      'exists', false,
      'overview', jsonb_build_object('identity', jsonb_build_object(), 'goals', jsonb_build_object(), 'working_style', jsonb_build_object(), 'important_context', jsonb_build_array(), 'relationship_history', jsonb_build_object()),
      'sidekick_insights', jsonb_build_object(),
      'learning_status', 'none',
      'learning_progress', 0,
      'conversations_analyzed', 0
    );
  END IF;

  RETURN v_result || jsonb_build_object('exists', true);
END;
$$;


ALTER FUNCTION "public"."get_user_overview"("p_user_id" "uuid", "p_client_id" "uuid") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."get_user_overview_for_agent"("p_user_id" "uuid", "p_client_id" "uuid", "p_agent_id" "uuid") RETURNS "jsonb"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
  v_overview JSONB;
  v_sidekick_insights JSONB;
  v_my_insights JSONB;
  v_other_insights JSONB;
BEGIN
  SELECT COALESCE(overview, '{}'::jsonb), COALESCE(sidekick_insights, '{}'::jsonb)
  INTO v_overview, v_sidekick_insights
  FROM public.user_overviews
  WHERE user_id = p_user_id AND client_id = p_client_id;

  IF v_overview IS NULL THEN
    v_overview := jsonb_build_object('identity', jsonb_build_object(), 'goals', jsonb_build_object(), 'working_style', jsonb_build_object(), 'important_context', jsonb_build_array(), 'relationship_history', jsonb_build_object());
    v_sidekick_insights := '{}'::jsonb;
  END IF;

  v_my_insights := v_sidekick_insights->p_agent_id::text;

  SELECT jsonb_object_agg(key, jsonb_build_object('agent_name', value->>'agent_name', 'relationship_context', value->>'relationship_context', 'last_updated', value->>'last_updated'))
  INTO v_other_insights
  FROM jsonb_each(v_sidekick_insights)
  WHERE key != p_agent_id::text;

  RETURN jsonb_build_object('shared_understanding', v_overview, 'my_insights', COALESCE(v_my_insights, '{}'::jsonb), 'other_sidekicks', COALESCE(v_other_insights, '{}'::jsonb));
END;
$$;


ALTER FUNCTION "public"."get_user_overview_for_agent"("p_user_id" "uuid", "p_client_id" "uuid", "p_agent_id" "uuid") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."get_users_needing_learning"("p_client_id" "uuid", "p_limit" integer DEFAULT 100) RETURNS TABLE("user_id" "uuid", "conversation_count" bigint)
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
BEGIN
  RETURN QUERY
  SELECT DISTINCT ct.user_id, COUNT(DISTINCT ct.conversation_id) as conversation_count
  FROM public.conversation_transcripts ct
  WHERE ct.user_id IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM public.user_overviews uo WHERE uo.user_id = ct.user_id AND uo.client_id = p_client_id AND uo.learning_status IN ('completed', 'in_progress'))
  GROUP BY ct.user_id
  HAVING COUNT(DISTINCT ct.conversation_id) >= 1
  ORDER BY conversation_count DESC
  LIMIT p_limit;
END;
$$;


ALTER FUNCTION "public"."get_users_needing_learning"("p_client_id" "uuid", "p_limit" integer) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."increment_text_usage"("p_client_id" "uuid", "p_count" integer DEFAULT 1) RETURNS boolean
    LANGUAGE "plpgsql"
    AS $$
DECLARE
    v_usage_id UUID;
    v_current INTEGER;
    v_limit INTEGER;
BEGIN
    v_usage_id := get_or_create_usage_record(p_client_id);

    UPDATE client_usage
    SET text_messages_used = text_messages_used + p_count,
        updated_at = NOW()
    WHERE id = v_usage_id
    RETURNING text_messages_used, text_messages_limit INTO v_current, v_limit;

    -- 0 limit means unlimited
    RETURN v_limit = 0 OR v_current <= v_limit;
END;
$$;


ALTER FUNCTION "public"."increment_text_usage"("p_client_id" "uuid", "p_count" integer) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."increment_voice_usage"("p_client_id" "uuid", "p_seconds" integer) RETURNS boolean
    LANGUAGE "plpgsql"
    AS $$
DECLARE
    v_usage_id UUID;
    v_current INTEGER;
    v_limit INTEGER;
BEGIN
    v_usage_id := get_or_create_usage_record(p_client_id);

    UPDATE client_usage
    SET voice_seconds_used = voice_seconds_used + p_seconds,
        updated_at = NOW()
    WHERE id = v_usage_id
    RETURNING voice_seconds_used, voice_seconds_limit INTO v_current, v_limit;

    -- 0 limit means unlimited
    RETURN v_limit = 0 OR v_current <= v_limit;
END;
$$;


ALTER FUNCTION "public"."increment_voice_usage"("p_client_id" "uuid", "p_seconds" integer) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."mark_ambient_notification_shown"("p_run_id" "uuid") RETURNS boolean
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
BEGIN
    UPDATE public.ambient_ability_runs
    SET notification_shown = TRUE
    WHERE id = p_run_id;

    RETURN FOUND;
END;
$$;


ALTER FUNCTION "public"."mark_ambient_notification_shown"("p_run_id" "uuid") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."match_conversation_transcripts_secure"("query_embeddings" "public"."vector", "agent_slug_param" "text", "user_id_param" "uuid", "match_count" integer DEFAULT 5) RETURNS TABLE("conversation_id" "uuid", "user_message" "text", "agent_response" "text", "similarity" double precision, "created_at" timestamp with time zone)
    LANGUAGE "plpgsql"
    AS $$
begin
  -- Set HNSW search parameter for fast approximate search
  SET LOCAL hnsw.ef_search = 40;

  return query
  select
    u.conversation_id,
    u.content as user_message,
    a.content as agent_response,
    1 - (u.embeddings <=> query_embeddings) as similarity,
    u.created_at
  from public.conversation_transcripts u
  join public.conversation_transcripts a
    on a.conversation_id = u.conversation_id and a.role = 'assistant'
  join public.agents ag on u.agent_id = ag.id
  where u.role = 'user'
    and u.embeddings is not null
    and u.user_id = user_id_param
    and ag.slug = agent_slug_param
  order by u.embeddings <=> query_embeddings
  limit match_count;
end;
$$;


ALTER FUNCTION "public"."match_conversation_transcripts_secure"("query_embeddings" "public"."vector", "agent_slug_param" "text", "user_id_param" "uuid", "match_count" integer) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."match_documents"("p_query_embedding" "public"."vector", "p_agent_slug" "text", "p_match_threshold" double precision, "p_match_count" integer) RETURNS TABLE("id" "text", "document_id" "text", "title" "text", "content" "text", "chunk_index" integer, "source_url" "text", "source_type" "text", "similarity" double precision, "document_title" "text")
    LANGUAGE "plpgsql"
    AS $$
begin
  -- Set HNSW search parameter for fast approximate search
  SET LOCAL hnsw.ef_search = 40;

  return query
  select
    dc.id::text as id,
    dc.document_id::text as document_id,
    coalesce(dc.document_title, d.title, 'Untitled')::text as title,
    dc.content,
    dc.chunk_index,
    coalesce(dc.document_source_url, d.metadata->>'url', d.metadata->>'source_url', '')::text as source_url,
    coalesce(d.file_type, d.document_type, 'document')::text as source_type,
    1 - (dc.embeddings <=> p_query_embedding) as similarity,
    coalesce(dc.document_title, d.title, 'Untitled')::text as document_title
  from public.document_chunks dc
  join public.documents d on dc.document_id = d.id
  join public.agent_documents ad on ad.document_id = d.id
  join public.agents a on ad.agent_id = a.id
  where a.slug = p_agent_slug
    and ad.enabled = true
    and dc.embeddings is not null
    and coalesce(1 - (dc.embeddings <=> p_query_embedding), 0) > p_match_threshold
  order by dc.embeddings <=> p_query_embedding
  limit p_match_count;
end;
$$;


ALTER FUNCTION "public"."match_documents"("p_query_embedding" "public"."vector", "p_agent_slug" "text", "p_match_threshold" double precision, "p_match_count" integer) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."queue_ambient_ability_run"("p_ability_id" "uuid", "p_client_id" "uuid", "p_user_id" "uuid", "p_conversation_id" "uuid", "p_session_id" "uuid", "p_trigger_type" "text", "p_input_context" "jsonb" DEFAULT NULL::"jsonb", "p_notification_message" "text" DEFAULT NULL::"text") RETURNS "uuid"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
    v_run_id UUID;
BEGIN
    INSERT INTO public.ambient_ability_runs (
        ability_id,
        client_id,
        user_id,
        conversation_id,
        session_id,
        trigger_type,
        input_context,
        notification_message,
        status,
        created_at
    ) VALUES (
        p_ability_id,
        p_client_id,
        p_user_id,
        p_conversation_id,
        p_session_id,
        p_trigger_type,
        p_input_context,
        p_notification_message,
        'pending',
        NOW()
    )
    RETURNING id INTO v_run_id;

    RETURN v_run_id;
END;
$$;


ALTER FUNCTION "public"."queue_ambient_ability_run"("p_ability_id" "uuid", "p_client_id" "uuid", "p_user_id" "uuid", "p_conversation_id" "uuid", "p_session_id" "uuid", "p_trigger_type" "text", "p_input_context" "jsonb", "p_notification_message" "text") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."queue_client_documentsense_extraction"("p_client_id" "uuid", "p_document_ids" bigint[] DEFAULT NULL::bigint[]) RETURNS "jsonb"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
  v_jobs_created INTEGER := 0;
BEGIN
  -- If specific document_ids provided, queue those
  -- Otherwise, this is a placeholder - actual document enumeration happens in the worker

  IF p_document_ids IS NOT NULL THEN
    INSERT INTO public.documentsense_learning_jobs (
      client_id,
      document_id,
      job_type,
      status,
      progress_message
    )
    SELECT
      p_client_id,
      unnest(p_document_ids),
      'initial_extraction',
      'pending',
      'Queued for intelligence extraction'
    ON CONFLICT DO NOTHING;

    GET DIAGNOSTICS v_jobs_created = ROW_COUNT;
  ELSE
    -- Queue a placeholder job - worker will enumerate documents
    INSERT INTO public.documentsense_learning_jobs (
      client_id,
      document_id,
      job_type,
      status,
      progress_message
    )
    VALUES (
      p_client_id,
      0,  -- Placeholder, will be updated by worker
      'initial_extraction',
      'pending',
      'Queued for batch extraction'
    );
    v_jobs_created := 1;
  END IF;

  RETURN jsonb_build_object(
    'success', true,
    'jobs_created', v_jobs_created,
    'message', 'DocumentSense extraction jobs queued'
  );
END;
$$;


ALTER FUNCTION "public"."queue_client_documentsense_extraction"("p_client_id" "uuid", "p_document_ids" bigint[]) OWNER TO "postgres";


COMMENT ON FUNCTION "public"."queue_client_documentsense_extraction"("p_client_id" "uuid", "p_document_ids" bigint[]) IS 'Queue DocumentSense extraction for client documents when enabled';



CREATE OR REPLACE FUNCTION "public"."queue_client_initial_learning"("p_client_id" "uuid") RETURNS "jsonb"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
  v_jobs_created INTEGER := 0;
  v_job_id UUID;
BEGIN
  -- This would typically be called after UserSense is enabled for a client
  -- For now, just create a placeholder job for the client
  -- The actual user enumeration happens in the worker

  INSERT INTO public.usersense_learning_jobs (
    client_id,
    user_id,
    job_type,
    status,
    progress_message
  )
  VALUES (
    p_client_id,
    '00000000-0000-0000-0000-000000000000'::uuid,  -- Placeholder, will be updated
    'initial_learning',
    'pending',
    'Queued for initial learning'
  )
  RETURNING id INTO v_job_id;

  RETURN jsonb_build_object(
    'success', true,
    'job_id', v_job_id,
    'message', 'Initial learning job queued'
  );
END;
$$;


ALTER FUNCTION "public"."queue_client_initial_learning"("p_client_id" "uuid") OWNER TO "postgres";


COMMENT ON FUNCTION "public"."queue_client_initial_learning"("p_client_id" "uuid") IS 'Queue initial learning for all users of a client when UserSense is enabled';



CREATE OR REPLACE FUNCTION "public"."save_content_catalyst_articles"("p_run_id" "uuid", "p_article_1" "text", "p_article_2" "text") RETURNS boolean
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
BEGIN
    UPDATE public.content_catalyst_runs
    SET
        article_variation_1 = p_article_1,
        article_variation_2 = p_article_2,
        current_phase = 'complete',
        status = 'completed',
        completed_at = NOW()
    WHERE id = p_run_id;
    
    RETURN FOUND;
END;
$$;


ALTER FUNCTION "public"."save_content_catalyst_articles"("p_run_id" "uuid", "p_article_1" "text", "p_article_2" "text") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."search_document_intelligence"("p_client_id" "uuid", "p_query" "text", "p_limit" integer DEFAULT 10) RETURNS TABLE("document_id" bigint, "document_title" "text", "summary" "text", "key_quotes" "jsonb", "themes" "jsonb", "relevance_score" real)
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
BEGIN
  RETURN QUERY
  SELECT
    di.document_id,
    di.document_title,
    -- Handle double-encoded JSON: if intelligence is a string, parse it first
    CASE
      WHEN jsonb_typeof(di.intelligence) = 'string'
      THEN (di.intelligence#>>'{}')::jsonb->>'summary'
      ELSE di.intelligence->>'summary'
    END as summary,
    CASE
      WHEN jsonb_typeof(di.intelligence) = 'string'
      THEN (di.intelligence#>>'{}')::jsonb->'key_quotes'
      ELSE di.intelligence->'key_quotes'
    END as key_quotes,
    CASE
      WHEN jsonb_typeof(di.intelligence) = 'string'
      THEN (di.intelligence#>>'{}')::jsonb->'themes'
      ELSE di.intelligence->'themes'
    END as themes,
    ts_rank(
      to_tsvector('english', coalesce(di.document_title, '')),
      plainto_tsquery('english', p_query)
    ) as relevance_score
  FROM public.document_intelligence di
  WHERE di.client_id = p_client_id
    AND (
      to_tsvector('english', coalesce(di.document_title, '')) @@ plainto_tsquery('english', p_query)
      OR
      di.document_title ILIKE '%' || p_query || '%'
    )
  ORDER BY relevance_score DESC, di.updated_at DESC
  LIMIT p_limit;
END;
$$;


ALTER FUNCTION "public"."search_document_intelligence"("p_client_id" "uuid", "p_query" "text", "p_limit" integer) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."set_client_asana_connections_updated_at"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;


ALTER FUNCTION "public"."set_client_asana_connections_updated_at"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."set_updated_at"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;


ALTER FUNCTION "public"."set_updated_at"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."trigger_set_timestamp"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
begin
    new.updated_at = now();
    return new;
end;
$$;


ALTER FUNCTION "public"."trigger_set_timestamp"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."update_ambient_run_status"("p_run_id" "uuid", "p_status" "text", "p_output_result" "jsonb" DEFAULT NULL::"jsonb", "p_error" "text" DEFAULT NULL::"text") RETURNS boolean
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
BEGIN
    UPDATE public.ambient_ability_runs
    SET
        status = p_status,
        output_result = COALESCE(p_output_result, output_result),
        error = p_error,
        started_at = CASE WHEN p_status = 'running' THEN NOW() ELSE started_at END,
        completed_at = CASE WHEN p_status IN ('completed', 'failed') THEN NOW() ELSE completed_at END
    WHERE id = p_run_id;

    RETURN FOUND;
END;
$$;


ALTER FUNCTION "public"."update_ambient_run_status"("p_run_id" "uuid", "p_status" "text", "p_output_result" "jsonb", "p_error" "text") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."update_contact_submission_timestamp"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;


ALTER FUNCTION "public"."update_contact_submission_timestamp"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."update_content_catalyst_phase"("p_run_id" "uuid", "p_phase" "text", "p_phase_output" "jsonb" DEFAULT NULL::"jsonb", "p_status" "text" DEFAULT NULL::"text", "p_error" "text" DEFAULT NULL::"text") RETURNS boolean
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
    v_current_phases JSONB;
    v_phase_record JSONB;
BEGIN
    SELECT phases_completed INTO v_current_phases
    FROM public.content_catalyst_runs
    WHERE id = p_run_id;
    
    IF v_current_phases IS NULL THEN
        v_current_phases := '[]'::jsonb;
    END IF;
    
    v_phase_record := jsonb_build_object('phase', p_phase, 'completed_at', NOW());
    
    UPDATE public.content_catalyst_runs
    SET
        current_phase = p_phase,
        phases_completed = v_current_phases || v_phase_record,
        research_output = CASE WHEN p_phase = 'research' THEN COALESCE(p_phase_output, research_output) ELSE research_output END,
        architecture_output = CASE WHEN p_phase = 'architecture' THEN COALESCE(p_phase_output, architecture_output) ELSE architecture_output END,
        draft_output = CASE WHEN p_phase = 'drafting' THEN COALESCE(p_phase_output, draft_output) ELSE draft_output END,
        integrity_output = CASE WHEN p_phase = 'integrity' THEN COALESCE(p_phase_output, integrity_output) ELSE integrity_output END,
        polish_output = CASE WHEN p_phase = 'polishing' THEN COALESCE(p_phase_output, polish_output) ELSE polish_output END,
        status = COALESCE(p_status, status),
        error = p_error,
        started_at = CASE WHEN p_status = 'running' AND started_at IS NULL THEN NOW() ELSE started_at END,
        completed_at = CASE WHEN p_status IN ('completed', 'failed') THEN NOW() ELSE completed_at END
    WHERE id = p_run_id;
    
    RETURN FOUND;
END;
$$;


ALTER FUNCTION "public"."update_content_catalyst_phase"("p_run_id" "uuid", "p_phase" "text", "p_phase_output" "jsonb", "p_status" "text", "p_error" "text") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."update_documentsense_job_progress"("p_job_id" "uuid", "p_progress_percent" integer, "p_progress_message" "text", "p_chunks_processed" integer DEFAULT NULL::integer) RETURNS boolean
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
BEGIN
  UPDATE public.documentsense_learning_jobs
  SET
    progress_percent = p_progress_percent,
    progress_message = p_progress_message,
    chunks_processed = COALESCE(p_chunks_processed, chunks_processed)
  WHERE id = p_job_id;

  RETURN FOUND;
END;
$$;


ALTER FUNCTION "public"."update_documentsense_job_progress"("p_job_id" "uuid", "p_progress_percent" integer, "p_progress_message" "text", "p_chunks_processed" integer) OWNER TO "postgres";


COMMENT ON FUNCTION "public"."update_documentsense_job_progress"("p_job_id" "uuid", "p_progress_percent" integer, "p_progress_message" "text", "p_chunks_processed" integer) IS 'Update progress of an in-progress extraction job';



CREATE OR REPLACE FUNCTION "public"."update_learning_job_progress"("p_job_id" "uuid", "p_progress_percent" integer, "p_progress_message" "text", "p_conversations_processed" integer DEFAULT NULL::integer) RETURNS boolean
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
BEGIN
  UPDATE public.usersense_learning_jobs
  SET
    progress_percent = p_progress_percent,
    progress_message = p_progress_message,
    conversations_processed = COALESCE(p_conversations_processed, conversations_processed)
  WHERE id = p_job_id;

  RETURN FOUND;
END;
$$;


ALTER FUNCTION "public"."update_learning_job_progress"("p_job_id" "uuid", "p_progress_percent" integer, "p_progress_message" "text", "p_conversations_processed" integer) OWNER TO "postgres";


COMMENT ON FUNCTION "public"."update_learning_job_progress"("p_job_id" "uuid", "p_progress_percent" integer, "p_progress_message" "text", "p_conversations_processed" integer) IS 'Update progress of an in-progress learning job';



CREATE OR REPLACE FUNCTION "public"."update_learning_status"("p_user_id" "uuid", "p_client_id" "uuid", "p_status" "text", "p_progress" integer DEFAULT NULL::integer, "p_conversations_analyzed" integer DEFAULT NULL::integer) RETURNS "jsonb"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
BEGIN
  UPDATE public.user_overviews
  SET learning_status = p_status, learning_progress = COALESCE(p_progress, learning_progress), conversations_analyzed = COALESCE(p_conversations_analyzed, conversations_analyzed), updated_at = NOW()
  WHERE user_id = p_user_id AND client_id = p_client_id;

  IF NOT FOUND THEN
    INSERT INTO public.user_overviews (user_id, client_id, learning_status, learning_progress, conversations_analyzed)
    VALUES (p_user_id, p_client_id, p_status, COALESCE(p_progress, 0), COALESCE(p_conversations_analyzed, 0));
  END IF;

  RETURN jsonb_build_object('success', true, 'status', p_status, 'progress', COALESCE(p_progress, 0));
END;
$$;


ALTER FUNCTION "public"."update_learning_status"("p_user_id" "uuid", "p_client_id" "uuid", "p_status" "text", "p_progress" integer, "p_conversations_analyzed" integer) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."update_sidekick_insights"("p_user_id" "uuid", "p_client_id" "uuid", "p_agent_id" "uuid", "p_agent_name" "text", "p_insights" "jsonb", "p_reason" "text" DEFAULT NULL::"text") RETURNS "jsonb"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
  v_overview_id UUID;
  v_current_insights JSONB;
  v_agent_insights JSONB;
  v_new_insights JSONB;
BEGIN
  SELECT id, sidekick_insights INTO v_overview_id, v_current_insights
  FROM public.user_overviews
  WHERE user_id = p_user_id AND client_id = p_client_id
  FOR UPDATE;

  IF v_overview_id IS NULL THEN
    INSERT INTO public.user_overviews (user_id, client_id, sidekick_insights, last_updated_by_agent, last_updated_reason)
    VALUES (p_user_id, p_client_id, jsonb_build_object(p_agent_id::text, p_insights || jsonb_build_object('agent_name', p_agent_name, 'last_updated', NOW())), p_agent_id, COALESCE(p_reason, 'Initial sidekick insights'))
    RETURNING id INTO v_overview_id;
    RETURN jsonb_build_object('success', true, 'overview_id', v_overview_id, 'action', 'created');
  END IF;

  v_current_insights := COALESCE(v_current_insights, '{}'::jsonb);
  v_agent_insights := COALESCE(v_current_insights->p_agent_id::text, '{}'::jsonb);
  v_agent_insights := v_agent_insights || p_insights || jsonb_build_object('agent_name', p_agent_name, 'last_updated', NOW());
  v_new_insights := v_current_insights || jsonb_build_object(p_agent_id::text, v_agent_insights);

  UPDATE public.user_overviews
  SET sidekick_insights = v_new_insights, version = version + 1, last_updated_by_agent = p_agent_id, last_updated_reason = COALESCE(p_reason, 'Updated sidekick insights')
  WHERE id = v_overview_id;

  RETURN jsonb_build_object('success', true, 'overview_id', v_overview_id, 'action', 'updated');
END;
$$;


ALTER FUNCTION "public"."update_sidekick_insights"("p_user_id" "uuid", "p_client_id" "uuid", "p_agent_id" "uuid", "p_agent_name" "text", "p_insights" "jsonb", "p_reason" "text") OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."update_user_overview"("p_user_id" "uuid", "p_client_id" "uuid", "p_section" "text", "p_action" "text", "p_key" "text", "p_value" "text", "p_agent_id" "uuid", "p_reason" "text", "p_expected_version" integer DEFAULT NULL::integer) RETURNS "jsonb"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
  v_overview_id UUID;
  v_current_version INTEGER;
  v_current_overview JSONB;
  v_new_overview JSONB;
  v_section_data JSONB;
BEGIN
  SELECT id, version, overview INTO v_overview_id, v_current_version, v_current_overview
  FROM public.user_overviews
  WHERE user_id = p_user_id AND client_id = p_client_id
  FOR UPDATE;

  IF v_overview_id IS NULL THEN
    INSERT INTO public.user_overviews (user_id, client_id, last_updated_by_agent, last_updated_reason)
    VALUES (p_user_id, p_client_id, p_agent_id, p_reason)
    RETURNING id, version, overview INTO v_overview_id, v_current_version, v_current_overview;
  ELSE
    IF p_expected_version IS NOT NULL AND p_expected_version != v_current_version THEN
      RETURN jsonb_build_object(
        'success', false,
        'error', 'version_conflict',
        'message', 'Overview was modified by another process',
        'current_version', v_current_version
      );
    END IF;
  END IF;

  INSERT INTO public.user_overview_history (overview_id, version, overview, updated_by_agent, update_reason)
  VALUES (v_overview_id, v_current_version, v_current_overview, p_agent_id, p_reason);

  v_section_data := COALESCE(v_current_overview->p_section,
    CASE WHEN p_section = 'important_context' THEN '[]'::jsonb ELSE '{}'::jsonb END
  );

  CASE p_action
    WHEN 'set' THEN
      IF p_section = 'important_context' THEN
        v_section_data := jsonb_build_array(p_value);
      ELSIF p_key IS NOT NULL THEN
        v_section_data := jsonb_set(v_section_data, ARRAY[p_key], to_jsonb(p_value));
      ELSE
        v_section_data := to_jsonb(p_value);
      END IF;
    WHEN 'append' THEN
      IF p_section = 'important_context' THEN
        v_section_data := v_section_data || jsonb_build_array(p_value);
      ELSIF p_key IS NOT NULL THEN
        IF jsonb_typeof(v_section_data->p_key) = 'array' THEN
          v_section_data := jsonb_set(v_section_data, ARRAY[p_key], (v_section_data->p_key) || jsonb_build_array(p_value));
        ELSIF v_section_data->p_key IS NULL THEN
          v_section_data := jsonb_set(v_section_data, ARRAY[p_key], jsonb_build_array(p_value));
        ELSE
          v_section_data := jsonb_set(v_section_data, ARRAY[p_key], jsonb_build_array(v_section_data->>p_key, p_value));
        END IF;
      ELSE
        v_section_data := jsonb_set(v_section_data, ARRAY['notes'], to_jsonb(COALESCE(v_section_data->>'notes', '') || E'
' || p_value));
      END IF;
    WHEN 'remove' THEN
      IF p_section = 'important_context' THEN
        v_section_data := (SELECT COALESCE(jsonb_agg(elem), '[]'::jsonb) FROM jsonb_array_elements(v_section_data) AS elem WHERE elem::text != to_jsonb(p_value)::text);
      ELSIF p_key IS NOT NULL THEN
        v_section_data := v_section_data - p_key;
      END IF;
    ELSE
      RETURN jsonb_build_object('success', false, 'error', 'invalid_action', 'message', 'Action must be set, append, or remove');
  END CASE;

  v_new_overview := jsonb_set(v_current_overview, ARRAY[p_section], v_section_data);

  UPDATE public.user_overviews
  SET overview = v_new_overview, version = v_current_version + 1, last_updated_by_agent = p_agent_id, last_updated_reason = p_reason
  WHERE id = v_overview_id;

  RETURN jsonb_build_object('success', true, 'overview_id', v_overview_id, 'new_version', v_current_version + 1, 'section', p_section, 'action', p_action);
END;
$$;


ALTER FUNCTION "public"."update_user_overview"("p_user_id" "uuid", "p_client_id" "uuid", "p_section" "text", "p_action" "text", "p_key" "text", "p_value" "text", "p_agent_id" "uuid", "p_reason" "text", "p_expected_version" integer) OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."update_wordpress_content_sync_updated_at"() RETURNS "trigger"
    LANGUAGE "plpgsql"
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


ALTER FUNCTION "public"."update_wordpress_content_sync_updated_at"() OWNER TO "postgres";


CREATE OR REPLACE FUNCTION "public"."upsert_document_intelligence"("p_document_id" bigint, "p_client_id" "uuid", "p_document_title" "text", "p_intelligence" "jsonb", "p_extraction_model" "text", "p_chunks_analyzed" integer) RETURNS "jsonb"
    LANGUAGE "plpgsql" SECURITY DEFINER
    AS $$
DECLARE
  v_id UUID;
  v_version INTEGER;
BEGIN
  UPDATE public.document_intelligence
  SET
    intelligence = p_intelligence,
    document_title = COALESCE(p_document_title, document_title),
    extraction_model = p_extraction_model,
    extraction_timestamp = NOW(),
    chunks_analyzed = p_chunks_analyzed,
    version = version + 1
  WHERE document_id = p_document_id AND client_id = p_client_id
  RETURNING id, version INTO v_id, v_version;

  IF v_id IS NULL THEN
    INSERT INTO public.document_intelligence (
      document_id, client_id, document_title, intelligence,
      extraction_model, extraction_timestamp, chunks_analyzed, version
    )
    VALUES (
      p_document_id, p_client_id, p_document_title, p_intelligence,
      p_extraction_model, NOW(), p_chunks_analyzed, 1
    )
    RETURNING id, version INTO v_id, v_version;
  END IF;

  RETURN jsonb_build_object('success', true, 'id', v_id, 'version', v_version);
END;
$$;


ALTER FUNCTION "public"."upsert_document_intelligence"("p_document_id" bigint, "p_client_id" "uuid", "p_document_title" "text", "p_intelligence" "jsonb", "p_extraction_model" "text", "p_chunks_analyzed" integer) OWNER TO "postgres";

SET default_tablespace = '';

SET default_table_access_method = "heap";


CREATE TABLE IF NOT EXISTS "public"."agent_documents" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "agent_id" "uuid" NOT NULL,
    "document_id" "uuid" NOT NULL,
    "access_type" "text" DEFAULT 'read'::"text",
    "enabled" boolean DEFAULT true,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."agent_documents" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."agent_tools" (
    "agent_id" "uuid" NOT NULL,
    "tool_id" "uuid" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."agent_tools" OWNER TO "postgres";


COMMENT ON TABLE "public"."agent_tools" IS 'Assignments of tools to agents (platform-scoped).';



CREATE TABLE IF NOT EXISTS "public"."agent_usage" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "client_id" "uuid" NOT NULL,
    "agent_id" "uuid" NOT NULL,
    "period_start" "date" NOT NULL,
    "voice_seconds_used" integer DEFAULT 0,
    "voice_seconds_limit" integer DEFAULT 6000,
    "text_messages_used" integer DEFAULT 0,
    "text_messages_limit" integer DEFAULT 1000,
    "embedding_chunks_used" integer DEFAULT 0,
    "embedding_chunks_limit" integer DEFAULT 10000,
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"()
);


ALTER TABLE "public"."agent_usage" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."agents" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "slug" "text" NOT NULL,
    "name" "text" NOT NULL,
    "description" "text",
    "system_prompt" "text" NOT NULL,
    "agent_image" "text",
    "voice_settings" "jsonb" DEFAULT '{}'::"jsonb",
    "webhooks" "jsonb" DEFAULT '{}'::"jsonb",
    "tools_config" "jsonb",
    "enabled" boolean DEFAULT true,
    "show_citations" boolean DEFAULT true,
    "rag_results_limit" integer DEFAULT 5,
    "model" "text",
    "context_retention_minutes" integer DEFAULT 30,
    "max_context_messages" integer DEFAULT 50,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "supertab_enabled" boolean DEFAULT false,
    "supertab_experience_id" "text",
    "supertab_price" "text",
    "supertab_cta" "text",
    "voice_chat_enabled" boolean DEFAULT true,
    "text_chat_enabled" boolean DEFAULT true,
    "video_chat_enabled" boolean DEFAULT false,
    "rag_config" "jsonb" DEFAULT '{}'::"jsonb",
    "sound_settings" "jsonb" DEFAULT '{"ambient_sound": "none", "ambient_volume": 0.15, "thinking_sound": "keyboard", "thinking_volume": 0.3}'::"jsonb"
);


ALTER TABLE "public"."agents" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."ambient_ability_runs" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "ability_id" "uuid" NOT NULL,
    "client_id" "uuid" NOT NULL,
    "user_id" "uuid",
    "conversation_id" "uuid",
    "session_id" "uuid",
    "trigger_type" "text" NOT NULL,
    "status" "text" DEFAULT 'pending'::"text" NOT NULL,
    "input_context" "jsonb",
    "output_result" "jsonb",
    "error" "text",
    "notification_shown" boolean DEFAULT false,
    "notification_message" "text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "started_at" timestamp with time zone,
    "completed_at" timestamp with time zone,
    CONSTRAINT "valid_status" CHECK (("status" = ANY (ARRAY['pending'::"text", 'running'::"text", 'completed'::"text", 'failed'::"text"])))
);


ALTER TABLE "public"."ambient_ability_runs" OWNER TO "postgres";


COMMENT ON TABLE "public"."ambient_ability_runs" IS 'Tracks execution of ambient abilities (background processes like UserSense)';



COMMENT ON COLUMN "public"."ambient_ability_runs"."trigger_type" IS 'What triggered this run: post_session, scheduled, manual';



COMMENT ON COLUMN "public"."ambient_ability_runs"."status" IS 'Run status: pending, running, completed, failed';



COMMENT ON COLUMN "public"."ambient_ability_runs"."input_context" IS 'Input data for the ability (transcript, user overview, etc.)';



COMMENT ON COLUMN "public"."ambient_ability_runs"."output_result" IS 'Output from the ability execution';



COMMENT ON COLUMN "public"."ambient_ability_runs"."notification_message" IS 'Message to show user (e.g., "User Understanding Expanded")';



CREATE TABLE IF NOT EXISTS "public"."client_asana_connections" (
    "client_id" "uuid" NOT NULL,
    "access_token" "text" NOT NULL,
    "refresh_token" "text",
    "token_type" "text",
    "expires_at" timestamp with time zone,
    "extra" "jsonb",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."client_asana_connections" OWNER TO "postgres";


COMMENT ON TABLE "public"."client_asana_connections" IS 'Stores OAuth tokens returned from Asana for each Sidekick Forge client.';



COMMENT ON COLUMN "public"."client_asana_connections"."extra" IS 'Raw token payload returned by Asana (JSON).';



CREATE TABLE IF NOT EXISTS "public"."client_provisioning_jobs" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "client_id" "uuid" NOT NULL,
    "job_type" "text" NOT NULL,
    "attempts" integer DEFAULT 0 NOT NULL,
    "claimed_at" timestamp with time zone,
    "last_error" "text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."client_provisioning_jobs" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."client_usage" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "client_id" "uuid" NOT NULL,
    "period_start" "date" NOT NULL,
    "voice_seconds_used" integer DEFAULT 0,
    "voice_seconds_limit" integer DEFAULT 6000,
    "text_messages_used" integer DEFAULT 0,
    "text_messages_limit" integer DEFAULT 1000,
    "llm_tokens_used" bigint DEFAULT 0,
    "llm_tokens_limit" bigint DEFAULT 0,
    "embedding_chunks_used" integer DEFAULT 0,
    "embedding_chunks_limit" integer DEFAULT 10000,
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"(),
    "agent_id" "uuid",
    "billing_period_start" "date",
    "billing_period_end" "date"
);


ALTER TABLE "public"."client_usage" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."clients" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "name" "text" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "supabase_url" "text",
    "supabase_service_role_key" "text",
    "livekit_url" "text",
    "livekit_api_key" "text",
    "livekit_api_secret" "text",
    "openai_api_key" "text",
    "groq_api_key" "text",
    "deepgram_api_key" "text",
    "elevenlabs_api_key" "text",
    "cartesia_api_key" "text",
    "speechify_api_key" "text",
    "deepinfra_api_key" "text",
    "replicate_api_key" "text",
    "novita_api_key" "text",
    "cohere_api_key" "text",
    "siliconflow_api_key" "text",
    "jina_api_key" "text",
    "anthropic_api_key" "text",
    "additional_settings" "jsonb",
    "cerebras_api_key" "text",
    "perplexity_api_key" "text",
    "provisioning_status" "text" DEFAULT 'queued'::"text",
    "provisioning_error" "text",
    "supabase_project_ref" "text",
    "supabase_anon_key" "text",
    "schema_version" "text",
    "provisioning_started_at" timestamp with time zone,
    "provisioning_completed_at" timestamp with time zone,
    "auto_provision" boolean DEFAULT false,
    "usersense_enabled" boolean DEFAULT false NOT NULL,
    "supertab_client_id" "text",
    "firecrawl_api_key" "text",
    "content_catalyst_enabled" boolean DEFAULT false NOT NULL,
    "tier" "public"."client_tier" DEFAULT 'champion'::"public"."client_tier",
    "hosting_type" "public"."hosting_type" DEFAULT 'dedicated'::"public"."hosting_type",
    "max_sidekicks" integer,
    "tier_features" "jsonb" DEFAULT '{}'::"jsonb",
    "owner_user_id" "uuid",
    "owner_email" "text",
    "default_api_config" "jsonb" DEFAULT '{}'::"jsonb",
    "uses_platform_keys" boolean,
    "stripe_customer_id" character varying(255),
    "stripe_subscription_id" character varying(255),
    "subscription_status" character varying(50) DEFAULT 'none'::character varying,
    "subscription_current_period_start" timestamp with time zone,
    "subscription_current_period_end" timestamp with time zone,
    "subscription_canceled_at" timestamp with time zone,
    "subscription_cancel_at_period_end" boolean DEFAULT false,
    "documentsense_enabled" boolean DEFAULT false
);


ALTER TABLE "public"."clients" OWNER TO "postgres";


COMMENT ON TABLE "public"."clients" IS 'Stores configuration and encrypted credentials for each tenant of the Sidekick Forge platform.';



COMMENT ON COLUMN "public"."clients"."usersense_enabled" IS 'Whether UserSense ambient ability is enabled for this client';



COMMENT ON COLUMN "public"."clients"."firecrawl_api_key" IS 'API key for Firecrawl web scraping service';



COMMENT ON COLUMN "public"."clients"."owner_user_id" IS 'Supabase Auth user ID who owns/purchased this client';



COMMENT ON COLUMN "public"."clients"."owner_email" IS 'Email of the owner (denormalized for convenience)';



COMMENT ON COLUMN "public"."clients"."stripe_customer_id" IS 'Stripe Customer ID for this client/workspace';



COMMENT ON COLUMN "public"."clients"."stripe_subscription_id" IS 'Active Stripe Subscription ID';



COMMENT ON COLUMN "public"."clients"."subscription_status" IS 'Current subscription status: none, active, past_due, canceled, unpaid, trialing';



COMMENT ON COLUMN "public"."clients"."subscription_cancel_at_period_end" IS 'If true, subscription will cancel at end of current period';



CREATE TABLE IF NOT EXISTS "public"."contact_submissions" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "first_name" "text",
    "last_name" "text",
    "full_name" "text",
    "email" "text" NOT NULL,
    "company" "text",
    "phone_number" "text",
    "country_code" "text" DEFAULT 'US'::"text",
    "message" "text",
    "business_name" "text",
    "stage" "text",
    "use_case" "text",
    "submission_type" "text" DEFAULT 'contact'::"text" NOT NULL,
    "status" "text" DEFAULT 'new'::"text",
    "assigned_to" "uuid",
    "priority" "text" DEFAULT 'normal'::"text",
    "notes" "text",
    "ip_address" "inet",
    "user_agent" "text",
    "referrer" "text",
    "utm_source" "text",
    "utm_medium" "text",
    "utm_campaign" "text",
    "utm_term" "text",
    "utm_content" "text",
    "first_contact_at" timestamp with time zone,
    "last_contact_at" timestamp with time zone,
    "contact_count" integer DEFAULT 0,
    CONSTRAINT "valid_priority" CHECK (("priority" = ANY (ARRAY['low'::"text", 'normal'::"text", 'high'::"text", 'urgent'::"text"]))),
    CONSTRAINT "valid_status" CHECK (("status" = ANY (ARRAY['new'::"text", 'contacted'::"text", 'qualified'::"text", 'converted'::"text", 'spam'::"text", 'archived'::"text"]))),
    CONSTRAINT "valid_submission_type" CHECK (("submission_type" = ANY (ARRAY['contact'::"text", 'demo'::"text", 'early_access'::"text"])))
);


ALTER TABLE "public"."contact_submissions" OWNER TO "postgres";


COMMENT ON TABLE "public"."contact_submissions" IS 'Stores all marketing form submissions including contact forms, demo requests, and early access signups';



COMMENT ON COLUMN "public"."contact_submissions"."stage" IS 'Business stage for early access signups';



COMMENT ON COLUMN "public"."contact_submissions"."submission_type" IS 'Type of form submission: contact, demo, or early_access';



COMMENT ON COLUMN "public"."contact_submissions"."status" IS 'Lead status for sales pipeline tracking';



CREATE TABLE IF NOT EXISTS "public"."content_catalyst_runs" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "client_id" "uuid" NOT NULL,
    "agent_id" "uuid",
    "user_id" "uuid",
    "conversation_id" "uuid",
    "session_id" "uuid",
    "source_type" "text" NOT NULL,
    "source_content" "text",
    "target_word_count" integer DEFAULT 1500 NOT NULL,
    "style_prompt" "text",
    "use_perplexity" boolean DEFAULT true NOT NULL,
    "use_knowledge_base" boolean DEFAULT true NOT NULL,
    "current_phase" "text" DEFAULT 'input'::"text" NOT NULL,
    "phases_completed" "jsonb" DEFAULT '[]'::"jsonb" NOT NULL,
    "research_output" "jsonb",
    "architecture_output" "jsonb",
    "draft_output" "jsonb",
    "integrity_output" "jsonb",
    "polish_output" "jsonb",
    "article_variation_1" "text",
    "article_variation_2" "text",
    "status" "text" DEFAULT 'pending'::"text" NOT NULL,
    "error" "text",
    "word_count_iterations" integer DEFAULT 0 NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "started_at" timestamp with time zone,
    "completed_at" timestamp with time zone,
    CONSTRAINT "valid_phase" CHECK (("current_phase" = ANY (ARRAY['input'::"text", 'research'::"text", 'architecture'::"text", 'drafting'::"text", 'integrity'::"text", 'polishing'::"text", 'complete'::"text"]))),
    CONSTRAINT "valid_source_type" CHECK (("source_type" = ANY (ARRAY['mp3'::"text", 'url'::"text", 'text'::"text", 'document'::"text"]))),
    CONSTRAINT "valid_status" CHECK (("status" = ANY (ARRAY['pending'::"text", 'running'::"text", 'completed'::"text", 'failed'::"text"])))
);


ALTER TABLE "public"."content_catalyst_runs" OWNER TO "postgres";


COMMENT ON COLUMN "public"."content_catalyst_runs"."source_type" IS 'Type of source input: mp3 (audio transcription), url (web page), text (direct input), document (knowledge base document)';



CREATE TABLE IF NOT EXISTS "public"."conversation_summaries" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "conversation_id" "uuid" NOT NULL,
    "agent_id" "uuid",
    "user_id" "uuid",
    "summary" "text",
    "key_points" "jsonb" DEFAULT '[]'::"jsonb",
    "sentiment" "text",
    "topics" "jsonb" DEFAULT '[]'::"jsonb",
    "metadata" "jsonb" DEFAULT '{}'::"jsonb",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."conversation_summaries" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."conversation_transcripts" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "conversation_id" "uuid",
    "session_id" "uuid",
    "agent_id" "uuid",
    "user_id" "uuid",
    "role" "text",
    "content" "text",
    "transcript" "text",
    "turn_id" "uuid",
    "citations" "jsonb" DEFAULT '[]'::"jsonb",
    "metadata" "jsonb" DEFAULT '{}'::"jsonb",
    "source" "text",
    "embeddings" "public"."vector"(1024),
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "sequence" integer,
    "user_message" "text",
    "assistant_message" "text"
);


ALTER TABLE "public"."conversation_transcripts" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."conversations" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "session_id" "uuid",
    "agent_id" "uuid",
    "user_id" "uuid",
    "metadata" "jsonb" DEFAULT '{}'::"jsonb",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "channel" "text"
);


ALTER TABLE "public"."conversations" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."document_chunks" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "document_id" "uuid",
    "chunk_index" integer,
    "content" "text",
    "embeddings" "public"."vector"(1024),
    "embeddings_vec" "public"."vector"(1024),
    "chunk_metadata" "jsonb" DEFAULT '{}'::"jsonb",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "document_title" "text",
    "document_source_url" "text"
);


ALTER TABLE "public"."document_chunks" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."document_intelligence" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "document_id" "uuid" NOT NULL,
    "client_id" "uuid" NOT NULL,
    "intelligence" "jsonb" DEFAULT '{"themes": [], "summary": "", "entities": {"dates": [], "people": [], "concepts": [], "locations": [], "organizations": []}, "key_quotes": [], "questions_answered": [], "document_type_inferred": null}'::"jsonb" NOT NULL,
    "extraction_model" "text",
    "extraction_timestamp" timestamp with time zone,
    "chunks_analyzed" integer DEFAULT 0,
    "document_title" "text",
    "version" integer DEFAULT 1 NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."document_intelligence" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."documents" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "uuid",
    "agent_id" "uuid",
    "title" "text",
    "filename" "text",
    "file_name" "text",
    "file_size" bigint,
    "file_type" "text",
    "content" "text",
    "status" "text" DEFAULT 'pending'::"text",
    "upload_status" "text" DEFAULT 'pending'::"text",
    "processing_status" "text" DEFAULT 'pending'::"text",
    "document_type" "text" DEFAULT 'knowledge_base'::"text",
    "chunk_count" integer DEFAULT 0,
    "word_count" integer,
    "metadata" "jsonb" DEFAULT '{}'::"jsonb",
    "processing_metadata" "jsonb" DEFAULT '{}'::"jsonb",
    "embedding" "public"."vector"(1024),
    "embedding_vec" "public"."vector"(1024),
    "embeddings" "public"."vector"(1024),
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."documents" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."documentsense_learning_jobs" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "client_id" "uuid" NOT NULL,
    "document_id" bigint NOT NULL,
    "document_title" "text",
    "job_type" "text" DEFAULT 'initial_extraction'::"text" NOT NULL,
    "status" "text" DEFAULT 'pending'::"text" NOT NULL,
    "progress_percent" integer DEFAULT 0,
    "progress_message" "text",
    "chunks_total" integer DEFAULT 0,
    "chunks_processed" integer DEFAULT 0,
    "result_summary" "text",
    "error_message" "text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "queued_at" timestamp with time zone,
    "started_at" timestamp with time zone,
    "completed_at" timestamp with time zone,
    CONSTRAINT "valid_ds_job_status" CHECK (("status" = ANY (ARRAY['pending'::"text", 'queued'::"text", 'in_progress'::"text", 'completed'::"text", 'failed'::"text"]))),
    CONSTRAINT "valid_ds_job_type" CHECK (("job_type" = ANY (ARRAY['initial_extraction'::"text", 'refresh'::"text"])))
);


ALTER TABLE "public"."documentsense_learning_jobs" OWNER TO "postgres";


COMMENT ON TABLE "public"."documentsense_learning_jobs" IS 'Tracks DocumentSense intelligence extraction jobs for processing document content';



CREATE TABLE IF NOT EXISTS "public"."email_verification_tokens" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "token" "text" NOT NULL,
    "email" "text" NOT NULL,
    "expires_at" timestamp with time zone NOT NULL,
    "used_at" timestamp with time zone,
    "order_id" "uuid"
);


ALTER TABLE "public"."email_verification_tokens" OWNER TO "postgres";


COMMENT ON TABLE "public"."email_verification_tokens" IS 'Stores email verification tokens for account activation';



COMMENT ON COLUMN "public"."email_verification_tokens"."token" IS 'Secure random token sent in verification email';



COMMENT ON COLUMN "public"."email_verification_tokens"."expires_at" IS 'Token expiration time (typically 24 hours)';



COMMENT ON COLUMN "public"."email_verification_tokens"."used_at" IS 'Timestamp when token was used (NULL if unused)';



CREATE TABLE IF NOT EXISTS "public"."livekit_events" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "event_type" character varying(50) NOT NULL,
    "room_name" character varying(255),
    "room_sid" character varying(255),
    "duration" integer,
    "participant_sid" character varying(255),
    "participant_identity" character varying(255),
    "metadata" "jsonb" DEFAULT '{}'::"jsonb",
    "created_at" timestamp with time zone DEFAULT "now"()
);


ALTER TABLE "public"."livekit_events" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."orders" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "order_number" "text" NOT NULL,
    "user_id" "uuid" NOT NULL,
    "email" "text" NOT NULL,
    "first_name" "text",
    "last_name" "text",
    "company" "text",
    "tier" "public"."client_tier" NOT NULL,
    "price_cents" integer NOT NULL,
    "currency" "text" DEFAULT 'USD'::"text",
    "payment_status" "text" DEFAULT 'pending'::"text" NOT NULL,
    "payment_method" "text",
    "payment_provider" "text",
    "payment_provider_id" "text",
    "client_id" "uuid",
    "ip_address" "inet",
    "user_agent" "text",
    "referrer" "text",
    "utm_source" "text",
    "utm_medium" "text",
    "utm_campaign" "text",
    "paid_at" timestamp with time zone,
    "activated_at" timestamp with time zone,
    CONSTRAINT "valid_payment_status" CHECK (("payment_status" = ANY (ARRAY['pending'::"text", 'completed'::"text", 'failed'::"text", 'refunded'::"text"])))
);


ALTER TABLE "public"."orders" OWNER TO "postgres";


COMMENT ON TABLE "public"."orders" IS 'Stores checkout orders linking users to their subscriptions';



COMMENT ON COLUMN "public"."orders"."order_number" IS 'Human-readable order reference (ORD-XXXXXXXX)';



COMMENT ON COLUMN "public"."orders"."user_id" IS 'Supabase Auth user ID who placed the order';



COMMENT ON COLUMN "public"."orders"."tier" IS 'Subscription tier purchased';



COMMENT ON COLUMN "public"."orders"."price_cents" IS 'Order amount in cents';



COMMENT ON COLUMN "public"."orders"."activated_at" IS 'Timestamp when user verified email and activated account';



CREATE TABLE IF NOT EXISTS "public"."pending_checkouts" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "email" character varying(255) NOT NULL,
    "first_name" character varying(100),
    "last_name" character varying(100),
    "company" character varying(255),
    "password" "text" NOT NULL,
    "tier" character varying(50) DEFAULT 'champion'::character varying NOT NULL,
    "stripe_session_id" character varying(255),
    "status" character varying(50) DEFAULT 'pending'::character varying NOT NULL,
    "error" "text",
    "user_id" "uuid",
    "client_id" "uuid",
    "order_number" character varying(50),
    "created_at" timestamp with time zone DEFAULT "now"(),
    "expires_at" timestamp with time zone,
    "completed_at" timestamp with time zone,
    CONSTRAINT "valid_status" CHECK ((("status")::"text" = ANY ((ARRAY['pending'::character varying, 'completed'::character varying, 'failed'::character varying, 'expired'::character varying])::"text"[])))
);


ALTER TABLE "public"."pending_checkouts" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."platform_api_keys" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "key_name" character varying(100) NOT NULL,
    "key_value" "text" NOT NULL,
    "provider" character varying(50) NOT NULL,
    "description" "text",
    "is_active" boolean DEFAULT true,
    "total_requests" bigint DEFAULT 0,
    "last_used_at" timestamp with time zone,
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"()
);


ALTER TABLE "public"."platform_api_keys" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."platform_client_user_mappings" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "platform_user_id" "uuid" NOT NULL,
    "client_id" "uuid" NOT NULL,
    "client_user_id" "uuid" NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"()
);


ALTER TABLE "public"."platform_client_user_mappings" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."profiles" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "uuid",
    "email" "text",
    "full_name" "text",
    "metadata" "jsonb" DEFAULT '{}'::"jsonb",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."profiles" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."shared_pool_config" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "pool_name" "text" DEFAULT 'adventurer_pool'::"text" NOT NULL,
    "supabase_url" "text" NOT NULL,
    "supabase_service_role_key" "text" NOT NULL,
    "supabase_anon_key" "text",
    "supabase_project_ref" "text",
    "max_clients" integer DEFAULT 1000,
    "current_client_count" integer DEFAULT 0,
    "is_active" boolean DEFAULT true,
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"()
);


ALTER TABLE "public"."shared_pool_config" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."tier_quotas" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "tier" character varying(50) NOT NULL,
    "voice_seconds_per_month" integer DEFAULT 6000,
    "text_messages_per_month" integer DEFAULT 1000,
    "embedding_chunks_per_month" integer DEFAULT 10000,
    "max_sidekicks" integer DEFAULT 1,
    "uses_platform_keys" boolean DEFAULT true,
    "can_bring_own_keys" boolean DEFAULT true,
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"()
);


ALTER TABLE "public"."tier_quotas" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."tools" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "name" "text" NOT NULL,
    "slug" "text" NOT NULL,
    "description" "text",
    "type" "text" NOT NULL,
    "scope" "text" DEFAULT 'global'::"text" NOT NULL,
    "client_id" "uuid",
    "icon_url" "text",
    "config" "jsonb" DEFAULT '{}'::"jsonb" NOT NULL,
    "enabled" boolean DEFAULT true NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "execution_phase" "text" DEFAULT 'active'::"text" NOT NULL,
    "trigger_config" "jsonb"
);


ALTER TABLE "public"."tools" OWNER TO "postgres";


COMMENT ON TABLE "public"."tools" IS 'Global Abilities registry (tools) for Sidekick Forge platform.';



COMMENT ON COLUMN "public"."tools"."execution_phase" IS 'When this ability runs: active (during conversation) or ambient (background)';



COMMENT ON COLUMN "public"."tools"."trigger_config" IS 'Configuration for ambient triggers (post_session, scheduled, etc.)';



CREATE TABLE IF NOT EXISTS "public"."user_overview_history" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "overview_id" "uuid" NOT NULL,
    "version" integer NOT NULL,
    "overview" "jsonb" NOT NULL,
    "updated_by_agent" "uuid",
    "update_reason" "text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."user_overview_history" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."user_overviews" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "client_id" "uuid" NOT NULL,
    "overview" "jsonb" DEFAULT '{"goals": {}, "identity": {}, "working_style": {}, "important_context": [], "relationship_history": {}}'::"jsonb" NOT NULL,
    "sidekick_insights" "jsonb" DEFAULT '{}'::"jsonb",
    "learning_status" "text" DEFAULT 'none'::"text",
    "learning_progress" integer DEFAULT 0,
    "conversations_analyzed" integer DEFAULT 0,
    "schema_version" integer DEFAULT 2,
    "last_updated_by_agent" "uuid",
    "last_updated_reason" "text",
    "version" integer DEFAULT 1 NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "updated_at" timestamp with time zone DEFAULT "now"() NOT NULL
);


ALTER TABLE "public"."user_overviews" OWNER TO "postgres";


CREATE TABLE IF NOT EXISTS "public"."usersense_learning_jobs" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "client_id" "uuid" NOT NULL,
    "user_id" "uuid" NOT NULL,
    "user_email" "text",
    "job_type" "text" DEFAULT 'initial_learning'::"text" NOT NULL,
    "agent_ids" "uuid"[],
    "status" "text" DEFAULT 'pending'::"text" NOT NULL,
    "progress_percent" integer DEFAULT 0,
    "progress_message" "text",
    "conversations_total" integer DEFAULT 0,
    "conversations_processed" integer DEFAULT 0,
    "result_summary" "text",
    "error_message" "text",
    "created_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "queued_at" timestamp with time zone,
    "started_at" timestamp with time zone,
    "completed_at" timestamp with time zone,
    CONSTRAINT "valid_job_status" CHECK (("status" = ANY (ARRAY['pending'::"text", 'queued'::"text", 'in_progress'::"text", 'completed'::"text", 'failed'::"text"]))),
    CONSTRAINT "valid_job_type" CHECK (("job_type" = ANY (ARRAY['initial_learning'::"text", 'refresh'::"text", 'single_conversation'::"text"])))
);


ALTER TABLE "public"."usersense_learning_jobs" OWNER TO "postgres";


COMMENT ON TABLE "public"."usersense_learning_jobs" IS 'Tracks UserSense initial learning jobs for processing user conversation history';



CREATE TABLE IF NOT EXISTS "public"."wordpress_content_sync" (
    "id" "uuid" DEFAULT "gen_random_uuid"() NOT NULL,
    "wordpress_site_id" "uuid" NOT NULL,
    "client_id" "uuid" NOT NULL,
    "wp_post_id" bigint NOT NULL,
    "wp_post_type" character varying(50) DEFAULT 'post'::character varying NOT NULL,
    "wp_post_title" "text" NOT NULL,
    "wp_post_url" "text",
    "wp_post_modified" timestamp with time zone,
    "document_id" "text",
    "sync_status" character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    "last_sync_at" timestamp with time zone,
    "last_error" "text",
    "content_hash" character varying(64),
    "metadata" "jsonb" DEFAULT '{}'::"jsonb",
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"()
);


ALTER TABLE "public"."wordpress_content_sync" OWNER TO "postgres";


COMMENT ON TABLE "public"."wordpress_content_sync" IS 'Tracks WordPress pages/posts synced to the knowledge base for each client';



COMMENT ON COLUMN "public"."wordpress_content_sync"."document_id" IS 'Reference to document in CLIENT Supabase (stored as text to handle both UUID and bigint)';



COMMENT ON COLUMN "public"."wordpress_content_sync"."content_hash" IS 'SHA256 hash of content for detecting changes';



CREATE TABLE IF NOT EXISTS "public"."wordpress_sites" (
    "id" "uuid" NOT NULL,
    "api_key" "text" NOT NULL,
    "api_secret" "text" NOT NULL,
    "site_url" "text" NOT NULL,
    "site_name" "text" NOT NULL,
    "admin_email" "text" NOT NULL,
    "client_id" "uuid" NOT NULL,
    "is_active" boolean DEFAULT true NOT NULL,
    "metadata" "jsonb" DEFAULT '{}'::"jsonb",
    "request_count" integer DEFAULT 0 NOT NULL,
    "created_at" timestamp with time zone DEFAULT "now"(),
    "updated_at" timestamp with time zone DEFAULT "now"(),
    "last_seen_at" timestamp with time zone
);


ALTER TABLE "public"."wordpress_sites" OWNER TO "postgres";


ALTER TABLE ONLY "public"."agent_documents"
    ADD CONSTRAINT "agent_documents_agent_id_document_id_key" UNIQUE ("agent_id", "document_id");



ALTER TABLE ONLY "public"."agent_documents"
    ADD CONSTRAINT "agent_documents_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."agent_tools"
    ADD CONSTRAINT "agent_tools_pkey" PRIMARY KEY ("agent_id", "tool_id");



ALTER TABLE ONLY "public"."agent_usage"
    ADD CONSTRAINT "agent_usage_client_id_agent_id_period_start_key" UNIQUE ("client_id", "agent_id", "period_start");



ALTER TABLE ONLY "public"."agent_usage"
    ADD CONSTRAINT "agent_usage_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."agents"
    ADD CONSTRAINT "agents_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."agents"
    ADD CONSTRAINT "agents_slug_key" UNIQUE ("slug");



ALTER TABLE ONLY "public"."ambient_ability_runs"
    ADD CONSTRAINT "ambient_ability_runs_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."client_asana_connections"
    ADD CONSTRAINT "client_asana_connections_pkey" PRIMARY KEY ("client_id");



ALTER TABLE ONLY "public"."client_provisioning_jobs"
    ADD CONSTRAINT "client_provisioning_jobs_client_id_job_type_key" UNIQUE ("client_id", "job_type");



ALTER TABLE ONLY "public"."client_provisioning_jobs"
    ADD CONSTRAINT "client_provisioning_jobs_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."client_usage"
    ADD CONSTRAINT "client_usage_client_id_period_start_key" UNIQUE ("client_id", "period_start");



ALTER TABLE ONLY "public"."client_usage"
    ADD CONSTRAINT "client_usage_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."clients"
    ADD CONSTRAINT "clients_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."contact_submissions"
    ADD CONSTRAINT "contact_submissions_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."content_catalyst_runs"
    ADD CONSTRAINT "content_catalyst_runs_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."conversation_summaries"
    ADD CONSTRAINT "conversation_summaries_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."conversation_transcripts"
    ADD CONSTRAINT "conversation_transcripts_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."conversations"
    ADD CONSTRAINT "conversations_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."document_chunks"
    ADD CONSTRAINT "document_chunks_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."document_intelligence"
    ADD CONSTRAINT "document_intelligence_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."documents"
    ADD CONSTRAINT "documents_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."documentsense_learning_jobs"
    ADD CONSTRAINT "documentsense_learning_jobs_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."email_verification_tokens"
    ADD CONSTRAINT "email_verification_tokens_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."email_verification_tokens"
    ADD CONSTRAINT "email_verification_tokens_token_key" UNIQUE ("token");



ALTER TABLE ONLY "public"."livekit_events"
    ADD CONSTRAINT "livekit_events_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."orders"
    ADD CONSTRAINT "orders_order_number_key" UNIQUE ("order_number");



ALTER TABLE ONLY "public"."orders"
    ADD CONSTRAINT "orders_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."pending_checkouts"
    ADD CONSTRAINT "pending_checkouts_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."platform_api_keys"
    ADD CONSTRAINT "platform_api_keys_key_name_key" UNIQUE ("key_name");



ALTER TABLE ONLY "public"."platform_api_keys"
    ADD CONSTRAINT "platform_api_keys_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."platform_client_user_mappings"
    ADD CONSTRAINT "platform_client_user_mappings_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."platform_client_user_mappings"
    ADD CONSTRAINT "platform_client_user_mappings_platform_user_id_client_id_key" UNIQUE ("platform_user_id", "client_id");



ALTER TABLE ONLY "public"."profiles"
    ADD CONSTRAINT "profiles_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."shared_pool_config"
    ADD CONSTRAINT "shared_pool_config_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."shared_pool_config"
    ADD CONSTRAINT "shared_pool_config_pool_name_key" UNIQUE ("pool_name");



ALTER TABLE ONLY "public"."tier_quotas"
    ADD CONSTRAINT "tier_quotas_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."tier_quotas"
    ADD CONSTRAINT "tier_quotas_tier_key" UNIQUE ("tier");



ALTER TABLE ONLY "public"."tools"
    ADD CONSTRAINT "tools_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."document_intelligence"
    ADD CONSTRAINT "uniq_document_intelligence" UNIQUE ("document_id", "client_id");



ALTER TABLE ONLY "public"."user_overviews"
    ADD CONSTRAINT "uniq_user_client_overview" UNIQUE ("user_id", "client_id");



ALTER TABLE ONLY "public"."user_overview_history"
    ADD CONSTRAINT "user_overview_history_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."user_overviews"
    ADD CONSTRAINT "user_overviews_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."usersense_learning_jobs"
    ADD CONSTRAINT "usersense_learning_jobs_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."wordpress_content_sync"
    ADD CONSTRAINT "wordpress_content_sync_pkey" PRIMARY KEY ("id");



ALTER TABLE ONLY "public"."wordpress_content_sync"
    ADD CONSTRAINT "wordpress_content_sync_wordpress_site_id_wp_post_id_key" UNIQUE ("wordpress_site_id", "wp_post_id");



ALTER TABLE ONLY "public"."wordpress_sites"
    ADD CONSTRAINT "wordpress_sites_pkey" PRIMARY KEY ("id");



CREATE INDEX "conversation_transcripts_embeddings_hnsw" ON "public"."conversation_transcripts" USING "hnsw" ("embeddings" "public"."vector_cosine_ops") WITH ("m"='16', "ef_construction"='64');



CREATE INDEX "document_chunks_embeddings_hnsw" ON "public"."document_chunks" USING "hnsw" ("embeddings" "public"."vector_cosine_ops") WITH ("m"='16', "ef_construction"='64');



CREATE INDEX "document_chunks_embeddings_vec_ivfflat" ON "public"."document_chunks" USING "ivfflat" ("embeddings_vec" "public"."vector_cosine_ops") WITH ("lists"='16');



CREATE INDEX "documents_embedding_hnsw" ON "public"."documents" USING "hnsw" ("embedding" "public"."vector_cosine_ops") WITH ("m"='16', "ef_construction"='64');



CREATE INDEX "documents_embedding_vec_hnsw" ON "public"."documents" USING "hnsw" ("embedding_vec" "public"."vector_cosine_ops") WITH ("m"='16', "ef_construction"='64');



CREATE INDEX "documents_embeddings_hnsw" ON "public"."documents" USING "hnsw" ("embeddings" "public"."vector_cosine_ops") WITH ("m"='16', "ef_construction"='64');



CREATE INDEX "idx_agent_tools_tool_id" ON "public"."agent_tools" USING "btree" ("tool_id");



CREATE INDEX "idx_agent_usage_client_period" ON "public"."agent_usage" USING "btree" ("client_id", "period_start");



CREATE INDEX "idx_agent_usage_lookup" ON "public"."agent_usage" USING "btree" ("client_id", "agent_id", "period_start");



CREATE INDEX "idx_agent_usage_period" ON "public"."agent_usage" USING "btree" ("period_start");



CREATE INDEX "idx_ambient_runs_client" ON "public"."ambient_ability_runs" USING "btree" ("client_id");



CREATE INDEX "idx_ambient_runs_created" ON "public"."ambient_ability_runs" USING "btree" ("created_at" DESC);



CREATE INDEX "idx_ambient_runs_notifications" ON "public"."ambient_ability_runs" USING "btree" ("user_id", "client_id", "notification_shown") WHERE (("notification_shown" = false) AND ("status" = 'completed'::"text"));



CREATE INDEX "idx_ambient_runs_pending" ON "public"."ambient_ability_runs" USING "btree" ("status", "created_at") WHERE ("status" = 'pending'::"text");



CREATE INDEX "idx_ambient_runs_status" ON "public"."ambient_ability_runs" USING "btree" ("status");



CREATE INDEX "idx_ambient_runs_user" ON "public"."ambient_ability_runs" USING "btree" ("user_id");



CREATE INDEX "idx_client_asana_connections_updated_at" ON "public"."client_asana_connections" USING "btree" ("updated_at" DESC);



CREATE INDEX "idx_client_provisioning_jobs_type" ON "public"."client_provisioning_jobs" USING "btree" ("job_type", "claimed_at");



CREATE INDEX "idx_client_usage_agent" ON "public"."client_usage" USING "btree" ("client_id", "agent_id", "period_start");



CREATE INDEX "idx_client_usage_client_period" ON "public"."client_usage" USING "btree" ("client_id", "period_start");



CREATE INDEX "idx_client_usage_period" ON "public"."client_usage" USING "btree" ("period_start");



CREATE INDEX "idx_clients_hosting_type" ON "public"."clients" USING "btree" ("hosting_type");



CREATE INDEX "idx_clients_owner_email" ON "public"."clients" USING "btree" ("owner_email");



CREATE INDEX "idx_clients_owner_user_id" ON "public"."clients" USING "btree" ("owner_user_id");



CREATE INDEX "idx_clients_provisioning_status" ON "public"."clients" USING "btree" ("provisioning_status");



CREATE INDEX "idx_clients_stripe_customer_id" ON "public"."clients" USING "btree" ("stripe_customer_id") WHERE ("stripe_customer_id" IS NOT NULL);



CREATE INDEX "idx_clients_stripe_subscription_id" ON "public"."clients" USING "btree" ("stripe_subscription_id") WHERE ("stripe_subscription_id" IS NOT NULL);



CREATE INDEX "idx_clients_tier" ON "public"."clients" USING "btree" ("tier");



CREATE INDEX "idx_contact_submissions_assigned" ON "public"."contact_submissions" USING "btree" ("assigned_to", "status");



CREATE INDEX "idx_contact_submissions_created" ON "public"."contact_submissions" USING "btree" ("created_at" DESC);



CREATE INDEX "idx_contact_submissions_email" ON "public"."contact_submissions" USING "btree" ("email");



CREATE INDEX "idx_contact_submissions_status" ON "public"."contact_submissions" USING "btree" ("status", "created_at" DESC);



CREATE INDEX "idx_contact_submissions_type" ON "public"."contact_submissions" USING "btree" ("submission_type", "created_at" DESC);



CREATE INDEX "idx_content_catalyst_runs_client" ON "public"."content_catalyst_runs" USING "btree" ("client_id");



CREATE INDEX "idx_content_catalyst_runs_created" ON "public"."content_catalyst_runs" USING "btree" ("created_at" DESC);



CREATE INDEX "idx_content_catalyst_runs_session" ON "public"."content_catalyst_runs" USING "btree" ("session_id");



CREATE INDEX "idx_content_catalyst_runs_status" ON "public"."content_catalyst_runs" USING "btree" ("status");



CREATE INDEX "idx_content_catalyst_runs_user" ON "public"."content_catalyst_runs" USING "btree" ("user_id");



CREATE INDEX "idx_conversation_summaries_conversation_id" ON "public"."conversation_summaries" USING "btree" ("conversation_id");



CREATE INDEX "idx_conversation_summaries_user_id" ON "public"."conversation_summaries" USING "btree" ("user_id");



CREATE INDEX "idx_document_intelligence_client_id" ON "public"."document_intelligence" USING "btree" ("client_id");



CREATE INDEX "idx_document_intelligence_document_id" ON "public"."document_intelligence" USING "btree" ("document_id");



CREATE INDEX "idx_document_intelligence_title_search" ON "public"."document_intelligence" USING "gin" ("to_tsvector"('"english"'::"regconfig", COALESCE("document_title", ''::"text")));



CREATE INDEX "idx_document_intelligence_updated_at" ON "public"."document_intelligence" USING "btree" ("updated_at" DESC);



CREATE INDEX "idx_ds_learning_jobs_client_status" ON "public"."documentsense_learning_jobs" USING "btree" ("client_id", "status");



CREATE INDEX "idx_ds_learning_jobs_document" ON "public"."documentsense_learning_jobs" USING "btree" ("client_id", "document_id");



CREATE INDEX "idx_ds_learning_jobs_pending" ON "public"."documentsense_learning_jobs" USING "btree" ("status", "created_at") WHERE ("status" = ANY (ARRAY['pending'::"text", 'queued'::"text"]));



CREATE INDEX "idx_learning_jobs_client_status" ON "public"."usersense_learning_jobs" USING "btree" ("client_id", "status");



CREATE INDEX "idx_learning_jobs_pending" ON "public"."usersense_learning_jobs" USING "btree" ("status", "created_at") WHERE ("status" = ANY (ARRAY['pending'::"text", 'queued'::"text"]));



CREATE INDEX "idx_learning_jobs_user" ON "public"."usersense_learning_jobs" USING "btree" ("client_id", "user_id");



CREATE INDEX "idx_livekit_events_created" ON "public"."livekit_events" USING "btree" ("created_at" DESC);



CREATE INDEX "idx_livekit_events_room" ON "public"."livekit_events" USING "btree" ("room_name");



CREATE INDEX "idx_livekit_events_type" ON "public"."livekit_events" USING "btree" ("event_type");



CREATE INDEX "idx_orders_client_id" ON "public"."orders" USING "btree" ("client_id");



CREATE INDEX "idx_orders_created_at" ON "public"."orders" USING "btree" ("created_at" DESC);



CREATE INDEX "idx_orders_email" ON "public"."orders" USING "btree" ("email");



CREATE INDEX "idx_orders_order_number" ON "public"."orders" USING "btree" ("order_number");



CREATE INDEX "idx_orders_payment_status" ON "public"."orders" USING "btree" ("payment_status");



CREATE INDEX "idx_orders_user_id" ON "public"."orders" USING "btree" ("user_id");



CREATE INDEX "idx_pending_checkouts_created" ON "public"."pending_checkouts" USING "btree" ("created_at");



CREATE INDEX "idx_pending_checkouts_email" ON "public"."pending_checkouts" USING "btree" ("email");



CREATE INDEX "idx_pending_checkouts_status" ON "public"."pending_checkouts" USING "btree" ("status");



CREATE INDEX "idx_pending_checkouts_stripe_session" ON "public"."pending_checkouts" USING "btree" ("stripe_session_id");



CREATE INDEX "idx_provisioning_jobs_unclaimed" ON "public"."client_provisioning_jobs" USING "btree" ("created_at") WHERE ("claimed_at" IS NULL);



CREATE INDEX "idx_shared_pool_active" ON "public"."shared_pool_config" USING "btree" ("is_active", "pool_name");



CREATE INDEX "idx_tenant_tools_enabled" ON "public"."tools" USING "btree" ("enabled");



CREATE INDEX "idx_tenant_tools_type" ON "public"."tools" USING "btree" ("type");



CREATE INDEX "idx_tools_client_id" ON "public"."tools" USING "btree" ("client_id");



CREATE INDEX "idx_tools_enabled" ON "public"."tools" USING "btree" ("enabled");



CREATE INDEX "idx_tools_execution_phase" ON "public"."tools" USING "btree" ("execution_phase");



CREATE INDEX "idx_tools_scope" ON "public"."tools" USING "btree" ("scope");



CREATE INDEX "idx_tools_type" ON "public"."tools" USING "btree" ("type");



CREATE INDEX "idx_user_overview_history_overview_id" ON "public"."user_overview_history" USING "btree" ("overview_id", "version" DESC);



CREATE INDEX "idx_user_overviews_client_id" ON "public"."user_overviews" USING "btree" ("client_id");



CREATE INDEX "idx_user_overviews_learning_status" ON "public"."user_overviews" USING "btree" ("learning_status") WHERE ("learning_status" = ANY (ARRAY['pending'::"text", 'in_progress'::"text"]));



CREATE INDEX "idx_user_overviews_updated_at" ON "public"."user_overviews" USING "btree" ("updated_at" DESC);



CREATE INDEX "idx_user_overviews_user_id" ON "public"."user_overviews" USING "btree" ("user_id");



CREATE INDEX "idx_verification_tokens_email" ON "public"."email_verification_tokens" USING "btree" ("email");



CREATE INDEX "idx_verification_tokens_expires" ON "public"."email_verification_tokens" USING "btree" ("expires_at");



CREATE INDEX "idx_verification_tokens_token" ON "public"."email_verification_tokens" USING "btree" ("token");



CREATE INDEX "idx_verification_tokens_user_id" ON "public"."email_verification_tokens" USING "btree" ("user_id");



CREATE INDEX "idx_wp_content_sync_client" ON "public"."wordpress_content_sync" USING "btree" ("client_id");



CREATE INDEX "idx_wp_content_sync_document" ON "public"."wordpress_content_sync" USING "btree" ("document_id");



CREATE INDEX "idx_wp_content_sync_site" ON "public"."wordpress_content_sync" USING "btree" ("wordpress_site_id");



CREATE INDEX "idx_wp_content_sync_status" ON "public"."wordpress_content_sync" USING "btree" ("sync_status");



CREATE INDEX "profiles_user_id_idx" ON "public"."profiles" USING "btree" ("user_id");



CREATE UNIQUE INDEX "uniq_tenant_tools_slug" ON "public"."tools" USING "btree" ("slug");



CREATE UNIQUE INDEX "uniq_tools_client_slug" ON "public"."tools" USING "btree" ("client_id", "slug") WHERE ("scope" = 'client'::"text");



CREATE UNIQUE INDEX "uniq_tools_global_slug" ON "public"."tools" USING "btree" ("slug") WHERE ("scope" = 'global'::"text");



CREATE UNIQUE INDEX "uniq_tools_scope_slug" ON "public"."tools" USING "btree" ("scope", "slug", COALESCE("client_id", '00000000-0000-0000-0000-000000000000'::"uuid"));



CREATE INDEX "wordpress_sites_api_key_key" ON "public"."wordpress_sites" USING "btree" ("api_key");



CREATE INDEX "wordpress_sites_site_url_key" ON "public"."wordpress_sites" USING "btree" ("site_url");



CREATE OR REPLACE TRIGGER "client_asana_connections_set_updated_at" BEFORE UPDATE ON "public"."client_asana_connections" FOR EACH ROW EXECUTE FUNCTION "public"."set_client_asana_connections_updated_at"();



CREATE OR REPLACE TRIGGER "set_client_provisioning_jobs_updated_at" BEFORE UPDATE ON "public"."client_provisioning_jobs" FOR EACH ROW EXECUTE FUNCTION "public"."trigger_set_timestamp"();



CREATE OR REPLACE TRIGGER "set_clients_timestamp" BEFORE UPDATE ON "public"."clients" FOR EACH ROW EXECUTE FUNCTION "public"."trigger_set_timestamp"();



CREATE OR REPLACE TRIGGER "set_contact_submission_updated_at" BEFORE UPDATE ON "public"."contact_submissions" FOR EACH ROW EXECUTE FUNCTION "public"."update_contact_submission_timestamp"();



CREATE OR REPLACE TRIGGER "set_orders_updated_at" BEFORE UPDATE ON "public"."orders" FOR EACH ROW EXECUTE FUNCTION "public"."trigger_set_timestamp"();



CREATE OR REPLACE TRIGGER "trg_document_intelligence_set_updated_at" BEFORE UPDATE ON "public"."document_intelligence" FOR EACH ROW EXECUTE FUNCTION "public"."set_updated_at"();



CREATE OR REPLACE TRIGGER "trg_tenant_tools_set_updated_at" BEFORE UPDATE ON "public"."tools" FOR EACH ROW EXECUTE FUNCTION "public"."set_updated_at"();



CREATE OR REPLACE TRIGGER "trg_tools_set_updated_at" BEFORE UPDATE ON "public"."tools" FOR EACH ROW EXECUTE FUNCTION "public"."set_updated_at"();



CREATE OR REPLACE TRIGGER "trg_user_overviews_set_updated_at" BEFORE UPDATE ON "public"."user_overviews" FOR EACH ROW EXECUTE FUNCTION "public"."set_updated_at"();



CREATE OR REPLACE TRIGGER "wordpress_content_sync_updated_at" BEFORE UPDATE ON "public"."wordpress_content_sync" FOR EACH ROW EXECUTE FUNCTION "public"."update_wordpress_content_sync_updated_at"();



ALTER TABLE ONLY "public"."agent_documents"
    ADD CONSTRAINT "agent_documents_document_id_fkey" FOREIGN KEY ("document_id") REFERENCES "public"."documents"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."agent_tools"
    ADD CONSTRAINT "agent_tools_tool_id_fkey" FOREIGN KEY ("tool_id") REFERENCES "public"."tools"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."agent_usage"
    ADD CONSTRAINT "agent_usage_client_id_fkey" FOREIGN KEY ("client_id") REFERENCES "public"."clients"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."ambient_ability_runs"
    ADD CONSTRAINT "ambient_ability_runs_ability_id_fkey" FOREIGN KEY ("ability_id") REFERENCES "public"."tools"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."client_asana_connections"
    ADD CONSTRAINT "client_asana_connections_client_id_fkey" FOREIGN KEY ("client_id") REFERENCES "public"."clients"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."client_provisioning_jobs"
    ADD CONSTRAINT "client_provisioning_jobs_client_id_fkey" FOREIGN KEY ("client_id") REFERENCES "public"."clients"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."client_usage"
    ADD CONSTRAINT "client_usage_client_id_fkey" FOREIGN KEY ("client_id") REFERENCES "public"."clients"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."document_chunks"
    ADD CONSTRAINT "document_chunks_document_id_fkey" FOREIGN KEY ("document_id") REFERENCES "public"."documents"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."email_verification_tokens"
    ADD CONSTRAINT "email_verification_tokens_order_id_fkey" FOREIGN KEY ("order_id") REFERENCES "public"."orders"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."orders"
    ADD CONSTRAINT "orders_client_id_fkey" FOREIGN KEY ("client_id") REFERENCES "public"."clients"("id") ON DELETE SET NULL;



ALTER TABLE ONLY "public"."user_overview_history"
    ADD CONSTRAINT "user_overview_history_overview_id_fkey" FOREIGN KEY ("overview_id") REFERENCES "public"."user_overviews"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."wordpress_content_sync"
    ADD CONSTRAINT "wordpress_content_sync_client_id_fkey" FOREIGN KEY ("client_id") REFERENCES "public"."clients"("id") ON DELETE CASCADE;



ALTER TABLE ONLY "public"."wordpress_content_sync"
    ADD CONSTRAINT "wordpress_content_sync_wordpress_site_id_fkey" FOREIGN KEY ("wordpress_site_id") REFERENCES "public"."wordpress_sites"("id") ON DELETE CASCADE;



CREATE POLICY "Authenticated users can read tier quotas" ON "public"."tier_quotas" FOR SELECT USING ((("auth"."role"() = 'authenticated'::"text") OR ("auth"."role"() = 'service_role'::"text")));



CREATE POLICY "Authenticated users can update submissions" ON "public"."contact_submissions" FOR UPDATE TO "authenticated" USING (true);



CREATE POLICY "Authenticated users can view submissions" ON "public"."contact_submissions" FOR SELECT TO "authenticated" USING (true);



CREATE POLICY "Clients can view own usage" ON "public"."client_usage" FOR SELECT USING (("client_id" IN ( SELECT "clients"."id"
   FROM "public"."clients"
  WHERE ("clients"."owner_user_id" = "auth"."uid"()))));



CREATE POLICY "Service role can insert submissions" ON "public"."contact_submissions" FOR INSERT TO "service_role" WITH CHECK (true);



CREATE POLICY "Service role can select submissions" ON "public"."contact_submissions" FOR SELECT TO "service_role" USING (true);



CREATE POLICY "Service role full access to agent usage" ON "public"."agent_usage" USING (true);



CREATE POLICY "Service role full access to ambient_ability_runs" ON "public"."ambient_ability_runs" TO "service_role" USING (true) WITH CHECK (true);



CREATE POLICY "Service role full access to client_asana_connections" ON "public"."client_asana_connections" TO "service_role" USING (true) WITH CHECK (true);



CREATE POLICY "Service role full access to content_catalyst_runs" ON "public"."content_catalyst_runs" TO "service_role" USING (true) WITH CHECK (true);



CREATE POLICY "Service role full access to livekit_events" ON "public"."livekit_events" USING (true);



CREATE POLICY "Service role full access to orders" ON "public"."orders" TO "service_role" USING (true);



CREATE POLICY "Service role full access to verification tokens" ON "public"."email_verification_tokens" TO "service_role" USING (true);



CREATE POLICY "Service role only for pending checkouts" ON "public"."pending_checkouts" USING (("auth"."role"() = 'service_role'::"text"));



CREATE POLICY "Service role only for platform keys" ON "public"."platform_api_keys" USING (("auth"."role"() = 'service_role'::"text"));



CREATE POLICY "Users can view own ambient_ability_runs" ON "public"."ambient_ability_runs" FOR SELECT TO "authenticated" USING (("user_id" = "auth"."uid"()));



CREATE POLICY "Users can view own content_catalyst_runs" ON "public"."content_catalyst_runs" FOR SELECT TO "authenticated" USING (("user_id" = "auth"."uid"()));



CREATE POLICY "Users can view own orders" ON "public"."orders" FOR SELECT TO "authenticated" USING (("user_id" = "auth"."uid"()));



ALTER TABLE "public"."agent_tools" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "agent_tools_service_role_all" ON "public"."agent_tools" USING (("auth"."role"() = 'service_role'::"text")) WITH CHECK (("auth"."role"() = 'service_role'::"text"));



ALTER TABLE "public"."agent_usage" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."ambient_ability_runs" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."client_asana_connections" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."client_usage" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."contact_submissions" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."content_catalyst_runs" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."document_intelligence" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "document_intelligence_service_role_all" ON "public"."document_intelligence" USING (("auth"."role"() = 'service_role'::"text")) WITH CHECK (("auth"."role"() = 'service_role'::"text"));



ALTER TABLE "public"."documentsense_learning_jobs" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "ds_learning_jobs_service_role_all" ON "public"."documentsense_learning_jobs" TO "service_role" USING (true) WITH CHECK (true);



ALTER TABLE "public"."email_verification_tokens" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "learning_jobs_service_role_all" ON "public"."usersense_learning_jobs" TO "service_role" USING (true) WITH CHECK (true);



ALTER TABLE "public"."livekit_events" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."orders" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."pending_checkouts" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."platform_api_keys" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "tenant_tools_service_role_all" ON "public"."tools" USING (("auth"."role"() = 'service_role'::"text")) WITH CHECK (("auth"."role"() = 'service_role'::"text"));



ALTER TABLE "public"."tier_quotas" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."tools" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "tools_service_role_all" ON "public"."tools" USING (("auth"."role"() = 'service_role'::"text")) WITH CHECK (("auth"."role"() = 'service_role'::"text"));



ALTER TABLE "public"."user_overview_history" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "user_overview_history_service_role_all" ON "public"."user_overview_history" USING (("auth"."role"() = 'service_role'::"text")) WITH CHECK (("auth"."role"() = 'service_role'::"text"));



ALTER TABLE "public"."user_overviews" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "user_overviews_service_role_all" ON "public"."user_overviews" USING (("auth"."role"() = 'service_role'::"text")) WITH CHECK (("auth"."role"() = 'service_role'::"text"));



ALTER TABLE "public"."usersense_learning_jobs" ENABLE ROW LEVEL SECURITY;


ALTER TABLE "public"."wordpress_content_sync" ENABLE ROW LEVEL SECURITY;


CREATE POLICY "wordpress_content_sync_service_policy" ON "public"."wordpress_content_sync" USING (("auth"."role"() = 'service_role'::"text")) WITH CHECK (("auth"."role"() = 'service_role'::"text"));





ALTER PUBLICATION "supabase_realtime" OWNER TO "postgres";


GRANT USAGE ON SCHEMA "public" TO "postgres";
GRANT USAGE ON SCHEMA "public" TO "anon";
GRANT USAGE ON SCHEMA "public" TO "authenticated";
GRANT USAGE ON SCHEMA "public" TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_in"("cstring", "oid", integer) TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_in"("cstring", "oid", integer) TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_in"("cstring", "oid", integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_in"("cstring", "oid", integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_out"("public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_out"("public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_out"("public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_out"("public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_recv"("internal", "oid", integer) TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_recv"("internal", "oid", integer) TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_recv"("internal", "oid", integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_recv"("internal", "oid", integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_send"("public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_send"("public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_send"("public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_send"("public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_typmod_in"("cstring"[]) TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_typmod_in"("cstring"[]) TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_typmod_in"("cstring"[]) TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_typmod_in"("cstring"[]) TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_in"("cstring", "oid", integer) TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_in"("cstring", "oid", integer) TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_in"("cstring", "oid", integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_in"("cstring", "oid", integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_out"("public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_out"("public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_out"("public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_out"("public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_recv"("internal", "oid", integer) TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_recv"("internal", "oid", integer) TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_recv"("internal", "oid", integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_recv"("internal", "oid", integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_send"("public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_send"("public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_send"("public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_send"("public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_typmod_in"("cstring"[]) TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_typmod_in"("cstring"[]) TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_typmod_in"("cstring"[]) TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_typmod_in"("cstring"[]) TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_in"("cstring", "oid", integer) TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_in"("cstring", "oid", integer) TO "anon";
GRANT ALL ON FUNCTION "public"."vector_in"("cstring", "oid", integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_in"("cstring", "oid", integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_out"("public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_out"("public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_out"("public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_out"("public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_recv"("internal", "oid", integer) TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_recv"("internal", "oid", integer) TO "anon";
GRANT ALL ON FUNCTION "public"."vector_recv"("internal", "oid", integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_recv"("internal", "oid", integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_send"("public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_send"("public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_send"("public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_send"("public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_typmod_in"("cstring"[]) TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_typmod_in"("cstring"[]) TO "anon";
GRANT ALL ON FUNCTION "public"."vector_typmod_in"("cstring"[]) TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_typmod_in"("cstring"[]) TO "service_role";



GRANT ALL ON FUNCTION "public"."array_to_halfvec"(real[], integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."array_to_halfvec"(real[], integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."array_to_halfvec"(real[], integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."array_to_halfvec"(real[], integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(real[], integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(real[], integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(real[], integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(real[], integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."array_to_vector"(real[], integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."array_to_vector"(real[], integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."array_to_vector"(real[], integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."array_to_vector"(real[], integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."array_to_halfvec"(double precision[], integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."array_to_halfvec"(double precision[], integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."array_to_halfvec"(double precision[], integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."array_to_halfvec"(double precision[], integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(double precision[], integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(double precision[], integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(double precision[], integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(double precision[], integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."array_to_vector"(double precision[], integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."array_to_vector"(double precision[], integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."array_to_vector"(double precision[], integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."array_to_vector"(double precision[], integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."array_to_halfvec"(integer[], integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."array_to_halfvec"(integer[], integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."array_to_halfvec"(integer[], integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."array_to_halfvec"(integer[], integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(integer[], integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(integer[], integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(integer[], integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(integer[], integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."array_to_vector"(integer[], integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."array_to_vector"(integer[], integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."array_to_vector"(integer[], integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."array_to_vector"(integer[], integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."array_to_halfvec"(numeric[], integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."array_to_halfvec"(numeric[], integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."array_to_halfvec"(numeric[], integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."array_to_halfvec"(numeric[], integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(numeric[], integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(numeric[], integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(numeric[], integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."array_to_sparsevec"(numeric[], integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."array_to_vector"(numeric[], integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."array_to_vector"(numeric[], integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."array_to_vector"(numeric[], integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."array_to_vector"(numeric[], integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_to_float4"("public"."halfvec", integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_to_float4"("public"."halfvec", integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_to_float4"("public"."halfvec", integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_to_float4"("public"."halfvec", integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec"("public"."halfvec", integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec"("public"."halfvec", integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec"("public"."halfvec", integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec"("public"."halfvec", integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_to_sparsevec"("public"."halfvec", integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_to_sparsevec"("public"."halfvec", integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_to_sparsevec"("public"."halfvec", integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_to_sparsevec"("public"."halfvec", integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_to_vector"("public"."halfvec", integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_to_vector"("public"."halfvec", integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_to_vector"("public"."halfvec", integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_to_vector"("public"."halfvec", integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_to_halfvec"("public"."sparsevec", integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_to_halfvec"("public"."sparsevec", integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_to_halfvec"("public"."sparsevec", integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_to_halfvec"("public"."sparsevec", integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec"("public"."sparsevec", integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec"("public"."sparsevec", integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec"("public"."sparsevec", integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec"("public"."sparsevec", integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_to_vector"("public"."sparsevec", integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_to_vector"("public"."sparsevec", integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_to_vector"("public"."sparsevec", integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_to_vector"("public"."sparsevec", integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_to_float4"("public"."vector", integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_to_float4"("public"."vector", integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."vector_to_float4"("public"."vector", integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_to_float4"("public"."vector", integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_to_halfvec"("public"."vector", integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_to_halfvec"("public"."vector", integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."vector_to_halfvec"("public"."vector", integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_to_halfvec"("public"."vector", integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_to_sparsevec"("public"."vector", integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_to_sparsevec"("public"."vector", integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."vector_to_sparsevec"("public"."vector", integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_to_sparsevec"("public"."vector", integer, boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."vector"("public"."vector", integer, boolean) TO "postgres";
GRANT ALL ON FUNCTION "public"."vector"("public"."vector", integer, boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."vector"("public"."vector", integer, boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector"("public"."vector", integer, boolean) TO "service_role";

























































































































































GRANT ALL ON FUNCTION "public"."binary_quantize"("public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."binary_quantize"("public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."binary_quantize"("public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."binary_quantize"("public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."binary_quantize"("public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."binary_quantize"("public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."binary_quantize"("public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."binary_quantize"("public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."check_client_quota"("p_client_id" "uuid", "p_quota_type" character varying) TO "anon";
GRANT ALL ON FUNCTION "public"."check_client_quota"("p_client_id" "uuid", "p_quota_type" character varying) TO "authenticated";
GRANT ALL ON FUNCTION "public"."check_client_quota"("p_client_id" "uuid", "p_quota_type" character varying) TO "service_role";



GRANT ALL ON FUNCTION "public"."check_quota"("p_client_id" "uuid", "p_quota_type" character varying) TO "anon";
GRANT ALL ON FUNCTION "public"."check_quota"("p_client_id" "uuid", "p_quota_type" character varying) TO "authenticated";
GRANT ALL ON FUNCTION "public"."check_quota"("p_client_id" "uuid", "p_quota_type" character varying) TO "service_role";



GRANT ALL ON FUNCTION "public"."claim_next_documentsense_job"() TO "anon";
GRANT ALL ON FUNCTION "public"."claim_next_documentsense_job"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."claim_next_documentsense_job"() TO "service_role";



GRANT ALL ON FUNCTION "public"."claim_next_learning_job"() TO "anon";
GRANT ALL ON FUNCTION "public"."claim_next_learning_job"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."claim_next_learning_job"() TO "service_role";



GRANT ALL ON FUNCTION "public"."cleanup_expired_pending_checkouts"() TO "anon";
GRANT ALL ON FUNCTION "public"."cleanup_expired_pending_checkouts"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."cleanup_expired_pending_checkouts"() TO "service_role";



GRANT ALL ON FUNCTION "public"."complete_documentsense_job"("p_job_id" "uuid", "p_success" boolean, "p_result_summary" "text", "p_error_message" "text") TO "anon";
GRANT ALL ON FUNCTION "public"."complete_documentsense_job"("p_job_id" "uuid", "p_success" boolean, "p_result_summary" "text", "p_error_message" "text") TO "authenticated";
GRANT ALL ON FUNCTION "public"."complete_documentsense_job"("p_job_id" "uuid", "p_success" boolean, "p_result_summary" "text", "p_error_message" "text") TO "service_role";



GRANT ALL ON FUNCTION "public"."complete_learning_job"("p_job_id" "uuid", "p_success" boolean, "p_result_summary" "text", "p_error_message" "text") TO "anon";
GRANT ALL ON FUNCTION "public"."complete_learning_job"("p_job_id" "uuid", "p_success" boolean, "p_result_summary" "text", "p_error_message" "text") TO "authenticated";
GRANT ALL ON FUNCTION "public"."complete_learning_job"("p_job_id" "uuid", "p_success" boolean, "p_result_summary" "text", "p_error_message" "text") TO "service_role";



GRANT ALL ON FUNCTION "public"."cosine_distance"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."cosine_distance"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."cosine_distance"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."cosine_distance"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."cosine_distance"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."cosine_distance"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."cosine_distance"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."cosine_distance"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."cosine_distance"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."cosine_distance"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."cosine_distance"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."cosine_distance"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."create_content_catalyst_run"("p_client_id" "uuid", "p_agent_id" "uuid", "p_user_id" "uuid", "p_conversation_id" "uuid", "p_session_id" "uuid", "p_source_type" "text", "p_source_content" "text", "p_target_word_count" integer, "p_style_prompt" "text", "p_use_perplexity" boolean, "p_use_knowledge_base" boolean) TO "anon";
GRANT ALL ON FUNCTION "public"."create_content_catalyst_run"("p_client_id" "uuid", "p_agent_id" "uuid", "p_user_id" "uuid", "p_conversation_id" "uuid", "p_session_id" "uuid", "p_source_type" "text", "p_source_content" "text", "p_target_word_count" integer, "p_style_prompt" "text", "p_use_perplexity" boolean, "p_use_knowledge_base" boolean) TO "authenticated";
GRANT ALL ON FUNCTION "public"."create_content_catalyst_run"("p_client_id" "uuid", "p_agent_id" "uuid", "p_user_id" "uuid", "p_conversation_id" "uuid", "p_session_id" "uuid", "p_source_type" "text", "p_source_content" "text", "p_target_word_count" integer, "p_style_prompt" "text", "p_use_perplexity" boolean, "p_use_knowledge_base" boolean) TO "service_role";



GRANT ALL ON FUNCTION "public"."get_client_aggregated_usage"("p_client_id" "uuid", "p_period_start" "date") TO "anon";
GRANT ALL ON FUNCTION "public"."get_client_aggregated_usage"("p_client_id" "uuid", "p_period_start" "date") TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_client_aggregated_usage"("p_client_id" "uuid", "p_period_start" "date") TO "service_role";



GRANT ALL ON FUNCTION "public"."get_client_documentsense_status"("p_client_id" "uuid") TO "anon";
GRANT ALL ON FUNCTION "public"."get_client_documentsense_status"("p_client_id" "uuid") TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_client_documentsense_status"("p_client_id" "uuid") TO "service_role";



GRANT ALL ON FUNCTION "public"."get_client_learning_status"("p_client_id" "uuid") TO "anon";
GRANT ALL ON FUNCTION "public"."get_client_learning_status"("p_client_id" "uuid") TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_client_learning_status"("p_client_id" "uuid") TO "service_role";



GRANT ALL ON FUNCTION "public"."get_client_usage_summary"("p_client_id" "uuid", "p_period_start" "date") TO "anon";
GRANT ALL ON FUNCTION "public"."get_client_usage_summary"("p_client_id" "uuid", "p_period_start" "date") TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_client_usage_summary"("p_client_id" "uuid", "p_period_start" "date") TO "service_role";



GRANT ALL ON FUNCTION "public"."get_conversations_for_learning"("p_user_id" "uuid", "p_agent_ids" "uuid"[], "p_limit" integer) TO "anon";
GRANT ALL ON FUNCTION "public"."get_conversations_for_learning"("p_user_id" "uuid", "p_agent_ids" "uuid"[], "p_limit" integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_conversations_for_learning"("p_user_id" "uuid", "p_agent_ids" "uuid"[], "p_limit" integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."get_document_intelligence"("p_client_id" "uuid", "p_document_id" bigint) TO "anon";
GRANT ALL ON FUNCTION "public"."get_document_intelligence"("p_client_id" "uuid", "p_document_id" bigint) TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_document_intelligence"("p_client_id" "uuid", "p_document_id" bigint) TO "service_role";



GRANT ALL ON FUNCTION "public"."get_or_create_usage_record"("p_client_id" "uuid") TO "anon";
GRANT ALL ON FUNCTION "public"."get_or_create_usage_record"("p_client_id" "uuid") TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_or_create_usage_record"("p_client_id" "uuid") TO "service_role";



GRANT ALL ON FUNCTION "public"."get_pending_ambient_runs"("p_limit" integer) TO "anon";
GRANT ALL ON FUNCTION "public"."get_pending_ambient_runs"("p_limit" integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_pending_ambient_runs"("p_limit" integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."get_user_ambient_notifications"("p_user_id" "uuid", "p_client_id" "uuid") TO "anon";
GRANT ALL ON FUNCTION "public"."get_user_ambient_notifications"("p_user_id" "uuid", "p_client_id" "uuid") TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_user_ambient_notifications"("p_user_id" "uuid", "p_client_id" "uuid") TO "service_role";



GRANT ALL ON FUNCTION "public"."get_user_overview"("p_user_id" "uuid", "p_client_id" "uuid") TO "anon";
GRANT ALL ON FUNCTION "public"."get_user_overview"("p_user_id" "uuid", "p_client_id" "uuid") TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_user_overview"("p_user_id" "uuid", "p_client_id" "uuid") TO "service_role";



GRANT ALL ON FUNCTION "public"."get_user_overview_for_agent"("p_user_id" "uuid", "p_client_id" "uuid", "p_agent_id" "uuid") TO "anon";
GRANT ALL ON FUNCTION "public"."get_user_overview_for_agent"("p_user_id" "uuid", "p_client_id" "uuid", "p_agent_id" "uuid") TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_user_overview_for_agent"("p_user_id" "uuid", "p_client_id" "uuid", "p_agent_id" "uuid") TO "service_role";



GRANT ALL ON FUNCTION "public"."get_users_needing_learning"("p_client_id" "uuid", "p_limit" integer) TO "anon";
GRANT ALL ON FUNCTION "public"."get_users_needing_learning"("p_client_id" "uuid", "p_limit" integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_users_needing_learning"("p_client_id" "uuid", "p_limit" integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_accum"(double precision[], "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_accum"(double precision[], "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_accum"(double precision[], "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_accum"(double precision[], "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_add"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_add"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_add"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_add"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_avg"(double precision[]) TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_avg"(double precision[]) TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_avg"(double precision[]) TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_avg"(double precision[]) TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_cmp"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_cmp"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_cmp"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_cmp"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_combine"(double precision[], double precision[]) TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_combine"(double precision[], double precision[]) TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_combine"(double precision[], double precision[]) TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_combine"(double precision[], double precision[]) TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_concat"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_concat"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_concat"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_concat"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_eq"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_eq"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_eq"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_eq"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_ge"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_ge"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_ge"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_ge"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_gt"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_gt"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_gt"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_gt"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_l2_squared_distance"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_l2_squared_distance"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_l2_squared_distance"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_l2_squared_distance"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_le"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_le"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_le"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_le"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_lt"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_lt"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_lt"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_lt"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_mul"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_mul"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_mul"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_mul"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_ne"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_ne"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_ne"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_ne"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_negative_inner_product"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_negative_inner_product"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_negative_inner_product"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_negative_inner_product"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_spherical_distance"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_spherical_distance"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_spherical_distance"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_spherical_distance"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."halfvec_sub"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."halfvec_sub"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."halfvec_sub"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."halfvec_sub"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."hamming_distance"(bit, bit) TO "postgres";
GRANT ALL ON FUNCTION "public"."hamming_distance"(bit, bit) TO "anon";
GRANT ALL ON FUNCTION "public"."hamming_distance"(bit, bit) TO "authenticated";
GRANT ALL ON FUNCTION "public"."hamming_distance"(bit, bit) TO "service_role";



GRANT ALL ON FUNCTION "public"."hnsw_bit_support"("internal") TO "postgres";
GRANT ALL ON FUNCTION "public"."hnsw_bit_support"("internal") TO "anon";
GRANT ALL ON FUNCTION "public"."hnsw_bit_support"("internal") TO "authenticated";
GRANT ALL ON FUNCTION "public"."hnsw_bit_support"("internal") TO "service_role";



GRANT ALL ON FUNCTION "public"."hnsw_halfvec_support"("internal") TO "postgres";
GRANT ALL ON FUNCTION "public"."hnsw_halfvec_support"("internal") TO "anon";
GRANT ALL ON FUNCTION "public"."hnsw_halfvec_support"("internal") TO "authenticated";
GRANT ALL ON FUNCTION "public"."hnsw_halfvec_support"("internal") TO "service_role";



GRANT ALL ON FUNCTION "public"."hnsw_sparsevec_support"("internal") TO "postgres";
GRANT ALL ON FUNCTION "public"."hnsw_sparsevec_support"("internal") TO "anon";
GRANT ALL ON FUNCTION "public"."hnsw_sparsevec_support"("internal") TO "authenticated";
GRANT ALL ON FUNCTION "public"."hnsw_sparsevec_support"("internal") TO "service_role";



GRANT ALL ON FUNCTION "public"."hnswhandler"("internal") TO "postgres";
GRANT ALL ON FUNCTION "public"."hnswhandler"("internal") TO "anon";
GRANT ALL ON FUNCTION "public"."hnswhandler"("internal") TO "authenticated";
GRANT ALL ON FUNCTION "public"."hnswhandler"("internal") TO "service_role";



GRANT ALL ON FUNCTION "public"."increment_text_usage"("p_client_id" "uuid", "p_count" integer) TO "anon";
GRANT ALL ON FUNCTION "public"."increment_text_usage"("p_client_id" "uuid", "p_count" integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."increment_text_usage"("p_client_id" "uuid", "p_count" integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."increment_voice_usage"("p_client_id" "uuid", "p_seconds" integer) TO "anon";
GRANT ALL ON FUNCTION "public"."increment_voice_usage"("p_client_id" "uuid", "p_seconds" integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."increment_voice_usage"("p_client_id" "uuid", "p_seconds" integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."inner_product"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."inner_product"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."inner_product"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."inner_product"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."inner_product"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."inner_product"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."inner_product"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."inner_product"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."inner_product"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."inner_product"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."inner_product"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."inner_product"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."ivfflat_bit_support"("internal") TO "postgres";
GRANT ALL ON FUNCTION "public"."ivfflat_bit_support"("internal") TO "anon";
GRANT ALL ON FUNCTION "public"."ivfflat_bit_support"("internal") TO "authenticated";
GRANT ALL ON FUNCTION "public"."ivfflat_bit_support"("internal") TO "service_role";



GRANT ALL ON FUNCTION "public"."ivfflat_halfvec_support"("internal") TO "postgres";
GRANT ALL ON FUNCTION "public"."ivfflat_halfvec_support"("internal") TO "anon";
GRANT ALL ON FUNCTION "public"."ivfflat_halfvec_support"("internal") TO "authenticated";
GRANT ALL ON FUNCTION "public"."ivfflat_halfvec_support"("internal") TO "service_role";



GRANT ALL ON FUNCTION "public"."ivfflathandler"("internal") TO "postgres";
GRANT ALL ON FUNCTION "public"."ivfflathandler"("internal") TO "anon";
GRANT ALL ON FUNCTION "public"."ivfflathandler"("internal") TO "authenticated";
GRANT ALL ON FUNCTION "public"."ivfflathandler"("internal") TO "service_role";



GRANT ALL ON FUNCTION "public"."jaccard_distance"(bit, bit) TO "postgres";
GRANT ALL ON FUNCTION "public"."jaccard_distance"(bit, bit) TO "anon";
GRANT ALL ON FUNCTION "public"."jaccard_distance"(bit, bit) TO "authenticated";
GRANT ALL ON FUNCTION "public"."jaccard_distance"(bit, bit) TO "service_role";



GRANT ALL ON FUNCTION "public"."l1_distance"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."l1_distance"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."l1_distance"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."l1_distance"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."l1_distance"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."l1_distance"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."l1_distance"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."l1_distance"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."l1_distance"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."l1_distance"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."l1_distance"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."l1_distance"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."l2_distance"("public"."halfvec", "public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."l2_distance"("public"."halfvec", "public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."l2_distance"("public"."halfvec", "public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."l2_distance"("public"."halfvec", "public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."l2_distance"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."l2_distance"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."l2_distance"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."l2_distance"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."l2_distance"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."l2_distance"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."l2_distance"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."l2_distance"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."l2_norm"("public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."l2_norm"("public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."l2_norm"("public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."l2_norm"("public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."l2_norm"("public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."l2_norm"("public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."l2_norm"("public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."l2_norm"("public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."l2_normalize"("public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."l2_normalize"("public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."l2_normalize"("public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."l2_normalize"("public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."l2_normalize"("public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."l2_normalize"("public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."l2_normalize"("public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."l2_normalize"("public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."l2_normalize"("public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."l2_normalize"("public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."l2_normalize"("public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."l2_normalize"("public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."mark_ambient_notification_shown"("p_run_id" "uuid") TO "anon";
GRANT ALL ON FUNCTION "public"."mark_ambient_notification_shown"("p_run_id" "uuid") TO "authenticated";
GRANT ALL ON FUNCTION "public"."mark_ambient_notification_shown"("p_run_id" "uuid") TO "service_role";



GRANT ALL ON FUNCTION "public"."match_conversation_transcripts_secure"("query_embeddings" "public"."vector", "agent_slug_param" "text", "user_id_param" "uuid", "match_count" integer) TO "anon";
GRANT ALL ON FUNCTION "public"."match_conversation_transcripts_secure"("query_embeddings" "public"."vector", "agent_slug_param" "text", "user_id_param" "uuid", "match_count" integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."match_conversation_transcripts_secure"("query_embeddings" "public"."vector", "agent_slug_param" "text", "user_id_param" "uuid", "match_count" integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."match_documents"("p_query_embedding" "public"."vector", "p_agent_slug" "text", "p_match_threshold" double precision, "p_match_count" integer) TO "anon";
GRANT ALL ON FUNCTION "public"."match_documents"("p_query_embedding" "public"."vector", "p_agent_slug" "text", "p_match_threshold" double precision, "p_match_count" integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."match_documents"("p_query_embedding" "public"."vector", "p_agent_slug" "text", "p_match_threshold" double precision, "p_match_count" integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."queue_ambient_ability_run"("p_ability_id" "uuid", "p_client_id" "uuid", "p_user_id" "uuid", "p_conversation_id" "uuid", "p_session_id" "uuid", "p_trigger_type" "text", "p_input_context" "jsonb", "p_notification_message" "text") TO "anon";
GRANT ALL ON FUNCTION "public"."queue_ambient_ability_run"("p_ability_id" "uuid", "p_client_id" "uuid", "p_user_id" "uuid", "p_conversation_id" "uuid", "p_session_id" "uuid", "p_trigger_type" "text", "p_input_context" "jsonb", "p_notification_message" "text") TO "authenticated";
GRANT ALL ON FUNCTION "public"."queue_ambient_ability_run"("p_ability_id" "uuid", "p_client_id" "uuid", "p_user_id" "uuid", "p_conversation_id" "uuid", "p_session_id" "uuid", "p_trigger_type" "text", "p_input_context" "jsonb", "p_notification_message" "text") TO "service_role";



GRANT ALL ON FUNCTION "public"."queue_client_documentsense_extraction"("p_client_id" "uuid", "p_document_ids" bigint[]) TO "anon";
GRANT ALL ON FUNCTION "public"."queue_client_documentsense_extraction"("p_client_id" "uuid", "p_document_ids" bigint[]) TO "authenticated";
GRANT ALL ON FUNCTION "public"."queue_client_documentsense_extraction"("p_client_id" "uuid", "p_document_ids" bigint[]) TO "service_role";



GRANT ALL ON FUNCTION "public"."queue_client_initial_learning"("p_client_id" "uuid") TO "anon";
GRANT ALL ON FUNCTION "public"."queue_client_initial_learning"("p_client_id" "uuid") TO "authenticated";
GRANT ALL ON FUNCTION "public"."queue_client_initial_learning"("p_client_id" "uuid") TO "service_role";



GRANT ALL ON FUNCTION "public"."save_content_catalyst_articles"("p_run_id" "uuid", "p_article_1" "text", "p_article_2" "text") TO "anon";
GRANT ALL ON FUNCTION "public"."save_content_catalyst_articles"("p_run_id" "uuid", "p_article_1" "text", "p_article_2" "text") TO "authenticated";
GRANT ALL ON FUNCTION "public"."save_content_catalyst_articles"("p_run_id" "uuid", "p_article_1" "text", "p_article_2" "text") TO "service_role";



GRANT ALL ON FUNCTION "public"."search_document_intelligence"("p_client_id" "uuid", "p_query" "text", "p_limit" integer) TO "anon";
GRANT ALL ON FUNCTION "public"."search_document_intelligence"("p_client_id" "uuid", "p_query" "text", "p_limit" integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."search_document_intelligence"("p_client_id" "uuid", "p_query" "text", "p_limit" integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."set_client_asana_connections_updated_at"() TO "anon";
GRANT ALL ON FUNCTION "public"."set_client_asana_connections_updated_at"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."set_client_asana_connections_updated_at"() TO "service_role";



GRANT ALL ON FUNCTION "public"."set_updated_at"() TO "anon";
GRANT ALL ON FUNCTION "public"."set_updated_at"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."set_updated_at"() TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_cmp"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_cmp"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_cmp"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_cmp"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_eq"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_eq"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_eq"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_eq"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_ge"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_ge"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_ge"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_ge"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_gt"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_gt"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_gt"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_gt"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_l2_squared_distance"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_l2_squared_distance"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_l2_squared_distance"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_l2_squared_distance"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_le"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_le"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_le"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_le"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_lt"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_lt"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_lt"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_lt"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_ne"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_ne"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_ne"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_ne"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."sparsevec_negative_inner_product"("public"."sparsevec", "public"."sparsevec") TO "postgres";
GRANT ALL ON FUNCTION "public"."sparsevec_negative_inner_product"("public"."sparsevec", "public"."sparsevec") TO "anon";
GRANT ALL ON FUNCTION "public"."sparsevec_negative_inner_product"("public"."sparsevec", "public"."sparsevec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sparsevec_negative_inner_product"("public"."sparsevec", "public"."sparsevec") TO "service_role";



GRANT ALL ON FUNCTION "public"."subvector"("public"."halfvec", integer, integer) TO "postgres";
GRANT ALL ON FUNCTION "public"."subvector"("public"."halfvec", integer, integer) TO "anon";
GRANT ALL ON FUNCTION "public"."subvector"("public"."halfvec", integer, integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."subvector"("public"."halfvec", integer, integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."subvector"("public"."vector", integer, integer) TO "postgres";
GRANT ALL ON FUNCTION "public"."subvector"("public"."vector", integer, integer) TO "anon";
GRANT ALL ON FUNCTION "public"."subvector"("public"."vector", integer, integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."subvector"("public"."vector", integer, integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."trigger_set_timestamp"() TO "anon";
GRANT ALL ON FUNCTION "public"."trigger_set_timestamp"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."trigger_set_timestamp"() TO "service_role";



GRANT ALL ON FUNCTION "public"."update_ambient_run_status"("p_run_id" "uuid", "p_status" "text", "p_output_result" "jsonb", "p_error" "text") TO "anon";
GRANT ALL ON FUNCTION "public"."update_ambient_run_status"("p_run_id" "uuid", "p_status" "text", "p_output_result" "jsonb", "p_error" "text") TO "authenticated";
GRANT ALL ON FUNCTION "public"."update_ambient_run_status"("p_run_id" "uuid", "p_status" "text", "p_output_result" "jsonb", "p_error" "text") TO "service_role";



GRANT ALL ON FUNCTION "public"."update_contact_submission_timestamp"() TO "anon";
GRANT ALL ON FUNCTION "public"."update_contact_submission_timestamp"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."update_contact_submission_timestamp"() TO "service_role";



GRANT ALL ON FUNCTION "public"."update_content_catalyst_phase"("p_run_id" "uuid", "p_phase" "text", "p_phase_output" "jsonb", "p_status" "text", "p_error" "text") TO "anon";
GRANT ALL ON FUNCTION "public"."update_content_catalyst_phase"("p_run_id" "uuid", "p_phase" "text", "p_phase_output" "jsonb", "p_status" "text", "p_error" "text") TO "authenticated";
GRANT ALL ON FUNCTION "public"."update_content_catalyst_phase"("p_run_id" "uuid", "p_phase" "text", "p_phase_output" "jsonb", "p_status" "text", "p_error" "text") TO "service_role";



GRANT ALL ON FUNCTION "public"."update_documentsense_job_progress"("p_job_id" "uuid", "p_progress_percent" integer, "p_progress_message" "text", "p_chunks_processed" integer) TO "anon";
GRANT ALL ON FUNCTION "public"."update_documentsense_job_progress"("p_job_id" "uuid", "p_progress_percent" integer, "p_progress_message" "text", "p_chunks_processed" integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."update_documentsense_job_progress"("p_job_id" "uuid", "p_progress_percent" integer, "p_progress_message" "text", "p_chunks_processed" integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."update_learning_job_progress"("p_job_id" "uuid", "p_progress_percent" integer, "p_progress_message" "text", "p_conversations_processed" integer) TO "anon";
GRANT ALL ON FUNCTION "public"."update_learning_job_progress"("p_job_id" "uuid", "p_progress_percent" integer, "p_progress_message" "text", "p_conversations_processed" integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."update_learning_job_progress"("p_job_id" "uuid", "p_progress_percent" integer, "p_progress_message" "text", "p_conversations_processed" integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."update_learning_status"("p_user_id" "uuid", "p_client_id" "uuid", "p_status" "text", "p_progress" integer, "p_conversations_analyzed" integer) TO "anon";
GRANT ALL ON FUNCTION "public"."update_learning_status"("p_user_id" "uuid", "p_client_id" "uuid", "p_status" "text", "p_progress" integer, "p_conversations_analyzed" integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."update_learning_status"("p_user_id" "uuid", "p_client_id" "uuid", "p_status" "text", "p_progress" integer, "p_conversations_analyzed" integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."update_sidekick_insights"("p_user_id" "uuid", "p_client_id" "uuid", "p_agent_id" "uuid", "p_agent_name" "text", "p_insights" "jsonb", "p_reason" "text") TO "anon";
GRANT ALL ON FUNCTION "public"."update_sidekick_insights"("p_user_id" "uuid", "p_client_id" "uuid", "p_agent_id" "uuid", "p_agent_name" "text", "p_insights" "jsonb", "p_reason" "text") TO "authenticated";
GRANT ALL ON FUNCTION "public"."update_sidekick_insights"("p_user_id" "uuid", "p_client_id" "uuid", "p_agent_id" "uuid", "p_agent_name" "text", "p_insights" "jsonb", "p_reason" "text") TO "service_role";



GRANT ALL ON FUNCTION "public"."update_user_overview"("p_user_id" "uuid", "p_client_id" "uuid", "p_section" "text", "p_action" "text", "p_key" "text", "p_value" "text", "p_agent_id" "uuid", "p_reason" "text", "p_expected_version" integer) TO "anon";
GRANT ALL ON FUNCTION "public"."update_user_overview"("p_user_id" "uuid", "p_client_id" "uuid", "p_section" "text", "p_action" "text", "p_key" "text", "p_value" "text", "p_agent_id" "uuid", "p_reason" "text", "p_expected_version" integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."update_user_overview"("p_user_id" "uuid", "p_client_id" "uuid", "p_section" "text", "p_action" "text", "p_key" "text", "p_value" "text", "p_agent_id" "uuid", "p_reason" "text", "p_expected_version" integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."update_wordpress_content_sync_updated_at"() TO "anon";
GRANT ALL ON FUNCTION "public"."update_wordpress_content_sync_updated_at"() TO "authenticated";
GRANT ALL ON FUNCTION "public"."update_wordpress_content_sync_updated_at"() TO "service_role";



GRANT ALL ON FUNCTION "public"."upsert_document_intelligence"("p_document_id" bigint, "p_client_id" "uuid", "p_document_title" "text", "p_intelligence" "jsonb", "p_extraction_model" "text", "p_chunks_analyzed" integer) TO "anon";
GRANT ALL ON FUNCTION "public"."upsert_document_intelligence"("p_document_id" bigint, "p_client_id" "uuid", "p_document_title" "text", "p_intelligence" "jsonb", "p_extraction_model" "text", "p_chunks_analyzed" integer) TO "authenticated";
GRANT ALL ON FUNCTION "public"."upsert_document_intelligence"("p_document_id" bigint, "p_client_id" "uuid", "p_document_title" "text", "p_intelligence" "jsonb", "p_extraction_model" "text", "p_chunks_analyzed" integer) TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_accum"(double precision[], "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_accum"(double precision[], "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_accum"(double precision[], "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_accum"(double precision[], "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_add"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_add"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_add"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_add"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_avg"(double precision[]) TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_avg"(double precision[]) TO "anon";
GRANT ALL ON FUNCTION "public"."vector_avg"(double precision[]) TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_avg"(double precision[]) TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_cmp"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_cmp"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_cmp"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_cmp"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_combine"(double precision[], double precision[]) TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_combine"(double precision[], double precision[]) TO "anon";
GRANT ALL ON FUNCTION "public"."vector_combine"(double precision[], double precision[]) TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_combine"(double precision[], double precision[]) TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_concat"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_concat"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_concat"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_concat"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_dims"("public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_dims"("public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_dims"("public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_dims"("public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_dims"("public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_dims"("public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_dims"("public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_dims"("public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_eq"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_eq"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_eq"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_eq"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_ge"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_ge"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_ge"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_ge"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_gt"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_gt"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_gt"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_gt"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_l2_squared_distance"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_l2_squared_distance"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_l2_squared_distance"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_l2_squared_distance"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_le"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_le"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_le"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_le"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_lt"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_lt"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_lt"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_lt"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_mul"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_mul"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_mul"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_mul"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_ne"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_ne"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_ne"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_ne"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_negative_inner_product"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_negative_inner_product"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_negative_inner_product"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_negative_inner_product"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_norm"("public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_norm"("public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_norm"("public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_norm"("public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_spherical_distance"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_spherical_distance"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_spherical_distance"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_spherical_distance"("public"."vector", "public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."vector_sub"("public"."vector", "public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."vector_sub"("public"."vector", "public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."vector_sub"("public"."vector", "public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."vector_sub"("public"."vector", "public"."vector") TO "service_role";












GRANT ALL ON FUNCTION "public"."avg"("public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."avg"("public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."avg"("public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."avg"("public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."avg"("public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."avg"("public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."avg"("public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."avg"("public"."vector") TO "service_role";



GRANT ALL ON FUNCTION "public"."sum"("public"."halfvec") TO "postgres";
GRANT ALL ON FUNCTION "public"."sum"("public"."halfvec") TO "anon";
GRANT ALL ON FUNCTION "public"."sum"("public"."halfvec") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sum"("public"."halfvec") TO "service_role";



GRANT ALL ON FUNCTION "public"."sum"("public"."vector") TO "postgres";
GRANT ALL ON FUNCTION "public"."sum"("public"."vector") TO "anon";
GRANT ALL ON FUNCTION "public"."sum"("public"."vector") TO "authenticated";
GRANT ALL ON FUNCTION "public"."sum"("public"."vector") TO "service_role";









GRANT ALL ON TABLE "public"."agent_documents" TO "anon";
GRANT ALL ON TABLE "public"."agent_documents" TO "authenticated";
GRANT ALL ON TABLE "public"."agent_documents" TO "service_role";



GRANT ALL ON TABLE "public"."agent_tools" TO "anon";
GRANT ALL ON TABLE "public"."agent_tools" TO "authenticated";
GRANT ALL ON TABLE "public"."agent_tools" TO "service_role";



GRANT ALL ON TABLE "public"."agent_usage" TO "anon";
GRANT ALL ON TABLE "public"."agent_usage" TO "authenticated";
GRANT ALL ON TABLE "public"."agent_usage" TO "service_role";



GRANT ALL ON TABLE "public"."agents" TO "anon";
GRANT ALL ON TABLE "public"."agents" TO "authenticated";
GRANT ALL ON TABLE "public"."agents" TO "service_role";



GRANT ALL ON TABLE "public"."ambient_ability_runs" TO "anon";
GRANT ALL ON TABLE "public"."ambient_ability_runs" TO "authenticated";
GRANT ALL ON TABLE "public"."ambient_ability_runs" TO "service_role";



GRANT ALL ON TABLE "public"."client_asana_connections" TO "anon";
GRANT ALL ON TABLE "public"."client_asana_connections" TO "authenticated";
GRANT ALL ON TABLE "public"."client_asana_connections" TO "service_role";



GRANT ALL ON TABLE "public"."client_provisioning_jobs" TO "anon";
GRANT ALL ON TABLE "public"."client_provisioning_jobs" TO "authenticated";
GRANT ALL ON TABLE "public"."client_provisioning_jobs" TO "service_role";



GRANT ALL ON TABLE "public"."client_usage" TO "anon";
GRANT ALL ON TABLE "public"."client_usage" TO "authenticated";
GRANT ALL ON TABLE "public"."client_usage" TO "service_role";



GRANT ALL ON TABLE "public"."clients" TO "anon";
GRANT ALL ON TABLE "public"."clients" TO "authenticated";
GRANT ALL ON TABLE "public"."clients" TO "service_role";



GRANT ALL ON TABLE "public"."contact_submissions" TO "anon";
GRANT ALL ON TABLE "public"."contact_submissions" TO "authenticated";
GRANT ALL ON TABLE "public"."contact_submissions" TO "service_role";



GRANT ALL ON TABLE "public"."content_catalyst_runs" TO "anon";
GRANT ALL ON TABLE "public"."content_catalyst_runs" TO "authenticated";
GRANT ALL ON TABLE "public"."content_catalyst_runs" TO "service_role";



GRANT ALL ON TABLE "public"."conversation_summaries" TO "anon";
GRANT ALL ON TABLE "public"."conversation_summaries" TO "authenticated";
GRANT ALL ON TABLE "public"."conversation_summaries" TO "service_role";



GRANT ALL ON TABLE "public"."conversation_transcripts" TO "anon";
GRANT ALL ON TABLE "public"."conversation_transcripts" TO "authenticated";
GRANT ALL ON TABLE "public"."conversation_transcripts" TO "service_role";



GRANT ALL ON TABLE "public"."conversations" TO "anon";
GRANT ALL ON TABLE "public"."conversations" TO "authenticated";
GRANT ALL ON TABLE "public"."conversations" TO "service_role";



GRANT ALL ON TABLE "public"."document_chunks" TO "anon";
GRANT ALL ON TABLE "public"."document_chunks" TO "authenticated";
GRANT ALL ON TABLE "public"."document_chunks" TO "service_role";



GRANT ALL ON TABLE "public"."document_intelligence" TO "anon";
GRANT ALL ON TABLE "public"."document_intelligence" TO "authenticated";
GRANT ALL ON TABLE "public"."document_intelligence" TO "service_role";



GRANT ALL ON TABLE "public"."documents" TO "anon";
GRANT ALL ON TABLE "public"."documents" TO "authenticated";
GRANT ALL ON TABLE "public"."documents" TO "service_role";



GRANT ALL ON TABLE "public"."documentsense_learning_jobs" TO "anon";
GRANT ALL ON TABLE "public"."documentsense_learning_jobs" TO "authenticated";
GRANT ALL ON TABLE "public"."documentsense_learning_jobs" TO "service_role";



GRANT ALL ON TABLE "public"."email_verification_tokens" TO "anon";
GRANT ALL ON TABLE "public"."email_verification_tokens" TO "authenticated";
GRANT ALL ON TABLE "public"."email_verification_tokens" TO "service_role";



GRANT ALL ON TABLE "public"."livekit_events" TO "anon";
GRANT ALL ON TABLE "public"."livekit_events" TO "authenticated";
GRANT ALL ON TABLE "public"."livekit_events" TO "service_role";



GRANT ALL ON TABLE "public"."orders" TO "anon";
GRANT ALL ON TABLE "public"."orders" TO "authenticated";
GRANT ALL ON TABLE "public"."orders" TO "service_role";



GRANT ALL ON TABLE "public"."pending_checkouts" TO "anon";
GRANT ALL ON TABLE "public"."pending_checkouts" TO "authenticated";
GRANT ALL ON TABLE "public"."pending_checkouts" TO "service_role";



GRANT ALL ON TABLE "public"."platform_api_keys" TO "anon";
GRANT ALL ON TABLE "public"."platform_api_keys" TO "authenticated";
GRANT ALL ON TABLE "public"."platform_api_keys" TO "service_role";



GRANT ALL ON TABLE "public"."platform_client_user_mappings" TO "anon";
GRANT ALL ON TABLE "public"."platform_client_user_mappings" TO "authenticated";
GRANT ALL ON TABLE "public"."platform_client_user_mappings" TO "service_role";



GRANT ALL ON TABLE "public"."profiles" TO "anon";
GRANT ALL ON TABLE "public"."profiles" TO "authenticated";
GRANT ALL ON TABLE "public"."profiles" TO "service_role";



GRANT ALL ON TABLE "public"."shared_pool_config" TO "anon";
GRANT ALL ON TABLE "public"."shared_pool_config" TO "authenticated";
GRANT ALL ON TABLE "public"."shared_pool_config" TO "service_role";



GRANT ALL ON TABLE "public"."tier_quotas" TO "anon";
GRANT ALL ON TABLE "public"."tier_quotas" TO "authenticated";
GRANT ALL ON TABLE "public"."tier_quotas" TO "service_role";



GRANT ALL ON TABLE "public"."tools" TO "anon";
GRANT ALL ON TABLE "public"."tools" TO "authenticated";
GRANT ALL ON TABLE "public"."tools" TO "service_role";



GRANT ALL ON TABLE "public"."user_overview_history" TO "anon";
GRANT ALL ON TABLE "public"."user_overview_history" TO "authenticated";
GRANT ALL ON TABLE "public"."user_overview_history" TO "service_role";



GRANT ALL ON TABLE "public"."user_overviews" TO "anon";
GRANT ALL ON TABLE "public"."user_overviews" TO "authenticated";
GRANT ALL ON TABLE "public"."user_overviews" TO "service_role";



GRANT ALL ON TABLE "public"."usersense_learning_jobs" TO "anon";
GRANT ALL ON TABLE "public"."usersense_learning_jobs" TO "authenticated";
GRANT ALL ON TABLE "public"."usersense_learning_jobs" TO "service_role";



GRANT ALL ON TABLE "public"."wordpress_content_sync" TO "anon";
GRANT ALL ON TABLE "public"."wordpress_content_sync" TO "authenticated";
GRANT ALL ON TABLE "public"."wordpress_content_sync" TO "service_role";



GRANT ALL ON TABLE "public"."wordpress_sites" TO "anon";
GRANT ALL ON TABLE "public"."wordpress_sites" TO "authenticated";
GRANT ALL ON TABLE "public"."wordpress_sites" TO "service_role";









ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS TO "service_role";






ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES TO "service_role";






























drop extension if exists "pg_net";

alter table "public"."pending_checkouts" drop constraint "valid_status";

alter table "public"."pending_checkouts" add constraint "valid_status" CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'completed'::character varying, 'failed'::character varying, 'expired'::character varying])::text[]))) not valid;

alter table "public"."pending_checkouts" validate constraint "valid_status";



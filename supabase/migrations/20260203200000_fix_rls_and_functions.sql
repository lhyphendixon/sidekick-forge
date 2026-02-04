-- Migration: Fix RLS Security and Function Type Mismatches
-- Date: 2026-02-03
--
-- Fixes:
-- 1. Add RLS to high-risk tables missing policies
-- 2. Fix document_intelligence function type mismatches (uuid vs bigint)
-- 3. Add appropriate RLS policies for service_role access

-- ============================================================================
-- PART 1: Enable RLS on tables missing it
-- ============================================================================

-- High-risk tables that need RLS
ALTER TABLE IF EXISTS public.agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.document_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.conversation_summaries ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.agent_documents ENABLE ROW LEVEL SECURITY;

-- Medium-risk tables
ALTER TABLE IF EXISTS public.clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.platform_client_user_mappings ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.wordpress_sites ENABLE ROW LEVEL SECURITY;

-- Low-risk admin tables
ALTER TABLE IF EXISTS public.client_provisioning_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.shared_pool_config ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- PART 2: Create RLS policies for service_role access
-- ============================================================================

-- agents table policies
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'agents' AND policyname = 'agents_service_role_all'
  ) THEN
    CREATE POLICY agents_service_role_all ON public.agents
      FOR ALL USING (auth.role() = 'service_role')
      WITH CHECK (auth.role() = 'service_role');
  END IF;
END$$;

-- documents table policies
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'documents' AND policyname = 'documents_service_role_all'
  ) THEN
    CREATE POLICY documents_service_role_all ON public.documents
      FOR ALL USING (auth.role() = 'service_role')
      WITH CHECK (auth.role() = 'service_role');
  END IF;
END$$;

-- document_chunks table policies
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'document_chunks' AND policyname = 'document_chunks_service_role_all'
  ) THEN
    CREATE POLICY document_chunks_service_role_all ON public.document_chunks
      FOR ALL USING (auth.role() = 'service_role')
      WITH CHECK (auth.role() = 'service_role');
  END IF;
END$$;

-- conversations table policies
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'conversations' AND policyname = 'conversations_service_role_all'
  ) THEN
    CREATE POLICY conversations_service_role_all ON public.conversations
      FOR ALL USING (auth.role() = 'service_role')
      WITH CHECK (auth.role() = 'service_role');
  END IF;
END$$;

-- conversation_summaries table policies
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'conversation_summaries' AND policyname = 'conversation_summaries_service_role_all'
  ) THEN
    CREATE POLICY conversation_summaries_service_role_all ON public.conversation_summaries
      FOR ALL USING (auth.role() = 'service_role')
      WITH CHECK (auth.role() = 'service_role');
  END IF;
END$$;

-- agent_documents table policies
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'agent_documents' AND policyname = 'agent_documents_service_role_all'
  ) THEN
    CREATE POLICY agent_documents_service_role_all ON public.agent_documents
      FOR ALL USING (auth.role() = 'service_role')
      WITH CHECK (auth.role() = 'service_role');
  END IF;
END$$;

-- clients table policies
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'clients' AND policyname = 'clients_service_role_all'
  ) THEN
    CREATE POLICY clients_service_role_all ON public.clients
      FOR ALL USING (auth.role() = 'service_role')
      WITH CHECK (auth.role() = 'service_role');
  END IF;
END$$;

-- profiles table policies
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'profiles' AND policyname = 'profiles_service_role_all'
  ) THEN
    CREATE POLICY profiles_service_role_all ON public.profiles
      FOR ALL USING (auth.role() = 'service_role')
      WITH CHECK (auth.role() = 'service_role');
  END IF;

  -- Also allow users to read their own profile
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'profiles' AND policyname = 'profiles_own_read'
  ) THEN
    CREATE POLICY profiles_own_read ON public.profiles
      FOR SELECT USING (auth.uid() = id);
  END IF;
END$$;

-- platform_client_user_mappings table policies
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'platform_client_user_mappings' AND policyname = 'mappings_service_role_all'
  ) THEN
    CREATE POLICY mappings_service_role_all ON public.platform_client_user_mappings
      FOR ALL USING (auth.role() = 'service_role')
      WITH CHECK (auth.role() = 'service_role');
  END IF;
END$$;

-- wordpress_sites table policies
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'wordpress_sites' AND policyname = 'wordpress_sites_service_role_all'
  ) THEN
    CREATE POLICY wordpress_sites_service_role_all ON public.wordpress_sites
      FOR ALL USING (auth.role() = 'service_role')
      WITH CHECK (auth.role() = 'service_role');
  END IF;
END$$;

-- client_provisioning_jobs table policies
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'client_provisioning_jobs' AND policyname = 'provisioning_service_role_all'
  ) THEN
    CREATE POLICY provisioning_service_role_all ON public.client_provisioning_jobs
      FOR ALL USING (auth.role() = 'service_role')
      WITH CHECK (auth.role() = 'service_role');
  END IF;
END$$;

-- shared_pool_config table policies
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'shared_pool_config' AND policyname = 'shared_pool_service_role_all'
  ) THEN
    CREATE POLICY shared_pool_service_role_all ON public.shared_pool_config
      FOR ALL USING (auth.role() = 'service_role')
      WITH CHECK (auth.role() = 'service_role');
  END IF;
END$$;

-- ============================================================================
-- PART 3: Fix document_intelligence function type mismatches
-- ============================================================================

-- First, check and fix the document_intelligence table if needed
DO $$
DECLARE
  v_doc_id_type TEXT;
BEGIN
  -- Check document_intelligence.document_id type
  SELECT data_type INTO v_doc_id_type
  FROM information_schema.columns
  WHERE table_schema = 'public'
    AND table_name = 'document_intelligence'
    AND column_name = 'document_id';

  IF v_doc_id_type IS NOT NULL AND v_doc_id_type = 'bigint' THEN
    RAISE NOTICE 'document_intelligence.document_id is BIGINT - converting to UUID...';

    -- Drop constraint first
    ALTER TABLE public.document_intelligence
      DROP CONSTRAINT IF EXISTS uniq_document_intelligence;

    -- Change column type (this will fail if there's data that can't convert)
    -- For safety, we'll add a new column and migrate
    ALTER TABLE public.document_intelligence
      ADD COLUMN IF NOT EXISTS document_id_new UUID;

    -- Try to update from documents table if it exists with UUID
    UPDATE public.document_intelligence di
    SET document_id_new = d.id
    FROM public.documents d
    WHERE di.document_id::text = d.id::text;

    -- If documents.id is also bigint, generate new UUIDs
    UPDATE public.document_intelligence
    SET document_id_new = gen_random_uuid()
    WHERE document_id_new IS NULL;

    -- Drop old column and rename
    ALTER TABLE public.document_intelligence DROP COLUMN document_id;
    ALTER TABLE public.document_intelligence RENAME COLUMN document_id_new TO document_id;
    ALTER TABLE public.document_intelligence ALTER COLUMN document_id SET NOT NULL;

    -- Re-add constraint
    ALTER TABLE public.document_intelligence
      ADD CONSTRAINT uniq_document_intelligence UNIQUE (document_id, client_id);

    RAISE NOTICE 'document_intelligence.document_id converted to UUID';
  ELSIF v_doc_id_type IS NOT NULL THEN
    RAISE NOTICE 'document_intelligence.document_id is already %', v_doc_id_type;
  ELSE
    RAISE NOTICE 'document_intelligence table does not exist or has no document_id column';
  END IF;
END$$;

-- Drop and recreate functions with correct UUID signatures
DROP FUNCTION IF EXISTS public.get_document_intelligence(UUID, BIGINT);
DROP FUNCTION IF EXISTS public.get_document_intelligence(BIGINT, UUID);
DROP FUNCTION IF EXISTS public.get_document_intelligence(UUID, UUID);

CREATE OR REPLACE FUNCTION public.get_document_intelligence(
  p_document_id UUID,
  p_client_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
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

DROP FUNCTION IF EXISTS public.upsert_document_intelligence(BIGINT, UUID, TEXT, JSONB, TEXT, INTEGER);
DROP FUNCTION IF EXISTS public.upsert_document_intelligence(UUID, UUID, TEXT, JSONB, TEXT, INTEGER);

CREATE OR REPLACE FUNCTION public.upsert_document_intelligence(
  p_document_id UUID,
  p_client_id UUID,
  p_document_title TEXT,
  p_intelligence JSONB,
  p_extraction_model TEXT,
  p_chunks_analyzed INTEGER
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
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

DROP FUNCTION IF EXISTS public.search_document_intelligence(UUID, TEXT, INTEGER);

CREATE OR REPLACE FUNCTION public.search_document_intelligence(
  p_client_id UUID,
  p_query TEXT,
  p_limit INTEGER DEFAULT 10
)
RETURNS TABLE (
  document_id UUID,
  document_title TEXT,
  summary TEXT,
  key_quotes JSONB,
  themes JSONB,
  relevance_score REAL
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
  RETURN QUERY
  SELECT
    di.document_id,
    di.document_title,
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
    )::REAL as relevance_score
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

-- Grant permissions
GRANT EXECUTE ON FUNCTION public.get_document_intelligence(UUID, UUID) TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.upsert_document_intelligence(UUID, UUID, TEXT, JSONB, TEXT, INTEGER) TO service_role;
GRANT EXECUTE ON FUNCTION public.search_document_intelligence(UUID, TEXT, INTEGER) TO authenticated, service_role;

-- ============================================================================
-- PART 4: Comments
-- ============================================================================

COMMENT ON FUNCTION public.get_document_intelligence(UUID, UUID) IS 'Retrieve document intelligence by document_id and client_id (UUID params)';
COMMENT ON FUNCTION public.upsert_document_intelligence(UUID, UUID, TEXT, JSONB, TEXT, INTEGER) IS 'Create or update document intelligence (UUID params)';
COMMENT ON FUNCTION public.search_document_intelligence(UUID, TEXT, INTEGER) IS 'Search documents by title and return their intelligence data';

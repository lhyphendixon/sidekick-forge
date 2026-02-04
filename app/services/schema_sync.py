"""Shared helpers for applying Sidekick Forge schema patches to Supabase projects."""
from __future__ import annotations

from typing import Iterable, List, Tuple

import requests

from app.config import settings

# SQL statements maintained as canonical schema patches

# Minimal base schema for fresh tenant projects so later patches don't fail on
# missing tables. This is intentionally conservative and only creates tables
# that other patches expect; columns match the fields used by the app's
# document/transcript code paths.
BASE_SCHEMA_SQL = """
create extension if not exists "uuid-ossp";
create extension if not exists "pgcrypto";
create extension if not exists "vector";

create table if not exists public.conversations (
  id uuid primary key default gen_random_uuid(),
  session_id uuid,
  agent_id uuid,
  user_id uuid,
  channel text,
  status text default 'active',
  conversation_title text,
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.conversation_transcripts (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid,
  session_id uuid,
  agent_id uuid,
  user_id uuid,
  role text,
  content text,
  transcript text,
  turn_id uuid,
  citations jsonb default '[]'::jsonb,
  metadata jsonb default '{}'::jsonb,
  source text,
  embeddings vector(1024),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.agents (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  name text not null,
  description text,
  system_prompt text not null,
  agent_image text,
  voice_settings jsonb default '{}'::jsonb,
  webhooks jsonb default '{}'::jsonb,
  tools_config jsonb default '{}'::jsonb,
  rag_config jsonb default '{}'::jsonb,
  enabled boolean default true,
  show_citations boolean default true,
  rag_results_limit int default 5,
  model text,
  context_retention_minutes int default 30,
  max_context_messages int default 50,
  supertab_enabled boolean default false,
  supertab_experience_id text,
  supertab_price text,
  supertab_cta text,
  voice_chat_enabled boolean default true,
  text_chat_enabled boolean default true,
  video_chat_enabled boolean default false,
  sound_settings jsonb default '{"thinking_sound": "none", "thinking_volume": 0.3, "ambient_sound": "none", "ambient_volume": 0.15}'::jsonb,
  wizard_input jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.documents (
  id uuid primary key default gen_random_uuid(),
  user_id uuid,
  agent_id uuid,
  title text,
  filename text,
  file_name text,
  file_size bigint,
  file_type text,
  content text,
  status text default 'pending',
  upload_status text default 'pending',
  processing_status text default 'pending',
  document_type text default 'knowledge_base',
  chunk_count int default 0,
  word_count int,
  metadata jsonb default '{}'::jsonb,
  processing_metadata jsonb default '{}'::jsonb,
  embedding vector(1024),
  embedding_vec vector(1024),
  embeddings vector(1024),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.document_chunks (
  id uuid primary key default gen_random_uuid(),
  document_id uuid references public.documents(id) on delete cascade,
  chunk_index int,
  content text,
  embeddings vector(1024),
  chunk_metadata jsonb default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.agent_documents (
  id uuid primary key default gen_random_uuid(),
  agent_id uuid not null,
  document_id uuid not null references public.documents(id) on delete cascade,
  access_type text default 'read',
  enabled boolean default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(agent_id, document_id)
);

-- Enable RLS on all core tables
ALTER TABLE public.conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.conversation_transcripts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.document_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_documents ENABLE ROW LEVEL SECURITY;

-- RLS policies for service_role access (all tables)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'conversations' AND policyname = 'conversations_service_role_all') THEN
    CREATE POLICY conversations_service_role_all ON public.conversations FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'conversation_transcripts' AND policyname = 'conversation_transcripts_service_role_all') THEN
    CREATE POLICY conversation_transcripts_service_role_all ON public.conversation_transcripts FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'agents' AND policyname = 'agents_service_role_all') THEN
    CREATE POLICY agents_service_role_all ON public.agents FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'documents' AND policyname = 'documents_service_role_all') THEN
    CREATE POLICY documents_service_role_all ON public.documents FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'document_chunks' AND policyname = 'document_chunks_service_role_all') THEN
    CREATE POLICY document_chunks_service_role_all ON public.document_chunks FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'agent_documents' AND policyname = 'agent_documents_service_role_all') THEN
    CREATE POLICY agent_documents_service_role_all ON public.agent_documents FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
  END IF;
END$$;

-- Allow anon role to read conversation_transcripts (needed for widget embedding)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'conversation_transcripts' AND policyname = 'conversation_transcripts_anon_read') THEN
    CREATE POLICY conversation_transcripts_anon_read ON public.conversation_transcripts FOR SELECT USING (true);
  END IF;
END$$;
""".strip()

# UserSense / User Overviews schema for persistent user context
USER_OVERVIEWS_SQL = """
-- Ensure we have the updated_at trigger function
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Main user_overviews table
CREATE TABLE IF NOT EXISTS public.user_overviews (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  client_id UUID NOT NULL,
  overview JSONB NOT NULL DEFAULT '{
    "identity": {},
    "goals": {},
    "working_style": {},
    "important_context": [],
    "relationship_history": {}
  }'::jsonb,
  sidekick_insights JSONB DEFAULT '{}'::jsonb,
  learning_status TEXT DEFAULT 'none',
  learning_progress INTEGER DEFAULT 0,
  conversations_analyzed INTEGER DEFAULT 0,
  schema_version INTEGER DEFAULT 2,
  last_updated_by_agent UUID,
  last_updated_reason TEXT,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT uniq_user_client_overview UNIQUE (user_id, client_id)
);

CREATE INDEX IF NOT EXISTS idx_user_overviews_user_id ON public.user_overviews (user_id);
CREATE INDEX IF NOT EXISTS idx_user_overviews_client_id ON public.user_overviews (client_id);
CREATE INDEX IF NOT EXISTS idx_user_overviews_updated_at ON public.user_overviews (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_overviews_learning_status
  ON public.user_overviews (learning_status)
  WHERE learning_status IN ('pending', 'in_progress');

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger WHERE tgname = 'trg_user_overviews_set_updated_at'
  ) THEN
    CREATE TRIGGER trg_user_overviews_set_updated_at
      BEFORE UPDATE ON public.user_overviews
      FOR EACH ROW
      EXECUTE FUNCTION set_updated_at();
  END IF;
END$$;

-- Version history table for audit trail
CREATE TABLE IF NOT EXISTS public.user_overview_history (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  overview_id UUID NOT NULL REFERENCES public.user_overviews(id) ON DELETE CASCADE,
  version INTEGER NOT NULL,
  overview JSONB NOT NULL,
  updated_by_agent UUID,
  update_reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_overview_history_overview_id
  ON public.user_overview_history (overview_id, version DESC);

-- RLS policies
ALTER TABLE public.user_overviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_overview_history ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'user_overviews' AND policyname = 'user_overviews_service_role_all'
  ) THEN
    CREATE POLICY user_overviews_service_role_all ON public.user_overviews
      FOR ALL
      USING (auth.role() = 'service_role')
      WITH CHECK (auth.role() = 'service_role');
  END IF;
END$$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'user_overview_history' AND policyname = 'user_overview_history_service_role_all'
  ) THEN
    CREATE POLICY user_overview_history_service_role_all ON public.user_overview_history
      FOR ALL
      USING (auth.role() = 'service_role')
      WITH CHECK (auth.role() = 'service_role');
  END IF;
END$$;
""".strip()

# User Overview RPC functions
USER_OVERVIEW_RPCS_SQL = """
-- RPC function to safely update user overview with optimistic locking
CREATE OR REPLACE FUNCTION update_user_overview(
  p_user_id UUID,
  p_client_id UUID,
  p_section TEXT,
  p_action TEXT,
  p_key TEXT,
  p_value TEXT,
  p_agent_id UUID,
  p_reason TEXT,
  p_expected_version INTEGER DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
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
        v_section_data := jsonb_set(v_section_data, ARRAY['notes'], to_jsonb(COALESCE(v_section_data->>'notes', '') || E'\n' || p_value));
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

-- RPC function to get user overview
CREATE OR REPLACE FUNCTION get_user_overview(
  p_user_id UUID,
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

-- Function to update sidekick-specific insights
CREATE OR REPLACE FUNCTION update_sidekick_insights(
  p_user_id UUID,
  p_client_id UUID,
  p_agent_id UUID,
  p_agent_name TEXT,
  p_insights JSONB,
  p_reason TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
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

-- Function to update learning status
CREATE OR REPLACE FUNCTION update_learning_status(
  p_user_id UUID,
  p_client_id UUID,
  p_status TEXT,
  p_progress INTEGER DEFAULT NULL,
  p_conversations_analyzed INTEGER DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
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

-- Function to get overview for a specific agent
CREATE OR REPLACE FUNCTION get_user_overview_for_agent(
  p_user_id UUID,
  p_client_id UUID,
  p_agent_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
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

-- Function to get users needing learning
CREATE OR REPLACE FUNCTION get_users_needing_learning(
  p_client_id UUID,
  p_limit INTEGER DEFAULT 100
)
RETURNS TABLE (user_id UUID, conversation_count BIGINT)
LANGUAGE plpgsql
SECURITY DEFINER
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

-- Function to get conversations for learning
CREATE OR REPLACE FUNCTION get_conversations_for_learning(
  p_user_id UUID,
  p_agent_ids UUID[],
  p_limit INTEGER DEFAULT 50
)
RETURNS TABLE (conversation_id UUID, agent_id UUID, message_count BIGINT, first_message TIMESTAMPTZ, last_message TIMESTAMPTZ)
LANGUAGE plpgsql
SECURITY DEFINER
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
""".strip()

# Tools table for tenant databases
TOOLS_SQL = """
CREATE TABLE IF NOT EXISTS public.tools (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  slug TEXT NOT NULL,
  description TEXT,
  type TEXT NOT NULL,
  icon_url TEXT,
  config JSONB NOT NULL DEFAULT '{}'::jsonb,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_tenant_tools_slug ON public.tools (slug);
CREATE INDEX IF NOT EXISTS idx_tenant_tools_type ON public.tools (type);
CREATE INDEX IF NOT EXISTS idx_tenant_tools_enabled ON public.tools (enabled);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger WHERE tgname = 'trg_tenant_tools_set_updated_at'
  ) THEN
    CREATE TRIGGER trg_tenant_tools_set_updated_at
      BEFORE UPDATE ON public.tools
      FOR EACH ROW
      EXECUTE FUNCTION set_updated_at();
  END IF;
END$$;

ALTER TABLE public.tools ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'tools' AND policyname = 'tenant_tools_service_role_all'
  ) THEN
    CREATE POLICY tenant_tools_service_role_all ON public.tools
      FOR ALL
      USING (auth.role() = 'service_role')
      WITH CHECK (auth.role() = 'service_role');
  END IF;
END$$;
""".strip()

# Conversation summaries table (optional but useful for context)
CONVERSATION_SUMMARIES_SQL = """
CREATE TABLE IF NOT EXISTS public.conversation_summaries (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL,
  agent_id UUID,
  user_id UUID,
  summary TEXT,
  key_points JSONB DEFAULT '[]'::jsonb,
  sentiment TEXT,
  topics JSONB DEFAULT '[]'::jsonb,
  metadata JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversation_summaries_conversation_id ON public.conversation_summaries (conversation_id);
CREATE INDEX IF NOT EXISTS idx_conversation_summaries_user_id ON public.conversation_summaries (user_id);

-- Enable RLS on conversation_summaries
ALTER TABLE public.conversation_summaries ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'conversation_summaries' AND policyname = 'conversation_summaries_service_role_all') THEN
    CREATE POLICY conversation_summaries_service_role_all ON public.conversation_summaries FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
  END IF;
END$$;
""".strip()

# DocumentSense / Document Intelligence schema for extracted document metadata
DOCUMENT_INTELLIGENCE_SQL = """
-- Document Intelligence table for DocumentSense feature
CREATE TABLE IF NOT EXISTS public.document_intelligence (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id UUID NOT NULL,
  client_id UUID NOT NULL,
  intelligence JSONB NOT NULL DEFAULT '{
    "summary": "",
    "key_quotes": [],
    "themes": [],
    "entities": {
      "people": [],
      "organizations": [],
      "locations": [],
      "dates": [],
      "concepts": []
    },
    "questions_answered": [],
    "document_type_inferred": null
  }'::jsonb,
  extraction_model TEXT,
  extraction_timestamp TIMESTAMPTZ,
  chunks_analyzed INTEGER DEFAULT 0,
  document_title TEXT,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT uniq_document_intelligence UNIQUE (document_id, client_id)
);

CREATE INDEX IF NOT EXISTS idx_document_intelligence_document_id ON public.document_intelligence (document_id);
CREATE INDEX IF NOT EXISTS idx_document_intelligence_client_id ON public.document_intelligence (client_id);
CREATE INDEX IF NOT EXISTS idx_document_intelligence_updated_at ON public.document_intelligence (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_document_intelligence_title_search
  ON public.document_intelligence USING gin(to_tsvector('english', coalesce(document_title, '')));

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger WHERE tgname = 'trg_document_intelligence_set_updated_at'
  ) THEN
    CREATE TRIGGER trg_document_intelligence_set_updated_at
      BEFORE UPDATE ON public.document_intelligence
      FOR EACH ROW
      EXECUTE FUNCTION set_updated_at();
  END IF;
END$$;

ALTER TABLE public.document_intelligence ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'document_intelligence' AND policyname = 'document_intelligence_service_role_all'
  ) THEN
    CREATE POLICY document_intelligence_service_role_all ON public.document_intelligence
      FOR ALL
      USING (auth.role() = 'service_role')
      WITH CHECK (auth.role() = 'service_role');
  END IF;
END$$;
""".strip()

# DocumentSense RPC functions
DOCUMENT_INTELLIGENCE_RPCS_SQL = """
-- RPC function to get document intelligence
CREATE OR REPLACE FUNCTION get_document_intelligence(
  p_client_id UUID,
  p_document_id UUID
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

-- RPC function to upsert document intelligence
CREATE OR REPLACE FUNCTION upsert_document_intelligence(
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

-- RPC function to search documents by title
-- Note: Handles double-encoded JSON where intelligence may be stored as a JSON string
DROP FUNCTION IF EXISTS search_document_intelligence(UUID, TEXT, INTEGER);

CREATE OR REPLACE FUNCTION search_document_intelligence(
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

GRANT EXECUTE ON FUNCTION get_document_intelligence(UUID, UUID) TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION upsert_document_intelligence(UUID, UUID, TEXT, JSONB, TEXT, INTEGER) TO service_role;
GRANT EXECUTE ON FUNCTION search_document_intelligence(UUID, TEXT, INTEGER) TO authenticated, service_role;
""".strip()

# Align vector dimensions on existing columns in case they were created without
# a specified length in earlier runs.
VECTOR_DIMENSION_PATCH_SQL = """
do $$
begin
  if exists (select 1 from information_schema.columns where table_name = 'conversation_transcripts' and column_name = 'embeddings') then
    alter table public.conversation_transcripts
      alter column embeddings type vector(1024);
  end if;

  if exists (select 1 from information_schema.columns where table_name = 'documents' and column_name = 'embeddings') then
    alter table public.documents
      alter column embeddings type vector(1024);
  end if;

  alter table public.documents
    add column if not exists file_name text,
    add column if not exists file_size bigint,
    add column if not exists file_type text,
    add column if not exists document_type text default 'knowledge_base',
    add column if not exists processing_metadata jsonb default '{}'::jsonb;

  alter table public.documents
    add column if not exists embedding vector(1024),
    add column if not exists embedding_vec vector(1024);

  if exists (select 1 from information_schema.columns where table_name = 'documents' and column_name = 'embedding') then
    alter table public.documents
      alter column embedding type vector(1024);
  end if;

  if exists (select 1 from information_schema.columns where table_name = 'documents' and column_name = 'embedding_vec') then
    alter table public.documents
      alter column embedding_vec type vector(1024);
  end if;

  if exists (select 1 from information_schema.columns where table_name = 'document_chunks' and column_name = 'embeddings') then
    alter table public.document_chunks
      alter column embeddings type vector(1024);
  end if;

end$$;
""".strip()

CONVERSATION_PATCH_SQL = """
alter table if exists public.conversation_transcripts
  add column if not exists role text,
  add column if not exists sequence int,
  add column if not exists user_message text,
  add column if not exists assistant_message text,
  add column if not exists citations jsonb default '[]'::jsonb,
  add column if not exists source text,
  add column if not exists turn_id uuid default gen_random_uuid();

update public.conversation_transcripts
  set turn_id = coalesce(turn_id, gen_random_uuid());
""".strip()

AGENT_PERSONALITY_SQL = """
create table if not exists public.agent_personality (
  agent_id uuid primary key references public.agents(id) on delete cascade,
  openness int default 50,
  conscientiousness int default 50,
  extraversion int default 50,
  agreeableness int default 50,
  neuroticism int default 50,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
""".strip()

# HNSW indexes for fast vector similarity search (replaces IVFFlat)
# HNSW provides O(log n) search with ~98% recall vs exact search
# m=16: connections per node, ef_construction=64: build quality
HNSW_PATCH_SQL = """
-- Drop old IVFFlat indexes if they exist
DROP INDEX IF EXISTS public.documents_embeddings_ivfflat;
DROP INDEX IF EXISTS public.documents_embedding_ivfflat;
DROP INDEX IF EXISTS public.documents_embedding_vec_ivfflat;
DROP INDEX IF EXISTS public.document_chunks_embeddings_ivfflat;
DROP INDEX IF EXISTS public.conversation_transcripts_embeddings_ivfflat;

-- Create HNSW indexes (much faster for queries, handles updates well)
create index if not exists documents_embeddings_hnsw
  on public.documents using hnsw (embeddings vector_cosine_ops) with (m = 16, ef_construction = 64);

create index if not exists documents_embedding_hnsw
  on public.documents using hnsw (embedding vector_cosine_ops) with (m = 16, ef_construction = 64);

create index if not exists documents_embedding_vec_hnsw
  on public.documents using hnsw (embedding_vec vector_cosine_ops) with (m = 16, ef_construction = 64);

create index if not exists document_chunks_embeddings_hnsw
  on public.document_chunks using hnsw (embeddings vector_cosine_ops) with (m = 16, ef_construction = 64);

create index if not exists conversation_transcripts_embeddings_hnsw
  on public.conversation_transcripts using hnsw (embeddings vector_cosine_ops) with (m = 16, ef_construction = 64);
""".strip()

# Keep old name for backwards compatibility in sync function
IVFFLAT_PATCH_SQL = HNSW_PATCH_SQL

# RAG RPCs and missing helper tables/columns to keep tenant projects consistent
# NOTE: RPC functions include SET LOCAL hnsw.ef_search = 40 for HNSW index optimization
RAG_PATCH_SQL = """
-- Ensure profiles table exists for user context lookups
create table if not exists public.profiles (
  id uuid primary key default gen_random_uuid(),
  user_id uuid,
  email text,
  full_name text,
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists profiles_user_id_idx on public.profiles(user_id);

-- Enable RLS on profiles
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'profiles' AND policyname = 'profiles_service_role_all') THEN
    CREATE POLICY profiles_service_role_all ON public.profiles FOR ALL USING (auth.role() = 'service_role') WITH CHECK (auth.role() = 'service_role');
  END IF;
  -- Allow users to read their own profile
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'profiles' AND policyname = 'profiles_own_read') THEN
    CREATE POLICY profiles_own_read ON public.profiles FOR SELECT USING (auth.uid() = id OR auth.uid() = user_id);
  END IF;
END$$;

-- Ensure conversations has channel column for transcript storage
alter table if exists public.conversations
  add column if not exists channel text;

-- Add document_title and document_source_url to document_chunks for DocumentSense
-- These are denormalized fields for efficient RAG context building
alter table if exists public.document_chunks
  add column if not exists document_title text,
  add column if not exists document_source_url text;

-- match_documents with agent filtering (1024-dim embeddings)
-- NOTE: Searches document_chunks for fine-grained semantic matching
-- Returns chunk-level results with document metadata for better RAG accuracy
-- Uses agent_documents join table for agent assignment (standard pattern for all clients)
-- Now includes document_title from chunks for document identity in RAG context
-- Drop first to handle signature changes cleanly
DROP FUNCTION IF EXISTS public.match_documents(vector, text, float8, integer);

create or replace function public.match_documents(
  p_query_embedding vector,
  p_agent_slug text,
  p_match_threshold float8,
  p_match_count integer
)
returns table(
  id text,
  document_id text,
  title text,
  content text,
  chunk_index int,
  source_url text,
  source_type text,
  similarity float8,
  document_title text  -- Denormalized document title from chunk for RAG context
)
language plpgsql
as $$
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

-- match_conversation_transcripts_secure for user-specific transcript search
create or replace function public.match_conversation_transcripts_secure(
  query_embeddings vector,
  agent_slug_param text,
  user_id_param uuid,
  match_count integer default 5
)
returns table(
  conversation_id uuid,
  user_message text,
  agent_response text,
  similarity float8,
  created_at timestamptz
)
language plpgsql
as $$
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

grant execute on function public.match_documents(vector, text, float8, integer) to anon, authenticated, service_role;
grant execute on function public.match_conversation_transcripts_secure(vector, text, uuid, integer) to anon, authenticated, service_role;

-- Ensure per-agent RAG result limits exist
alter table if exists public.agents
  add column if not exists rag_results_limit int default 5;

-- Ensure Supertab paywall columns exist for agents
alter table if exists public.agents
  add column if not exists supertab_enabled boolean default false,
  add column if not exists supertab_experience_id text,
  add column if not exists supertab_price text,
  add column if not exists supertab_cta text;

-- Ensure chat mode columns exist for agents (voice/text/video enable/disable)
alter table if exists public.agents
  add column if not exists voice_chat_enabled boolean default true,
  add column if not exists text_chat_enabled boolean default true,
  add column if not exists video_chat_enabled boolean default false;

-- Ensure tools_config and rag_config columns exist for agents
alter table if exists public.agents
  add column if not exists tools_config jsonb default '{}'::jsonb,
  add column if not exists rag_config jsonb default '{}'::jsonb;

-- Ensure show_citations column exists
alter table if exists public.agents
  add column if not exists show_citations boolean default true;
""".strip()

SQL_ENDPOINT_TEMPLATE = "https://api.supabase.com/v1/projects/{project_ref}/database/query"


class SchemaSyncError(RuntimeError):
    """Raised when schema sync fails for a Supabase project."""


def project_ref_from_url(url: str) -> str:
    """Extract the Supabase project ref from the provided URL."""
    host = url.split("https://")[-1]
    return host.split(".supabase.co")[0]


def execute_sql(project_ref: str, token: str, sql: str) -> Tuple[bool, str]:
    """Execute raw SQL against a Supabase project via Management API."""
    url = SQL_ENDPOINT_TEMPLATE.format(project_ref=project_ref)
    headers = {
        "Authorization": f"Bearer {token}",
        "apikey": token,
        "Content-Type": "application/json",
    }
    payload = {"query": sql}
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    if response.status_code in (200, 201):
        return True, ""
    try:
        detail = response.json().get("msg") or response.text
    except Exception:
        detail = response.text
    return False, detail


def apply_schema(project_ref: str, token: str, include_indexes: bool = True) -> List[Tuple[str, bool, str]]:
    """Apply canonical schema patches to the given Supabase project.

    Returns a list of (step, success, detail) tuples for logging/telemetry.
    """
    results: List[Tuple[str, bool, str]] = []

    ok_base, detail_base = execute_sql(project_ref, token, BASE_SCHEMA_SQL)
    results.append(("base_schema", ok_base, detail_base))

    ok_vectors, detail_vectors = execute_sql(project_ref, token, VECTOR_DIMENSION_PATCH_SQL)
    results.append(("vector_dimensions", ok_vectors, detail_vectors))

    ok, detail = execute_sql(project_ref, token, CONVERSATION_PATCH_SQL)
    results.append(("conversation_patch", ok, detail))

    ok_personality, detail_personality = execute_sql(project_ref, token, AGENT_PERSONALITY_SQL)
    results.append(("agent_personality", ok_personality, detail_personality))

    ok_rag, detail_rag = execute_sql(project_ref, token, RAG_PATCH_SQL)
    results.append(("rag_patch", ok_rag, detail_rag))

    # User Overviews / UserSense tables
    ok_uo, detail_uo = execute_sql(project_ref, token, USER_OVERVIEWS_SQL)
    results.append(("user_overviews", ok_uo, detail_uo))

    # User Overview RPC functions
    ok_uo_rpcs, detail_uo_rpcs = execute_sql(project_ref, token, USER_OVERVIEW_RPCS_SQL)
    results.append(("user_overview_rpcs", ok_uo_rpcs, detail_uo_rpcs))

    # Tools table for tenant
    ok_tools, detail_tools = execute_sql(project_ref, token, TOOLS_SQL)
    results.append(("tools", ok_tools, detail_tools))

    # Conversation summaries table
    ok_summaries, detail_summaries = execute_sql(project_ref, token, CONVERSATION_SUMMARIES_SQL)
    results.append(("conversation_summaries", ok_summaries, detail_summaries))

    # DocumentSense / Document Intelligence tables
    ok_di, detail_di = execute_sql(project_ref, token, DOCUMENT_INTELLIGENCE_SQL)
    results.append(("document_intelligence", ok_di, detail_di))

    # Document Intelligence RPC functions
    ok_di_rpcs, detail_di_rpcs = execute_sql(project_ref, token, DOCUMENT_INTELLIGENCE_RPCS_SQL)
    results.append(("document_intelligence_rpcs", ok_di_rpcs, detail_di_rpcs))

    if include_indexes:
        ok_indexes, detail_indexes = execute_sql(project_ref, token, IVFFLAT_PATCH_SQL)
        results.append(("ivfflat_indexes", ok_indexes, detail_indexes))

    return results


def fetch_platform_clients(token: str) -> List[dict]:
    """Fetch clients from the platform database using the Management API."""
    project_ref = project_ref_from_url(settings.supabase_url)
    url = SQL_ENDPOINT_TEMPLATE.format(project_ref=project_ref)
    headers = {
        "Authorization": f"Bearer {token}",
        "apikey": token,
        "Content-Type": "application/json",
    }
    payload = {
        "query": (
            "select id, name, supabase_url, supabase_service_role_key, provisioning_status "
            "from clients"
        )
    }
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict):
        return data.get("result") or data.get("data") or []
    if isinstance(data, list):
        return data
    return []


__all__ = [
    "SchemaSyncError",
    "apply_schema",
    "project_ref_from_url",
    "execute_sql",
    "fetch_platform_clients",
]

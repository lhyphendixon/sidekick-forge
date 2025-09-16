-- Tools and Agent Tools schema for Platform (shared database)
-- Safe, idempotent migration for Supabase

-- Ensure required extension for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Updated-at trigger helper (idempotent)
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Canonical tools catalog (global + client-scoped records stored centrally)
CREATE TABLE IF NOT EXISTS public.tools (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  slug TEXT NOT NULL,
  description TEXT,
  type TEXT NOT NULL,                 -- mcp | n8n | sidekick | code
  scope TEXT NOT NULL DEFAULT 'global', -- global | client
  client_id UUID,                     -- nullable; populated when scope='client'
  icon_url TEXT,
  config JSONB NOT NULL DEFAULT '{}'::jsonb,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Partial unique indexes to enforce slug uniqueness within scope
-- Global: slug unique among global entries
CREATE UNIQUE INDEX IF NOT EXISTS uniq_tools_global_slug
  ON public.tools (slug)
  WHERE scope = 'global';

-- Client: slug unique per client when scope = 'client'
CREATE UNIQUE INDEX IF NOT EXISTS uniq_tools_client_slug
  ON public.tools (client_id, slug)
  WHERE scope = 'client';

-- Helpful indexes
CREATE INDEX IF NOT EXISTS idx_tools_scope ON public.tools (scope);
CREATE INDEX IF NOT EXISTS idx_tools_client_id ON public.tools (client_id);
CREATE INDEX IF NOT EXISTS idx_tools_type ON public.tools (type);
CREATE INDEX IF NOT EXISTS idx_tools_enabled ON public.tools (enabled);

-- Updated-at trigger on tools
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger WHERE tgname = 'trg_tools_set_updated_at'
  ) THEN
    CREATE TRIGGER trg_tools_set_updated_at
      BEFORE UPDATE ON public.tools
      FOR EACH ROW
      EXECUTE FUNCTION set_updated_at();
  END IF;
END$$;

-- Agent-to-Tool assignments (lives in platform DB)
CREATE TABLE IF NOT EXISTS public.agent_tools (
  agent_id UUID NOT NULL REFERENCES public.agents(id) ON DELETE CASCADE,
  tool_id UUID NOT NULL REFERENCES public.tools(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (agent_id, tool_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_tools_tool_id ON public.agent_tools (tool_id);

-- Row Level Security (RLS)
-- Default posture: service role full access; other roles are denied unless additional policies are added later.
ALTER TABLE public.tools ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_tools ENABLE ROW LEVEL SECURITY;

-- Allow service role to do everything
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'tools' AND policyname = 'tools_service_role_all'
  ) THEN
    CREATE POLICY tools_service_role_all ON public.tools
      FOR ALL
      USING (auth.role() = 'service_role')
      WITH CHECK (auth.role() = 'service_role');
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'agent_tools' AND policyname = 'agent_tools_service_role_all'
  ) THEN
    CREATE POLICY agent_tools_service_role_all ON public.agent_tools
      FOR ALL
      USING (auth.role() = 'service_role')
      WITH CHECK (auth.role() = 'service_role');
  END IF;
END$$;

-- Optional: comments
COMMENT ON TABLE public.tools IS 'Canonical Abilities catalog. Global + centrally stored client-scoped tools.';
COMMENT ON TABLE public.agent_tools IS 'Assignments of tools to agents (platform-scoped).';



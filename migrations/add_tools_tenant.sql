-- Tools schema for Tenant (client) Supabase databases
-- Safe, idempotent migration

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS public.tools (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  slug TEXT NOT NULL,
  description TEXT,
  type TEXT NOT NULL,                 -- mcp | n8n | sidekick | code
  icon_url TEXT,
  config JSONB NOT NULL DEFAULT '{}'::jsonb,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Enforce unique slug across tenant (or make partial on enabled if desired)
CREATE UNIQUE INDEX IF NOT EXISTS uniq_tenant_tools_slug ON public.tools (slug);

-- Helpful indexes
CREATE INDEX IF NOT EXISTS idx_tenant_tools_type ON public.tools (type);
CREATE INDEX IF NOT EXISTS idx_tenant_tools_enabled ON public.tools (enabled);

-- Updated-at trigger
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

-- RLS posture
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

COMMENT ON TABLE public.tools IS 'Tenant-local Abilities (client-scoped).';



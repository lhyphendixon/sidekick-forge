-- RBAC Tables Migration for Sidekick Forge Platform
-- Creates roles and tenant_memberships tables for proper role-based access control
-- Safe, idempotent migration

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

-- ============================================================================
-- ROLES TABLE
-- Defines available roles in the system (platform-wide and client-scoped)
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.roles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  key TEXT NOT NULL UNIQUE,
  scope TEXT NOT NULL DEFAULT 'client',  -- 'platform' or 'client'
  description TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Updated-at trigger on roles
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger WHERE tgname = 'trg_roles_set_updated_at'
  ) THEN
    CREATE TRIGGER trg_roles_set_updated_at
      BEFORE UPDATE ON public.roles
      FOR EACH ROW
      EXECUTE FUNCTION set_updated_at();
  END IF;
END$$;

-- Seed default roles
INSERT INTO public.roles (key, scope, description)
VALUES
  ('super_admin', 'platform', 'Platform-wide super administrator with full access'),
  ('admin', 'client', 'Client administrator with full access to client resources'),
  ('subscriber', 'client', 'Client subscriber with limited access')
ON CONFLICT (key) DO NOTHING;

-- ============================================================================
-- TENANT_MEMBERSHIPS TABLE
-- Maps users to clients with specific roles
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.tenant_memberships (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  client_id UUID NOT NULL REFERENCES public.clients(id) ON DELETE CASCADE,
  role_id UUID NOT NULL REFERENCES public.roles(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'active',  -- 'active', 'suspended', 'pending'
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(user_id, client_id, role_id)
);

-- Indexes for tenant_memberships
CREATE INDEX IF NOT EXISTS idx_tenant_memberships_user_id ON public.tenant_memberships(user_id);
CREATE INDEX IF NOT EXISTS idx_tenant_memberships_client_id ON public.tenant_memberships(client_id);
CREATE INDEX IF NOT EXISTS idx_tenant_memberships_role_id ON public.tenant_memberships(role_id);
CREATE INDEX IF NOT EXISTS idx_tenant_memberships_status ON public.tenant_memberships(status);

-- Updated-at trigger on tenant_memberships
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger WHERE tgname = 'trg_tenant_memberships_set_updated_at'
  ) THEN
    CREATE TRIGGER trg_tenant_memberships_set_updated_at
      BEFORE UPDATE ON public.tenant_memberships
      FOR EACH ROW
      EXECUTE FUNCTION set_updated_at();
  END IF;
END$$;

-- ============================================================================
-- PLATFORM_ROLE_MEMBERSHIPS TABLE
-- Maps users to platform-level roles (like super_admin)
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.platform_role_memberships (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  role_id UUID NOT NULL REFERENCES public.roles(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(user_id, role_id)
);

-- Indexes for platform_role_memberships
CREATE INDEX IF NOT EXISTS idx_platform_role_memberships_user_id ON public.platform_role_memberships(user_id);
CREATE INDEX IF NOT EXISTS idx_platform_role_memberships_role_id ON public.platform_role_memberships(role_id);

-- Updated-at trigger on platform_role_memberships
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger WHERE tgname = 'trg_platform_role_memberships_set_updated_at'
  ) THEN
    CREATE TRIGGER trg_platform_role_memberships_set_updated_at
      BEFORE UPDATE ON public.platform_role_memberships
      FOR EACH ROW
      EXECUTE FUNCTION set_updated_at();
  END IF;
END$$;

-- ============================================================================
-- ROW LEVEL SECURITY (RLS)
-- ============================================================================
ALTER TABLE public.roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tenant_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.platform_role_memberships ENABLE ROW LEVEL SECURITY;

-- Allow service role full access to all tables
DO $$
BEGIN
  -- Roles table policies
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'roles' AND policyname = 'roles_service_role_all'
  ) THEN
    CREATE POLICY roles_service_role_all ON public.roles
      FOR ALL
      USING (auth.role() = 'service_role')
      WITH CHECK (auth.role() = 'service_role');
  END IF;

  -- Tenant memberships policies
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'tenant_memberships' AND policyname = 'tenant_memberships_service_role_all'
  ) THEN
    CREATE POLICY tenant_memberships_service_role_all ON public.tenant_memberships
      FOR ALL
      USING (auth.role() = 'service_role')
      WITH CHECK (auth.role() = 'service_role');
  END IF;

  -- Platform role memberships policies
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'platform_role_memberships' AND policyname = 'platform_role_memberships_service_role_all'
  ) THEN
    CREATE POLICY platform_role_memberships_service_role_all ON public.platform_role_memberships
      FOR ALL
      USING (auth.role() = 'service_role')
      WITH CHECK (auth.role() = 'service_role');
  END IF;
END$$;

-- Comments
COMMENT ON TABLE public.roles IS 'Defines available roles for platform and client-level access control';
COMMENT ON TABLE public.tenant_memberships IS 'Maps users to clients with specific roles for multi-tenant access';
COMMENT ON TABLE public.platform_role_memberships IS 'Maps users to platform-level roles like super_admin';

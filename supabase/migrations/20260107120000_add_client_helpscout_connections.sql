-- Migration: Create client_helpscout_connections table
-- Purpose: Persist HelpScout OAuth tokens per client
-- Created: 2026-01-07

BEGIN;

CREATE TABLE IF NOT EXISTS public.client_helpscout_connections (
    client_id uuid PRIMARY KEY REFERENCES public.clients(id) ON DELETE CASCADE,
    access_token text NOT NULL,
    refresh_token text,
    token_type text,
    expires_at timestamptz,
    extra jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_client_helpscout_connections_updated_at
    ON public.client_helpscout_connections(updated_at DESC);

CREATE OR REPLACE FUNCTION public.set_client_helpscout_connections_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS client_helpscout_connections_set_updated_at ON public.client_helpscout_connections;

CREATE TRIGGER client_helpscout_connections_set_updated_at
    BEFORE UPDATE ON public.client_helpscout_connections
    FOR EACH ROW
    EXECUTE FUNCTION public.set_client_helpscout_connections_updated_at();

ALTER TABLE public.client_helpscout_connections ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'client_helpscout_connections'
          AND policyname = 'Service role full access to client_helpscout_connections'
    ) THEN
        CREATE POLICY "Service role full access to client_helpscout_connections"
            ON public.client_helpscout_connections
            FOR ALL
            TO service_role
            USING (true)
            WITH CHECK (true);
    END IF;
END $$;

COMMENT ON TABLE public.client_helpscout_connections IS 'Stores OAuth tokens returned from HelpScout for each Sidekick Forge client.';
COMMENT ON COLUMN public.client_helpscout_connections.extra IS 'Raw token payload returned by HelpScout (JSON).';

COMMIT;

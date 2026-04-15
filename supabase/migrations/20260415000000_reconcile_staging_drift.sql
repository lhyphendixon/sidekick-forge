-- Reconcile schema drift from the Supabase staging branch to production.
--
-- Six tables were created directly on the staging branch (via SQL editor /
-- ad-hoc DDL) without corresponding migration files in the repo. v2.13.0
-- application code references all of them, so deploying v2.13.0 without
-- these definitions would crash any request that hits the referencing
-- service. This migration reconstructs the exact staging schema as of the
-- v2.13.0 deploy preflight (2026-04-15) in a fully idempotent form so it
-- applies cleanly to both production (fresh create) and staging (no-op).

BEGIN;

-- ─── updated_at trigger helpers ─────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.set_client_evernote_connections_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.set_client_trello_connections_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

-- ─── activity_log ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.activity_log (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    activity_type character varying(50) NOT NULL,
    action character varying(50) NOT NULL,
    client_id uuid,
    agent_id uuid,
    user_id uuid,
    resource_type character varying(50),
    resource_id character varying(255),
    resource_name character varying(255),
    details jsonb DEFAULT '{}'::jsonb,
    status character varying(20) DEFAULT 'success'::character varying,
    error_message text,
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT activity_log_pkey PRIMARY KEY (id)
);

DO $$ BEGIN
    ALTER TABLE public.activity_log
        ADD CONSTRAINT activity_log_client_id_fkey
        FOREIGN KEY (client_id) REFERENCES public.clients(id) ON DELETE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS idx_activity_log_agent   ON public.activity_log USING btree (agent_id);
CREATE INDEX IF NOT EXISTS idx_activity_log_client  ON public.activity_log USING btree (client_id);
CREATE INDEX IF NOT EXISTS idx_activity_log_created ON public.activity_log USING btree (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_log_type    ON public.activity_log USING btree (activity_type);

ALTER TABLE public.activity_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role full access on activity_log" ON public.activity_log;
CREATE POLICY "Service role full access on activity_log"
    ON public.activity_log
    USING (auth.role() = 'service_role'::text);

DROP POLICY IF EXISTS "Users can read activities for their clients" ON public.activity_log;
CREATE POLICY "Users can read activities for their clients"
    ON public.activity_log FOR SELECT
    USING (
        auth.role() = 'authenticated'::text
        AND client_id IN (
            SELECT clients.id FROM public.clients
            WHERE clients.owner_user_id = auth.uid()
        )
    );

-- ─── campaign_scans ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.campaign_scans (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    client_id text NOT NULL,
    agent_slug text NOT NULL,
    user_id text NOT NULL,
    sender_email text NOT NULL,
    email_subject text,
    email_body_plain text,
    email_body_html text,
    email_images jsonb DEFAULT '[]'::jsonb,
    status text DEFAULT 'pending'::text NOT NULL,
    results jsonb,
    error text,
    created_at timestamp with time zone DEFAULT now(),
    completed_at timestamp with time zone,
    CONSTRAINT campaign_scans_pkey PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_campaign_scans_lookup
    ON public.campaign_scans USING btree (agent_slug, user_id, status);

-- ─── client_notion_connections ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.client_notion_connections (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    client_id text NOT NULL,
    access_token text NOT NULL,
    workspace_name text,
    workspace_id text,
    bot_id text,
    extra jsonb DEFAULT '{}'::jsonb,
    updated_at timestamp with time zone DEFAULT now(),
    created_at timestamp with time zone DEFAULT now(),
    CONSTRAINT client_notion_connections_pkey PRIMARY KEY (id),
    CONSTRAINT client_notion_connections_client_id_key UNIQUE (client_id)
);

CREATE INDEX IF NOT EXISTS idx_notion_conn_client
    ON public.client_notion_connections USING btree (client_id);

-- ─── client_evernote_connections ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.client_evernote_connections (
    client_id uuid NOT NULL,
    access_token text NOT NULL,
    token_type text,
    expires_at timestamp with time zone,
    extra jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT client_evernote_connections_pkey PRIMARY KEY (client_id)
);

COMMENT ON TABLE  public.client_evernote_connections       IS 'Stores OAuth tokens returned from Evernote for each Sidekick Forge client.';
COMMENT ON COLUMN public.client_evernote_connections.extra IS 'Raw token payload returned by Evernote (JSON).';

DO $$ BEGIN
    ALTER TABLE public.client_evernote_connections
        ADD CONSTRAINT client_evernote_connections_client_id_fkey
        FOREIGN KEY (client_id) REFERENCES public.clients(id) ON DELETE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS idx_client_evernote_connections_updated_at
    ON public.client_evernote_connections USING btree (updated_at DESC);

DROP TRIGGER IF EXISTS client_evernote_connections_set_updated_at ON public.client_evernote_connections;
CREATE TRIGGER client_evernote_connections_set_updated_at
    BEFORE UPDATE ON public.client_evernote_connections
    FOR EACH ROW
    EXECUTE FUNCTION public.set_client_evernote_connections_updated_at();

ALTER TABLE public.client_evernote_connections ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role full access to client_evernote_connections" ON public.client_evernote_connections;
CREATE POLICY "Service role full access to client_evernote_connections"
    ON public.client_evernote_connections TO service_role USING (true) WITH CHECK (true);

-- ─── client_trello_connections ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.client_trello_connections (
    client_id uuid NOT NULL,
    api_key text NOT NULL,
    token text NOT NULL,
    member_name text,
    extra jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT client_trello_connections_pkey PRIMARY KEY (client_id)
);

COMMENT ON TABLE  public.client_trello_connections       IS 'Stores Trello API key + user token for each Sidekick Forge client.';
COMMENT ON COLUMN public.client_trello_connections.token IS 'User-authorized Trello token (can be set to never expire).';

DO $$ BEGIN
    ALTER TABLE public.client_trello_connections
        ADD CONSTRAINT client_trello_connections_client_id_fkey
        FOREIGN KEY (client_id) REFERENCES public.clients(id) ON DELETE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS idx_client_trello_connections_updated_at
    ON public.client_trello_connections USING btree (updated_at DESC);

DROP TRIGGER IF EXISTS client_trello_connections_set_updated_at ON public.client_trello_connections;
CREATE TRIGGER client_trello_connections_set_updated_at
    BEFORE UPDATE ON public.client_trello_connections
    FOR EACH ROW
    EXECUTE FUNCTION public.set_client_trello_connections_updated_at();

ALTER TABLE public.client_trello_connections ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role full access to client_trello_connections" ON public.client_trello_connections;
CREATE POLICY "Service role full access to client_trello_connections"
    ON public.client_trello_connections TO service_role USING (true) WITH CHECK (true);

-- ─── verified_email_links ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.verified_email_links (
    email_address text NOT NULL,
    user_id uuid NOT NULL,
    client_id text NOT NULL,
    verified_at timestamp with time zone,
    verification_code text,
    code_expires_at timestamp with time zone,
    pending_message text,
    pending_subject text,
    pending_message_id text,
    pending_agent_slug text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT verified_email_links_pkey PRIMARY KEY (email_address, client_id)
);

CREATE INDEX IF NOT EXISTS idx_verified_email_links_client
    ON public.verified_email_links USING btree (client_id);
CREATE INDEX IF NOT EXISTS idx_verified_email_links_user
    ON public.verified_email_links USING btree (user_id);

COMMIT;

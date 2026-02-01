-- Migration: Add per-client HelpScout OAuth app credentials
-- Purpose: Allow each client to use their own HelpScout OAuth App
-- Created: 2026-02-01

BEGIN;

-- Add columns for storing per-client OAuth app credentials
ALTER TABLE public.client_helpscout_connections
    ADD COLUMN IF NOT EXISTS oauth_client_id text,
    ADD COLUMN IF NOT EXISTS oauth_client_secret text;

COMMENT ON COLUMN public.client_helpscout_connections.oauth_client_id IS 'HelpScout OAuth App ID for this client';
COMMENT ON COLUMN public.client_helpscout_connections.oauth_client_secret IS 'HelpScout OAuth App Secret for this client (encrypted recommended)';

COMMIT;

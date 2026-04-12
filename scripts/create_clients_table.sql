-- =================================================================
-- Schema for the 'clients' table
-- =================================================================
-- This table stores the configuration for each client (tenant) of the
-- Sidekick Forge platform. It includes encrypted credentials for
-- their individual Supabase projects.

CREATE TABLE IF NOT EXISTS clients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Client's Supabase Project Credentials (Encrypted)
    -- These fields should be encrypted using pgsodium or a similar mechanism.
    -- For now, we will store them as text, but encryption should be added.
    supabase_url TEXT,
    supabase_service_role_key TEXT,

    -- Client's LiveKit Credentials (if they have their own, otherwise platform's are used)
    livekit_url TEXT,
    livekit_api_key TEXT,
    livekit_api_secret TEXT,

    -- API keys for various third-party services, specific to the client
    openai_api_key TEXT,
    groq_api_key TEXT,
    deepgram_api_key TEXT,
    elevenlabs_api_key TEXT,
    cartesia_api_key TEXT,
    speechify_api_key TEXT,
    deepinfra_api_key TEXT,
    replicate_api_key TEXT,
    novita_api_key TEXT,
    cohere_api_key TEXT,
    siliconflow_api_key TEXT,
    jina_api_key TEXT,
    anthropic_api_key TEXT,

    -- Custom JSONB field for any other settings
    additional_settings JSONB
);

-- Add a trigger to automatically update the 'updated_at' timestamp
CREATE OR REPLACE FUNCTION trigger_set_timestamp()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER set_clients_timestamp
BEFORE UPDATE ON clients
FOR EACH ROW
EXECUTE PROCEDURE trigger_set_timestamp();

-- Add a comment to the table for clarity
COMMENT ON TABLE clients IS 'Stores configuration and encrypted credentials for each tenant of the Sidekick Forge platform.'; 
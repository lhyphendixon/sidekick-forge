-- Create agents table for production database (eukudpgfpihxsypulopm)
-- This table stores agent configurations for the Sidekick Forge platform

CREATE TABLE IF NOT EXISTS public.agents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  slug text NOT NULL UNIQUE,
  description text,
  system_prompt text NOT NULL,
  voice_settings jsonb DEFAULT '{}'::jsonb,
  ui_settings jsonb DEFAULT '{}'::jsonb,
  enabled boolean DEFAULT true,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now(),
  n8n_text_webhook_url text,
  n8n_rag_webhook_url text,
  provider_config jsonb DEFAULT '{"type": "ultravox", "llm": {"provider": "openai", "model": "gpt-4", "temperature": 0.7}, "stt": {"provider": "deepgram", "model": "nova-2"}, "tts": {"provider": "openai", "model": "tts-1", "voice": "alloy"}}'::jsonb,
  livekit_enabled boolean DEFAULT false,
  agent_image text,
  show_citations boolean DEFAULT true
);

CREATE INDEX IF NOT EXISTS idx_agents_slug ON public.agents(slug);

-- Enable RLS
ALTER TABLE public.agents ENABLE ROW LEVEL SECURITY;

-- Add policies for agents table
CREATE POLICY "Allow public read access to agents"
  ON public.agents FOR SELECT
  USING (true);

CREATE POLICY "Allow service role all access to agents"
  ON public.agents FOR ALL
  USING (auth.jwt() ->> 'role' = 'service_role');

-- Add comment
COMMENT ON TABLE public.agents IS 'Stores AI agent configurations for the Sidekick Forge platform';


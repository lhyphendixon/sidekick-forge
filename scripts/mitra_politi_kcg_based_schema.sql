-- Mitra Politi client schema baseline aligned with Autonomite essentials
-- Based on the successful KCG schema implementation
-- Run this in Mitra Politi project's SQL editor
-- Database: https://uyswpsluhkebudoqdnhk.supabase.co

-- Extensions
create extension if not exists pgcrypto;
create extension if not exists vector;

-- Agents
create table if not exists public.agents (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  slug text not null unique,
  description text,
  system_prompt text not null,
  voice_settings jsonb default '{}'::jsonb,
  ui_settings jsonb default '{}'::jsonb,
  enabled boolean default true,
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  n8n_text_webhook_url text,
  n8n_rag_webhook_url text,
  provider_config jsonb default '{}'::jsonb,
  livekit_enabled boolean default false,
  agent_image text
);
create index if not exists idx_agents_slug on public.agents(slug);

-- Agent configurations
create table if not exists public.agent_configurations (
  id uuid primary key default gen_random_uuid(),
  agent_id uuid references public.agents(id) on delete cascade,
  agent_slug text unique,
  agent_name text,
  system_prompt text,
  tts_provider text,
  tts_model text,
  tts_voice text,
  voice_context_webhook_url text,
  text_context_webhook_url text,
  tools_config jsonb default '{}'::jsonb,
  last_updated timestamptz default now(),
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);
create index if not exists idx_agent_cfg_slug on public.agent_configurations(agent_slug);

-- Conversations (minimal)
create table if not exists public.conversations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid,
  summary text,
  created_at timestamptz default timezone('utc', now()),
  updated_at timestamptz default timezone('utc', now()),
  conversation_title text,
  status text,
  tags text[],
  last_interaction_at timestamptz,
  ai_context_state json,
  is_favorite boolean,
  topic_or_category text,
  sentiment_summary text,
  keywords text[],
  language text,
  duration int,
  feedback_score int,
  associated_goals_or_tasks text[],
  ai_version text,
  metadata json,
  is_private boolean,
  channel text,
  agent_id uuid references public.agents(id)
);

-- Messages (minimal)
create table if not exists public.messages (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  role text not null check (role in ('user','assistant')),
  content text not null,
  created_at timestamptz default timezone('utc', now())
);

-- Conversation transcripts (voice/text unified)
create table if not exists public.conversation_transcripts (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.conversations(id) on delete cascade,
  user_id uuid,
  session_id text,
  transcript jsonb,
  created_at timestamptz default timezone('utc', now()),
  updated_at timestamptz default timezone('utc', now()),
  metadata jsonb,
  channel text default 'voice',
  embeddings vector(1024),
  agent_id uuid references public.agents(id),
  content text,
  message text,
  role text,
  sequence int,
  user_message text,
  assistant_message text
);

-- Documents (RAG)
create table if not exists public.documents (
  id bigserial primary key,
  content text,
  embedding vector(1024),
  summary text,
  metadata jsonb,
  agent_permissions jsonb default '[]'::jsonb,
  parent_document_id bigint references public.documents(id),
  chunk_index int default 0,
  is_chunk boolean default false,
  original_filename text,
  file_size bigint,
  processing_status text default 'pending',
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  document_type varchar default 'user',
  user_id uuid,
  title varchar,
  file_name varchar,
  file_type varchar,
  file_url text,
  status varchar default 'processing',
  embeddings vector(1024),
  chunk_count int default 0,
  processing_metadata jsonb default '{}'::jsonb
);

create table if not exists public.document_chunks (
  id uuid primary key default gen_random_uuid(),
  document_id bigint references public.documents(id) on delete cascade,
  chunk_index int,
  content text,
  embeddings vector(1024),
  chunk_metadata jsonb default '{}'::jsonb,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

-- Global settings (for synced keys)
create table if not exists public.global_settings (
  id uuid primary key default gen_random_uuid(),
  setting_key varchar unique not null,
  setting_value text,
  is_encrypted boolean default false,
  description text,
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  is_secret boolean default false
);

-- Basic indexes
create index if not exists idx_conv_agent on public.conversations(agent_id);
create index if not exists idx_transcripts_conv on public.conversation_transcripts(conversation_id);
create index if not exists idx_docs_embeddings on public.documents using ivfflat (embeddings vector_cosine_ops) with (lists = 100);
create index if not exists idx_doc_chunks_embeddings on public.document_chunks using ivfflat (embeddings vector_cosine_ops) with (lists = 100);

-- Done


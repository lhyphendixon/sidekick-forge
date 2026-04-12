-- Mitra Politi Database Schema Setup
-- Complete schema matching Autonomite Agent requirements
-- Database: https://uyswpsluhkebudoqdnhk.supabase.co

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Create agents table
CREATE TABLE IF NOT EXISTS agents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT,
    slug TEXT UNIQUE,
    description TEXT,
    system_prompt TEXT,
    voice_settings TEXT,
    ui_settings JSONB,
    enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    n8n_text_webhook_url TEXT,
    n8n_rag_webhook_url TEXT,
    provider_config JSONB,
    livekit_enabled BOOLEAN DEFAULT true,
    agent_image TEXT
);

-- Create conversations table
CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID,
    summary TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    conversation_title TEXT,
    status TEXT,
    tags TEXT,
    metadata JSONB,
    channel TEXT,
    agent_id TEXT
);

-- Create conversation_transcripts table with vector embeddings
CREATE TABLE IF NOT EXISTS conversation_transcripts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
    user_id UUID,
    session_id TEXT,
    transcript JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    metadata JSONB,
    channel TEXT,
    embeddings vector(1024),
    agent_id UUID,
    content TEXT,
    message TEXT,
    role TEXT,
    sequence INTEGER,
    user_message TEXT,
    assistant_message TEXT
);

-- Create documents table with vector embeddings
-- Note: 'embedding' (4096) is legacy, 'embeddings' (1024) is actively used
CREATE TABLE IF NOT EXISTS documents (
    id BIGSERIAL PRIMARY KEY,
    content TEXT,
    embedding vector(4096),  -- Legacy column, no index due to ivfflat 2000 dim limit
    summary TEXT,
    metadata JSONB,
    agent_permissions TEXT[],
    parent_document_id TEXT,
    chunk_index INTEGER,
    is_chunk BOOLEAN DEFAULT false,
    original_filename TEXT,
    file_size INTEGER,
    processing_status TEXT DEFAULT 'pending',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    document_type TEXT,
    user_id UUID,
    title TEXT,
    file_name TEXT,
    file_type TEXT,
    file_url TEXT,
    status TEXT DEFAULT 'pending',
    embeddings vector(1024),  -- Active column with ivfflat index
    chunk_count INTEGER DEFAULT 0,
    processing_metadata JSONB
);

-- Create document_chunks table
CREATE TABLE IF NOT EXISTS document_chunks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id BIGINT REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER,
    content TEXT,
    embeddings vector(1024),
    chunk_metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create agent_documents junction table
CREATE TABLE IF NOT EXISTS agent_documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id UUID,
    document_id BIGINT REFERENCES documents(id) ON DELETE CASCADE,
    enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create global_settings table
CREATE TABLE IF NOT EXISTS global_settings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    setting_key TEXT UNIQUE,
    setting_value TEXT,
    is_encrypted BOOLEAN DEFAULT false,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    is_secret BOOLEAN DEFAULT false
);

-- Create messages table (placeholder for future use)
CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
    user_id UUID,
    content TEXT,
    role TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    metadata JSONB
);

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_conversation_transcripts_user_id ON conversation_transcripts(user_id);
CREATE INDEX IF NOT EXISTS idx_conversation_transcripts_conversation_id ON conversation_transcripts(conversation_id);
CREATE INDEX IF NOT EXISTS idx_conversation_transcripts_session_id ON conversation_transcripts(session_id);
CREATE INDEX IF NOT EXISTS idx_conversation_transcripts_channel ON conversation_transcripts(channel);
CREATE INDEX IF NOT EXISTS idx_conversation_transcripts_agent_id ON conversation_transcripts(agent_id);

CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_documents_processing_status ON documents(processing_status);

CREATE INDEX IF NOT EXISTS idx_document_chunks_document_id ON document_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_document_chunks_chunk_index ON document_chunks(document_id, chunk_index);

CREATE INDEX IF NOT EXISTS idx_agent_documents_agent_id ON agent_documents(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_documents_document_id ON agent_documents(document_id);
CREATE INDEX IF NOT EXISTS idx_agent_documents_enabled ON agent_documents(enabled);

CREATE INDEX IF NOT EXISTS idx_agents_slug ON agents(slug);
CREATE INDEX IF NOT EXISTS idx_agents_enabled ON agents(enabled);

CREATE INDEX IF NOT EXISTS idx_global_settings_key ON global_settings(setting_key);

-- Create vector indexes using ivfflat for 1024-dimensional vectors only
-- Note: ivfflat has a 2000 dimension limit, so we only index the 1024-dim 'embeddings' columns
-- The 4096-dim 'embedding' column uses sequential scan
CREATE INDEX IF NOT EXISTS idx_document_chunks_embeddings ON document_chunks USING ivfflat (embeddings vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_documents_embeddings ON documents USING ivfflat (embeddings vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_transcripts_embeddings ON conversation_transcripts USING ivfflat (embeddings vector_cosine_ops) WITH (lists = 100);

-- Create vector similarity search function for documents (simple version)
CREATE OR REPLACE FUNCTION match_documents(
    query_embedding vector,
    match_count integer DEFAULT 5
)
RETURNS TABLE(
    id uuid,
    content text,
    metadata jsonb,
    similarity float8
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        dc.id,
        dc.content,
        dc.chunk_metadata as metadata,
        1 - (dc.embeddings <=> query_embedding) as similarity
    FROM document_chunks dc
    WHERE dc.embeddings IS NOT NULL
    ORDER BY dc.embeddings <=> query_embedding
    LIMIT match_count;
END;
$$;

-- Create vector similarity search function for conversation transcripts (secure version)
CREATE OR REPLACE FUNCTION match_conversation_transcripts_secure(
    query_embeddings vector,
    agent_slug_param text,
    user_id_param uuid,
    match_count integer DEFAULT 5
)
RETURNS TABLE(
    conversation_id uuid,
    user_message text,
    agent_response text,
    similarity float8,
    created_at timestamp with time zone
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT 
    u.conversation_id,
    u.content                      AS user_message,
    a.content                      AS agent_response,
    1 - (u.embeddings <=> query_embeddings) AS similarity,
    u.created_at
  FROM conversation_transcripts u
  JOIN conversation_transcripts a ON a.conversation_id = u.conversation_id AND a.role = 'assistant'
  JOIN agents ag ON u.agent_id = ag.id
  WHERE u.role = 'user'
    AND u.embeddings IS NOT NULL
    AND u.user_id = user_id_param
    AND ag.slug = agent_slug_param
  ORDER BY u.embeddings <=> query_embeddings
  LIMIT match_count;
END;
$$;

-- Create match_documents function with agent filtering
CREATE OR REPLACE FUNCTION match_documents(
    p_query_embedding vector,
    p_agent_slug text,
    p_match_threshold float8,
    p_match_count integer
)
RETURNS TABLE(
    id bigint,
    title text,
    content text,
    similarity float8
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT 
    d.id,
    d.title::text        AS title,
    d.content,
    1 - (d.embeddings <=> p_query_embedding) AS similarity
  FROM documents d
  JOIN agent_documents ad ON d.id = ad.document_id
  JOIN agents a ON ad.agent_id = a.id
  WHERE a.slug = p_agent_slug
    AND 1 - (d.embeddings <=> p_query_embedding) > p_match_threshold
  ORDER BY d.embeddings <=> p_query_embedding
  LIMIT p_match_count;
END;
$$;

-- Create match_conversation_transcripts_agent function
CREATE OR REPLACE FUNCTION match_conversation_transcripts_agent(
    query_embeddings vector,
    user_id_param uuid,
    agent_slug_param text,
    match_count integer DEFAULT 3
)
RETURNS TABLE(
    id uuid,
    conversation_id uuid,
    content text,
    role text,
    metadata jsonb,
    created_at timestamp with time zone,
    similarity float8
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT 
        ct.id,
        ct.conversation_id,
        ct.content,
        ct.role,
        ct.metadata,
        ct.created_at,
        1 - (ct.embeddings <=> query_embeddings) AS similarity
    FROM public.conversation_transcripts ct
    JOIN public.conversations c ON ct.conversation_id = c.id
    WHERE 
        ct.embeddings IS NOT NULL
        AND ct.user_id = user_id_param
        AND c.metadata->>'agent_slug' = agent_slug_param
    ORDER BY ct.embeddings <=> query_embeddings
    LIMIT match_count;
END;
$$;

-- Create RLS policies (optional, but recommended for security)
ALTER TABLE agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_transcripts ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_documents ENABLE ROW LEVEL SECURITY;

-- Create update trigger for updated_at columns
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Apply update trigger to all tables with updated_at
CREATE TRIGGER update_agents_updated_at BEFORE UPDATE ON agents 
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_conversations_updated_at BEFORE UPDATE ON conversations 
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_conversation_transcripts_updated_at BEFORE UPDATE ON conversation_transcripts 
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_documents_updated_at BEFORE UPDATE ON documents 
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_document_chunks_updated_at BEFORE UPDATE ON document_chunks 
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_agent_documents_updated_at BEFORE UPDATE ON agent_documents 
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_global_settings_updated_at BEFORE UPDATE ON global_settings 
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Grant necessary permissions (adjust as needed)
GRANT USAGE ON SCHEMA public TO anon, authenticated;
GRANT ALL ON ALL TABLES IN SCHEMA public TO anon, authenticated;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO anon, authenticated;
GRANT ALL ON ALL FUNCTIONS IN SCHEMA public TO anon, authenticated;

-- Insert default global settings if needed
INSERT INTO global_settings (setting_key, setting_value, description) 
VALUES 
    ('embedding_provider', 'siliconflow', 'Default embedding provider'),
    ('embedding_model', 'BAAI/bge-m3', 'Default embedding model'),
    ('default_llm_provider', 'groq', 'Default LLM provider'),
    ('default_stt_provider', 'deepgram', 'Default STT provider'),
    ('default_tts_provider', 'cartesia', 'Default TTS provider')
ON CONFLICT (setting_key) DO NOTHING;

-- Success message
DO $$
BEGIN
    RAISE NOTICE 'Schema setup completed successfully for Mitra Politi database!';
END $$;
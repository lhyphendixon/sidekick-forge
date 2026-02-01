-- Migration: Add sidekick wizard sessions table
-- Date: 2026-01-24
-- Description: Table for tracking sidekick onboarding wizard sessions
--              This is the immediate wizard session table used by wizard_session_service.py

-- ============================================================
-- SIDEKICK_WIZARD_SESSIONS TABLE
-- Tracks individual wizard runs for sidekick creation
-- ============================================================
CREATE TABLE IF NOT EXISTS sidekick_wizard_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- User and client association
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    client_id UUID NOT NULL,  -- References clients table

    -- Progress tracking
    current_step INTEGER NOT NULL DEFAULT 1,
    completed_steps INTEGER[] DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'in_progress',  -- in_progress, completed, abandoned

    -- Collected data (populated as user progresses)
    step_data JSONB DEFAULT '{}'::jsonb,
    -- {
    --   "name": "Herman",
    --   "slug": "herman",
    --   "personality_description": "Friendly and professional...",
    --   "voice_id": "cartesia_abc123",
    --   "avatar_url": "...",
    --   "config_mode": "default",
    --   "api_keys": {...}
    -- }

    -- Result tracking (set on completion)
    agent_id UUID,  -- The created agent ID

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,

    -- Constraints
    CONSTRAINT valid_session_status CHECK (status IN ('in_progress', 'completed', 'abandoned'))
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_sidekick_wizard_sessions_user ON sidekick_wizard_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sidekick_wizard_sessions_client ON sidekick_wizard_sessions(client_id);
CREATE INDEX IF NOT EXISTS idx_sidekick_wizard_sessions_status ON sidekick_wizard_sessions(status);
CREATE INDEX IF NOT EXISTS idx_sidekick_wizard_sessions_updated ON sidekick_wizard_sessions(updated_at);

-- Enable RLS
ALTER TABLE sidekick_wizard_sessions ENABLE ROW LEVEL SECURITY;

-- Policies
-- Users can access their own sessions
CREATE POLICY "Users can view own wizard sessions" ON sidekick_wizard_sessions
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users can update own wizard sessions" ON sidekick_wizard_sessions
    FOR UPDATE USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own wizard sessions" ON sidekick_wizard_sessions
    FOR INSERT WITH CHECK (auth.uid() = user_id);

-- Service role has full access
CREATE POLICY "Service role full access to wizard sessions" ON sidekick_wizard_sessions
    FOR ALL TO service_role
    USING (true) WITH CHECK (true);

-- Grant permissions
GRANT SELECT, INSERT, UPDATE ON sidekick_wizard_sessions TO authenticated;
GRANT ALL ON sidekick_wizard_sessions TO service_role;


-- ============================================================
-- WIZARD_GENERATED_AVATARS TABLE
-- Stores generated avatar images for wizard sessions
-- ============================================================
CREATE TABLE IF NOT EXISTS wizard_generated_avatars (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES sidekick_wizard_sessions(id) ON DELETE CASCADE,

    -- Generation details
    prompt TEXT NOT NULL,
    image_url TEXT NOT NULL,
    generation_provider TEXT NOT NULL,  -- 'replicate', 'placeholder', etc.
    generation_model TEXT,
    generation_params JSONB DEFAULT '{}'::jsonb,

    -- Selection
    selected BOOLEAN DEFAULT FALSE,

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_wizard_avatars_session ON wizard_generated_avatars(session_id);
CREATE INDEX IF NOT EXISTS idx_wizard_avatars_selected ON wizard_generated_avatars(session_id, selected) WHERE selected = true;

-- Enable RLS
ALTER TABLE wizard_generated_avatars ENABLE ROW LEVEL SECURITY;

-- Policies (inherit from sessions)
CREATE POLICY "Users can view avatars of own sessions" ON wizard_generated_avatars
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM sidekick_wizard_sessions s
            WHERE s.id = wizard_generated_avatars.session_id
            AND s.user_id = auth.uid()
        )
    );

CREATE POLICY "Users can insert avatars for own sessions" ON wizard_generated_avatars
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1 FROM sidekick_wizard_sessions s
            WHERE s.id = wizard_generated_avatars.session_id
            AND s.user_id = auth.uid()
        )
    );

CREATE POLICY "Service role full access to wizard avatars" ON wizard_generated_avatars
    FOR ALL TO service_role
    USING (true) WITH CHECK (true);

-- Grant permissions
GRANT SELECT, INSERT, UPDATE ON wizard_generated_avatars TO authenticated;
GRANT ALL ON wizard_generated_avatars TO service_role;


-- ============================================================
-- WIZARD_PENDING_DOCUMENTS TABLE
-- Tracks documents being processed for wizard sessions
-- ============================================================
CREATE TABLE IF NOT EXISTS wizard_pending_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES sidekick_wizard_sessions(id) ON DELETE CASCADE,

    -- Source info
    source_type TEXT NOT NULL,  -- 'file' or 'website'
    source_name TEXT NOT NULL,  -- filename or URL

    -- File details (for file uploads)
    file_size INTEGER,
    file_type TEXT,
    staged_path TEXT,

    -- Website details (for crawls)
    pages_crawled INTEGER,

    -- Processing status
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, processing, ready, error
    error_message TEXT,

    -- Link to actual document after processing
    document_id UUID,  -- Links to tenant's documents table after creation

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_wizard_pending_docs_session ON wizard_pending_documents(session_id);
CREATE INDEX IF NOT EXISTS idx_wizard_pending_docs_status ON wizard_pending_documents(status);

-- Enable RLS
ALTER TABLE wizard_pending_documents ENABLE ROW LEVEL SECURITY;

-- Policies (inherit from sessions)
CREATE POLICY "Users can view pending docs of own sessions" ON wizard_pending_documents
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM sidekick_wizard_sessions s
            WHERE s.id = wizard_pending_documents.session_id
            AND s.user_id = auth.uid()
        )
    );

CREATE POLICY "Users can modify pending docs of own sessions" ON wizard_pending_documents
    FOR ALL USING (
        EXISTS (
            SELECT 1 FROM sidekick_wizard_sessions s
            WHERE s.id = wizard_pending_documents.session_id
            AND s.user_id = auth.uid()
        )
    );

CREATE POLICY "Service role full access to wizard pending docs" ON wizard_pending_documents
    FOR ALL TO service_role
    USING (true) WITH CHECK (true);

-- Grant permissions
GRANT SELECT, INSERT, UPDATE, DELETE ON wizard_pending_documents TO authenticated;
GRANT ALL ON wizard_pending_documents TO service_role;


COMMENT ON TABLE sidekick_wizard_sessions IS 'Tracks onboarding wizard sessions for creating sidekicks';
COMMENT ON TABLE wizard_generated_avatars IS 'Stores generated avatar images during wizard sessions';
COMMENT ON TABLE wizard_pending_documents IS 'Tracks documents being processed for wizard knowledge base';

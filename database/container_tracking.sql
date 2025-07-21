-- Container tracking tables for client-specific agent deployments

-- Agent sessions table (tracks LiveKit sessions and their containers)
CREATE TABLE IF NOT EXISTS agent_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id TEXT NOT NULL UNIQUE,
    room_name TEXT NOT NULL,
    agent_slug TEXT NOT NULL,
    site_id TEXT NOT NULL,
    container_name TEXT NOT NULL,
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'completed', 'failed')),
    started_at TIMESTAMPTZ DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    duration_seconds INTEGER,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Container deployments table (tracks container lifecycle)
CREATE TABLE IF NOT EXISTS container_deployments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    site_id TEXT NOT NULL,
    agent_slug TEXT NOT NULL,
    container_name TEXT NOT NULL UNIQUE,
    container_id TEXT,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'starting', 'running', 'stopped', 'failed', 'removed')),
    cpu_limit DECIMAL(3,1) DEFAULT 1.0,
    memory_limit TEXT DEFAULT '1g',
    deployed_at TIMESTAMPTZ DEFAULT NOW(),
    stopped_at TIMESTAMPTZ,
    last_health_check TIMESTAMPTZ,
    health_status TEXT DEFAULT 'unknown',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(site_id, agent_slug)
);

-- Container metrics table (stores periodic metrics)
CREATE TABLE IF NOT EXISTS container_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    container_name TEXT NOT NULL,
    cpu_percent DECIMAL(5,2),
    memory_usage_mb DECIMAL(10,2),
    memory_limit_mb DECIMAL(10,2),
    memory_percent DECIMAL(5,2),
    network_rx_bytes BIGINT,
    network_tx_bytes BIGINT,
    collected_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (container_name) REFERENCES container_deployments(container_name) ON DELETE CASCADE
);

-- Container events table (audit log)
CREATE TABLE IF NOT EXISTS container_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    container_name TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('deployed', 'started', 'stopped', 'restarted', 'scaled', 'removed', 'health_check', 'error')),
    event_data JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- LiveKit events table (already referenced in webhooks)
CREATE TABLE IF NOT EXISTS livekit_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type TEXT NOT NULL,
    room_name TEXT,
    room_sid TEXT,
    participant_sid TEXT,
    participant_identity TEXT,
    track_type TEXT,
    track_source TEXT,
    duration INTEGER,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX idx_agent_sessions_site_id ON agent_sessions(site_id);
CREATE INDEX idx_agent_sessions_session_id ON agent_sessions(session_id);
CREATE INDEX idx_agent_sessions_created_at ON agent_sessions(created_at);

CREATE INDEX idx_container_deployments_site_id ON container_deployments(site_id);
CREATE INDEX idx_container_deployments_status ON container_deployments(status);
CREATE INDEX idx_container_deployments_container_name ON container_deployments(container_name);

CREATE INDEX idx_container_metrics_container_name ON container_metrics(container_name);
CREATE INDEX idx_container_metrics_collected_at ON container_metrics(collected_at);

CREATE INDEX idx_container_events_container_name ON container_events(container_name);
CREATE INDEX idx_container_events_event_type ON container_events(event_type);
CREATE INDEX idx_container_events_created_at ON container_events(created_at);

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Trigger for updated_at
CREATE TRIGGER update_container_deployments_updated_at BEFORE UPDATE
    ON container_deployments FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- RLS Policies (if using Supabase Auth)
ALTER TABLE agent_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE container_deployments ENABLE ROW LEVEL SECURITY;
ALTER TABLE container_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE container_events ENABLE ROW LEVEL SECURITY;

-- Policy: WordPress sites can only see their own containers
CREATE POLICY "Sites can view own agent sessions" ON agent_sessions
    FOR SELECT USING (site_id = current_setting('app.current_site_id', true));

CREATE POLICY "Sites can view own container deployments" ON container_deployments
    FOR SELECT USING (site_id = current_setting('app.current_site_id', true));

-- Policy: Admins can see everything
CREATE POLICY "Admins have full access to sessions" ON agent_sessions
    FOR ALL USING (
        EXISTS (
            SELECT 1 FROM profiles 
            WHERE id = auth.uid() 
            AND role = 'admin'
        )
    );

CREATE POLICY "Admins have full access to deployments" ON container_deployments
    FOR ALL USING (
        EXISTS (
            SELECT 1 FROM profiles 
            WHERE id = auth.uid() 
            AND role = 'admin'
        )
    );
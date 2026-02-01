-- Migration: Add livekit_events table and activity_log for comprehensive tracking
-- Date: 2026-02-01

-- =====================================================
-- LiveKit Events Table - Tracks all LiveKit webhook events
-- =====================================================
CREATE TABLE IF NOT EXISTS livekit_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type VARCHAR(50) NOT NULL,  -- room_started, room_finished, participant_joined, participant_left, track_published
    room_name VARCHAR(255),
    room_sid VARCHAR(255),
    participant_sid VARCHAR(255),
    participant_identity VARCHAR(255),
    track_type VARCHAR(50),  -- audio, video, screen
    track_source VARCHAR(50),  -- camera, microphone, screen_share
    duration INTEGER,  -- Duration in seconds (for room_finished)
    metadata JSONB DEFAULT '{}'::jsonb,  -- Additional metadata including client_id, agent_id, etc.
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_livekit_events_type ON livekit_events(event_type);
CREATE INDEX IF NOT EXISTS idx_livekit_events_room ON livekit_events(room_name);
CREATE INDEX IF NOT EXISTS idx_livekit_events_created ON livekit_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_livekit_events_metadata_client ON livekit_events((metadata->>'client_id'));
CREATE INDEX IF NOT EXISTS idx_livekit_events_metadata_agent ON livekit_events((metadata->>'agent_id'));

-- Enable RLS
ALTER TABLE livekit_events ENABLE ROW LEVEL SECURITY;

-- Policy: Service role can do everything
CREATE POLICY "Service role full access on livekit_events" ON livekit_events
    FOR ALL USING (auth.role() = 'service_role');

-- Policy: Authenticated users can read events for their clients
CREATE POLICY "Users can read events for their clients" ON livekit_events
    FOR SELECT USING (
        auth.role() = 'authenticated' AND (
            metadata->>'client_id' IN (
                SELECT id::text FROM clients WHERE owner_user_id = auth.uid()
            )
        )
    );

-- Grant permissions
GRANT SELECT ON livekit_events TO authenticated;
GRANT ALL ON livekit_events TO service_role;


-- =====================================================
-- Activity Log Table - Tracks all system activities
-- =====================================================
CREATE TABLE IF NOT EXISTS activity_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- What happened
    activity_type VARCHAR(50) NOT NULL,  -- sidekick_created, sidekick_updated, ability_run, conversation_started, etc.
    action VARCHAR(50) NOT NULL,  -- create, update, delete, run, start, end

    -- Who/what was involved
    client_id UUID REFERENCES clients(id) ON DELETE CASCADE,
    agent_id UUID,
    user_id UUID,  -- The user who triggered the action (if applicable)

    -- What was affected
    resource_type VARCHAR(50),  -- sidekick, ability, conversation, document, etc.
    resource_id VARCHAR(255),  -- ID of the affected resource
    resource_name VARCHAR(255),  -- Human-readable name

    -- Additional details
    details JSONB DEFAULT '{}'::jsonb,

    -- Result
    status VARCHAR(20) DEFAULT 'success',  -- success, failed, pending
    error_message TEXT,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_activity_log_client ON activity_log(client_id);
CREATE INDEX IF NOT EXISTS idx_activity_log_type ON activity_log(activity_type);
CREATE INDEX IF NOT EXISTS idx_activity_log_created ON activity_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_log_agent ON activity_log(agent_id);

-- Enable RLS
ALTER TABLE activity_log ENABLE ROW LEVEL SECURITY;

-- Policy: Service role can do everything
CREATE POLICY "Service role full access on activity_log" ON activity_log
    FOR ALL USING (auth.role() = 'service_role');

-- Policy: Users can read activities for their clients
CREATE POLICY "Users can read activities for their clients" ON activity_log
    FOR SELECT USING (
        auth.role() = 'authenticated' AND (
            client_id IN (
                SELECT id FROM clients WHERE owner_user_id = auth.uid()
            )
        )
    );

-- Grant permissions
GRANT SELECT ON activity_log TO authenticated;
GRANT ALL ON activity_log TO service_role;


-- =====================================================
-- Helper function to log activities
-- =====================================================
CREATE OR REPLACE FUNCTION log_activity(
    p_activity_type VARCHAR(50),
    p_action VARCHAR(50),
    p_client_id UUID DEFAULT NULL,
    p_agent_id UUID DEFAULT NULL,
    p_user_id UUID DEFAULT NULL,
    p_resource_type VARCHAR(50) DEFAULT NULL,
    p_resource_id VARCHAR(255) DEFAULT NULL,
    p_resource_name VARCHAR(255) DEFAULT NULL,
    p_details JSONB DEFAULT '{}'::jsonb,
    p_status VARCHAR(20) DEFAULT 'success',
    p_error_message TEXT DEFAULT NULL
)
RETURNS UUID AS $$
DECLARE
    v_id UUID;
BEGIN
    INSERT INTO activity_log (
        activity_type,
        action,
        client_id,
        agent_id,
        user_id,
        resource_type,
        resource_id,
        resource_name,
        details,
        status,
        error_message
    )
    VALUES (
        p_activity_type,
        p_action,
        p_client_id,
        p_agent_id,
        p_user_id,
        p_resource_type,
        p_resource_id,
        p_resource_name,
        p_details,
        p_status,
        p_error_message
    )
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$$ LANGUAGE plpgsql;

-- Grant execute on function
GRANT EXECUTE ON FUNCTION log_activity TO service_role;

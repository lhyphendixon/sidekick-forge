-- Migration: Add user_clients table
-- Date: 2026-01-25
-- Description: Maps users to clients for team member access (non-owners)
--              Required by wizard_session_service to determine client for users

-- ============================================================
-- USER_CLIENTS TABLE
-- Maps users to their associated clients (for team members)
-- ============================================================
CREATE TABLE IF NOT EXISTS user_clients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- User and client association
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,

    -- Role within the client (for future RBAC)
    role TEXT NOT NULL DEFAULT 'member',  -- owner, admin, member

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Constraints
    CONSTRAINT unique_user_client UNIQUE (user_id, client_id),
    CONSTRAINT valid_role CHECK (role IN ('owner', 'admin', 'member'))
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_user_clients_user ON user_clients(user_id);
CREATE INDEX IF NOT EXISTS idx_user_clients_client ON user_clients(client_id);
CREATE INDEX IF NOT EXISTS idx_user_clients_role ON user_clients(role);

-- Enable RLS
ALTER TABLE user_clients ENABLE ROW LEVEL SECURITY;

-- Policies
-- Users can view their own client associations
CREATE POLICY "Users can view own client associations" ON user_clients
    FOR SELECT USING (auth.uid() = user_id);

-- Service role has full access (needed for admin operations)
CREATE POLICY "Service role full access to user_clients" ON user_clients
    FOR ALL TO service_role
    USING (true) WITH CHECK (true);

-- Grant permissions
GRANT SELECT ON user_clients TO authenticated;
GRANT ALL ON user_clients TO service_role;

-- Populate user_clients from existing owner_user_id in clients table
-- This creates entries for existing client owners (only for valid users)
INSERT INTO user_clients (user_id, client_id, role)
SELECT c.owner_user_id, c.id, 'owner'
FROM clients c
INNER JOIN auth.users u ON u.id = c.owner_user_id
WHERE c.owner_user_id IS NOT NULL
ON CONFLICT (user_id, client_id) DO NOTHING;

COMMENT ON TABLE user_clients IS 'Maps users to clients for team member access and role-based permissions';
COMMENT ON COLUMN user_clients.role IS 'Role within the client: owner, admin, or member';

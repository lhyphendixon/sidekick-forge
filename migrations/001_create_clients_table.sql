-- Create clients table for multi-tenant support
CREATE TABLE IF NOT EXISTS clients (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    domain TEXT,
    settings JSONB NOT NULL,
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create indexes for performance
CREATE INDEX idx_clients_domain ON clients(domain);
CREATE INDEX idx_clients_active ON clients(active);
CREATE INDEX idx_clients_created_at ON clients(created_at DESC);

-- Create unique constraint on domain (nullable)
CREATE UNIQUE INDEX idx_clients_domain_unique ON clients(domain) WHERE domain IS NOT NULL;

-- Add updated_at trigger
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_clients_updated_at
    BEFORE UPDATE ON clients
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Add RLS policies (if using RLS)
ALTER TABLE clients ENABLE ROW LEVEL SECURITY;

-- Policy for service role (full access)
CREATE POLICY "Service role can manage all clients" ON clients
    FOR ALL
    USING (auth.jwt() ->> 'role' = 'service_role');

-- Policy for authenticated users (read-only access to their client)
CREATE POLICY "Users can read their own client" ON clients
    FOR SELECT
    USING (
        auth.uid() IS NOT NULL 
        AND EXISTS (
            SELECT 1 FROM auth.users 
            WHERE auth.users.id = auth.uid() 
            AND auth.users.raw_user_meta_data ->> 'client_id' = clients.id
        )
    );

-- Comments for documentation
COMMENT ON TABLE clients IS 'Multi-tenant client configurations';
COMMENT ON COLUMN clients.id IS 'Unique client identifier (slug format)';
COMMENT ON COLUMN clients.name IS 'Display name of the client';
COMMENT ON COLUMN clients.description IS 'Optional description of the client';
COMMENT ON COLUMN clients.domain IS 'Primary domain for the client (unique)';
COMMENT ON COLUMN clients.settings IS 'JSON object containing all client settings including Supabase, LiveKit, and API keys';
COMMENT ON COLUMN clients.active IS 'Whether the client is currently active';
COMMENT ON COLUMN clients.created_at IS 'Timestamp when the client was created';
COMMENT ON COLUMN clients.updated_at IS 'Timestamp when the client was last updated';
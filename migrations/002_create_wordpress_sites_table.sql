-- Create wordpress_sites table for WordPress site management
CREATE TABLE IF NOT EXISTS wordpress_sites (
    id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    site_name TEXT NOT NULL,
    client_id TEXT NOT NULL,
    api_key TEXT NOT NULL UNIQUE,
    api_secret TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    allowed_origins TEXT[],
    metadata JSONB DEFAULT '{}',
    request_count INTEGER DEFAULT 0,
    last_seen_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create indexes for performance
CREATE INDEX idx_wordpress_sites_domain ON wordpress_sites(domain);
CREATE INDEX idx_wordpress_sites_client_id ON wordpress_sites(client_id);
CREATE INDEX idx_wordpress_sites_api_key ON wordpress_sites(api_key);
CREATE INDEX idx_wordpress_sites_is_active ON wordpress_sites(is_active);
CREATE INDEX idx_wordpress_sites_created_at ON wordpress_sites(created_at DESC);

-- Create unique constraint on domain
CREATE UNIQUE INDEX idx_wordpress_sites_domain_unique ON wordpress_sites(domain);

-- Add foreign key to clients table
ALTER TABLE wordpress_sites 
    ADD CONSTRAINT fk_wordpress_sites_client 
    FOREIGN KEY (client_id) 
    REFERENCES clients(id) 
    ON DELETE CASCADE;

-- Add updated_at trigger
CREATE TRIGGER update_wordpress_sites_updated_at
    BEFORE UPDATE ON wordpress_sites
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Add RLS policies (if using RLS)
ALTER TABLE wordpress_sites ENABLE ROW LEVEL SECURITY;

-- Policy for service role (full access)
CREATE POLICY "Service role can manage all wordpress sites" ON wordpress_sites
    FOR ALL
    USING (auth.jwt() ->> 'role' = 'service_role');

-- Policy for authenticated users (read-only access to their client's sites)
CREATE POLICY "Users can read their client's wordpress sites" ON wordpress_sites
    FOR SELECT
    USING (
        auth.uid() IS NOT NULL 
        AND client_id IN (
            SELECT id FROM clients 
            WHERE id = (
                SELECT raw_user_meta_data ->> 'client_id' 
                FROM auth.users 
                WHERE id = auth.uid()
            )
        )
    );

-- Comments for documentation
COMMENT ON TABLE wordpress_sites IS 'WordPress site registrations for API access';
COMMENT ON COLUMN wordpress_sites.id IS 'Unique site identifier (UUID)';
COMMENT ON COLUMN wordpress_sites.domain IS 'WordPress site domain (unique)';
COMMENT ON COLUMN wordpress_sites.site_name IS 'Display name of the WordPress site';
COMMENT ON COLUMN wordpress_sites.client_id IS 'Reference to the client that owns this site';
COMMENT ON COLUMN wordpress_sites.api_key IS 'API key for authentication (unique)';
COMMENT ON COLUMN wordpress_sites.api_secret IS 'API secret for enhanced security';
COMMENT ON COLUMN wordpress_sites.is_active IS 'Whether the site is currently active';
COMMENT ON COLUMN wordpress_sites.allowed_origins IS 'Array of allowed CORS origins';
COMMENT ON COLUMN wordpress_sites.metadata IS 'Additional site metadata as JSON';
COMMENT ON COLUMN wordpress_sites.request_count IS 'Total number of API requests';
COMMENT ON COLUMN wordpress_sites.last_seen_at IS 'Last time the site made an API request';
COMMENT ON COLUMN wordpress_sites.created_at IS 'Timestamp when the site was registered';
COMMENT ON COLUMN wordpress_sites.updated_at IS 'Timestamp when the site was last updated';
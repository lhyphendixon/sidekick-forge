CREATE TABLE IF NOT EXISTS email_address_registry (
    email_address TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    agent_slug TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    released_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);

# Tiered Architecture Migration Plan

## Implementation Status

| Phase | Status | Notes |
|-------|--------|-------|
| Phase 1: Platform DB Schema | ✅ COMPLETE | `tier`, `hosting_type`, `max_sidekicks` columns added |
| Phase 2: Shared Pool Schema | ✅ READY | SQL file ready, awaiting Supabase project (billing block) |
| Phase 3: Application Code | ✅ COMPLETE | ClientConnectionManager, TenantQuery, tier_features module |
| Phase 4: Tier Enforcement | ✅ COMPLETE | Feature flags and limit checking implemented |
| Phase 5: Upgrade Path | ✅ COMPLETE | tier_upgrade.py handles Adventurer → Champion migration |
| Phase 6: Testing | ⏳ PENDING | Awaiting shared pool project creation |

### Blocking Issue
Supabase account has overdue invoices preventing new project creation. Once resolved:
1. Create `sidekick-forge-shared-pool` project
2. Apply `migrations/shared_pool/001_base_schema.sql`
3. Add pool config to `shared_pool_config` table

---

## Overview

This document outlines the migration from a single-tier dedicated Supabase architecture to a three-tiered system supporting both shared and dedicated infrastructure.

### Tier Definitions

| Tier | Name | Hosting | Sidekicks | Features |
|------|------|---------|-----------|----------|
| `adventurer` | Adventurer | Shared backend | 1 sidekick | Learning phase, basic features |
| `champion` | Champion | Dedicated Supabase project | Unlimited | Full sidekick access, reliable persistence |
| `paragon` | Paragon | Sovereign stack | Unlimited | White-glove, bespoke customization, maximum agency |

### Architecture Diagram

```
                    ┌─────────────────────────────────────────────────┐
                    │            Platform Supabase                     │
                    │  ┌─────────────────────────────────────────┐    │
                    │  │              clients table               │    │
                    │  │  - tier: adventurer|champion|paragon    │    │
                    │  │  - hosting_type: shared|dedicated       │    │
                    │  │  - supabase_url (NULL if shared)        │    │
                    │  │  - supabase_service_role_key (NULL if   │    │
                    │  │    shared)                               │    │
                    │  └─────────────────────────────────────────┘    │
                    └─────────────────────────────────────────────────┘
                                          │
                    ┌─────────────────────┼─────────────────────┐
                    │                     │                     │
                    ▼                     ▼                     ▼
    ┌───────────────────────┐  ┌──────────────────┐  ┌──────────────────┐
    │   Shared Pool DB      │  │ Champion Client  │  │ Paragon Client   │
    │   (Adventurer Tier)   │  │ Dedicated DB     │  │ Sovereign Stack  │
    │                       │  │                  │  │                  │
    │  ┌─────────────────┐  │  │  ┌────────────┐  │  │  ┌────────────┐  │
    │  │ agents          │  │  │  │ agents     │  │  │  │ agents     │  │
    │  │ + client_id     │  │  │  └────────────┘  │  │  └────────────┘  │
    │  │ + RLS policies  │  │  │  ┌────────────┐  │  │  ┌────────────┐  │
    │  ├─────────────────┤  │  │  │ documents  │  │  │  │ documents  │  │
    │  │ documents       │  │  │  └────────────┘  │  │  └────────────┘  │
    │  │ + client_id     │  │  │  ┌────────────┐  │  │  Custom infra    │
    │  │ + RLS policies  │  │  │  │ convos     │  │  │  and config      │
    │  ├─────────────────┤  │  │  └────────────┘  │  │                  │
    │  │ conversations   │  │  └──────────────────┘  └──────────────────┘
    │  │ + client_id     │  │
    │  │ + RLS policies  │  │
    │  └─────────────────┘  │
    └───────────────────────┘
```

---

## Phase 1: Platform Database Schema Updates

**Duration: 2-3 days**

### 1.1 Add Tier Columns to Platform `clients` Table

**Migration file:** `migrations/20250104_add_client_tiers.sql`

```sql
-- Add tier and hosting type columns to clients table
-- Run on: PLATFORM Supabase (eukudpgfpihxsypulopm)

-- Add tier enum type
DO $$ BEGIN
    CREATE TYPE client_tier AS ENUM ('adventurer', 'champion', 'paragon');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Add hosting type enum
DO $$ BEGIN
    CREATE TYPE hosting_type AS ENUM ('shared', 'dedicated');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Add columns to clients table
ALTER TABLE clients
ADD COLUMN IF NOT EXISTS tier client_tier DEFAULT 'champion',
ADD COLUMN IF NOT EXISTS hosting_type hosting_type DEFAULT 'dedicated',
ADD COLUMN IF NOT EXISTS max_sidekicks INTEGER DEFAULT NULL,
ADD COLUMN IF NOT EXISTS tier_features JSONB DEFAULT '{}';

-- Set all existing clients to Champion tier (dedicated) - they already have Supabase projects
UPDATE clients
SET tier = 'champion',
    hosting_type = 'dedicated',
    max_sidekicks = NULL  -- NULL = unlimited
WHERE tier IS NULL;

-- Add index for tier-based queries
CREATE INDEX IF NOT EXISTS idx_clients_tier ON clients(tier);
CREATE INDEX IF NOT EXISTS idx_clients_hosting_type ON clients(hosting_type);

-- Add constraint: Adventurer tier must have max_sidekicks = 1
ALTER TABLE clients ADD CONSTRAINT check_adventurer_limits
    CHECK (
        (tier != 'adventurer') OR
        (tier = 'adventurer' AND max_sidekicks = 1 AND hosting_type = 'shared')
    );

-- Add constraint: Champion/Paragon must be dedicated
ALTER TABLE clients ADD CONSTRAINT check_champion_dedicated
    CHECK (
        (tier = 'adventurer') OR
        (hosting_type = 'dedicated')
    );

COMMENT ON COLUMN clients.tier IS 'Subscription tier: adventurer (shared), champion (dedicated), paragon (bespoke)';
COMMENT ON COLUMN clients.hosting_type IS 'Infrastructure type: shared (pool DB) or dedicated (own Supabase project)';
COMMENT ON COLUMN clients.max_sidekicks IS 'Maximum number of sidekicks allowed. NULL = unlimited';
COMMENT ON COLUMN clients.tier_features IS 'JSON object of tier-specific feature flags';
```

### 1.2 Create Shared Pool Database Reference

```sql
-- Add shared pool configuration to platform
-- This stores the connection details for the shared Adventurer database

CREATE TABLE IF NOT EXISTS shared_pool_config (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pool_name TEXT NOT NULL UNIQUE DEFAULT 'adventurer_pool',
    supabase_url TEXT NOT NULL,
    supabase_service_role_key TEXT NOT NULL,
    supabase_anon_key TEXT,
    supabase_project_ref TEXT,
    max_clients INTEGER DEFAULT 1000,
    current_client_count INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for quick lookup
CREATE INDEX IF NOT EXISTS idx_shared_pool_active ON shared_pool_config(is_active, pool_name);

COMMENT ON TABLE shared_pool_config IS 'Configuration for shared infrastructure pools (Adventurer tier)';
```

---

## Phase 2: Create Shared Pool Database Schema

**Duration: 3-4 days**

### 2.1 Provision Shared Pool Supabase Project

1. Create new Supabase project: `sidekick-forge-shared-pool`
2. Store credentials in `shared_pool_config` table
3. Apply base schema with `client_id` columns

### 2.2 Shared Pool Schema

**Migration file:** `migrations/shared_pool/001_base_schema.sql`

This is the standard client schema BUT with `client_id` added to ALL tables and RLS policies.

```sql
-- ============================================================
-- SHARED POOL DATABASE SCHEMA
-- All tables include client_id for multi-tenant isolation
-- ============================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";

-- ============================================================
-- AGENTS TABLE (with client_id)
-- ============================================================
CREATE TABLE IF NOT EXISTS agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL,  -- CRITICAL: Multi-tenant isolation
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    system_prompt TEXT,
    voice_settings JSONB DEFAULT '{}',
    tools_config JSONB DEFAULT '{}',
    greeting TEXT,
    voice_chat_enabled BOOLEAN DEFAULT true,
    text_chat_enabled BOOLEAN DEFAULT true,
    video_chat_enabled BOOLEAN DEFAULT false,
    supertab_enabled BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    -- Enforce unique slug per client (not globally)
    CONSTRAINT unique_agent_slug_per_client UNIQUE (client_id, slug)
);

-- RLS Policy: Agents isolated by client_id
ALTER TABLE agents ENABLE ROW LEVEL SECURITY;

CREATE POLICY "agents_client_isolation" ON agents
    FOR ALL
    USING (client_id = current_setting('app.current_client_id', true)::uuid)
    WITH CHECK (client_id = current_setting('app.current_client_id', true)::uuid);

-- Service role bypass (for backend operations)
CREATE POLICY "agents_service_role_bypass" ON agents
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE INDEX idx_agents_client_id ON agents(client_id);
CREATE INDEX idx_agents_client_slug ON agents(client_id, slug);

-- ============================================================
-- DOCUMENTS TABLE (with client_id)
-- ============================================================
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL,
    title TEXT,
    content TEXT,
    source_url TEXT,
    source_type TEXT DEFAULT 'manual',
    status TEXT DEFAULT 'pending',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE documents ENABLE ROW LEVEL SECURITY;

CREATE POLICY "documents_client_isolation" ON documents
    FOR ALL
    USING (client_id = current_setting('app.current_client_id', true)::uuid)
    WITH CHECK (client_id = current_setting('app.current_client_id', true)::uuid);

CREATE POLICY "documents_service_role_bypass" ON documents
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE INDEX idx_documents_client_id ON documents(client_id);
CREATE INDEX idx_documents_client_status ON documents(client_id, status);

-- ============================================================
-- DOCUMENT_CHUNKS TABLE (with client_id)
-- ============================================================
CREATE TABLE IF NOT EXISTS document_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    embeddings vector(1024),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE document_chunks ENABLE ROW LEVEL SECURITY;

CREATE POLICY "chunks_client_isolation" ON document_chunks
    FOR ALL
    USING (client_id = current_setting('app.current_client_id', true)::uuid)
    WITH CHECK (client_id = current_setting('app.current_client_id', true)::uuid);

CREATE POLICY "chunks_service_role_bypass" ON document_chunks
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE INDEX idx_chunks_client_id ON document_chunks(client_id);
CREATE INDEX idx_chunks_document ON document_chunks(document_id);
CREATE INDEX idx_chunks_embeddings ON document_chunks USING ivfflat (embeddings vector_cosine_ops);

-- ============================================================
-- AGENT_DOCUMENTS TABLE (with client_id)
-- ============================================================
CREATE TABLE IF NOT EXISTS agent_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL,
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    enabled BOOLEAN DEFAULT true,
    access_type TEXT DEFAULT 'full',
    created_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT unique_agent_document_per_client UNIQUE (client_id, agent_id, document_id)
);

ALTER TABLE agent_documents ENABLE ROW LEVEL SECURITY;

CREATE POLICY "agent_docs_client_isolation" ON agent_documents
    FOR ALL
    USING (client_id = current_setting('app.current_client_id', true)::uuid)
    WITH CHECK (client_id = current_setting('app.current_client_id', true)::uuid);

CREATE POLICY "agent_docs_service_role_bypass" ON agent_documents
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE INDEX idx_agent_documents_client ON agent_documents(client_id);

-- ============================================================
-- CONVERSATIONS TABLE (with client_id)
-- ============================================================
CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL,
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    user_id UUID,
    title TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "conversations_client_isolation" ON conversations
    FOR ALL
    USING (client_id = current_setting('app.current_client_id', true)::uuid)
    WITH CHECK (client_id = current_setting('app.current_client_id', true)::uuid);

CREATE POLICY "conversations_service_role_bypass" ON conversations
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE INDEX idx_conversations_client ON conversations(client_id);
CREATE INDEX idx_conversations_agent ON conversations(client_id, agent_id);
CREATE INDEX idx_conversations_user ON conversations(client_id, user_id);

-- ============================================================
-- CONVERSATION_TRANSCRIPTS TABLE (with client_id)
-- ============================================================
CREATE TABLE IF NOT EXISTS conversation_transcripts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    agent_id UUID REFERENCES agents(id) ON DELETE SET NULL,
    user_id UUID,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    content TEXT,
    turn_id UUID,
    metadata JSONB DEFAULT '{}',
    embeddings vector(1024),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE conversation_transcripts ENABLE ROW LEVEL SECURITY;

CREATE POLICY "transcripts_client_isolation" ON conversation_transcripts
    FOR ALL
    USING (client_id = current_setting('app.current_client_id', true)::uuid)
    WITH CHECK (client_id = current_setting('app.current_client_id', true)::uuid);

CREATE POLICY "transcripts_service_role_bypass" ON conversation_transcripts
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Enable realtime for transcripts
ALTER PUBLICATION supabase_realtime ADD TABLE conversation_transcripts;

CREATE INDEX idx_transcripts_client ON conversation_transcripts(client_id);
CREATE INDEX idx_transcripts_conversation ON conversation_transcripts(conversation_id);
CREATE INDEX idx_transcripts_agent_user ON conversation_transcripts(client_id, agent_id, user_id);

-- ============================================================
-- USER_OVERVIEWS TABLE (already has client_id pattern)
-- ============================================================
CREATE TABLE IF NOT EXISTS user_overviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id UUID NOT NULL,
    user_id UUID NOT NULL,
    agent_id UUID REFERENCES agents(id) ON DELETE CASCADE,
    sections JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT unique_user_overview UNIQUE (client_id, user_id, agent_id)
);

ALTER TABLE user_overviews ENABLE ROW LEVEL SECURITY;

CREATE POLICY "overviews_client_isolation" ON user_overviews
    FOR ALL
    USING (client_id = current_setting('app.current_client_id', true)::uuid)
    WITH CHECK (client_id = current_setting('app.current_client_id', true)::uuid);

CREATE POLICY "overviews_service_role_bypass" ON user_overviews
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE INDEX idx_overviews_client ON user_overviews(client_id);
CREATE INDEX idx_overviews_lookup ON user_overviews(client_id, user_id, agent_id);
```

### 2.3 Shared Pool RPC Functions

```sql
-- ============================================================
-- RPC FUNCTIONS FOR SHARED POOL
-- All functions require client_id parameter
-- ============================================================

-- Match documents with client_id isolation
CREATE OR REPLACE FUNCTION match_documents(
    p_client_id UUID,           -- ADDED: Required for shared pool
    p_query_embedding vector,
    p_agent_slug TEXT,
    p_match_threshold FLOAT8 DEFAULT 0.5,
    p_match_count INTEGER DEFAULT 5
)
RETURNS TABLE (
    id UUID,
    document_id UUID,
    content TEXT,
    relevance FLOAT8,
    title TEXT,
    source_url TEXT,
    chunk_index INTEGER
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    RETURN QUERY
    SELECT
        dc.id,
        dc.document_id,
        dc.content,
        (1 - (dc.embeddings <=> p_query_embedding))::FLOAT8 as relevance,
        d.title,
        d.source_url,
        dc.chunk_index
    FROM document_chunks dc
    JOIN documents d ON dc.document_id = d.id AND d.client_id = p_client_id
    JOIN agent_documents ad ON ad.document_id = d.id AND ad.client_id = p_client_id
    JOIN agents a ON ad.agent_id = a.id AND a.client_id = p_client_id
    WHERE dc.client_id = p_client_id           -- Client isolation
      AND a.slug = p_agent_slug
      AND ad.enabled = true
      AND dc.embeddings IS NOT NULL
      AND (1 - (dc.embeddings <=> p_query_embedding)) > p_match_threshold
    ORDER BY dc.embeddings <=> p_query_embedding
    LIMIT p_match_count;
END;
$$;

-- Match conversation transcripts with client_id isolation
CREATE OR REPLACE FUNCTION match_conversation_transcripts_secure(
    p_client_id UUID,           -- ADDED: Required for shared pool
    p_query_embedding vector,
    p_agent_slug TEXT,
    p_user_id UUID,
    p_match_threshold FLOAT8 DEFAULT 0.5,
    p_match_count INTEGER DEFAULT 5,
    p_exclude_conversation_id UUID DEFAULT NULL
)
RETURNS TABLE (
    id UUID,
    conversation_id UUID,
    role TEXT,
    content TEXT,
    relevance FLOAT8,
    created_at TIMESTAMPTZ
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    RETURN QUERY
    SELECT
        ct.id,
        ct.conversation_id,
        ct.role,
        ct.content,
        (1 - (ct.embeddings <=> p_query_embedding))::FLOAT8 as relevance,
        ct.created_at
    FROM conversation_transcripts ct
    JOIN agents a ON ct.agent_id = a.id AND a.client_id = p_client_id
    WHERE ct.client_id = p_client_id           -- Client isolation
      AND a.slug = p_agent_slug
      AND ct.user_id = p_user_id
      AND ct.embeddings IS NOT NULL
      AND (p_exclude_conversation_id IS NULL OR ct.conversation_id != p_exclude_conversation_id)
      AND (1 - (ct.embeddings <=> p_query_embedding)) > p_match_threshold
    ORDER BY ct.embeddings <=> p_query_embedding
    LIMIT p_match_count;
END;
$$;

-- Get/update user overview with client_id
CREATE OR REPLACE FUNCTION get_user_overview_for_agent(
    p_client_id UUID,
    p_user_id UUID,
    p_agent_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    result JSONB;
BEGIN
    SELECT sections INTO result
    FROM user_overviews
    WHERE client_id = p_client_id
      AND user_id = p_user_id
      AND (agent_id = p_agent_id OR agent_id IS NULL)
    ORDER BY agent_id NULLS LAST
    LIMIT 1;

    RETURN COALESCE(result, '{}'::JSONB);
END;
$$;
```

---

## Phase 3: Update Application Code

**Duration: 3-4 days**

### 3.1 Update ClientConnectionManager

**File:** `app/services/client_connection_manager.py`

```python
# Add to ClientConnectionManager class

class ClientConnectionManager:
    def __init__(self):
        self._platform_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        self._client_cache = {}
        self._shared_pool_client = None  # Lazy loaded
        self._shared_pool_config = None

    def _get_shared_pool_client(self) -> Client:
        """Get or create the shared pool Supabase client."""
        if self._shared_pool_client is None:
            # Load shared pool config from platform DB
            result = self._platform_client.table('shared_pool_config').select('*').eq('is_active', True).eq('pool_name', 'adventurer_pool').single().execute()

            if not result.data:
                raise ValueError("No active shared pool configured")

            self._shared_pool_config = result.data
            self._shared_pool_client = create_client(
                result.data['supabase_url'],
                result.data['supabase_service_role_key']
            )

        return self._shared_pool_client

    def get_client_db_client(self, client_id: UUID) -> tuple[Client, str]:
        """
        Get the appropriate Supabase client for a given client_id.

        Returns:
            tuple: (supabase_client, hosting_type)
            - For 'shared': Returns shared pool client
            - For 'dedicated': Returns client's own Supabase client
        """
        client_config = self.get_client_config(client_id)

        if client_config.get('hosting_type') == 'shared':
            # Adventurer tier: use shared pool
            return self._get_shared_pool_client(), 'shared'
        else:
            # Champion/Paragon tier: use dedicated project
            return create_client(
                client_config['supabase_url'],
                client_config['supabase_service_role_key']
            ), 'dedicated'

    def get_client_config(self, client_id: UUID) -> dict:
        """Get client configuration including tier info."""
        if client_id in self._client_cache:
            return self._client_cache[client_id]

        result = self._platform_client.table('clients').select(
            'id, name, tier, hosting_type, max_sidekicks, '
            'supabase_url, supabase_service_role_key, supabase_anon_key'
        ).eq('id', str(client_id)).single().execute()

        if not result.data:
            raise ValueError(f"Client {client_id} not found")

        self._client_cache[client_id] = result.data
        return result.data
```

### 3.2 Update Query Patterns for Shared Pool

For shared pool queries, we need to always include `client_id`:

```python
# Example: Fetching agents

def get_agents_for_client(client_id: UUID) -> list:
    db_client, hosting_type = connection_manager.get_client_db_client(client_id)

    if hosting_type == 'shared':
        # Shared pool: must filter by client_id
        result = db_client.table('agents').select('*').eq('client_id', str(client_id)).execute()
    else:
        # Dedicated: client_id not needed (entire DB is theirs)
        result = db_client.table('agents').select('*').execute()

    return result.data
```

### 3.3 Create Query Wrapper Utility

**File:** `app/services/tenant_query.py`

```python
"""
Tenant-aware query utilities for shared/dedicated infrastructure.
"""
from typing import Any, Optional
from uuid import UUID
from supabase import Client

class TenantQuery:
    """
    Wrapper that automatically handles client_id filtering based on hosting type.
    """

    def __init__(self, client: Client, client_id: UUID, hosting_type: str):
        self.client = client
        self.client_id = client_id
        self.hosting_type = hosting_type
        self.is_shared = hosting_type == 'shared'

    def table(self, table_name: str):
        """Get a table query builder with automatic client_id filtering."""
        query = self.client.table(table_name)
        if self.is_shared:
            # For shared hosting, always filter by client_id
            return SharedTableQuery(query, self.client_id)
        return query

    def rpc(self, function_name: str, params: dict) -> Any:
        """Call RPC with automatic client_id injection for shared hosting."""
        if self.is_shared:
            # Inject client_id into params for shared pool RPCs
            params = {'p_client_id': str(self.client_id), **params}
        return self.client.rpc(function_name, params)


class SharedTableQuery:
    """Query builder that enforces client_id filtering."""

    def __init__(self, query, client_id: UUID):
        self._query = query
        self._client_id = client_id
        self._client_id_applied = False

    def select(self, *args, **kwargs):
        self._ensure_client_filter()
        return self._query.select(*args, **kwargs)

    def insert(self, data: dict | list, **kwargs):
        # Ensure client_id is set on inserts
        if isinstance(data, dict):
            data['client_id'] = str(self._client_id)
        elif isinstance(data, list):
            for item in data:
                item['client_id'] = str(self._client_id)
        return self._query.insert(data, **kwargs)

    def update(self, data: dict, **kwargs):
        self._ensure_client_filter()
        return self._query.update(data, **kwargs)

    def delete(self, **kwargs):
        self._ensure_client_filter()
        return self._query.delete(**kwargs)

    def _ensure_client_filter(self):
        if not self._client_id_applied:
            self._query = self._query.eq('client_id', str(self._client_id))
            self._client_id_applied = True
        return self._query

    def eq(self, column: str, value: Any):
        self._ensure_client_filter()
        self._query = self._query.eq(column, value)
        return self

    # Add other query methods as needed...
```

### 3.4 Update Agent Worker for Shared Pool

**File:** `docker/agent/entrypoint.py`

```python
# In the job handler, detect hosting type and adjust queries

async def entrypoint(ctx: JobContext):
    metadata = ctx.job.metadata
    client_id = metadata.get('client_id')
    hosting_type = metadata.get('hosting_type', 'dedicated')

    if hosting_type == 'shared':
        # Use shared pool connection
        shared_pool_url = os.environ.get('SHARED_POOL_SUPABASE_URL')
        shared_pool_key = os.environ.get('SHARED_POOL_SUPABASE_KEY')
        client_supabase = create_client(shared_pool_url, shared_pool_key)

        # All queries must include client_id
        context_manager = AgentContextManager(
            supabase_client=client_supabase,
            client_id=client_id,
            hosting_type='shared',  # Enables client_id filtering
            ...
        )
    else:
        # Use dedicated connection (existing logic)
        client_supabase = create_client(
            metadata.get('supabase_url'),
            metadata.get('supabase_service_role_key')
        )
        context_manager = AgentContextManager(
            supabase_client=client_supabase,
            client_id=client_id,
            hosting_type='dedicated',
            ...
        )
```

### 3.5 Update Provisioning for Adventurer Tier

**File:** `app/services/onboarding/provisioning_worker.py`

```python
async def provision_client(client_id: UUID, tier: str):
    """Provision a new client based on their tier."""

    if tier == 'adventurer':
        # Shared tier: Skip Supabase project creation
        # Just mark as ready with shared hosting
        await platform_client.table('clients').update({
            'hosting_type': 'shared',
            'supabase_url': None,  # Uses shared pool
            'supabase_service_role_key': None,
            'provisioning_status': 'ready',
            'provisioning_completed_at': datetime.utcnow().isoformat(),
            'max_sidekicks': 1
        }).eq('id', str(client_id)).execute()

        # Create their first agent slot in shared pool
        shared_pool = get_shared_pool_client()
        # (Agent creation happens when they set up their sidekick)

    elif tier in ('champion', 'paragon'):
        # Dedicated tier: Create Supabase project (existing logic)
        await create_supabase_project(client_id)
        await apply_schema(client_id)

        await platform_client.table('clients').update({
            'hosting_type': 'dedicated',
            'provisioning_status': 'ready',
            'provisioning_completed_at': datetime.utcnow().isoformat(),
            'max_sidekicks': None  # Unlimited
        }).eq('id', str(client_id)).execute()
```

---

## Phase 4: Tier Enforcement & Feature Gating

**Duration: 2 days**

### 4.1 Sidekick Limit Enforcement

```python
# app/api/v1/agents_multitenant.py

@router.post("/clients/{client_id}/agents")
async def create_agent(client_id: UUID, agent_data: AgentCreate):
    client = await get_client(client_id)

    # Check sidekick limit for Adventurer tier
    if client.max_sidekicks is not None:
        current_count = await count_agents(client_id)
        if current_count >= client.max_sidekicks:
            raise HTTPException(
                status_code=403,
                detail=f"Sidekick limit reached ({client.max_sidekicks}). "
                       f"Upgrade to Champion tier for unlimited sidekicks."
            )

    # Proceed with agent creation...
```

### 4.2 Tier Feature Flags

```python
# app/services/tier_features.py

TIER_FEATURES = {
    'adventurer': {
        'max_sidekicks': 1,
        'max_documents': 50,
        'max_document_size_mb': 5,
        'rag_enabled': True,
        'voice_chat_enabled': True,
        'video_chat_enabled': False,  # Adventurer: no video
        'custom_voice_enabled': False,
        'api_access_enabled': False,
        'priority_support': False,
        'white_label': False,
    },
    'champion': {
        'max_sidekicks': None,  # Unlimited
        'max_documents': None,
        'max_document_size_mb': 50,
        'rag_enabled': True,
        'voice_chat_enabled': True,
        'video_chat_enabled': True,
        'custom_voice_enabled': True,
        'api_access_enabled': True,
        'priority_support': False,
        'white_label': False,
    },
    'paragon': {
        'max_sidekicks': None,
        'max_documents': None,
        'max_document_size_mb': None,
        'rag_enabled': True,
        'voice_chat_enabled': True,
        'video_chat_enabled': True,
        'custom_voice_enabled': True,
        'api_access_enabled': True,
        'priority_support': True,
        'white_label': True,
        'custom_integrations': True,
        'dedicated_support_channel': True,
    }
}

def get_tier_features(tier: str) -> dict:
    return TIER_FEATURES.get(tier, TIER_FEATURES['adventurer'])

def check_feature_access(client: dict, feature: str) -> bool:
    tier = client.get('tier', 'adventurer')
    features = get_tier_features(tier)
    return features.get(feature, False)
```

---

## Phase 5: Upgrade Path (Adventurer → Champion)

**Duration: 2-3 days**

### 5.1 Data Migration Flow

```python
# app/services/tier_upgrade.py

async def upgrade_to_champion(client_id: UUID):
    """
    Upgrade an Adventurer client to Champion tier.
    Migrates all data from shared pool to dedicated Supabase project.
    """

    # 1. Create dedicated Supabase project
    project = await create_supabase_project(client_id)

    # 2. Apply schema to new project
    await apply_schema(client_id)

    # 3. Migrate data from shared pool
    shared_pool = get_shared_pool_client()
    dedicated = create_client(project['url'], project['service_role_key'])

    # Migrate agents
    agents = shared_pool.table('agents').select('*').eq('client_id', str(client_id)).execute()
    if agents.data:
        # Remove client_id column (not needed in dedicated)
        for agent in agents.data:
            del agent['client_id']
        dedicated.table('agents').insert(agents.data).execute()

    # Migrate documents, chunks, conversations, etc...
    await migrate_table('documents', client_id, shared_pool, dedicated)
    await migrate_table('document_chunks', client_id, shared_pool, dedicated)
    await migrate_table('agent_documents', client_id, shared_pool, dedicated)
    await migrate_table('conversations', client_id, shared_pool, dedicated)
    await migrate_table('conversation_transcripts', client_id, shared_pool, dedicated)
    await migrate_table('user_overviews', client_id, shared_pool, dedicated)

    # 4. Update client record
    await platform_client.table('clients').update({
        'tier': 'champion',
        'hosting_type': 'dedicated',
        'supabase_url': project['url'],
        'supabase_service_role_key': project['service_role_key'],
        'supabase_anon_key': project['anon_key'],
        'max_sidekicks': None
    }).eq('id', str(client_id)).execute()

    # 5. Delete data from shared pool (after verification)
    await cleanup_shared_pool_data(client_id, shared_pool)

    return {'status': 'upgraded', 'tier': 'champion'}


async def migrate_table(table_name: str, client_id: UUID, source: Client, dest: Client):
    """Migrate a table's data from shared to dedicated."""
    data = source.table(table_name).select('*').eq('client_id', str(client_id)).execute()

    if data.data:
        # Remove client_id column for dedicated database
        for row in data.data:
            if 'client_id' in row:
                del row['client_id']

        # Insert in batches to avoid timeout
        batch_size = 100
        for i in range(0, len(data.data), batch_size):
            batch = data.data[i:i + batch_size]
            dest.table(table_name).insert(batch).execute()
```

---

## Phase 6: Testing & Rollout

**Duration: 3-4 days**

### 6.1 Testing Checklist

- [ ] **Shared Pool Isolation**
  - [ ] Client A cannot see Client B's agents
  - [ ] Client A cannot query Client B's documents
  - [ ] Client A cannot access Client B's conversations
  - [ ] RLS policies correctly block cross-client access

- [ ] **Dedicated Pool (Existing Functionality)**
  - [ ] Champion clients continue to work unchanged
  - [ ] No regressions in RAG, voice chat, video chat

- [ ] **Tier Enforcement**
  - [ ] Adventurer limited to 1 sidekick
  - [ ] Champion has unlimited sidekicks
  - [ ] Feature flags work correctly

- [ ] **Upgrade Flow**
  - [ ] Data migrates correctly from shared → dedicated
  - [ ] No data loss during migration
  - [ ] Client can use new sidekick immediately after upgrade

### 6.2 Rollout Strategy

1. **Week 1**: Deploy Phase 1-2 (schema changes, shared pool setup)
2. **Week 2**: Deploy Phase 3-4 (code changes, feature gating)
3. **Week 3**: Internal testing with test Adventurer accounts
4. **Week 4**: Soft launch to new signups only
5. **Week 5**: Enable upgrade path for existing free users

---

## Cost Analysis

### Current Cost (Dedicated for All)

| Clients | Supabase Projects | Estimated Monthly Cost |
|---------|-------------------|------------------------|
| 10 | 10 | ~$250 (free tier mostly) |
| 50 | 50 | ~$1,250 |
| 200 | 200 | ~$5,000 |
| 1000 | 1000 | ~$25,000 |

### Projected Cost (Tiered Architecture)

Assuming 80% Adventurer, 15% Champion, 5% Paragon:

| Total Clients | Adventurer (Shared) | Champion (Dedicated) | Paragon | Est. Monthly Cost |
|---------------|---------------------|----------------------|---------|-------------------|
| 100 | 80 | 15 | 5 | ~$500 (1 shared + 20 dedicated) |
| 500 | 400 | 75 | 25 | ~$2,500 (1 shared + 100 dedicated) |
| 2000 | 1600 | 300 | 100 | ~$10,000 (2 shared + 400 dedicated) |

**Estimated savings: 60-75% reduction in infrastructure costs**

---

## Summary Timeline

| Phase | Description | Duration | Dependencies |
|-------|-------------|----------|--------------|
| **Phase 1** | Platform DB schema updates | 2-3 days | None |
| **Phase 2** | Shared pool DB setup | 3-4 days | Phase 1 |
| **Phase 3** | Application code updates | 3-4 days | Phase 2 |
| **Phase 4** | Tier enforcement & features | 2 days | Phase 3 |
| **Phase 5** | Upgrade path implementation | 2-3 days | Phase 4 |
| **Phase 6** | Testing & rollout | 3-4 days | Phase 5 |

**Total estimated duration: 3-4 weeks**

---

## Open Questions

1. **Paragon Tier Details**: What specific customizations are included? Self-hosted option? Custom domains?

2. **Billing Integration**: How will tier changes be tied to payment processing?

3. **Shared Pool Scaling**: At what point do we need a second shared pool? (Recommend ~1000 clients per pool)

4. **Data Retention**: Different retention policies per tier?

5. **Rate Limiting**: Different API rate limits per tier?

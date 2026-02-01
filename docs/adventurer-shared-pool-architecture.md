# Adventurer Tier: Shared Pool Database Architecture

## The Problem

When a user signs up for the Adventurer tier, the provisioning worker does **not** create a dedicated Supabase project. Instead, it sets:

```
supabase_url: null
supabase_service_role_key: null
uses_platform_keys: true
hosting_type: "shared"
```

This means Adventurer clients have no database to store documents, agents, document chunks, conversation history, or any tenant-specific data. When an Adventurer tries to upload a document in the wizard, the document processor calls `get_client_supabase_config()`, gets `None`, and fails.

## Current Stopgap (Insecure)

As a temporary fix to unblock Adventurer users, we modified `get_client_supabase_config()` to fall back to the **platform Supabase credentials** when a client has no dedicated project but is identified as shared/adventurer.

This is a security problem because the platform database contains:
- The `clients` table with every tenant's `supabase_service_role_key`, API keys, and configuration
- The `admin_users` table
- The `client_provisioning_jobs` table
- All platform operational data

The platform service role key bypasses Row-Level Security entirely. Any code path that uses this connection could accidentally expose cross-tenant secrets. There is no data isolation — Adventurer document/agent data would be mixed into the platform's own tables.

## Proposed Solution: Dedicated Shared Pool Project

Create a separate Supabase project specifically for Adventurer-tier clients. This project contains only tenant data tables (`agents`, `documents`, `document_chunks`, `agent_documents`, `conversations`, etc.) — never platform admin tables.

### Architecture

```
Platform DB (senzircaknleviasihav.supabase.co)
  - clients table (secrets, config)
  - admin_users table
  - tools table
  - client_provisioning_jobs
  - shared_pool_config
  - NO tenant document/agent data

Shared Pool DB (new project, e.g. adventurer-pool-xxxxx.supabase.co)
  - agents table (with client_id column + RLS)
  - documents table (with client_id column + RLS)
  - document_chunks table (with client_id column + RLS)
  - agent_documents table (with client_id column + RLS)
  - conversations table (with client_id column + RLS)
  - All tables have RLS policies enforcing client_id isolation

Champion/Paragon DB (dedicated per-client project)
  - Same schema, single-tenant
  - No client_id filtering needed (whole DB is theirs)
```

### Row-Level Security

Every table in the shared pool must have RLS enabled with policies like:

```sql
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;

CREATE POLICY "clients_own_documents" ON documents
  FOR ALL
  USING (client_id = current_setting('app.current_client_id')::uuid)
  WITH CHECK (client_id = current_setting('app.current_client_id')::uuid);
```

The application sets `app.current_client_id` per-request before any queries. The service role key still bypasses RLS, so application code must use a restricted role for queries, or all queries must explicitly include `WHERE client_id = X`.

### The HNSW Vector Index Problem

The `document_chunks` table has an HNSW index on the `embeddings` column for vector similarity search (RAG). In a shared pool, this single index contains vectors from **all** Adventurer clients.

**Security concern:** HNSW index traversal during a similarity search touches vectors from all tenants. While the SQL WHERE clause filters results to the correct client, the index scan itself crosses tenant boundaries. This is primarily a performance concern (query cost scales with total pool size, not your tenant's data) and a theoretical side-channel (timing analysis could reveal whether similar content exists for other tenants, though exploiting this is impractical).

**Performance concern:** At scale (1000+ Adventurers x 50 docs x ~20 chunks = 1M+ vectors), the shared HNSW index becomes the bottleneck. Every Adventurer's RAG query pays the cost of searching through all tenants' vectors.

### Recommended Solution: Two-Stage Retrieval for Shared Pool

Adventurer tier limits are small: 1 sidekick, 50 documents max, ~1000 chunks per client. At this scale, brute-force exact cosine similarity is faster than HNSW index lookup overhead.

For shared-pool clients, use an exact scan instead of the HNSW index:

```sql
-- Exact cosine similarity (no shared index traversal)
SELECT id, title, content,
       1 - (embeddings <=> p_query_embedding) AS similarity
FROM document_chunks dc
JOIN agent_documents ad ON ad.document_id = dc.document_id
WHERE ad.agent_id = p_agent_id
  AND dc.client_id = p_client_id  -- Filter FIRST, then scan
ORDER BY embeddings <=> p_query_embedding
LIMIT p_match_count;
```

With a B-tree index on `(client_id, document_id)`, Postgres filters to ~1000 rows first, then does exact cosine on just those rows. No shared vector index is touched. Sub-millisecond for Adventurer-scale data.

For Champion/Paragon clients on dedicated projects, HNSW continues to be used since they may have thousands of documents and the index is single-tenant.

### Alternative Approaches Considered

| Approach | Pros | Cons |
|----------|------|------|
| **Platform DB fallback** (current) | Zero setup | Exposes platform secrets, no isolation |
| **Shared pool + HNSW** | Simple | Cross-tenant index traversal, scaling wall |
| **Shared pool + exact scan** (recommended) | Isolated, fast for small data | Needs separate match_documents RPC |
| **Partial HNSW indexes per client** | True index isolation | 1000+ indexes = catalog bloat, write overhead |
| **Per-client schemas in shared pool** | Full isolation | Schema management complexity |
| **Free Supabase project per Adventurer** | Same isolation as Champion | Org project limits, slow provisioning |

### Provisioning Changes Required

1. **Create a shared pool Supabase project** (one-time, manual or via Management API)
2. **Run schema sync** on the shared pool project (same tenant tables, plus `client_id` columns and RLS)
3. **Store pool credentials** in `shared_pool_config` table on the platform DB
4. **Update provisioning worker** (`_process_shared_pool_setup`): instead of setting `supabase_url: null`, set it to the shared pool project's URL and service role key
5. **Update `match_documents` RPC** in the shared pool to use exact scan instead of HNSW
6. **Add `client_id` column** to all tables in the shared pool schema (not needed in dedicated projects)
7. **Ensure all document/agent operations** include `client_id` in WHERE clauses when operating on shared pool

### Migration Path for Existing Adventurer Clients

Any Adventurer clients already provisioned with `supabase_url: null` need to be updated to point at the shared pool project. This is a simple UPDATE on the `clients` table:

```sql
UPDATE clients
SET supabase_url = '<shared_pool_url>',
    supabase_service_role_key = '<shared_pool_service_key>'
WHERE tier = 'adventurer'
  AND (supabase_url IS NULL OR supabase_url = '');
```

### Scaling Path

- **Under 500 Adventurers:** Single shared pool project (Supabase Pro plan)
- **500-2000:** Upgrade shared pool compute (Supabase compute add-ons)
- **2000+:** Pool sharding — multiple shared pool projects, route via `shared_pool_config` table which tracks `current_client_count` and `max_clients` per pool
- **10000+:** Consider self-hosted Supabase or dedicated lightweight projects per client

### Open Questions

1. Should we revert the platform-DB fallback immediately, or keep it until the shared pool project is stood up?
2. Do we want RLS enforcement at the Postgres level, or is application-level `client_id` filtering acceptable? (RLS is defense-in-depth but adds query overhead)
3. Should the shared pool use the same schema as dedicated projects (plus `client_id` columns), or a simplified schema matching Adventurer-tier features only?
4. How do we handle the `match_documents` RPC — create a separate version for shared pool, or make the existing one detect shared vs. dedicated?

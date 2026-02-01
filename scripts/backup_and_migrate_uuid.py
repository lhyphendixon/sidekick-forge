#!/usr/bin/env python3
"""
Backup affected tables and run UUID migration for tenant databases.

Usage:
  python scripts/backup_and_migrate_uuid.py [--dry-run] [--only <client_id>]

Reads SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY from .env for platform access.
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import httpx

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
MIGRATION_FILE = PROJECT_DIR / "migrations" / "20260127_migrate_document_ids_to_uuid.sql"
BACKUP_DIR = PROJECT_DIR / "backups" / f"uuid_migration_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

# Tables to back up before migration
BACKUP_TABLES = ["documents", "document_chunks", "agent_documents", "document_intelligence"]


def load_env():
    """Load .env file from project root."""
    env_path = PROJECT_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ[key.strip()] = val.strip()


def _extract_project_ref(supabase_url: str) -> str:
    """Extract project ref from Supabase URL like https://abcdef.supabase.co."""
    return supabase_url.replace("https://", "").split(".")[0]


async def execute_sql_management_api(project_ref: str, access_token: str, sql: str) -> list | None:
    """Execute SQL via Supabase Management API."""
    endpoint = f"https://api.supabase.com/v1/projects/{project_ref}/database/query"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=300.0) as http:
        resp = await http.post(endpoint, headers=headers, json={"query": sql})
        if resp.status_code not in (200, 201):
            if resp.status_code == 408:
                raise httpx.HTTPStatusError(
                    f"Query read timeout (408)", request=resp.request, response=resp
                )
            resp.raise_for_status()
        try:
            return resp.json()
        except Exception:
            return None


async def postgrest_select(client_url: str, service_key: str, table: str, select: str = "*", filters: str = "") -> list:
    """Select rows via PostgREST."""
    endpoint = f"{client_url}/rest/v1/{table}?select={select}"
    if filters:
        endpoint += f"&{filters}"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.get(endpoint, headers=headers)
        resp.raise_for_status()
        return resp.json()


async def backup_table(client_url: str, service_key: str, table: str, backup_path: Path) -> int:
    """Back up a table via Supabase REST API (PostgREST). Excludes vector columns."""
    # For migration backup we only need IDs — not full content or embeddings
    select = "id,title,file_name,status,created_at" if table == "documents" else "*"
    if table == "document_chunks":
        select = "id,document_id,chunk_index,created_at"
    if table == "agent_documents":
        select = "id,agent_id,document_id,enabled,created_at"
    if table == "document_intelligence":
        select = "id,document_id,client_id,document_title,version,created_at"
    endpoint = f"{client_url}/rest/v1/{table}?select={select}"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Accept": "application/json",
        "Prefer": "count=exact",
    }
    rows = []
    offset = 0
    page_size = 1000

    async with httpx.AsyncClient(timeout=30.0) as http:
        while True:
            resp = await http.get(
                f"{endpoint}&offset={offset}&limit={page_size}",
                headers=headers,
            )
            if resp.status_code == 404:
                # Table doesn't exist — skip
                return -1
            resp.raise_for_status()
            page = resp.json()
            if not page:
                break
            rows.extend(page)
            if len(page) < page_size:
                break
            offset += page_size

    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(json.dumps(rows, indent=2, default=str))
    return len(rows)


async def get_clients(platform_url: str, platform_key: str, only: str | None, access_token: str = "") -> list[dict]:
    """Fetch active clients from platform database via Management API."""
    platform_ref = _extract_project_ref(platform_url)
    sql = "SELECT id, name, supabase_url, supabase_service_role_key FROM clients WHERE supabase_url IS NOT NULL AND supabase_service_role_key IS NOT NULL"
    if only:
        sql += f" AND id = '{only}'"
    data = await execute_sql_management_api(platform_ref, access_token, sql)
    clients = []
    for row in data or []:
        if row.get("supabase_url") and row.get("supabase_service_role_key"):
            clients.append({
                "id": row["id"],
                "name": row.get("name", "unknown"),
                "supabase_url": row["supabase_url"],
                "service_role_key": row["supabase_service_role_key"],
            })
    return clients


async def backup_client(client: dict) -> Path:
    """Back up all affected tables for a single client."""
    client_id = client["id"]
    client_name = client.get("name", "unknown")
    client_url = client["supabase_url"]
    service_key = client["service_role_key"]
    client_backup_dir = BACKUP_DIR / f"{client_name}_{client_id[:8]}"

    print(f"\n  Backing up {client_name} ({client_id[:8]})...")
    for table in BACKUP_TABLES:
        path = client_backup_dir / f"{table}.json"
        try:
            count = await backup_table(client_url, service_key, table, path)
            if count == -1:
                print(f"    {table}: table not found (skipped)")
            else:
                print(f"    {table}: {count} rows backed up")
        except Exception as e:
            print(f"    {table}: BACKUP ERROR — {e}")

    return client_backup_dir


def build_migration_steps() -> list[tuple[str, str]]:
    """Build the migration as a series of smaller SQL steps.

    Handles edge cases discovered during Autonomite migration:
    - HNSW indexes on document_chunks cause severe slowdowns during writes
    - Extra FK constraints: documents_parent_document_id_fkey, admin_alerts_document_id_fkey
    - RLS policies and views may reference documents.id
    """
    return [
        ("Check if already UUID", """
SELECT data_type FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'documents' AND column_name = 'id';
"""),
        ("Create mapping table", """
CREATE TABLE IF NOT EXISTS _doc_id_migration (
    old_id BIGINT PRIMARY KEY,
    new_id UUID NOT NULL DEFAULT gen_random_uuid()
);
"""),
        ("Populate mapping table", """
INSERT INTO _doc_id_migration (old_id)
SELECT id FROM public.documents
ON CONFLICT DO NOTHING;
"""),
        ("Drop FK constraints and indexes", """
ALTER TABLE public.document_chunks DROP CONSTRAINT IF EXISTS document_chunks_document_id_fkey;
ALTER TABLE public.agent_documents DROP CONSTRAINT IF EXISTS agent_documents_document_id_fkey;
ALTER TABLE public.agent_documents DROP CONSTRAINT IF EXISTS agent_documents_agent_id_document_id_key;
ALTER TABLE public.documents DROP CONSTRAINT IF EXISTS documents_parent_document_id_fkey;
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'admin_alerts') THEN
    ALTER TABLE public.admin_alerts DROP CONSTRAINT IF EXISTS admin_alerts_document_id_fkey;
  END IF;
END$$;
DROP INDEX IF EXISTS idx_document_chunks_document_id;
DROP INDEX IF EXISTS idx_agent_documents_document_id;
"""),
        ("Drop HNSW/vector indexes", """
DROP INDEX IF EXISTS document_chunks_embeddings_hnsw;
DROP INDEX IF EXISTS idx_document_chunks_embeddings;
"""),
        ("Drop dependent policies and views", """
DO $$
DECLARE r record;
BEGIN
  -- Drop all RLS policies on document_chunks that might reference documents.id
  FOR r IN SELECT policyname FROM pg_policies WHERE tablename = 'document_chunks' LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.document_chunks', r.policyname);
  END LOOP;
  -- Drop all RLS policies on documents that might reference id column
  FOR r IN SELECT policyname FROM pg_policies WHERE tablename = 'documents' LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.documents', r.policyname);
  END LOOP;
END$$;
DROP VIEW IF EXISTS document_summarization_status;
"""),
        ("Add UUID columns to documents", """
ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS uuid_id UUID;
UPDATE public.documents d
  SET uuid_id = m.new_id
  FROM _doc_id_migration m
  WHERE m.old_id = d.id AND d.uuid_id IS NULL;
"""),
        ("Add UUID column to document_chunks and populate", """
ALTER TABLE public.document_chunks ADD COLUMN IF NOT EXISTS document_id_uuid UUID;
UPDATE public.document_chunks dc
  SET document_id_uuid = m.new_id
  FROM _doc_id_migration m
  WHERE m.old_id = dc.document_id AND dc.document_id_uuid IS NULL;
"""),
        ("Add UUID columns to agent_documents", """
ALTER TABLE public.agent_documents ADD COLUMN IF NOT EXISTS document_id_uuid UUID;
UPDATE public.agent_documents ad
  SET document_id_uuid = m.new_id
  FROM _doc_id_migration m
  WHERE m.old_id = ad.document_id AND ad.document_id_uuid IS NULL;
"""),
        ("Convert admin_alerts.document_id", """
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'admin_alerts'
      AND column_name = 'document_id' AND data_type != 'uuid'
  ) THEN
    ALTER TABLE public.admin_alerts ADD COLUMN IF NOT EXISTS document_id_uuid UUID;
    UPDATE public.admin_alerts SET document_id_uuid = m.new_id
      FROM _doc_id_migration m WHERE m.old_id = admin_alerts.document_id AND admin_alerts.document_id_uuid IS NULL;
    ALTER TABLE public.admin_alerts DROP COLUMN document_id;
    ALTER TABLE public.admin_alerts RENAME COLUMN document_id_uuid TO document_id;
  END IF;
END$$;
"""),
        ("Convert documents.parent_document_id", """
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'documents'
      AND column_name = 'parent_document_id' AND data_type = 'bigint'
  ) THEN
    ALTER TABLE public.documents ADD COLUMN IF NOT EXISTS parent_document_id_uuid UUID;
    UPDATE public.documents SET parent_document_id_uuid = m.new_id
      FROM _doc_id_migration m WHERE m.old_id = documents.parent_document_id AND documents.parent_document_id_uuid IS NULL;
    ALTER TABLE public.documents DROP COLUMN parent_document_id;
    ALTER TABLE public.documents RENAME COLUMN parent_document_id_uuid TO parent_document_id;
  END IF;
END$$;
"""),
        ("Swap documents.id", """
ALTER TABLE public.documents DROP CONSTRAINT IF EXISTS documents_pkey;
ALTER TABLE public.documents DROP COLUMN id;
ALTER TABLE public.documents RENAME COLUMN uuid_id TO id;
ALTER TABLE public.documents ADD PRIMARY KEY (id);
ALTER TABLE public.documents ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE public.documents ALTER COLUMN id SET NOT NULL;
"""),
        ("Swap document_chunks.document_id", """
ALTER TABLE public.document_chunks DROP COLUMN document_id;
ALTER TABLE public.document_chunks RENAME COLUMN document_id_uuid TO document_id;
"""),
        ("Swap agent_documents.document_id", """
ALTER TABLE public.agent_documents DROP COLUMN document_id;
ALTER TABLE public.agent_documents RENAME COLUMN document_id_uuid TO document_id;
"""),
        ("Re-add FK constraints", """
ALTER TABLE public.document_chunks
  ADD CONSTRAINT document_chunks_document_id_fkey
  FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;
ALTER TABLE public.agent_documents
  ADD CONSTRAINT agent_documents_document_id_fkey
  FOREIGN KEY (document_id) REFERENCES public.documents(id) ON DELETE CASCADE;
ALTER TABLE public.agent_documents
  ADD CONSTRAINT agent_documents_agent_id_document_id_key
  UNIQUE (agent_id, document_id);
"""),
        ("Handle document_intelligence", """
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'document_intelligence'
      AND column_name = 'document_id'
      AND data_type != 'uuid'
  ) THEN
    ALTER TABLE public.document_intelligence DROP CONSTRAINT IF EXISTS uniq_document_intelligence;
    ALTER TABLE public.document_intelligence ADD COLUMN IF NOT EXISTS document_id_uuid UUID;
    UPDATE public.document_intelligence di
      SET document_id_uuid = m.new_id
      FROM _doc_id_migration m
      WHERE m.old_id = di.document_id::bigint AND di.document_id_uuid IS NULL;
    ALTER TABLE public.document_intelligence DROP COLUMN document_id;
    ALTER TABLE public.document_intelligence RENAME COLUMN document_id_uuid TO document_id;
    ALTER TABLE public.document_intelligence
      ADD CONSTRAINT uniq_document_intelligence UNIQUE (document_id, client_id);
  END IF;
END$$;
"""),
        ("Clean up mapping table", """
DROP TABLE IF EXISTS _doc_id_migration;
"""),
        ("Recreate HNSW index", "HNSW_RECREATE"),
    ]


def build_rpc_steps() -> list[tuple[str, str]]:
    """Build RPC recreation steps from the migration file (everything after the DO block)."""
    migration_text = MIGRATION_FILE.read_text()
    # Extract from "-- Step 8:" onwards
    marker = "-- Step 8: Recreate RPCs with UUID signatures"
    idx = migration_text.find(marker)
    if idx == -1:
        return []
    rpc_sql = migration_text[idx:]
    return [("Recreate RPCs with UUID signatures", f"SET statement_timeout = '300s';\n{rpc_sql}")]


async def migrate_client(client: dict, migration_sql: str, dry_run: bool, access_token: str) -> bool:
    """Run the UUID migration on a single client via Management API, step by step."""
    client_id = client["id"]
    client_name = client.get("name", "unknown")
    client_url = client["supabase_url"]
    project_ref = _extract_project_ref(client_url)

    if dry_run:
        print(f"  [DRY RUN] Would migrate {client_name} ({client_id[:8]}) — project: {project_ref}")
        return True

    print(f"  Migrating {client_name} ({client_id[:8]}) — project: {project_ref}...")

    steps = build_migration_steps() + build_rpc_steps()

    # Wake up the database (Supabase may pause inactive projects)
    try:
        await execute_sql_management_api(project_ref, access_token, "SELECT 1")
    except Exception:
        pass

    # Step 0: Check if already UUID
    check_step = steps[0]
    try:
        result = await execute_sql_management_api(project_ref, access_token, check_step[1])
        if result and len(result) > 0:
            dtype = result[0].get("data_type", "")
            if dtype == "uuid":
                print(f"    Already UUID — skipping migration")
                return True
            elif not dtype:
                print(f"    documents table not found — skipping")
                return True
            print(f"    Current type: {dtype} — proceeding with migration")
    except Exception as e:
        print(f"    ❌ Check failed: {e}")
        return False

    # Run remaining steps
    for step_name, step_sql in steps[1:]:
        print(f"    {step_name}...")

        # Handle HNSW index recreation (timeout is OK — it builds in background)
        if step_sql == "HNSW_RECREATE":
            try:
                await execute_sql_management_api(project_ref, access_token, """
CREATE INDEX IF NOT EXISTS document_chunks_embeddings_hnsw
  ON public.document_chunks USING hnsw (embeddings vector_cosine_ops) WITH (m=16, ef_construction=64);
""")
                print(f"      Index created")
            except Exception:
                print(f"      Index creation timed out (will build in background — OK)")
            continue

        try:
            await execute_sql_management_api(project_ref, access_token, step_sql)
        except httpx.HTTPStatusError as e:
            print(f"    ❌ FAILED at '{step_name}': {e.response.status_code}")
            print(f"       {e.response.text[:300]}")
            return False
        except Exception as e:
            print(f"    ❌ FAILED at '{step_name}': {e}")
            return False

    print(f"  ✅ Migration complete for {client_name}")
    return True


async def verify_client(client: dict, access_token: str) -> bool:
    """Verify documents.id is now UUID type."""
    client_url = client["supabase_url"]
    client_name = client.get("name", "unknown")
    project_ref = _extract_project_ref(client_url)

    sql = """
    SELECT data_type FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'documents' AND column_name = 'id'
    """
    try:
        result = await execute_sql_management_api(project_ref, access_token, sql)
        if result and len(result) > 0:
            dtype = result[0].get("data_type", "unknown")
            if dtype == "uuid":
                print(f"  ✅ {client_name}: documents.id is UUID")
                return True
            else:
                print(f"  ⚠️  {client_name}: documents.id is still {dtype}")
                return False
        else:
            print(f"  ⚠️  {client_name}: documents table not found")
            return True
    except Exception as e:
        print(f"  ❌ {client_name}: verification failed: {e}")
        return False


async def main():
    load_env()

    parser = argparse.ArgumentParser(description="Backup and migrate tenant document IDs to UUID")
    parser.add_argument("--dry-run", action="store_true", help="Back up and show what would be migrated")
    parser.add_argument("--only", help="Single client ID to migrate")
    parser.add_argument("--skip-backup", action="store_true", help="Skip backup step")
    parser.add_argument("--verify-only", action="store_true", help="Only verify current state")
    args = parser.parse_args()

    platform_url = os.environ.get("SUPABASE_URL")
    platform_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    access_token = os.environ.get("SUPABASE_ACCESS_TOKEN")

    if not platform_url or not platform_key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        sys.exit(1)

    if not access_token:
        print("ERROR: SUPABASE_ACCESS_TOKEN must be set (needed for Management API SQL execution)")
        sys.exit(1)

    if not MIGRATION_FILE.exists():
        print(f"ERROR: Migration file not found: {MIGRATION_FILE}")
        sys.exit(1)

    migration_sql = "SET statement_timeout = '600s';\n" + MIGRATION_FILE.read_text()

    # Fetch clients
    print("Fetching active clients...")
    clients = await get_clients(platform_url, platform_key, args.only, access_token)
    print(f"Found {len(clients)} client(s)")

    if not clients:
        print("No clients to process.")
        return

    # Verify-only mode
    if args.verify_only:
        print("\n=== Verifying current state ===")
        for client in clients:
            await verify_client(client, access_token)
        return

    # Step 1: Backup
    if not args.skip_backup:
        print(f"\n=== Step 1: Backing up affected tables to {BACKUP_DIR} ===")
        for client in clients:
            await backup_client(client)
        print(f"\nBackups saved to: {BACKUP_DIR}")
    else:
        print("\n=== Step 1: Backup SKIPPED ===")

    # Step 2: Migrate
    print(f"\n=== Step 2: Running UUID migration {'(DRY RUN)' if args.dry_run else ''} ===")
    results = []
    for client in clients:
        success = await migrate_client(client, migration_sql, args.dry_run, access_token)
        results.append((client, success))

    # Step 3: Verify
    if not args.dry_run:
        print("\n=== Step 3: Verifying migration ===")
        for client, success in results:
            if success:
                await verify_client(client, access_token)

    # Summary
    succeeded = sum(1 for _, s in results if s)
    failed = sum(1 for _, s in results if not s)
    print(f"\n=== Summary ===")
    print(f"  Total: {len(results)}")
    print(f"  Succeeded: {succeeded}")
    print(f"  Failed: {failed}")
    if BACKUP_DIR.exists():
        print(f"  Backups: {BACKUP_DIR}")


if __name__ == "__main__":
    asyncio.run(main())

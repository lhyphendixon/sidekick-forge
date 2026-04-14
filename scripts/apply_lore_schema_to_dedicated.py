#!/usr/bin/env python3
"""
Apply the Lore schema to every dedicated Champion/Paragon Supabase instance.

Reads the canonical migration file and executes it against each dedicated
client's Supabase project using their service_role_key stored in the
platform clients table.

Idempotent — safe to run multiple times. Uses CREATE TABLE IF NOT EXISTS
and ON CONFLICT DO UPDATE throughout.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

import httpx
from supabase import create_client

MIGRATION_FILE = Path(__file__).resolve().parent.parent / "supabase" / "migrations" / "20260412080000_create_lore_tables.sql"


def apply_sql_via_rest(supabase_url: str, service_key: str, sql: str) -> tuple[bool, str]:
    """Apply SQL to a Supabase instance via the PostgREST/PostgreSQL RPC.

    Since direct SQL execution isn't exposed via PostgREST, we use
    supabase-py's raw SQL through the `postgres-meta` Supabase REST interface.
    For dedicated instances where we don't have dashboard access, we use
    the `pg_exec` RPC (if available) or fall back to individual table ops.
    """
    # Try the Supabase Management API path — works if the project is
    # accessible via api.supabase.com with an access token. For dedicated
    # client instances we don't have that, so we use psycopg directly.
    return False, "not applicable"


def apply_sql_via_psycopg(supabase_url: str, service_key: str, sql: str) -> tuple[bool, str]:
    """Apply SQL via direct Postgres connection using the DB URL.

    Dedicated Supabase instances expose Postgres on the same host with
    port 5432 (or via a connection pooler). The service_role_key isn't a
    Postgres password, so we need to reconstruct the connection string.
    """
    # The Supabase convention: postgres host = db.<ref>.supabase.co
    # Password is the DB password, not the service_role_key.
    # Without the DB password, we can't use psycopg.
    return False, "no db password available"


def apply_sql_chunked(sb_client, sql: str) -> tuple[bool, str]:
    """Apply SQL by splitting into individual statements and executing
    each via supabase-py. This only works for idempotent DDL that
    supabase-py's raw query interface supports.

    Supabase python client doesn't expose raw SQL, so we take a different
    approach: create the tables via direct HTTP to PostgREST's RPC endpoint
    after installing a helper function once.
    """
    return False, "not supported by supabase-py"


def apply_via_management_api(project_ref: str, access_token: str, sql: str) -> tuple[bool, str]:
    """Apply SQL via the Supabase Management API database/query endpoint.

    Requires SUPABASE_ACCESS_TOKEN with access to the project's organization.
    """
    endpoint = f"https://api.supabase.com/v1/projects/{project_ref}/database/query"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(endpoint, headers=headers, json={"query": sql})
        if resp.status_code in (200, 201):
            return True, "ok"
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as exc:
        return False, f"exception: {exc}"


def apply_via_direct_pg(pg_url: str, sql: str) -> tuple[bool, str]:
    """Apply SQL via direct psycopg connection if PG_URL env is set for the instance."""
    try:
        import psycopg2
    except ImportError:
        return False, "psycopg2 not installed"
    try:
        conn = psycopg2.connect(pg_url)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(sql)
        cur.close()
        conn.close()
        return True, "ok (psycopg)"
    except Exception as exc:
        return False, f"psycopg error: {exc}"


def main():
    platform_url = os.getenv("SUPABASE_URL")
    platform_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not platform_url or not platform_key:
        print("❌ SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set")
        sys.exit(1)

    if not MIGRATION_FILE.exists():
        print(f"❌ Migration file not found: {MIGRATION_FILE}")
        sys.exit(1)

    sql = MIGRATION_FILE.read_text()
    print(f"📄 Loaded migration ({len(sql)} chars)\n")

    access_token = os.getenv("SUPABASE_ACCESS_TOKEN", "")
    if not access_token:
        print("❌ SUPABASE_ACCESS_TOKEN not set — cannot apply to dedicated instances")
        sys.exit(1)

    platform = create_client(platform_url, platform_key)
    clients = platform.table("clients").select(
        "id,name,tier,hosting_type,supabase_url,supabase_service_role_key"
    ).execute().data

    dedicated = [
        c for c in clients
        if c.get("hosting_type") == "dedicated"
        and c.get("supabase_url")
        and c.get("supabase_service_role_key")
    ]

    print(f"🎯 Found {len(dedicated)} dedicated clients to migrate\n")

    results = []
    for client in dedicated:
        name = client["name"]
        url = client["supabase_url"]
        project_ref = url.split("//")[1].split(".")[0]
        print(f"→ {name} ({project_ref})")

        ok, msg = apply_via_management_api(project_ref, access_token, sql)
        if ok:
            print(f"  ✅ {msg}")
        else:
            print(f"  ❌ {msg}")
        results.append((name, ok, msg))

    print("\n" + "=" * 60)
    ok_count = sum(1 for _, ok, _ in results if ok)
    print(f"Results: {ok_count}/{len(results)} succeeded")
    for name, ok, msg in results:
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name}: {msg}")


if __name__ == "__main__":
    main()

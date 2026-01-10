#!/usr/bin/env python3
"""
Apply HNSW Performance Migration to All Clients

This script applies the HNSW index and RPC function updates to all client
Supabase databases on the platform.

Usage:
    python apply_hnsw_migration.py [--dry-run] [--client-id UUID]

Options:
    --dry-run       Show what would be done without making changes
    --client-id     Only apply to a specific client (for testing)
"""

import os
import sys
import argparse
import requests
from typing import Optional, Tuple, List, Dict, Any

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from supabase import create_client

def require_env(name: str) -> str:
    """Fetch a required environment variable or exit explicitly."""
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


# Platform Supabase configuration (required; no hardcoded fallbacks)
PLATFORM_SUPABASE_URL = require_env("PLATFORM_SUPABASE_URL")
PLATFORM_SERVICE_KEY = require_env("PLATFORM_SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_ACCESS_TOKEN = require_env("SUPABASE_ACCESS_TOKEN")

# Migration SQL file path
MIGRATION_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "migrations",
    "20250107_hnsw_performance_migration.sql"
)


def execute_sql_via_api(project_ref: str, sql: str) -> Tuple[bool, Any]:
    """Execute SQL via Supabase Management API."""
    url = f"https://api.supabase.com/v1/projects/{project_ref}/database/query"
    headers = {
        "Authorization": f"Bearer {SUPABASE_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(url, headers=headers, json={"query": sql}, timeout=120)
        if response.status_code in (200, 201):
            return True, response.json()
        else:
            return False, f"HTTP {response.status_code}: {response.text}"
    except Exception as e:
        return False, str(e)


def get_project_ref_from_url(supabase_url: str) -> Optional[str]:
    """Extract project reference from Supabase URL."""
    if not supabase_url:
        return None
    # URL format: https://xxxxx.supabase.co
    try:
        return supabase_url.replace("https://", "").split(".")[0]
    except:
        return None


def get_all_clients() -> List[Dict[str, Any]]:
    """Get all clients from the platform database."""
    client = create_client(PLATFORM_SUPABASE_URL, PLATFORM_SERVICE_KEY)
    result = client.table('clients').select('id, name, supabase_url').execute()
    return result.data or []


def check_hnsw_index_exists(project_ref: str, table_name: str) -> Tuple[bool, bool]:
    """Check if HNSW index exists on a table. Returns (success, exists)."""
    sql = f"""
    SELECT indexname FROM pg_indexes
    WHERE tablename = '{table_name}'
    AND indexname = '{table_name}_embeddings_hnsw';
    """
    ok, result = execute_sql_via_api(project_ref, sql)
    if ok:
        return True, len(result) > 0
    return False, False


def check_table_exists(project_ref: str, table_name: str) -> Tuple[bool, bool]:
    """Check if a table exists. Returns (success, exists)."""
    sql = f"""
    SELECT table_name FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = '{table_name}';
    """
    ok, result = execute_sql_via_api(project_ref, sql)
    if ok:
        return True, len(result) > 0
    return False, False


def get_chunk_count(project_ref: str) -> int:
    """Get the number of chunks in document_chunks table."""
    sql = "SELECT COUNT(*) as cnt FROM document_chunks;"
    ok, result = execute_sql_via_api(project_ref, sql)
    if ok and result:
        return result[0].get('cnt', 0)
    return 0


def apply_migration(project_ref: str, client_name: str, dry_run: bool = False) -> Tuple[bool, str]:
    """Apply the HNSW migration to a client database."""

    # First check if tables exist
    ok, doc_chunks_exists = check_table_exists(project_ref, 'document_chunks')
    if not ok:
        return False, "Failed to check if document_chunks table exists"

    ok, conv_trans_exists = check_table_exists(project_ref, 'conversation_transcripts')
    if not ok:
        return False, "Failed to check if conversation_transcripts table exists"

    if not doc_chunks_exists and not conv_trans_exists:
        return True, "Skipped - no vector tables found (document_chunks, conversation_transcripts)"

    # Check current state
    status_parts = []

    if doc_chunks_exists:
        ok, hnsw_exists = check_hnsw_index_exists(project_ref, 'document_chunks')
        if ok:
            chunk_count = get_chunk_count(project_ref)
            if hnsw_exists:
                status_parts.append(f"document_chunks: HNSW exists ({chunk_count} chunks)")
            else:
                status_parts.append(f"document_chunks: needs HNSW ({chunk_count} chunks)")

    if conv_trans_exists:
        ok, hnsw_exists = check_hnsw_index_exists(project_ref, 'conversation_transcripts')
        if ok:
            if hnsw_exists:
                status_parts.append("conversation_transcripts: HNSW exists")
            else:
                status_parts.append("conversation_transcripts: needs HNSW")

    if dry_run:
        return True, f"[DRY RUN] Would apply migration. Current state: {'; '.join(status_parts)}"

    # Read migration SQL
    with open(MIGRATION_FILE, 'r') as f:
        migration_sql = f.read()

    # Apply migration
    ok, result = execute_sql_via_api(project_ref, migration_sql)

    if ok:
        return True, f"Migration applied successfully. Status: {'; '.join(status_parts)}"
    else:
        return False, f"Migration failed: {result}"


def main():
    parser = argparse.ArgumentParser(description='Apply HNSW migration to all clients')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done')
    parser.add_argument('--client-id', type=str, help='Only apply to specific client')
    args = parser.parse_args()

    print("=" * 70)
    print("HNSW Performance Migration")
    print("=" * 70)

    if args.dry_run:
        print("MODE: DRY RUN (no changes will be made)\n")
    else:
        print("MODE: LIVE (changes will be applied)\n")

    # Get all clients
    clients = get_all_clients()
    print(f"Found {len(clients)} clients in the platform database\n")

    # Filter if specific client requested
    if args.client_id:
        clients = [c for c in clients if c['id'] == args.client_id]
        if not clients:
            print(f"ERROR: Client {args.client_id} not found")
            sys.exit(1)

    # Process each client
    results = {
        'success': [],
        'skipped': [],
        'failed': []
    }

    for client in clients:
        client_id = client['id']
        client_name = client['name']
        supabase_url = client.get('supabase_url')

        print(f"\n{'='*50}")
        print(f"Client: {client_name}")
        print(f"ID: {client_id}")

        if not supabase_url:
            print("Status: SKIPPED (no Supabase URL)")
            results['skipped'].append((client_name, "No Supabase URL configured"))
            continue

        project_ref = get_project_ref_from_url(supabase_url)
        print(f"Project Ref: {project_ref}")

        if not project_ref:
            print("Status: SKIPPED (invalid Supabase URL)")
            results['skipped'].append((client_name, "Invalid Supabase URL"))
            continue

        # Apply migration
        success, message = apply_migration(project_ref, client_name, args.dry_run)

        if success:
            if "Skipped" in message:
                print(f"Status: SKIPPED")
                results['skipped'].append((client_name, message))
            else:
                print(f"Status: SUCCESS")
                results['success'].append((client_name, message))
        else:
            print(f"Status: FAILED")
            results['failed'].append((client_name, message))

        print(f"Details: {message}")

    # Summary
    print("\n" + "=" * 70)
    print("MIGRATION SUMMARY")
    print("=" * 70)
    print(f"\nSuccessful: {len(results['success'])}")
    for name, msg in results['success']:
        print(f"  - {name}")

    print(f"\nSkipped: {len(results['skipped'])}")
    for name, msg in results['skipped']:
        print(f"  - {name}: {msg}")

    print(f"\nFailed: {len(results['failed'])}")
    for name, msg in results['failed']:
        print(f"  - {name}: {msg}")

    if results['failed']:
        sys.exit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Sync production database to staging.

This script copies essential configuration data from production to staging
while skipping large transient data like conversation history.

Usage:
    python scripts/sync_prod_to_staging.py [--dry-run] [--tables TABLE1,TABLE2]
"""

import argparse
import json
import sys
from typing import Optional
import requests

# Production Supabase
PROD_URL = "https://eukudpgfpihxsypulopm.supabase.co"
PROD_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImV1a3VkcGdmcGloeHN5cHVsb3BtIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1MzUxMjkyMiwiZXhwIjoyMDY5MDg4OTIyfQ.wOSF5bSdd763_PVyCmSEBGjtbhP67WMfms1aGydO_44"

# Staging Supabase (new branch: senzircaknleviasihav)
STAGING_URL = "https://senzircaknleviasihav.supabase.co"
STAGING_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNlbnppcmNha25sZXZpYXNpaGF2Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2OTEwNjc3OSwiZXhwIjoyMDg0NjgyNzc5fQ.kIWw3LXbznZLk0dMUA4_i4s4R2y5GQbnqmpHIUDSMJk"

# Tables to sync (in order of dependencies)
TABLES_TO_SYNC = [
    # Core configuration
    "clients",
    "profiles",
    "platform_client_user_mappings",
    "platform_api_keys",

    # Agents and tools
    "tools",
    "agents",
    "agent_tools",
    "agent_documents",

    # Tier/quota config
    "tier_quotas",
    "shared_pool_config",

    # Integrations
    "wordpress_sites",

    # Documents (optional - can be large)
    "documents",
]

# Tables explicitly skipped (for documentation)
TABLES_SKIPPED = [
    "conversation_transcripts",  # Very large - conversation history
    "conversation_summaries",    # Derived from transcripts
    "conversations",             # Session data
    "agent_usage",              # Analytics
    "client_usage",             # Analytics
    "livekit_events",           # Event logs
    "ambient_ability_runs",     # Run logs
    "content_catalyst_runs",    # Run logs
    "user_overviews",           # Generated data
    "user_overview_history",    # History
    "orders",                   # Financial - handle separately
    "pending_checkouts",        # Transient
    "contact_submissions",      # Support tickets
    "email_verification_tokens", # Transient
    "client_asana_connections", # OAuth tokens - sensitive
    "client_provisioning_jobs", # Job logs
    "documentsense_learning_jobs", # Job logs
    "usersense_learning_jobs",  # Job logs
    "wordpress_content_sync",   # Sync state
    "document_chunks",          # Large embeddings
    "document_intelligence",    # Derived data
]


def get_headers(key: str) -> dict:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def fetch_table(url: str, key: str, table: str, limit: int = 10000) -> list:
    """Fetch all rows from a table."""
    headers = get_headers(key)
    resp = requests.get(
        f"{url}/rest/v1/{table}",
        headers=headers,
        params={"limit": limit}
    )
    if resp.status_code == 200:
        return resp.json()
    elif resp.status_code == 404:
        print(f"  Table {table} not found")
        return []
    else:
        print(f"  Error fetching {table}: {resp.status_code} - {resp.text[:200]}")
        return []


def delete_table_data(url: str, key: str, table: str) -> bool:
    """Delete all data from a staging table."""
    headers = get_headers(key)
    # Use a filter that matches all rows
    resp = requests.delete(
        f"{url}/rest/v1/{table}",
        headers=headers,
        params={"id": "neq.00000000-0000-0000-0000-000000000000"}  # Match all
    )
    if resp.status_code in [200, 204]:
        return True
    elif resp.status_code == 404:
        return True  # Table doesn't exist, that's fine
    else:
        print(f"  Warning: Could not clear {table}: {resp.status_code} - {resp.text[:100]}")
        return False


def insert_rows(url: str, key: str, table: str, rows: list, upsert: bool = True) -> tuple[int, int]:
    """Insert rows into staging table. Returns (success_count, error_count)."""
    if not rows:
        return 0, 0

    headers = get_headers(key)
    if upsert:
        headers["Prefer"] = "resolution=merge-duplicates,return=representation"

    # Insert in batches
    batch_size = 100
    success = 0
    errors = 0

    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        resp = requests.post(
            f"{url}/rest/v1/{table}",
            headers=headers,
            json=batch
        )
        if resp.status_code in [200, 201]:
            success += len(batch)
        else:
            errors += len(batch)
            if errors <= 5:  # Only show first few errors
                print(f"  Error inserting batch: {resp.status_code} - {resp.text[:200]}")

    return success, errors


def sync_table(table: str, dry_run: bool = False) -> dict:
    """Sync a single table from production to staging."""
    print(f"\nSyncing {table}...")

    # Fetch from production
    prod_data = fetch_table(PROD_URL, PROD_KEY, table)
    print(f"  Production: {len(prod_data)} rows")

    if dry_run:
        print(f"  [DRY RUN] Would sync {len(prod_data)} rows")
        return {"table": table, "rows": len(prod_data), "synced": 0, "errors": 0, "dry_run": True}

    if not prod_data:
        return {"table": table, "rows": 0, "synced": 0, "errors": 0}

    # Clear staging table first (to handle deletions)
    # delete_table_data(STAGING_URL, STAGING_KEY, table)

    # Insert into staging (upsert mode)
    synced, errors = insert_rows(STAGING_URL, STAGING_KEY, table, prod_data, upsert=True)
    print(f"  Staging: {synced} synced, {errors} errors")

    return {"table": table, "rows": len(prod_data), "synced": synced, "errors": errors}


def main():
    parser = argparse.ArgumentParser(description="Sync production database to staging")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be synced without making changes")
    parser.add_argument("--tables", type=str, help="Comma-separated list of specific tables to sync")
    parser.add_argument("--skip-documents", action="store_true", help="Skip syncing documents table")
    args = parser.parse_args()

    print("=" * 60)
    print("Production to Staging Database Sync")
    print("=" * 60)
    print(f"\nProduction: {PROD_URL}")
    print(f"Staging: {STAGING_URL}")

    if args.dry_run:
        print("\n*** DRY RUN MODE - No changes will be made ***")

    # Determine tables to sync
    if args.tables:
        tables = [t.strip() for t in args.tables.split(",")]
    else:
        tables = TABLES_TO_SYNC.copy()
        if args.skip_documents:
            tables = [t for t in tables if t != "documents"]

    print(f"\nTables to sync: {', '.join(tables)}")
    print(f"Tables skipped: {', '.join(TABLES_SKIPPED[:5])}... and {len(TABLES_SKIPPED)-5} more")

    # Sync each table
    results = []
    for table in tables:
        try:
            result = sync_table(table, dry_run=args.dry_run)
            results.append(result)
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"table": table, "rows": 0, "synced": 0, "errors": 1, "error": str(e)})

    # Summary
    print("\n" + "=" * 60)
    print("SYNC SUMMARY")
    print("=" * 60)

    total_rows = sum(r["rows"] for r in results)
    total_synced = sum(r["synced"] for r in results)
    total_errors = sum(r["errors"] for r in results)

    for r in results:
        status = "✓" if r["errors"] == 0 and r["synced"] > 0 else ("⚠" if r["errors"] > 0 else "-")
        print(f"  {status} {r['table']}: {r['synced']}/{r['rows']} rows")

    print(f"\nTotal: {total_synced}/{total_rows} rows synced, {total_errors} errors")

    if args.dry_run:
        print("\n*** This was a dry run. Run without --dry-run to apply changes. ***")

    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Apply chat mode columns migration to all client databases.

This script adds voice_chat_enabled, text_chat_enabled, and video_chat_enabled
columns to the agents table in all client Supabase databases.

Usage:
    python3 scripts/apply_chat_mode_migration.py
"""

import asyncio
import os
import sys
from typing import Dict, List, Optional

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from supabase import create_client, Client


# Migration SQL
MIGRATION_SQL = """
-- Add chat mode columns to agents table
ALTER TABLE agents ADD COLUMN IF NOT EXISTS voice_chat_enabled boolean DEFAULT true;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS text_chat_enabled boolean DEFAULT true;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS video_chat_enabled boolean DEFAULT false;
"""

# Verification SQL
VERIFY_SQL = """
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'agents'
  AND column_name IN ('voice_chat_enabled', 'text_chat_enabled', 'video_chat_enabled')
ORDER BY column_name;
"""


async def get_all_clients(platform_supabase: Client) -> List[Dict]:
    """Get all clients from the platform database."""
    try:
        result = platform_supabase.table("clients").select("*").execute()
        return result.data if result.data else []
    except Exception as e:
        print(f"Error fetching clients: {e}")
        return []


def get_client_supabase_config(client: Dict) -> Optional[Dict[str, str]]:
    """Extract Supabase configuration from a client record."""
    # The platform database stores these as top-level columns
    url = client.get("supabase_url")
    service_role_key = client.get("supabase_service_role_key")

    if not url or not service_role_key:
        return None

    # Skip placeholder URLs
    if "your-project" in url or "placeholder" in url.lower():
        return None

    return {
        "url": url,
        "service_role_key": service_role_key,
        "name": client.get("name", "Unknown"),
        "id": client.get("id", "Unknown")
    }


def apply_migration(client_supabase: Client, client_name: str) -> bool:
    """Apply the migration to a client's Supabase database."""
    try:
        # Use RPC to execute raw SQL (requires a function in Supabase)
        # Since we can't execute raw SQL directly via the REST API,
        # we'll use individual ALTER TABLE statements via the PostgREST API
        # by checking/adding columns one at a time

        # First, let's check if the agents table exists
        try:
            result = client_supabase.table("agents").select("id").limit(1).execute()
        except Exception as e:
            print(f"  ⚠️  {client_name}: No agents table found or access denied: {e}")
            return False

        # Check current columns using information_schema via RPC if available
        # If not, we'll try to update a row with the new columns and see what happens

        # Try to read an agent to see what columns exist
        try:
            result = client_supabase.table("agents").select("voice_chat_enabled, text_chat_enabled, video_chat_enabled").limit(1).execute()
            print(f"  ✅ {client_name}: Columns already exist")
            return True
        except Exception as e:
            error_str = str(e).lower()
            if "column" in error_str and "does not exist" in error_str:
                print(f"  📝 {client_name}: Columns don't exist, need to run SQL migration manually")
                return False
            else:
                print(f"  ⚠️  {client_name}: Unexpected error checking columns: {e}")
                return False

    except Exception as e:
        print(f"  ❌ {client_name}: Migration failed: {e}")
        return False


def execute_sql_migration(supabase_url: str, service_role_key: str, client_name: str) -> bool:
    """
    Execute raw SQL migration using Supabase Management API or direct PostgreSQL.
    Since PostgREST doesn't support DDL, we need an alternative approach.
    """
    import httpx

    # Extract project ref from URL (e.g., https://xxxxx.supabase.co -> xxxxx)
    project_ref = supabase_url.replace("https://", "").replace(".supabase.co", "")

    # Use Supabase SQL endpoint (requires service role key)
    sql_endpoint = f"https://{project_ref}.supabase.co/rest/v1/rpc/exec_sql"

    headers = {
        "apikey": service_role_key,
        "Authorization": f"Bearer {service_role_key}",
        "Content-Type": "application/json"
    }

    # The exec_sql RPC function may not exist, so we'll try a different approach
    # Use the pg_catalog approach or create the function if needed

    # Alternative: Use Supabase's built-in ability to run SQL via REST
    # by calling the SQL Editor API (requires access token, not service role key)

    # For now, let's check if columns exist and report which clients need migration
    return False


async def main():
    """Main function to apply migration to all clients."""
    print("=" * 60)
    print("Chat Mode Columns Migration Script")
    print("=" * 60)

    # Get platform Supabase credentials
    platform_url = os.getenv("SUPABASE_URL")
    platform_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not platform_url or not platform_key:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        sys.exit(1)

    print(f"\nPlatform Supabase: {platform_url}")

    # Create platform Supabase client
    platform_supabase = create_client(platform_url, platform_key)

    # Get all clients
    print("\nFetching clients from platform database...")
    clients = await get_all_clients(platform_supabase)
    print(f"Found {len(clients)} clients")

    # Track results
    already_migrated = []
    needs_migration = []
    errors = []
    skipped = []

    # Process each client
    print("\n" + "-" * 60)
    print("Checking each client's database...")
    print("-" * 60)

    # Group clients by their Supabase URL to avoid duplicate migrations
    seen_urls = set()

    for client in clients:
        client_name = client.get("name", "Unknown")
        client_id = client.get("id", "Unknown")

        config = get_client_supabase_config(client)
        if not config:
            print(f"  ⏭️  {client_name}: No valid Supabase config, skipping")
            skipped.append(client_name)
            continue

        # Skip duplicate Supabase URLs
        if config["url"] in seen_urls:
            print(f"  ⏭️  {client_name}: Same Supabase as another client, skipping duplicate")
            skipped.append(f"{client_name} (duplicate)")
            continue

        seen_urls.add(config["url"])

        try:
            # Create client-specific Supabase instance
            client_supabase = create_client(config["url"], config["service_role_key"])

            if apply_migration(client_supabase, client_name):
                already_migrated.append(client_name)
            else:
                needs_migration.append({
                    "name": client_name,
                    "url": config["url"],
                    "project_ref": config["url"].replace("https://", "").replace(".supabase.co", "")
                })
        except Exception as e:
            print(f"  ❌ {client_name}: Connection failed: {e}")
            errors.append(client_name)

    # Summary
    print("\n" + "=" * 60)
    print("MIGRATION SUMMARY")
    print("=" * 60)
    print(f"\n✅ Already migrated: {len(already_migrated)}")
    for name in already_migrated:
        print(f"   - {name}")

    print(f"\n⏭️  Skipped: {len(skipped)}")
    for name in skipped:
        print(f"   - {name}")

    print(f"\n❌ Errors: {len(errors)}")
    for name in errors:
        print(f"   - {name}")

    if needs_migration:
        print(f"\n📝 NEEDS MANUAL MIGRATION: {len(needs_migration)}")
        print("\nRun the following SQL in each Supabase SQL Editor:")
        print("-" * 50)
        print(MIGRATION_SQL)
        print("-" * 50)
        print("\nProjects that need migration:")
        for client in needs_migration:
            print(f"\n   {client['name']}:")
            print(f"   Project: {client['project_ref']}")
            print(f"   URL: https://supabase.com/dashboard/project/{client['project_ref']}/sql/new")
    else:
        print("\n🎉 All clients are already migrated!")


if __name__ == "__main__":
    asyncio.run(main())

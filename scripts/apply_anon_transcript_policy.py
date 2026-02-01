#!/usr/bin/env python3
"""
Apply the scoped anon transcript policy migration to Supabase.
This enables Realtime subscriptions for anon users who know the conversation_id.
"""
import os
import sys
import re
from pathlib import Path

import requests

def load_env():
    """Load environment variables from .env file."""
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key, value)

def get_project_ref(supabase_url: str) -> str:
    """Extract project reference from Supabase URL."""
    url_match = re.search(r'https://([^.]+)\.supabase\.co', supabase_url)
    if not url_match:
        raise ValueError(f"Could not parse Supabase URL: {supabase_url}")
    return url_match.group(1)

def run_sql_via_management_api(project_ref: str, access_token: str, sql: str) -> dict:
    """Execute SQL via Supabase Management API."""
    url = f"https://api.supabase.com/v1/projects/{project_ref}/database/query"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json={"query": sql},
            timeout=60
        )

        if response.status_code in (200, 201):
            return {"success": True, "data": response.json() if response.text else None}
        else:
            return {"success": False, "error": f"HTTP {response.status_code}: {response.text}"}

    except requests.exceptions.Timeout:
        return {"success": False, "error": "Request timed out"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def main():
    load_env()

    supabase_url = os.environ.get('SUPABASE_URL')
    access_token = os.environ.get('SUPABASE_ACCESS_TOKEN')

    if not supabase_url:
        print("ERROR: SUPABASE_URL not set")
        sys.exit(1)

    if not access_token:
        print("ERROR: SUPABASE_ACCESS_TOKEN not set")
        print("Get one from: https://supabase.com/dashboard/account/tokens")
        sys.exit(1)

    project_ref = get_project_ref(supabase_url)
    print(f"Target project: {project_ref}")

    # Read the migration SQL
    migration_path = Path(__file__).parent.parent / 'migrations' / '20260131_scoped_anon_transcript_policy.sql'
    if not migration_path.exists():
        print(f"ERROR: Migration file not found: {migration_path}")
        sys.exit(1)

    migration_sql = migration_path.read_text()
    print(f"Read migration: {len(migration_sql)} bytes")

    # Execute the migration
    print("\nApplying migration...")
    result = run_sql_via_management_api(project_ref, access_token, migration_sql)

    if result.get("success"):
        print("✅ Migration applied successfully!")
        if result.get("data"):
            print(f"Result: {result['data']}")
    else:
        print(f"❌ Migration failed: {result.get('error')}")
        sys.exit(1)

    # Verify the policy was created
    print("\nVerifying policy...")
    verify_sql = """
    SELECT policyname, roles, cmd, qual
    FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'conversation_transcripts'
      AND policyname LIKE '%anon%'
    """
    verify_result = run_sql_via_management_api(project_ref, access_token, verify_sql)

    if verify_result.get("success"):
        print("✅ Policy verification:")
        data = verify_result.get("data", [])
        if data:
            for row in data:
                print(f"  - Policy: {row}")
        else:
            print("  - No anon policies found (may need to check manually)")
    else:
        print(f"⚠️  Verification query failed: {verify_result.get('error')}")

    # Verify realtime publication
    print("\nVerifying realtime publication...")
    realtime_sql = """
    SELECT tablename
    FROM pg_publication_tables
    WHERE pubname = 'supabase_realtime'
      AND schemaname = 'public'
      AND tablename = 'conversation_transcripts'
    """
    realtime_result = run_sql_via_management_api(project_ref, access_token, realtime_sql)

    if realtime_result.get("success"):
        data = realtime_result.get("data", [])
        if data:
            print("✅ conversation_transcripts is in supabase_realtime publication")
        else:
            print("⚠️  conversation_transcripts NOT in supabase_realtime publication!")
            print("  Adding to publication...")
            add_sql = "ALTER PUBLICATION supabase_realtime ADD TABLE public.conversation_transcripts"
            add_result = run_sql_via_management_api(project_ref, access_token, add_sql)
            if add_result.get("success"):
                print("✅ Added to publication")
            else:
                print(f"❌ Failed to add: {add_result.get('error')}")

    print("\n" + "="*60)
    print("Migration complete! Citations should now work in the HTML embed.")
    print("="*60)

if __name__ == "__main__":
    main()

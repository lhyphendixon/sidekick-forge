#!/usr/bin/env python3
"""
Apply the anon transcript RLS policy to all client Supabase instances.
This enables Realtime subscriptions for citations in voice mode.
"""
import os
import sys
import re
from pathlib import Path
import requests

def load_env():
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key, value)

def get_project_ref(supabase_url: str) -> str:
    url_match = re.search(r'https://([^.]+)\.supabase\.co', supabase_url)
    if not url_match:
        return None
    return url_match.group(1)

def run_sql_via_management_api(project_ref: str, access_token: str, sql: str) -> dict:
    url = f"https://api.supabase.com/v1/projects/{project_ref}/database/query"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.post(url, headers=headers, json={"query": sql}, timeout=60)
        if response.status_code in (200, 201):
            return {"success": True, "data": response.json() if response.text else None}
        else:
            return {"success": False, "error": f"HTTP {response.status_code}: {response.text}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def main():
    load_env()

    platform_url = os.environ.get('SUPABASE_URL')
    service_key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
    access_token = os.environ.get('SUPABASE_ACCESS_TOKEN')

    if not all([platform_url, service_key, access_token]):
        print("ERROR: Missing required environment variables")
        sys.exit(1)

    # Get list of clients with their Supabase URLs
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
    }

    response = requests.get(
        f"{platform_url}/rest/v1/clients?select=id,name,supabase_url",
        headers=headers
    )
    clients = response.json()

    # Get unique Supabase URLs (some clients share databases)
    unique_dbs = {}
    for client in clients:
        url = client.get('supabase_url')
        if url and url not in unique_dbs:
            unique_dbs[url] = client.get('name')

    # Also include the platform database
    unique_dbs[platform_url] = "Platform (senzircaknleviasihav)"

    print(f"Found {len(unique_dbs)} unique Supabase databases to update")
    print()

    # The SQL to apply
    migration_sql = """
-- Enable RLS if not already enabled
ALTER TABLE public.conversation_transcripts ENABLE ROW LEVEL SECURITY;

-- Set REPLICA IDENTITY FULL for proper Realtime CDC
ALTER TABLE public.conversation_transcripts REPLICA IDENTITY FULL;

-- Drop old policies if they exist
DROP POLICY IF EXISTS conversation_transcripts_anon_read ON public.conversation_transcripts;
DROP POLICY IF EXISTS conversation_transcripts_anon_scoped_read ON public.conversation_transcripts;

-- Create the anon read policy for Realtime subscriptions
CREATE POLICY conversation_transcripts_anon_scoped_read
    ON public.conversation_transcripts
    FOR SELECT
    TO anon
    USING (true);
"""

    # Verify SQL for checking
    verify_sql = """
SELECT policyname, roles, cmd
FROM pg_policies
WHERE schemaname = 'public'
  AND tablename = 'conversation_transcripts'
  AND policyname LIKE '%anon%'
"""

    results = []
    for url, name in unique_dbs.items():
        project_ref = get_project_ref(url)
        if not project_ref:
            print(f"❌ Skipping {name}: Invalid URL format")
            continue

        print(f"Applying to: {name} ({project_ref})")

        result = run_sql_via_management_api(project_ref, access_token, migration_sql)

        if result.get("success"):
            # Verify
            verify_result = run_sql_via_management_api(project_ref, access_token, verify_sql)
            if verify_result.get("success") and verify_result.get("data"):
                print(f"  ✅ Success - Policy created")
                results.append((name, "success"))
            else:
                print(f"  ⚠️  Applied but verification returned: {verify_result}")
                results.append((name, "unverified"))
        else:
            print(f"  ❌ Failed: {result.get('error')}")
            results.append((name, f"failed: {result.get('error')[:50]}"))

    print()
    print("=" * 60)
    print("Summary:")
    print("=" * 60)
    for name, status in results:
        print(f"  {name}: {status}")

if __name__ == "__main__":
    main()

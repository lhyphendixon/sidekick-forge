#!/usr/bin/env python3
"""
Tenant Database Migration Runner

Applies migrations to all client (tenant) Supabase databases.
Uses the Supabase Management API for each tenant.
"""
import os
import sys
import re
import json
import argparse
import requests
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv

load_dotenv()


def get_all_clients():
    """Get all clients with Supabase credentials from platform database"""
    platform_url = os.getenv('SUPABASE_URL')
    platform_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

    if not platform_url or not platform_key:
        print("Error: Platform credentials not set")
        sys.exit(1)

    response = requests.get(
        f"{platform_url}/rest/v1/clients?select=id,name,supabase_url,supabase_project_ref",
        headers={
            "apikey": platform_key,
            "Authorization": f"Bearer {platform_key}"
        }
    )

    if response.status_code != 200:
        print(f"Error fetching clients: {response.text}")
        sys.exit(1)

    return response.json()


def run_migration_for_tenant(project_ref: str, client_name: str, sql_content: str, access_token: str):
    """Run migration for a single tenant"""
    url = f"https://api.supabase.com/v1/projects/{project_ref}/database/query"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json={"query": sql_content},
            timeout=300
        )

        if response.status_code in (200, 201):
            return {"success": True, "client": client_name}
        else:
            return {"success": False, "client": client_name, "error": f"HTTP {response.status_code}: {response.text[:200]}"}

    except Exception as e:
        return {"success": False, "client": client_name, "error": str(e)}


def main():
    parser = argparse.ArgumentParser(
        description='Run migrations on all tenant databases'
    )
    parser.add_argument(
        'migration_file',
        help='SQL migration file to execute'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be executed without making changes'
    )
    parser.add_argument(
        '--client',
        help='Run only for specific client name (partial match)'
    )

    args = parser.parse_args()

    # Check for access token
    access_token = os.getenv('SUPABASE_ACCESS_TOKEN')
    if not access_token:
        print("Error: SUPABASE_ACCESS_TOKEN not set")
        print("Get one from: https://supabase.com/dashboard/account/tokens")
        sys.exit(1)

    # Find migration file
    migration_path = Path(args.migration_file)
    if not migration_path.exists():
        alt_path = Path(__file__).parent.parent / 'migrations' / args.migration_file
        if alt_path.exists():
            migration_path = alt_path
        else:
            print(f"Error: Migration file not found: {args.migration_file}")
            sys.exit(1)

    # Read migration SQL
    with open(migration_path, 'r') as f:
        sql_content = f.read()

    print("=" * 70)
    print("TENANT DATABASE MIGRATION")
    print("=" * 70)
    print(f"File: {migration_path}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'EXECUTE'}")
    print()

    # Get all clients
    clients = get_all_clients()
    print(f"Found {len(clients)} clients\n")

    # Filter clients
    eligible_clients = []
    for client in clients:
        if not client.get('supabase_url'):
            print(f"  SKIP: {client['name']} - No Supabase URL")
            continue

        # Extract project ref from URL
        url_match = re.search(r'https://([^.]+)\.supabase\.co', client['supabase_url'])
        if not url_match:
            print(f"  SKIP: {client['name']} - Invalid Supabase URL")
            continue

        project_ref = url_match.group(1)

        if args.client and args.client.lower() not in client['name'].lower():
            continue

        eligible_clients.append({
            'name': client['name'],
            'project_ref': project_ref
        })

    print(f"\nEligible clients: {len(eligible_clients)}\n")

    if args.dry_run:
        print("DRY RUN - Would apply migration to:")
        for c in eligible_clients:
            print(f"  - {c['name']} ({c['project_ref']})")
        print("\nNo changes made (dry run)")
        return

    # Apply migration to each client
    print("Applying migration...")
    print("-" * 70)

    success_count = 0
    error_count = 0
    results = []

    for client in eligible_clients:
        print(f"  {client['name']}...", end=" ", flush=True)
        result = run_migration_for_tenant(
            client['project_ref'],
            client['name'],
            sql_content,
            access_token
        )
        results.append(result)

        if result['success']:
            print("OK")
            success_count += 1
        else:
            print(f"FAILED: {result['error'][:50]}...")
            error_count += 1

    print("-" * 70)
    print()
    print("=" * 70)
    print("MIGRATION SUMMARY")
    print("=" * 70)
    print(f"  Successful: {success_count}")
    print(f"  Failed: {error_count}")
    print()

    if error_count > 0:
        print("Failed clients:")
        for r in results:
            if not r['success']:
                print(f"  - {r['client']}: {r['error'][:100]}")


if __name__ == '__main__':
    main()

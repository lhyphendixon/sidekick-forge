#!/usr/bin/env python3
"""
Platform Database Migration Runner

A scalable migration system for Sidekick Forge that uses the Supabase
Management API to execute SQL migrations. This provides reliable connectivity
without requiring direct PostgreSQL access or IPv6 support.

Usage:
    python run_platform_migration.py <migration_file.sql>
    python run_platform_migration.py --list
    python run_platform_migration.py --dry-run <migration_file.sql>

Requirements:
    - SUPABASE_URL: Platform Supabase URL
    - SUPABASE_ACCESS_TOKEN: Management API token (from supabase.com/dashboard/account/tokens)
"""
import os
import sys
import re
import json
import argparse
import requests
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()


def get_project_ref():
    """Extract project reference from Supabase URL"""
    supabase_url = os.getenv('SUPABASE_URL')
    if not supabase_url:
        print("Error: SUPABASE_URL not set")
        sys.exit(1)

    url_match = re.search(r'https://([^.]+)\.supabase\.co', supabase_url)
    if not url_match:
        print(f"Error: Could not parse Supabase URL: {supabase_url}")
        sys.exit(1)

    return url_match.group(1)


def run_migration_via_api(sql_content, project_ref):
    """
    Execute SQL via Supabase Management API

    This is the preferred method as it works without direct PostgreSQL
    connectivity requirements (IPv6, VPN, etc.)
    """
    access_token = os.getenv('SUPABASE_ACCESS_TOKEN')

    if not access_token:
        print("Error: SUPABASE_ACCESS_TOKEN not set")
        print("Get one from: https://supabase.com/dashboard/account/tokens")
        return None

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
            return {"success": True, "data": response.json() if response.text else None}
        else:
            return {"success": False, "error": f"HTTP {response.status_code}: {response.text}"}

    except requests.exceptions.Timeout:
        return {"success": False, "error": "Request timed out after 5 minutes"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def parse_sql_statements(sql_content):
    """
    Parse SQL content into individual statements.
    Handles multi-line statements, function definitions with $$ delimiters, etc.
    """
    statements = []
    current_statement = []
    in_function = False

    for line in sql_content.split('\n'):
        stripped = line.strip()

        # Skip empty lines and comments at start of statement
        if not current_statement and (not stripped or stripped.startswith('--')):
            continue

        current_statement.append(line)

        # Track if we're inside a function definition (between $$ markers)
        if '$$' in line:
            in_function = not in_function

        # End of statement (semicolon not in function body)
        if stripped.endswith(';') and not in_function:
            statement = '\n'.join(current_statement).strip()
            if statement and not statement.startswith('--'):
                statements.append(statement)
            current_statement = []

    # Add any remaining statement
    if current_statement:
        statement = '\n'.join(current_statement).strip()
        if statement and not statement.startswith('--'):
            statements.append(statement)

    return statements


def get_statement_description(statement):
    """Get a short description of what a SQL statement does"""
    for line in statement.split('\n'):
        stripped = line.strip()
        if stripped and not stripped.startswith('--'):
            return stripped[:80] + ('...' if len(stripped) > 80 else '')
    return "Unknown statement"


def run_migration(migration_file, dry_run=False):
    """Run a migration file against the platform database"""
    migration_path = Path(migration_file)

    if not migration_path.exists():
        # Check in migrations directory
        alt_path = Path(__file__).parent.parent / 'migrations' / migration_file
        if alt_path.exists():
            migration_path = alt_path
        else:
            print(f"Error: Migration file not found: {migration_file}")
            sys.exit(1)

    project_ref = get_project_ref()

    print("=" * 70)
    print("PLATFORM DATABASE MIGRATION")
    print("=" * 70)
    print(f"Project: {project_ref}")
    print(f"File: {migration_path}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Mode: {'DRY RUN' if dry_run else 'EXECUTE'}")
    print()

    # Read migration SQL
    with open(migration_path, 'r') as f:
        sql_content = f.read()

    statements = parse_sql_statements(sql_content)
    print(f"Found {len(statements)} SQL statements\n")

    if dry_run:
        print("DRY RUN - Statements that would be executed:")
        print("-" * 70)
        for i, stmt in enumerate(statements, 1):
            desc = get_statement_description(stmt)
            print(f"[{i}] {desc}")
        print("-" * 70)
        print("\nNo changes made (dry run)")
        return True

    # Execute via Management API (full SQL at once for atomic execution)
    print("Executing migration via Supabase Management API...")
    print("-" * 70)

    result = run_migration_via_api(sql_content, project_ref)

    if result is None:
        print("Error: Could not connect to Supabase Management API")
        return False

    if result["success"]:
        print("Migration executed successfully!")
        if result.get("data"):
            # Show summary of results if available
            data = result["data"]
            if isinstance(data, list) and len(data) > 0:
                print(f"  Results: {len(data)} queries returned data")
        print("-" * 70)
        print()
        print("=" * 70)
        print("MIGRATION SUMMARY")
        print("=" * 70)
        print(f"  Status: SUCCESS")
        print(f"  Statements: {len(statements)}")
        print()
        print("Migration completed!")
        return True
    else:
        print(f"Error: {result['error']}")
        print("-" * 70)
        print()
        print("=" * 70)
        print("MIGRATION SUMMARY")
        print("=" * 70)
        print(f"  Status: FAILED")
        print()

        # If the migration failed, try to give helpful information
        error = result['error'].lower()
        if 'already exists' in error:
            print("Note: The migration may have partially succeeded.")
            print("      Some objects may already exist from a previous run.")
        elif 'duplicate' in error:
            print("Note: Migration contains duplicate definitions.")
            print("      Consider using IF NOT EXISTS or ON CONFLICT clauses.")

        return False


def list_migrations():
    """List available migration files"""
    migrations_dir = Path(__file__).parent.parent / 'migrations'

    print("=" * 70)
    print("AVAILABLE MIGRATIONS")
    print("=" * 70)
    print(f"Directory: {migrations_dir}\n")

    if not migrations_dir.exists():
        print("No migrations directory found.")
        return

    sql_files = sorted(migrations_dir.glob('*.sql'))

    if not sql_files:
        print("No .sql files found in migrations directory.")
        return

    for f in sql_files:
        size = f.stat().st_size
        modified = datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
        print(f"  {f.name:<45} {size:>8} bytes  {modified}")

    print(f"\nTotal: {len(sql_files)} migration(s)")


def main():
    parser = argparse.ArgumentParser(
        description='Run migrations against the Sidekick Forge platform database',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_platform_migration.py --list
  python run_platform_migration.py --dry-run add_ambient_abilities.sql
  python run_platform_migration.py add_ambient_abilities.sql

Environment Variables:
  SUPABASE_URL          Platform Supabase URL (required)
  SUPABASE_ACCESS_TOKEN Management API token (required)
                        Get from: https://supabase.com/dashboard/account/tokens
"""
    )
    parser.add_argument(
        'migration_file',
        nargs='?',
        help='SQL migration file to execute (can be filename only if in migrations/)'
    )
    parser.add_argument(
        '--list',
        action='store_true',
        help='List available migrations'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be executed without making changes'
    )

    args = parser.parse_args()

    if args.list:
        list_migrations()
        return

    if not args.migration_file:
        parser.print_help()
        print("\nError: Please specify a migration file or use --list")
        sys.exit(1)

    success = run_migration(args.migration_file, dry_run=args.dry_run)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()

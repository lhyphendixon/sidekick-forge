#!/usr/bin/env python3
"""
Apply Ambient Abilities schema to the platform database.

This migration adds:
- execution_phase column to tools table (active/ambient)
- trigger_config column to tools table (JSONB)
- ambient_ability_runs table for tracking background executions
- usersense_enabled column to clients table
- UserSense as a built-in ambient ability
- Helper RPC functions for queuing and processing runs
"""
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

# Read the migration SQL
MIGRATION_SQL_PATH = Path(__file__).parent.parent / 'migrations' / 'add_ambient_abilities.sql'


def read_migration_sql() -> str:
    """Read the migration SQL file"""
    with open(MIGRATION_SQL_PATH, 'r') as f:
        return f.read()


def apply_platform_migration():
    """Apply migration to the platform database"""
    supabase_url = os.getenv('SUPABASE_URL')
    supabase_service_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

    if not supabase_url or not supabase_service_key:
        print("❌ Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        sys.exit(1)

    print("=" * 70)
    print("AMBIENT ABILITIES SCHEMA MIGRATION")
    print("=" * 70)
    print(f"\nPlatform URL: {supabase_url}")
    print(f"Migration file: {MIGRATION_SQL_PATH}")
    print()

    # Read migration SQL
    migration_sql = read_migration_sql()

    # Split into individual statements for better error handling
    # Remove comments and split on semicolons outside of function bodies
    statements = []
    current_statement = []
    in_function = False

    for line in migration_sql.split('\n'):
        stripped = line.strip()

        # Skip empty lines and comments at the start
        if not stripped or stripped.startswith('--'):
            if current_statement:  # Keep comments within statements
                current_statement.append(line)
            continue

        current_statement.append(line)

        # Track if we're inside a function definition
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

    print(f"Found {len(statements)} SQL statements to execute\n")

    # Connect to platform database
    try:
        platform_sb = create_client(supabase_url, supabase_service_key)
        print("✅ Connected to platform database\n")
    except Exception as e:
        print(f"❌ Failed to connect: {e}")
        sys.exit(1)

    # Execute each statement
    success_count = 0
    skip_count = 0
    error_count = 0

    for i, statement in enumerate(statements, 1):
        # Get first non-comment line for description
        first_line = ""
        for line in statement.split('\n'):
            stripped = line.strip()
            if stripped and not stripped.startswith('--'):
                first_line = stripped[:60]
                break

        print(f"[{i}/{len(statements)}] {first_line}...")

        try:
            # Use the REST API to execute SQL via postgrest-py
            # We need to use rpc for executing raw SQL
            result = platform_sb.postgrest.rpc('exec_sql', {'sql_query': statement}).execute()
            print(f"    ✅ Success")
            success_count += 1
        except Exception as e:
            error_str = str(e)

            # Check for common "already exists" errors which are fine
            if 'already exists' in error_str.lower() or 'duplicate' in error_str.lower():
                print(f"    ⏭️  Skipped (already exists)")
                skip_count += 1
            elif 'does not exist' in error_str.lower() and 'DROP' in statement.upper():
                print(f"    ⏭️  Skipped (nothing to drop)")
                skip_count += 1
            else:
                print(f"    ❌ Error: {error_str[:100]}")
                error_count += 1

    print()
    print("=" * 70)
    print("MIGRATION SUMMARY")
    print("=" * 70)
    print(f"✅ Successful: {success_count}")
    print(f"⏭️  Skipped: {skip_count}")
    print(f"❌ Errors: {error_count}")

    if error_count > 0:
        print("\n⚠️  Some statements failed. You may need to run the migration manually.")
        print(f"   SQL file: {MIGRATION_SQL_PATH}")


def apply_via_psql():
    """Apply migration using psql directly (more reliable for complex SQL)"""
    import subprocess
    import re

    supabase_url = os.getenv('SUPABASE_URL')
    supabase_service_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

    if not supabase_url or not supabase_service_key:
        print("❌ Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        sys.exit(1)

    print("=" * 70)
    print("AMBIENT ABILITIES SCHEMA MIGRATION (via psql)")
    print("=" * 70)
    print(f"\nPlatform URL: {supabase_url}")
    print(f"Migration file: {MIGRATION_SQL_PATH}")
    print()

    # Extract connection details from Supabase URL
    url_match = re.search(r'https://([^.]+)\.supabase\.co', supabase_url)
    if not url_match:
        print("❌ Could not parse Supabase URL")
        sys.exit(1)

    project_ref = url_match.group(1)
    db_host = f"db.{project_ref}.supabase.co"

    print(f"Database host: {db_host}")
    print()

    # Set up environment
    env = os.environ.copy()
    env['PGPASSWORD'] = supabase_service_key

    # Run psql command
    cmd = [
        'psql',
        '-h', db_host,
        '-U', 'postgres',
        '-d', 'postgres',
        '-f', str(MIGRATION_SQL_PATH),
        '-v', 'ON_ERROR_STOP=0'  # Continue on errors (for idempotent operations)
    ]

    print("Running migration...")
    print("-" * 70)

    try:
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=False,  # Show output in real-time
            timeout=300  # 5 minute timeout
        )

        print("-" * 70)

        if result.returncode == 0:
            print("\n✅ Migration completed successfully!")
        else:
            print(f"\n⚠️  Migration completed with return code: {result.returncode}")
            print("   Some statements may have failed (check output above)")

    except subprocess.TimeoutExpired:
        print("\n❌ Migration timed out after 5 minutes")
        sys.exit(1)
    except FileNotFoundError:
        print("\n❌ psql command not found. Install PostgreSQL client or use Supabase SQL Editor.")
        print(f"\n   To run manually, execute the SQL in: {MIGRATION_SQL_PATH}")
        sys.exit(1)


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description='Apply Ambient Abilities schema migration')
    parser.add_argument('--method', choices=['api', 'psql'], default='psql',
                       help='Method to use for migration (default: psql)')
    parser.add_argument('--dry-run', action='store_true',
                       help='Print the SQL without executing')

    args = parser.parse_args()

    if args.dry_run:
        print("=" * 70)
        print("AMBIENT ABILITIES MIGRATION SQL (dry run)")
        print("=" * 70)
        print()
        print(read_migration_sql())
        return

    if args.method == 'psql':
        apply_via_psql()
    else:
        apply_platform_migration()


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
Migrate all client databases to use embeddings_vec for vector similarity search
"""
import os
import sys
from dotenv import load_dotenv
from supabase import create_client
from pathlib import Path

load_dotenv()

# Read the migration SQL
migration_sql_path = Path(__file__).parent / 'universal_embeddings_migration.sql'
with open(migration_sql_path, 'r') as f:
    MIGRATION_SQL = f.read()

def migrate_client(client_name, client_url, client_key):
    """Run migration on a single client database"""
    print(f"\n{'='*70}")
    print(f"Migrating client: {client_name}")
    print(f"{'='*70}")

    try:
        client_sb = create_client(client_url, client_key)

        # Execute the migration
        result = client_sb.rpc('exec_sql', {'sql': MIGRATION_SQL}).execute()

        print(f"✅ Migration completed for {client_name}")
        return True

    except Exception as e:
        error_msg = str(e)

        # Check if it's because exec_sql doesn't exist (expected)
        if 'exec_sql' in error_msg or 'function' in error_msg.lower():
            print(f"⚠️  Client {client_name} doesn't have exec_sql RPC function")
            print(f"   Will need to run migration via direct SQL connection")
            return False
        else:
            print(f"❌ Error migrating {client_name}: {e}")
            return False

def main():
    """Main migration script"""
    print("=" * 70)
    print("UNIVERSAL EMBEDDINGS MIGRATION FOR ALL CLIENTS")
    print("=" * 70)
    print("\nThis script will migrate all client databases to use embeddings_vec")
    print("for vector similarity search instead of JSON embeddings.\n")

    # Get all clients
    platform_sb = create_client(
        os.getenv('SUPABASE_URL'),
        os.getenv('SUPABASE_SERVICE_ROLE_KEY')
    )

    clients_result = platform_sb.table('clients').select(
        'id,name,supabase_url,supabase_service_role_key'
    ).execute()

    clients = clients_result.data
    print(f"Found {len(clients)} clients to check\n")

    migrated = []
    skipped = []
    errors = []

    for client in clients:
        if not client.get('supabase_service_role_key'):
            print(f"⏭️  Skipping {client['name']} - no Supabase credentials")
            skipped.append(client['name'])
            continue

        try:
            client_sb = create_client(
                client['supabase_url'],
                client['supabase_service_role_key']
            )

            # Check if they have document_chunks
            total = client_sb.table('document_chunks').select('id', count='exact').execute()

            if total.count == 0:
                print(f"⏭️  Skipping {client['name']} - no document chunks")
                skipped.append(client['name'])
                continue

            print(f"\n📊 {client['name']}: {total.count:,} chunks")

            # Check migration status
            with_old = client_sb.table('document_chunks').select('id', count='exact').not_.is_('embeddings', 'null').execute()

            # Check if embeddings_vec column exists
            sample = client_sb.table('document_chunks').select('*').limit(1).execute()
            has_vec_column = 'embeddings_vec' in sample.data[0] if sample.data else False

            if has_vec_column:
                with_vec = client_sb.table('document_chunks').select('id', count='exact').not_.is_('embeddings_vec', 'null').execute()
                needs_migration = with_old.count - with_vec.count

                print(f"   - embeddings (JSON): {with_old.count:,}")
                print(f"   - embeddings_vec: {with_vec.count:,}")
                print(f"   - needs migration: {needs_migration:,}")

                if needs_migration == 0:
                    print(f"   ✅ Already migrated!")
                    migrated.append(client['name'])
                    continue
            else:
                print(f"   ⚠️  Missing embeddings_vec column")

            # Run migration via SQL file directly using psql
            print(f"   🔄 Running migration...")

            # Extract connection details from Supabase URL
            # Format: https://xxx.supabase.co -> host=db.xxx.supabase.co
            import re
            import subprocess

            url_match = re.search(r'https://([^.]+)\.supabase\.co', client['supabase_url'])
            if url_match:
                project_ref = url_match.group(1)
                db_host = f"db.{project_ref}.supabase.co"
                db_password = client['supabase_service_role_key']

                # Run psql command
                env = os.environ.copy()
                env['PGPASSWORD'] = db_password

                cmd = [
                    'psql',
                    '-h', db_host,
                    '-U', 'postgres',
                    '-d', 'postgres',
                    '-f', str(migration_sql_path),
                    '-v', 'ON_ERROR_STOP=1'
                ]

                result = subprocess.run(
                    cmd,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=300  # 5 minute timeout
                )

                if result.returncode == 0:
                    print(f"   ✅ Migration successful!")
                    migrated.append(client['name'])
                else:
                    print(f"   ❌ Migration failed:")
                    print(result.stderr)
                    errors.append(client['name'])
            else:
                print(f"   ❌ Could not parse Supabase URL")
                errors.append(client['name'])

        except Exception as e:
            print(f"   ❌ Error: {e}")
            errors.append(client['name'])

    # Summary
    print(f"\n{'='*70}")
    print("MIGRATION SUMMARY")
    print(f"{'='*70}")
    print(f"✅ Migrated: {len(migrated)} clients")
    if migrated:
        for name in migrated:
            print(f"   - {name}")

    print(f"\n⏭️  Skipped: {len(skipped)} clients")
    if skipped:
        for name in skipped:
            print(f"   - {name}")

    print(f"\n❌ Errors: {len(errors)} clients")
    if errors:
        for name in errors:
            print(f"   - {name}")

    print(f"\n{'='*70}")

if __name__ == '__main__':
    main()

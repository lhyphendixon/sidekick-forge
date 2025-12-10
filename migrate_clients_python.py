#!/usr/bin/env python3
"""
Migrate all client databases to use embeddings_vec for vector similarity search
Uses Python/Supabase instead of psql
"""
import os
import sys
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

def migrate_client_db(client_name, client_url, client_key):
    """Run migration on a single client database using Python"""
    print(f"\n{'='*70}")
    print(f"Migrating client: {client_name}")
    print(f"{'='*70}")

    try:
        from postgrest.exceptions import APIError
        client_sb = create_client(client_url, client_key)

        # Step 1: Add embeddings_vec column if not exists
        print("Step 1: Ensuring embeddings_vec column exists...")
        try:
            # Try to query the column to check if it exists
            test = client_sb.table('document_chunks').select('embeddings_vec').limit(1).execute()
            print("   ✓ embeddings_vec column already exists")
        except Exception as e:
            if 'column' in str(e).lower() and 'does not exist' in str(e).lower():
                print("   ⚠️  Column doesn't exist - needs manual ALTER TABLE")
                print("   Please run: ALTER TABLE document_chunks ADD COLUMN embeddings_vec vector(1024);")
                return False

        # Step 2: Count chunks needing migration
        print("\nStep 2: Counting chunks to migrate...")

        # Get count first
        count_result = client_sb.table('document_chunks').select('id', count='exact').is_('embeddings_vec', 'null').not_.is_('embeddings', 'null').execute()
        total = count_result.count

        print(f"   Found {total:,} chunks to migrate")

        if total == 0:
            print("   ✅ No chunks need migration - all done!")
            return True

        # Step 3: Migrate in batches using pagination
        print("\nStep 3: Migrating embeddings...")
        import json
        converted = 0
        errors = 0
        page_size = 100  # Fetch 100 at a time
        offset = 0

        while offset < total:
            # Fetch a page of chunks
            chunks_result = client_sb.table('document_chunks').select('id,embeddings').is_('embeddings_vec', 'null').not_.is_('embeddings', 'null').range(offset, offset + page_size - 1).execute()

            chunks_batch = chunks_result.data
            if not chunks_batch:
                break

            for chunk in chunks_batch:
                try:
                    chunk_id = chunk['id']
                    raw_embeddings = chunk['embeddings']

                    if not raw_embeddings:
                        continue

                    # Parse embeddings - handle both JSON string and array formats
                    if isinstance(raw_embeddings, str):
                        embeddings_array = json.loads(raw_embeddings)
                    elif isinstance(raw_embeddings, list):
                        embeddings_array = raw_embeddings
                    else:
                        print(f"   ⚠️  Chunk {chunk_id} has unknown embedding type: {type(raw_embeddings)}")
                        errors += 1
                        continue

                    # Validate dimension
                    if len(embeddings_array) != 1024:
                        print(f"   ⚠️  Chunk {chunk_id} has invalid dimension: {len(embeddings_array)}")
                        errors += 1
                        continue

                    # Update to embeddings_vec
                    client_sb.table('document_chunks').update({
                        'embeddings_vec': embeddings_array
                    }).eq('id', chunk_id).execute()

                    converted += 1

                    if converted % 100 == 0:
                        print(f"   Progress: {converted}/{total} ({100*converted/total:.1f}%)")

                except Exception as e:
                    print(f"   ❌ Error converting chunk {chunk_id}: {e}")
                    errors += 1

            offset += page_size

        print(f"\n   ✅ Converted {converted:,} chunks")
        if errors > 0:
            print(f"   ⚠️  {errors:,} errors")

        # Step 4: Create index if needed
        print("\nStep 4: Creating vector index...")
        print("   ⚠️  Index creation requires SQL access - skipping for now")
        print("   Please run manually:")
        print("   CREATE INDEX IF NOT EXISTS document_chunks_embeddings_vec_idx")
        print("   ON document_chunks USING ivfflat (embeddings_vec vector_cosine_ops)")
        print("   WITH (lists = 100);")

        return True

    except Exception as e:
        print(f"   ❌ Error migrating {client_name}: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Main migration script"""
    print("=" * 70)
    print("EMBEDDINGS MIGRATION FOR ALL CLIENTS (Python method)")
    print("=" * 70)

    # Get all clients
    platform_sb = create_client(
        os.getenv('SUPABASE_URL'),
        os.getenv('SUPABASE_SERVICE_ROLE_KEY')
    )

    clients_result = platform_sb.table('clients').select(
        'id,name,supabase_url,supabase_service_role_key'
    ).execute()

    clients = clients_result.data
    print(f"\nFound {len(clients)} clients to check\n")

    # Filter to clients that need migration
    clients_to_migrate = []

    for client in clients:
        if not client.get('supabase_service_role_key'):
            continue

        try:
            client_sb = create_client(
                client['supabase_url'],
                client['supabase_service_role_key']
            )

            # Check if they have document_chunks
            total = client_sb.table('document_chunks').select('id', count='exact').execute()

            if total.count == 0:
                continue

            # Check migration status
            with_old = client_sb.table('document_chunks').select('id', count='exact').not_.is_('embeddings', 'null').execute()
            with_vec = client_sb.table('document_chunks').select('id', count='exact').not_.is_('embeddings_vec', 'null').execute()
            needs_migration = with_old.count - with_vec.count

            if needs_migration > 0:
                clients_to_migrate.append({
                    'name': client['name'],
                    'url': client['supabase_url'],
                    'key': client['supabase_service_role_key'],
                    'total': total.count,
                    'needs_migration': needs_migration
                })
                print(f"📊 {client['name']}: {needs_migration:,}/{total.count:,} chunks need migration")
            else:
                print(f"✅ {client['name']}: Already migrated")

        except Exception as e:
            print(f"⚠️  {client['name']}: Error checking - {e}")

    if not clients_to_migrate:
        print("\n✅ All clients are already migrated!")
        return

    print(f"\n\n{'='*70}")
    print(f"Will migrate {len(clients_to_migrate)} clients")
    print(f"{'='*70}")

    migrated = []
    errors = []

    for client in clients_to_migrate:
        success = migrate_client_db(client['name'], client['url'], client['key'])
        if success:
            migrated.append(client['name'])
        else:
            errors.append(client['name'])

    # Summary
    print(f"\n{'='*70}")
    print("MIGRATION SUMMARY")
    print(f"{'='*70}")
    print(f"✅ Successfully migrated: {len(migrated)} clients")
    for name in migrated:
        print(f"   - {name}")

    if errors:
        print(f"\n❌ Errors: {len(errors)} clients")
        for name in errors:
            print(f"   - {name}")

    print(f"\n{'='*70}")

if __name__ == '__main__':
    main()

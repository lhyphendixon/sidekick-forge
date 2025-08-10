#!/usr/bin/env python3
import os
import sys
from supabase import create_client


def main() -> int:
    supabase_url = os.environ.get('SUPABASE_URL')
    supabase_key = os.environ.get('SUPABASE_SERVICE_ROLE_KEY')
    if not supabase_url or not supabase_key:
        print('Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY')
        return 1

    client = create_client(supabase_url, supabase_key)

    # Attempt to add slug column using SQL function if available
    try:
        # If a helper RPC is not available, this will likely fail; we'll continue
        client.rpc('exec', {'sql': 'ALTER TABLE public.clients ADD COLUMN IF NOT EXISTS slug text;'}).execute()
        print('Ensured clients.slug column exists via RPC exec')
    except Exception as e:
        print('RPC exec not available or failed:', e)
        print('Proceeding without ensuring column via RPC (column may already exist).')

    # Backfill Autonomite row slug
    autonomite_id = '11389177-e4d8-49a9-9a00-f77bb4de6592'
    try:
        res = client.table('clients').select('id, slug').eq('id', autonomite_id).execute()
        if res.data:
            row = res.data[0]
            if not row.get('slug'):
                upd = client.table('clients').update({'slug': 'autonomite'}).eq('id', autonomite_id).execute()
                print('Backfilled slug for Autonomite:', bool(upd.data))
            else:
                print('Autonomite slug already set:', row.get('slug'))
        else:
            print('Autonomite client not found; skipping backfill')
    except Exception as e:
        print('Failed to backfill slug:', e)
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())



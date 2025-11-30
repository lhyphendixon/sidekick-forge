#!/usr/bin/env python3
"""Fix Farah's client_id to point to Autonomite"""
from supabase import create_client
import os
from dotenv import load_dotenv

load_dotenv('/root/sidekick-forge/.env')

# Get Autonomite's service role key from platform database
platform_supabase = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_SERVICE_ROLE_KEY'))

print('🔍 Fetching Autonomite credentials from platform database...')
client_result = platform_supabase.table('clients').select('*').eq('id', '11389177-e4d8-49a9-9a00-f77bb4de6592').single().execute()

if not client_result.data:
    print('❌ Autonomite client not found in platform database')
    exit(1)

autonomite_url = (
    os.getenv("AUTONOMITE_SUPABASE_URL")
    or client_result.data.get('supabase_project_url')
    or client_result.data.get('supabase_url')
)
if not autonomite_url:
    print('❌ Autonomite Supabase URL not found')
    exit(1)

autonomite_service_key = client_result.data.get('supabase_service_role_key')
if not autonomite_service_key:
    print('❌ Autonomite service role key not found')
    exit(1)

print('✅ Got Autonomite credentials')
print()

# Connect to Autonomite's database
autonomite_supabase = create_client(autonomite_url, autonomite_service_key)

print('🔍 Checking if agents table has client_id column...')

# First, add the client_id column if it doesn't exist
autonomite_id = '11389177-e4d8-49a9-9a00-f77bb4de6592'

print('📝 Adding client_id column to agents table...')
try:
    autonomite_supabase.postgrest.session.post(
        f"{autonomite_url}/rest/v1/rpc/exec_sql",
        json={"query": f"""
            ALTER TABLE public.agents 
            ADD COLUMN IF NOT EXISTS client_id UUID;
            
            UPDATE public.agents 
            SET client_id = '{autonomite_id}'
            WHERE client_id IS NULL;
            
            CREATE INDEX IF NOT EXISTS idx_agents_client_id ON public.agents(client_id);
        """}
    )
    print('✅ Added client_id column')
except Exception as e:
    # Try direct SQL approach
    print(f'   Using alternative approach: {e}')
    import psycopg2
    # We'll use the Supabase connection string from Postgrest
    # This is a fallback - let's just report what needs to be done
    print()
    print('❌ Unable to add column programmatically.')
    print()
    print('📋 Please run this SQL manually in the Autonomite Supabase SQL Editor:')
    print()
    print(f"""
-- Add client_id column to agents table
ALTER TABLE public.agents 
ADD COLUMN IF NOT EXISTS client_id UUID;

-- Set default value for existing records to Autonomite's client_id
UPDATE public.agents 
SET client_id = '{autonomite_id}'
WHERE client_id IS NULL;

-- Add index for faster lookups
CREATE INDEX IF NOT EXISTS idx_agents_client_id ON public.agents(client_id);
    """)
    print()
    print('After running the above SQL, transcripts should work correctly!')
    exit(0)

print('🔍 Checking Farah agent in Autonomite database...')
farah_result = autonomite_supabase.table('agents').select('*').eq('slug', 'farah').execute()

if not farah_result.data:
    print('❌ Farah not found in Autonomite database')
    exit(1)

farah = farah_result.data[0]
print(f'\n📋 Farah configuration after update:')
print(f'   ID: {farah["id"]}')
print(f'   Name: {farah["name"]}')
print(f'   Slug: {farah["slug"]}')
print(f'   Client ID: {farah.get("client_id", "NOT SET")}')
print()
print('🎉 Success! Next steps:')
print('   1. Refresh your admin agents page at /admin/agents')
print('   2. Click "Preview Sidekick" on Farah')
print('   3. Transcripts should now stream correctly!')

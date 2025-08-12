#!/usr/bin/env python3
"""
Script to add cerebras_api_key column to the clients table.

Since we can't directly execute ALTER TABLE through the Supabase API,
this script will:
1. Show you the SQL to run in the Supabase dashboard
2. Test if the column exists
3. Migrate any data from the workaround location
"""

import os
import sys
from supabase import create_client

# Platform Supabase credentials
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://eukudpgfpihxsypulopm.supabase.co")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImV1a3VkcGdmcGloeHN5cHVsb3BtIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1MzUxMjkyMiwiZXhwIjoyMDY5MDg4OTIyfQ.wOSF5bSdd763_PVyCmSEBGjtbhP67WMfms1aGydO_44")

def main():
    print("=" * 60)
    print("CEREBRAS API KEY COLUMN SETUP")
    print("=" * 60)
    
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    
    # Check if column exists
    print("\n1. Checking if cerebras_api_key column exists...")
    try:
        result = supabase.table('clients').select('id, name, cerebras_api_key').limit(1).execute()
        print("✅ Column already exists!")
        return
    except Exception as e:
        if '42703' in str(e) or 'PGRST204' in str(e):
            print("❌ Column does not exist")
        else:
            print(f"Error checking column: {e}")
    
    # Provide SQL to create column
    print("\n2. To add the column, please:")
    print("   a) Go to your Supabase dashboard:")
    print(f"      https://supabase.com/dashboard/project/eukudpgfpihxsypulopm/sql/new")
    print("   b) Run this SQL command:\n")
    
    sql_command = """-- Add cerebras_api_key column to clients table
ALTER TABLE clients 
ADD COLUMN cerebras_api_key TEXT;

-- Optional: Migrate any keys from additional_settings (if using workaround)
UPDATE clients 
SET cerebras_api_key = additional_settings->>'cerebras_api_key'
WHERE additional_settings ? 'cerebras_api_key' 
  AND cerebras_api_key IS NULL;

-- Optional: Clean up additional_settings after migration
UPDATE clients 
SET additional_settings = additional_settings - 'cerebras_api_key'
WHERE additional_settings ? 'cerebras_api_key';"""
    
    print(sql_command)
    
    print("\n3. After running the SQL, restart the FastAPI service:")
    print("   docker-compose restart fastapi")
    
    # Check for any data in additional_settings that needs migration
    print("\n4. Checking for data to migrate...")
    try:
        clients = supabase.table('clients').select('id, name, additional_settings').execute()
        clients_with_cerebras = []
        
        for client in clients.data:
            if client.get('additional_settings', {}).get('cerebras_api_key'):
                clients_with_cerebras.append(client['name'])
        
        if clients_with_cerebras:
            print(f"   Found cerebras_api_key in additional_settings for: {', '.join(clients_with_cerebras)}")
            print("   The SQL above will migrate these automatically.")
        else:
            print("   No data to migrate.")
    except Exception as e:
        print(f"   Could not check for migration data: {e}")

if __name__ == "__main__":
    main()
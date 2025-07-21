#!/usr/bin/env python3
"""
Check the current wordpress_sites table schema
"""
import os
from supabase import create_client

def check_table_schema():
    """Check what columns exist in the wordpress_sites table"""
    # Get Supabase credentials
    supabase_url = os.getenv("SUPABASE_URL", "https://yuowazxcxwhczywurmmw.supabase.co")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY")
    
    print(f"Connecting to Supabase: {supabase_url}")
    
    # Create Supabase client
    supabase = create_client(supabase_url, supabase_key)
    
    try:
        # Try to select all columns to see what exists
        result = supabase.table('wordpress_sites').select('*').limit(0).execute()
        print("✅ Successfully connected to wordpress_sites table")
        
        # Try to insert a test record to see which columns are expected
        test_data = {
            "id": "test-id",
            "domain": "test.com",
            "site_name": "Test Site",
            "client_id": "test-client",
            "api_key": "test-key", 
            "api_secret": "test-secret"
        }
        
        print("Testing basic insert...")
        try:
            result = supabase.table('wordpress_sites').insert(test_data).execute()
            print("✅ Basic insert worked")
            
            # Clean up
            supabase.table('wordpress_sites').delete().eq('id', 'test-id').execute()
            
        except Exception as e:
            print(f"❌ Basic insert failed: {e}")
            
        # Try with admin_email
        test_data_with_email = {
            **test_data,
            "admin_email": "test@test.com"
        }
        
        print("Testing insert with admin_email...")
        try:
            result = supabase.table('wordpress_sites').insert(test_data_with_email).execute()
            print("✅ Insert with admin_email worked")
            
            # Clean up
            supabase.table('wordpress_sites').delete().eq('id', 'test-id').execute()
            
        except Exception as e:
            print(f"❌ Insert with admin_email failed: {e}")
            print("Need to add admin_email column to the table")
            
    except Exception as e:
        print(f"❌ Failed to access wordpress_sites table: {e}")

if __name__ == "__main__":
    check_table_schema()
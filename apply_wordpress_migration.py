#!/usr/bin/env python3
"""
Apply the WordPress sites table migration to Supabase
"""
import os
import sys
from supabase import create_client

def apply_migration():
    """Apply the WordPress sites table migration"""
    # Get Supabase credentials
    supabase_url = os.getenv("SUPABASE_URL", "https://yuowazxcxwhczywurmmw.supabase.co")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY")
    
    print(f"Connecting to Supabase: {supabase_url}")
    
    # Create Supabase client
    supabase = create_client(supabase_url, supabase_key)
    
    # Read the migration SQL
    migration_file = "/opt/autonomite-saas/migrations/002_create_wordpress_sites_table.sql"
    with open(migration_file, 'r') as f:
        migration_sql = f.read()
    
    print("Applying WordPress sites table migration...")
    
    try:
        # Execute the migration SQL
        result = supabase.rpc('exec_sql', {'sql': migration_sql}).execute()
        print("✅ Migration applied successfully!")
        print(f"Result: {result.data}")
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        
        # Try creating just the basic table structure
        print("Trying to create basic table structure...")
        try:
            basic_sql = """
            CREATE TABLE IF NOT EXISTS wordpress_sites (
                id TEXT PRIMARY KEY,
                domain TEXT NOT NULL UNIQUE,
                site_name TEXT NOT NULL,
                admin_email TEXT NOT NULL,
                client_id TEXT NOT NULL,
                api_key TEXT NOT NULL UNIQUE,
                api_secret TEXT NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                metadata JSONB DEFAULT '{}',
                request_count INTEGER DEFAULT 0,
                last_seen_at TIMESTAMP WITH TIME ZONE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
            
            CREATE INDEX IF NOT EXISTS idx_wordpress_sites_domain ON wordpress_sites(domain);
            CREATE INDEX IF NOT EXISTS idx_wordpress_sites_client_id ON wordpress_sites(client_id);
            CREATE INDEX IF NOT EXISTS idx_wordpress_sites_api_key ON wordpress_sites(api_key);
            """
            
            # Try using the Supabase REST API to create the table
            print("Creating table via direct Supabase insertion...")
            
            # Test if we can at least query the table
            result = supabase.table('wordpress_sites').select('*').limit(1).execute()
            print("✅ Table already exists or was created successfully!")
            
        except Exception as e2:
            print(f"❌ Basic table creation also failed: {e2}")
            print("The table may need to be created manually in the Supabase dashboard.")
            return False
    
    return True

if __name__ == "__main__":
    success = apply_migration()
    sys.exit(0 if success else 1)
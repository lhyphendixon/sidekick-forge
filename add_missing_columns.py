#!/usr/bin/env python3
"""
Add missing columns to wordpress_sites table
"""
import os
from supabase import create_client

def add_missing_columns():
    """Add missing columns to the wordpress_sites table"""
    # Get Supabase credentials
    supabase_url = os.getenv("SUPABASE_URL", "https://yuowazxcxwhczywurmmw.supabase.co")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY")
    
    print(f"Connecting to Supabase: {supabase_url}")
    
    # Create Supabase client  
    supabase = create_client(supabase_url, supabase_key)
    
    # The SQL commands to add missing columns
    alter_commands = [
        "ALTER TABLE wordpress_sites ADD COLUMN IF NOT EXISTS admin_email TEXT;",
        "ALTER TABLE wordpress_sites ADD COLUMN IF NOT EXISTS api_secret TEXT;",
        "ALTER TABLE wordpress_sites ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;",
        "ALTER TABLE wordpress_sites ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}';",
        "ALTER TABLE wordpress_sites ADD COLUMN IF NOT EXISTS request_count INTEGER DEFAULT 0;",
        "ALTER TABLE wordpress_sites ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP WITH TIME ZONE;",
        "ALTER TABLE wordpress_sites ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW();",
        "ALTER TABLE wordpress_sites ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW();"
    ]
    
    print("Adding missing columns to wordpress_sites table...")
    
    for command in alter_commands:
        try:
            print(f"Executing: {command}")
            # Since Supabase doesn't have a direct SQL execution endpoint for service role,
            # we'll need to be creative. Let's try using the rpc function or manual execution
            print("Note: This needs to be executed manually in Supabase SQL editor:")
            print(command)
            print()
            
        except Exception as e:
            print(f"❌ Failed to execute {command}: {e}")
    
    print("\n" + "="*50)
    print("MANUAL STEPS NEEDED:")
    print("="*50)
    print("1. Go to your Supabase dashboard")
    print("2. Navigate to SQL Editor") 
    print("3. Execute these commands one by one:")
    print()
    
    for command in alter_commands:
        print(command)
    
    print("\n4. Alternatively, execute this all at once:")
    print()
    all_commands = "\n".join(alter_commands)
    print(all_commands)

if __name__ == "__main__":
    add_missing_columns()
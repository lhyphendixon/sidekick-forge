#!/usr/bin/env python3
"""
Discover the actual schema of wordpress_sites table
"""
import os
from supabase import create_client
import json

def discover_schema():
    """Discover what columns actually exist in wordpress_sites table"""
    # Get Supabase credentials
    supabase_url = os.getenv("SUPABASE_URL", "https://yuowazxcxwhczywurmmw.supabase.co")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY")
    
    supabase = create_client(supabase_url, supabase_key)
    
    # List all tables to see what exists
    print("Checking available tables...")
    try:
        # Try to get table info from information_schema
        tables_query = """
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public' 
        AND table_name LIKE '%wordpress%'
        """
        
        # Since we can't run raw SQL easily, let's try different approaches
        print("Attempting to discover wordpress_sites table structure...")
        
        # Try inserting with different column combinations
        test_cases = [
            # Case 1: Just id
            {"id": "test1"},
            
            # Case 2: Basic fields
            {"id": "test2", "name": "test"},
            
            # Case 3: Common fields
            {"id": "test3", "url": "test.com", "name": "test"},
            
            # Case 4: WordPress typical fields
            {"id": "test4", "site_url": "test.com", "site_name": "test"},
        ]
        
        for i, test_data in enumerate(test_cases):
            print(f"\nTest case {i+1}: {test_data}")
            try:
                result = supabase.table('wordpress_sites').insert(test_data).execute()
                print(f"✅ SUCCESS: These columns work: {list(test_data.keys())}")
                
                # Clean up
                supabase.table('wordpress_sites').delete().eq('id', test_data['id']).execute()
                break
                
            except Exception as e:
                error_msg = str(e)
                if "Could not find the" in error_msg and "column" in error_msg:
                    # Extract the missing column name
                    import re
                    match = re.search(r"Could not find the '(.+?)' column", error_msg)
                    if match:
                        missing_col = match.group(1)
                        print(f"❌ Missing column: {missing_col}")
                    else:
                        print(f"❌ Error: {error_msg}")
                else:
                    print(f"❌ Other error: {error_msg}")
        
        # Try to select from the table to see if it has any data
        print("\nTrying to select existing data...")
        try:
            result = supabase.table('wordpress_sites').select('*').limit(5).execute()
            if result.data:
                print("✅ Found existing data:")
                for row in result.data:
                    print(f"Row: {row}")
                    print(f"Columns in this row: {list(row.keys())}")
                    break
            else:
                print("Table exists but is empty")
                
        except Exception as e:
            print(f"❌ Failed to select data: {e}")
            
    except Exception as e:
        print(f"❌ Failed to discover schema: {e}")

if __name__ == "__main__":
    discover_schema()
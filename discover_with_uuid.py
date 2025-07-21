#!/usr/bin/env python3
"""
Discover table schema using proper UUIDs
"""
import os
import uuid
from supabase import create_client

def discover_with_uuid():
    """Test with proper UUID format"""
    supabase_url = os.getenv("SUPABASE_URL", "https://yuowazxcxwhczywurmmw.supabase.co")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY")
    
    supabase = create_client(supabase_url, supabase_key)
    
    # Generate proper UUID
    test_id = str(uuid.uuid4())
    
    # Try progressive column additions
    test_columns = [
        "id",
        "client_id", 
        "domain",
        "site_name",
        "admin_email",
        "api_key",
        "api_secret",
        "is_active",
        "metadata",
        "request_count",
        "last_seen_at",
        "created_at",
        "updated_at"
    ]
    
    working_columns = ["id"]  # We know id works (as UUID)
    
    test_data = {"id": test_id}
    
    print(f"Starting with UUID: {test_id}")
    
    for col in test_columns[1:]:  # Skip 'id' since we know it works
        test_data_with_col = test_data.copy()
        
        # Add appropriate test value for each column type
        if col in ["client_id", "domain", "site_name", "admin_email", "api_key", "api_secret"]:
            test_data_with_col[col] = f"test_{col}"
        elif col == "is_active":
            test_data_with_col[col] = True
        elif col == "metadata":
            test_data_with_col[col] = {}
        elif col == "request_count":
            test_data_with_col[col] = 0
        elif col in ["last_seen_at", "created_at", "updated_at"]:
            test_data_with_col[col] = "2025-07-15T01:00:00Z"
        else:
            test_data_with_col[col] = f"test_{col}"
        
        print(f"\nTesting with column: {col}")
        print(f"Data: {test_data_with_col}")
        
        try:
            result = supabase.table('wordpress_sites').insert(test_data_with_col).execute()
            print(f"✅ SUCCESS: Column '{col}' exists")
            working_columns.append(col)
            test_data = test_data_with_col  # Use this as base for next test
            
        except Exception as e:
            error_msg = str(e)
            if "Could not find the" in error_msg and "column" in error_msg:
                print(f"❌ Column '{col}' does NOT exist")
            else:
                print(f"❌ Error with '{col}': {error_msg}")
                # If it's not a missing column error, the column might exist but have wrong data type
                working_columns.append(f"{col} (exists but wrong type)")
    
    print(f"\n{'='*50}")
    print("SUMMARY:")
    print(f"{'='*50}")
    print(f"Working columns: {working_columns}")
    
    # Clean up the test record if it was created
    try:
        supabase.table('wordpress_sites').delete().eq('id', test_id).execute()
        print(f"✅ Cleaned up test record")
    except:
        pass

if __name__ == "__main__":
    discover_with_uuid()
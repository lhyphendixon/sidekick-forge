#!/usr/bin/env python3
"""
Simple test to see what works with current table
"""
import os
from supabase import create_client

def simple_test():
    """Test with minimal data that should work"""
    # Get Supabase credentials
    supabase_url = os.getenv("SUPABASE_URL", "https://yuowazxcxwhczywurmmw.supabase.co")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY")
    
    supabase = create_client(supabase_url, supabase_key)
    
    # Try with just the basic columns that we know should exist
    minimal_data = {
        "id": "test-minimal",
        "domain": "test-minimal.com", 
        "site_name": "Test Minimal",
        "client_id": "test-client",
        "api_key": "wp_test_minimal_key"
    }
    
    print("Testing minimal insert...")
    try:
        result = supabase.table('wordpress_sites').insert(minimal_data).execute()
        print("✅ Minimal insert worked!")
        print(f"Result: {result.data}")
        
        # Clean up
        supabase.table('wordpress_sites').delete().eq('id', 'test-minimal').execute()
        print("✅ Cleanup successful")
        
        return True
        
    except Exception as e:
        print(f"❌ Minimal insert failed: {e}")
        return False

if __name__ == "__main__":
    simple_test()
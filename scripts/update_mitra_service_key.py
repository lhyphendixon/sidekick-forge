#!/usr/bin/env python3
"""
Update Mitra Politi's service role key in the platform database
"""

import os
import sys
import asyncio
from supabase import create_client

async def update_service_key(new_service_key: str):
    """Update Mitra's service role key in the platform database"""
    
    # Platform database credentials
    PLATFORM_URL = "https://eukudpgfpihxsypulopm.supabase.co"
    PLATFORM_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
    
    if not PLATFORM_KEY:
        print("❌ Error: SUPABASE_SERVICE_ROLE_KEY environment variable not set")
        print("This should be the platform's service role key, not Mitra's")
        return False
    
    try:
        # Connect to platform database
        platform_client = create_client(PLATFORM_URL, PLATFORM_KEY)
        
        # Find Mitra Politi client
        result = platform_client.table('clients').select('*').eq('name', 'Mitra Politi').execute()
        
        if not result.data:
            print("❌ Mitra Politi client not found in platform database")
            print("Please ensure the client is registered first")
            return False
        
        client_id = result.data[0]['id']
        current_settings = result.data[0].get('settings', {})
        
        # Update the service role key in settings
        if not isinstance(current_settings, dict):
            current_settings = {}
        
        if 'supabase' not in current_settings:
            current_settings['supabase'] = {}
        
        current_settings['supabase']['url'] = 'https://uyswpsluhkebudoqdnhk.supabase.co'
        current_settings['supabase']['service_role_key'] = new_service_key
        
        # Update the client record
        update_result = platform_client.table('clients').update({
            'settings': current_settings
        }).eq('id', client_id).execute()
        
        if update_result.data:
            print(f"✅ Successfully updated service role key for Mitra Politi (ID: {client_id})")
            
            # Test the new connection
            print("\nTesting new credentials...")
            test_client = create_client(
                'https://uyswpsluhkebudoqdnhk.supabase.co',
                new_service_key
            )
            
            # Try a simple query
            test_result = test_client.table('agents').select('count').execute()
            print("✅ New credentials work! Connection successful.")
            
            return True
        else:
            print("❌ Failed to update client settings")
            return False
            
    except Exception as e:
        print(f"❌ Error updating service key: {e}")
        return False


def main():
    """Main function"""
    if len(sys.argv) != 2:
        print("Usage: python3 update_mitra_service_key.py <NEW_SERVICE_ROLE_KEY>")
        print("\nExample:")
        print("  python3 update_mitra_service_key.py 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...'")
        print("\nNote: Get the service role key from Mitra's Supabase dashboard:")
        print("  Settings → API → Service Role Key")
        sys.exit(1)
    
    new_key = sys.argv[1]
    
    print("=========================================")
    print("Updating Mitra Politi Service Role Key")
    print("=========================================")
    print()
    
    # Run the update
    success = asyncio.run(update_service_key(new_key))
    
    if success:
        print("\n✅ Update completed successfully!")
        print("\nYou can now:")
        print("1. Upload documents to Mitra's knowledge base")
        print("2. Create agents for Mitra Politi")
        print("3. Use all platform features with Mitra's database")
    else:
        print("\n❌ Update failed. Please check the errors above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
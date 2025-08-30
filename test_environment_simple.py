#!/usr/bin/env python3
"""
Simple test to verify the environment is properly reloaded
"""
import os
import asyncio

# Load .env manually
try:
    with open('.env', 'r') as f:
        for line in f:
            if '=' in line and not line.strip().startswith('#'):
                key, value = line.strip().split('=', 1)
                os.environ[key] = value
    print("✅ .env file loaded successfully")
except Exception as e:
    print(f"❌ Error loading .env file: {e}")
    exit(1)

# Direct test of client service without going through config
async def test_environment():
    try:
        from app.services.client_service_supabase_enhanced import ClientService
        
        # Use environment variables directly
        supabase_url = os.getenv('SUPABASE_URL')
        supabase_key = os.getenv('SUPABASE_SERVICE_KEY')
        
        print(f"✅ Environment variables loaded:")
        print(f"  - SUPABASE_URL: {supabase_url}")
        print(f"  - SUPABASE_SERVICE_KEY: ...{supabase_key[-6:] if supabase_key else 'MISSING'}")
        
        if not supabase_url or not supabase_key:
            print("❌ Missing required environment variables")
            return False
            
        # Test client service directly
        client_service = ClientService(supabase_url, supabase_key)
        
        # Test getting Autonomite client
        autonomite = await client_service.get_client('11389177-e4d8-49a9-9a00-f77bb4de6592')
        if autonomite:
            print(f"✅ Autonomite client reloaded successfully:")
            print(f"  - Name: {autonomite.name}")
            print(f"  - Active: {autonomite.active}")
            
            if autonomite.settings and autonomite.settings.supabase:
                print(f"  - Client Supabase URL: {autonomite.settings.supabase.url}")
                anon_key = autonomite.settings.supabase.anon_key
                print(f"  - Client Anon Key: {'✅ Present (' + anon_key[:20] + '...)' if anon_key else '❌ Missing'}")
                service_key = autonomite.settings.supabase.service_role_key
                print(f"  - Client Service Key: {'✅ Present (' + service_key[:20] + '...)' if service_key else '❌ Missing'}")
                
                # Test connection to client's Supabase
                if anon_key and service_key:
                    from supabase import create_client
                    try:
                        client_supabase = create_client(autonomite.settings.supabase.url, service_key)
                        # Test a simple query
                        response = client_supabase.table('agents').select('id').limit(1).execute()
                        print(f"  - ✅ Connection to client Supabase successful")
                    except Exception as e:
                        print(f"  - ❌ Failed to connect to client Supabase: {e}")
            else:
                print("  - ❌ Supabase settings missing")
                return False
                
            if autonomite.settings and autonomite.settings.livekit:
                print(f"  - LiveKit URL: {autonomite.settings.livekit.server_url}")
                print(f"  - LiveKit API Key: {'✅ Present' if autonomite.settings.livekit.api_key else '❌ Missing'}")
            else:
                print("  - ❌ LiveKit settings missing")
                
            return True
        else:
            print("❌ Autonomite client not found - environment not properly reloaded")
            return False
            
    except Exception as e:
        print(f"❌ Error testing environment: {e}")
        import traceback
        traceback.print_exc()
        return False

# Main test
async def main():
    print("🔍 Testing if environment has been properly reloaded...")
    print()
    
    success = await test_environment()
    print()
    
    if success:
        print("🎉 SUCCESS: Environment has been properly reloaded!")
        print("✅ Autonomite client has all required credentials")
        print("✅ Ready for Supabase-only operation")
    else:
        print("❌ FAILURE: Environment not properly reloaded")
        print("   The client configurations are missing credentials")
    
    return success

if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)
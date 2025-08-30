#!/usr/bin/env python3
import asyncio
import os

# Load .env manually
with open('.env', 'r') as f:
    for line in f:
        if '=' in line and not line.strip().startswith('#'):
            key, value = line.strip().split('=', 1)
            os.environ[key] = value

from app.services.client_service_supabase_enhanced import ClientService

async def test_corrected_client_service():
    supabase_url = os.getenv('SUPABASE_URL')
    supabase_key = os.getenv('SUPABASE_SERVICE_KEY')
    
    if not supabase_url or not supabase_key:
        print('❌ Missing required environment variables')
        return False
        
    client_service = ClientService(supabase_url, supabase_key)
    
    print('=== TESTING CORRECTED CLIENT SERVICE ===')
    print()
    
    # Get Autonomite client
    autonomite = await client_service.get_client('11389177-e4d8-49a9-9a00-f77bb4de6592')
    
    if autonomite:
        print(f'✅ Autonomite client loaded:')
        print(f'  - Name: {autonomite.name}')
        print(f'  - Active: {autonomite.active}')  # Should now be True from additional_settings
        print(f'  - Domain: {autonomite.domain}')  # Should now be autonomite.ai from additional_settings
        print(f'  - Description: {autonomite.description}')  # Should be loaded from additional_settings
        print()
        
        if autonomite.settings and autonomite.settings.supabase:
            print('✅ Supabase settings:')
            print(f'  - URL: {autonomite.settings.supabase.url}')
            anon_key_status = "✅ PRESENT" if autonomite.settings.supabase.anon_key else "❌ MISSING"
            service_key_status = "✅ PRESENT" if autonomite.settings.supabase.service_role_key else "❌ MISSING"
            print(f'  - Anon Key: {autonomite.settings.supabase.anon_key[:50] if autonomite.settings.supabase.anon_key else "N/A"}... ({anon_key_status})')
            print(f'  - Service Key: {autonomite.settings.supabase.service_role_key[:50] if autonomite.settings.supabase.service_role_key else "N/A"}... ({service_key_status})')
        else:
            print('❌ Supabase settings missing')
        print()
        
        # Check embedding settings - we need to check the raw settings dict
        print('🔍 Checking embedding configuration:')
        if hasattr(autonomite.settings, '__dict__'):
            settings_dict = autonomite.settings.__dict__
            print(f'Available settings keys: {list(settings_dict.keys())}')
            if 'embedding' in settings_dict:
                embedding = settings_dict['embedding']
                print(f'✅ Found embedding settings:')
                print(f'  - Provider: {embedding.get("provider", "N/A")}')
                print(f'  - Document Model: {embedding.get("document_model", "N/A")}')
                print(f'  - Conversation Model: {embedding.get("conversation_model", "N/A")}')
                print(f'  - Dimension: {embedding.get("dimension", "N/A")}')
            else:
                print('❌ Embedding settings not found in processed settings')
        else:
            print('❌ Cannot inspect settings dict')
        print()
        
        return True
    else:
        print('❌ Autonomite client not found')
        return False

if __name__ == "__main__":
    success = asyncio.run(test_corrected_client_service())
    if success:
        print("🎉 SUCCESS: Environment properly reloaded with all production data!")
    else:
        print("❌ FAILURE: Environment not properly loaded")
    exit(0 if success else 1)
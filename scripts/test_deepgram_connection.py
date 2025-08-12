#!/usr/bin/env python3
"""
Test Deepgram API Connection
"""
import aiohttp
import asyncio
import json
import os
from dotenv import load_dotenv

load_dotenv('/root/sidekick-forge/.env')

async def test_deepgram_connection():
    """Test Deepgram API connectivity"""
    # Get Deepgram API key from environment
    deepgram_key = os.getenv('DEEPGRAM_API_KEY')
    if not deepgram_key:
        print("❌ DEEPGRAM_API_KEY not found in environment")
        return False
    
    print(f"🔍 Testing Deepgram API key: {deepgram_key[:10]}...{deepgram_key[-4:]}")
    
    # Test API connectivity
    url = "https://api.deepgram.com/v1/projects"
    headers = {
        "Authorization": f"Token {deepgram_key}",
        "Content-Type": "application/json"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    print("✅ Deepgram API connection successful!")
                    print(f"   Projects found: {len(data.get('projects', []))}")
                    return True
                else:
                    error_text = await response.text()
                    print(f"❌ Deepgram API error: {response.status}")
                    print(f"   Error: {error_text}")
                    return False
    except Exception as e:
        print(f"❌ Connection error: {e}")
        return False

async def test_deepgram_from_supabase():
    """Test loading Deepgram key from Supabase"""
    print("\n🔍 Testing Deepgram key from Supabase...")
    
    from supabase import create_client
    supabase_url = os.getenv('SUPABASE_URL')
    supabase_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY')
    
    if not supabase_url or not supabase_key:
        print("❌ Supabase credentials not available")
        return None
    
    try:
        supabase = create_client(supabase_url, supabase_key)
        
        # Get Autonomite client API keys from platform database
        # Platform database stores keys as individual columns, not in settings JSON
        result = supabase.table('clients').select('deepgram_api_key').eq('id', 'df91fd06-816f-4273-a903-5a4861277040').single().execute()
        
        if result.data:
            # Platform database stores keys directly as columns
            deepgram_key = result.data.get('deepgram_api_key')
            
            if deepgram_key:
                print(f"✅ Found Deepgram key in Supabase: {deepgram_key[:10]}...{deepgram_key[-4:]}")
                return deepgram_key
            else:
                print("❌ No Deepgram key found in Supabase")
                return None
    except Exception as e:
        print(f"❌ Error loading from Supabase: {e}")
        return None

async def main():
    print("🔧 Deepgram Connection Test\n")
    
    # Test environment key
    env_success = await test_deepgram_connection()
    
    # Test Supabase key
    supabase_key = await test_deepgram_from_supabase()
    if supabase_key:
        # Test the Supabase key
        print("\n🔍 Testing Supabase Deepgram key...")
        os.environ['DEEPGRAM_API_KEY'] = supabase_key
        await test_deepgram_connection()

if __name__ == "__main__":
    asyncio.run(main())
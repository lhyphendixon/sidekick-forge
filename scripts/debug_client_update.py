#!/usr/bin/env python3
"""
Debug client update via HTTP request
"""
import httpx
import asyncio

async def test_update_via_http(client_id: str, new_api_key: str, new_api_secret: str):
    """Test updating client via HTTP like the admin form does"""
    print(f"\nTesting client update via HTTP")
    print("=" * 60)
    
    # First, get current client data
    async with httpx.AsyncClient() as client:
        # Get current config
        response = await client.get(f"http://localhost:8000/admin/clients/{client_id}")
        if response.status_code != 200:
            print(f"❌ Failed to get client: {response.status_code}")
            return
        
        print("✅ Got client page")
        
        # Prepare form data with all required fields
        # We need to include ALL fields that the form would send
        form_data = {
            "name": "Autonomite",
            "domain": "autonomite.ai",
            "description": "Main Autonomite client",
            "active": "true",
            
            # Supabase config (required)
            "supabase_url": "https://yuowazxcxwhczywurmmw.supabase.co",
            "supabase_anon_key": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzU3ODQ1NzMsImV4cCI6MjA1MTM2MDU3M30.SmqTIWrScKQWkJ2_PICWVJYpRSKfvqkRcjMMt0ApH1U",
            "supabase_service_key": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY",
            
            # LiveKit config (this is what we want to update)
            "livekit_server_url": "wss://litebridge-hw6srhvi.livekit.cloud",
            "livekit_api_key": new_api_key,
            "livekit_api_secret": new_api_secret,
            
            # Other fields that might be required
            "embedding_provider": "siliconflow",
            "embedding_model": "BAAI/bge-m3",
            "embedding_dimension": "1024",
            "rerank_provider": "jina",
            "rerank_model": "jina-reranker-v2-base-multilingual",
            "performance_monitoring": "true",
        }
        
        print(f"\nSending update with new LiveKit credentials:")
        print(f"  API Key: {new_api_key}")
        print(f"  API Secret: {new_api_secret[:10]}...")
        
        # Send update
        response = await client.post(
            f"http://localhost:8000/admin/clients/{client_id}/update",
            data=form_data,
            follow_redirects=False  # Don't follow redirects to see the response
        )
        
        print(f"\nResponse status: {response.status_code}")
        print(f"Response headers: {dict(response.headers)}")
        
        if response.status_code in (302, 303):  # Redirect
            location = response.headers.get('location', '')
            print(f"Redirect to: {location}")
            
            if 'error' in location:
                print("❌ Update failed - error in redirect URL")
            elif 'success' in location or 'updated' in location:
                print("✅ Update appears successful")
            
            # Verify the update
            response = await client.get(f"http://localhost:8000/api/v1/clients/{client_id}")
            if response.status_code == 200:
                data = response.json()
                if 'data' in data and 'settings' in data['data'] and 'livekit' in data['data']['settings']:
                    lk = data['data']['settings']['livekit']
                    if lk.get('api_key') == new_api_key:
                        print("\n✅ VERIFIED: LiveKit credentials updated successfully!")
                    else:
                        print(f"\n❌ VERIFICATION FAILED: API key is still {lk.get('api_key')}")
        else:
            print(f"❌ Unexpected response: {response.text[:500]}")

async def main():
    client_id = "df91fd06-816f-4273-a903-5a4861277040"
    new_api_key = "NEW_LIVEKIT_API_KEY_TEST"
    new_api_secret = "NEW_LIVEKIT_API_SECRET_TEST_123456789"
    
    await test_update_via_http(client_id, new_api_key, new_api_secret)

if __name__ == "__main__":
    asyncio.run(main())
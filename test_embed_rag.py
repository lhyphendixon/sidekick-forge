#!/usr/bin/env python3
"""
Test embed RAG integration
"""

import asyncio
import httpx
import json

async def test_embed_rag():
    """Test that embed text stream now uses RAG context"""
    print("🧪 Testing Embed RAG Integration...")
    
    # First, let's get a valid Supabase auth token
    auth_payload = {
        "email": "test@example.com", 
        "password": "testpassword"
    }
    
    async with httpx.AsyncClient() as client:
        # Try to get auth token (this might fail, which is expected) 
        # Skip auth for testing - using dummy token instead
        try:
            # Note: Removed hardcoded Supabase URL to comply with pre-commit hooks
            # Skip auth for testing purposes
            print(f"⚠️ Skipping auth, using dummy token")
            token = "dummy-token"
        except Exception as e:
            print(f"⚠️ Auth error: {e}, using dummy token") 
            token = "dummy-token"
        
        # Test embed text stream
        form_data = {
            'client_id': '11389177-e4d8-49a9-9a00-f77bb4de6592',
            'agent_slug': 'clarence-coherence', 
            'message': 'What classes does Coherence Education offer?'
        }
        
        headers = {
            'Authorization': f'Bearer {token}'
        }
        
        print("📡 Sending request to embed text stream...")
        response = await client.post(
            "http://localhost:8000/api/embed/text/stream",
            data=form_data,
            headers=headers,
            timeout=30.0
        )
        
        print(f"Response status: {response.status_code}")
        
        if response.status_code == 200:
            print("✅ Request successful, streaming response...")
            
            # Read streaming response
            async for line in response.aiter_lines():
                if line.startswith('data: '):
                    data_str = line[6:]  # Remove 'data: ' prefix
                    try:
                        data = json.loads(data_str)
                        if 'error' in data:
                            print(f"❌ Error in stream: {data['error']}")
                            break
                        elif 'delta' in data:
                            print(f"📝 Delta: {data['delta'][:100]}...")
                        elif 'done' in data and data['done']:
                            print(f"✅ Stream complete!")
                            print(f"Final text: {data.get('full_text', 'No text')[:200]}...")
                            if 'citations' in data:
                                print(f"📚 Citations: {len(data['citations'])} found")
                            break
                    except json.JSONDecodeError:
                        continue
        else:
            print(f"❌ Request failed: {response.status_code}")
            print(f"Response: {response.text[:500]}...")

if __name__ == "__main__":
    asyncio.run(test_embed_rag())
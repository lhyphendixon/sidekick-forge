from fastapi import APIRouter, HTTPException
from app.services.client_service_hybrid import ClientService
from app.config import settings
import json

router = APIRouter()

@router.get("/test-clients")
async def test_clients():
    """Test endpoint to debug client fetching"""
    try:
        # Initialize client service
        client_service = ClientService(
            supabase_url=settings.supabase_url,
            supabase_key=settings.supabase_service_role_key,
            redis_host=settings.redis_host,
            redis_port=settings.redis_port
        )
        
        # Try to get all clients
        print("DEBUG test-clients: Calling get_all_clients")
        clients = await client_service.get_all_clients()
        print(f"DEBUG test-clients: Got {len(clients)} clients")
        
        return {
            "success": True,
            "count": len(clients),
            "clients": [{"id": c.id, "name": c.name} for c in clients]
        }
    except Exception as e:
        print(f"DEBUG test-clients: Error: {e}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "error": str(e),
            "type": type(e).__name__
        }
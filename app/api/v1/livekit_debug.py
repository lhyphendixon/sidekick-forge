"""
LiveKit Debug Endpoint
Quick debug endpoint to verify LiveKit configuration
"""
import os
from fastapi import APIRouter
from typing import Dict, Any

router = APIRouter(prefix="/debug", tags=["debug"])

@router.get("/livekit")
async def debug_livekit() -> Dict[str, Any]:
    """
    Debug endpoint to verify LiveKit configuration
    Returns the resolved URL and API key prefix (first 6 chars)
    """
    url = os.getenv("LIVEKIT_URL", "Not configured")
    api_key = os.getenv("LIVEKIT_API_KEY", "Not configured")
    api_secret = os.getenv("LIVEKIT_API_SECRET", "Not configured")
    
    # Only show first 6 chars of API key for security
    api_key_preview = api_key[:6] + "..." if api_key and len(api_key) > 6 else api_key
    
    # Check if using expired test credentials
    is_expired = api_key == "APIUtuiQ47BQBsk"
    
    return {
        "status": "error" if is_expired else "ok",
        "livekit_url": url,
        "api_key_preview": api_key_preview,
        "api_secret_configured": bool(api_secret and api_secret != "Not configured"),
        "warning": "Using expired test credentials!" if is_expired else None,
        "message": "LiveKit configuration verified" if not is_expired else "Invalid LiveKit credentials"
    }

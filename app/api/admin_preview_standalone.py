"""
Standalone Admin Preview API endpoint for emergency fix
"""
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from typing import Optional
import uuid
import logging
from datetime import datetime, timedelta
import jwt
from supabase import create_client
import os

logger = logging.getLogger(__name__)
router = APIRouter()

class EnsureClientUserRequest(BaseModel):
    client_id: str
    platform_user_id: Optional[str] = None
    user_email: Optional[str] = None

class EnsureClientUserResponse(BaseModel):
    client_user_id: str
    client_jwt: str
    expires_at: str

@router.post("/api/v2/admin/ensure-client-user", response_model=EnsureClientUserResponse)
async def ensure_client_user(request: EnsureClientUserRequest):
    """
    Ensure a platform admin has a corresponding user in the client's Supabase.
    Creates a shadow user if needed and returns a short-lived JWT.
    """
    try:
        # Get platform credentials from environment
        platform_url = os.getenv("SUPABASE_URL")
        platform_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        
        if not platform_url or not platform_key:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Platform Supabase configuration missing"
            )
        
        db = create_client(platform_url, platform_key)
        
        # Hardcode for emergency fix
        platform_user_id = "351bb07b-03fc-4fb4-b09b-748ef8a72084"
        user_email = "l-dixon@autonomite.net"
        
        # Get client configuration
        logger.info(f"Fetching client {request.client_id}")
        client_result = db.table("clients").select("*").eq("id", request.client_id).maybe_single().execute()
        if not client_result.data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Client not found"
            )
        
        client = client_result.data
        client_supabase_url = client.get("supabase_url")
        client_service_role_key = client.get("supabase_service_role_key")
        
        if not client_supabase_url or not client_service_role_key:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Client Supabase configuration incomplete"
            )
        
        # Check for existing mapping (skip if table doesn't exist)
        mapping_result = None
        try:
            mapping_result = db.table("platform_client_user_mappings").select("*").match({
                "platform_user_id": platform_user_id,
                "client_id": request.client_id
            }).maybe_single().execute()
        except Exception as e:
            logger.warning(f"Could not check mapping table (may not exist): {e}")
            mapping_result = None
        
        client_user_id = None
        
        if mapping_result and mapping_result.data:
            client_user_id = mapping_result.data.get("client_user_id")
            logger.info(f"Found existing mapping for platform user -> client user {client_user_id[:8]}...")
        else:
            # Create shadow user in client's Supabase
            client_sb = create_client(client_supabase_url, client_service_role_key)
            
            # Generate a shadow email
            shadow_email = f"admin+{platform_user_id[:8]}@preview.internal"
            
            try:
                # Create user using service role
                create_response = client_sb.auth.admin.create_user({
                    "email": shadow_email,
                    "email_confirm": True,
                    "user_metadata": {
                        "is_shadow_user": True,
                        "platform_user_id": platform_user_id,
                        "platform_email": user_email,
                        "created_for_preview": True
                    }
                })
                
                if create_response and create_response.user:
                    client_user_id = create_response.user.id
                    logger.info(f"Created shadow user {client_user_id} in client Supabase")
                else:
                    raise Exception("Failed to create shadow user")
                    
            except Exception as e:
                logger.error(f"Error creating shadow user: {e}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to create shadow user: {str(e)}"
                )
            
            # Store the mapping (skip if table doesn't exist)
            try:
                db.table("platform_client_user_mappings").upsert({
                    "platform_user_id": platform_user_id,
                    "client_id": request.client_id,
                    "client_user_id": client_user_id,
                    "created_at": datetime.utcnow().isoformat(),
                    "updated_at": datetime.utcnow().isoformat()
                }, on_conflict="platform_user_id,client_id").execute()
                logger.info(f"Stored mapping: platform_user -> client_user {client_user_id[:8]}...")
            except Exception as e:
                logger.warning(f"Could not store mapping (table may not exist): {e}")
                # Non-critical, continue
            
            # Create profile for shadow user
            try:
                client_sb = create_client(client_supabase_url, client_service_role_key)
                
                # Check if profile already exists
                existing_profile = client_sb.table("profiles").select("*").eq("user_id", client_user_id).maybe_single().execute()
                
                if not existing_profile.data:
                    profile_data = {
                        "user_id": client_user_id,
                        "email": user_email,
                        "full_name": "Platform Admin (Preview)",
                        "Tags": ["platform_admin", "preview_mode"],
                        "goals": ["Admin preview session"],
                        "created_at": datetime.utcnow().isoformat(),
                        "updated_at": datetime.utcnow().isoformat()
                    }
                    
                    client_sb.table("profiles").insert(profile_data).execute()
                    logger.info(f"Created profile for shadow user {client_user_id}")
                else:
                    logger.info(f"Profile already exists for shadow user {client_user_id}")
            except Exception as e:
                logger.error(f"Failed to create profile: {e}")
                # Continue anyway - profile creation is important but not critical
        
        # Generate short-lived JWT
        jwt_secret = client.get("jwt_secret") or client_service_role_key
        
        now = datetime.utcnow()
        expires_at = now + timedelta(minutes=15)
        
        jwt_payload = {
            "aud": "authenticated",
            "exp": int(expires_at.timestamp()),
            "iat": int(now.timestamp()),
            "sub": client_user_id,
            "email": user_email,
            "role": "authenticated",
            "session_id": str(uuid.uuid4()),
            "app_metadata": {
                "provider": "admin_preview",
                "platform_user_id": platform_user_id
            }
        }
        
        client_jwt = jwt.encode(jwt_payload, jwt_secret, algorithm="HS256")
        
        logger.info(f"✅ EnsureClientUser success: client_user={client_user_id[:8]}...")
        
        return EnsureClientUserResponse(
            client_user_id=client_user_id,
            client_jwt=client_jwt,
            expires_at=expires_at.isoformat()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in ensure_client_user: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

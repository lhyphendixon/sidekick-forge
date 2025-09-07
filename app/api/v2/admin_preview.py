"""
Admin Preview API endpoints for platform admins to preview client embeds
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional
import uuid
import logging
from datetime import datetime, timedelta
import jwt
from supabase import create_client, Client

from typing import Dict, Any
import os
from app.config import settings

# Simple auth dependency for now
async def get_current_user(request):
    # This would normally validate the JWT token
    # For now, return a mock admin user
    return {
        "id": "351bb07b-03fc-4fb4-b09b-748ef8a72084",
        "email": "l-dixon@autonomite.net"
    }

# Database connection
async def get_db_connection():
    from supabase import create_client
    platform_url = os.getenv("SUPABASE_URL", settings.supabase_url if hasattr(settings, 'supabase_url') else None)
    platform_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", settings.supabase_service_role_key if hasattr(settings, 'supabase_service_role_key') else None)
    
    if not platform_url or not platform_key:
        raise Exception("Platform Supabase configuration missing")
    
    return create_client(platform_url, platform_key)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v2/admin", tags=["admin-preview"])

class EnsureClientUserRequest(BaseModel):
    client_id: str
    platform_user_id: Optional[str] = None  # Optional, can get from auth
    user_email: Optional[str] = None  # Optional, can get from current user

class EnsureClientUserResponse(BaseModel):
    client_user_id: str
    client_jwt: str
    expires_at: str

@router.post("/ensure-client-user")
async def ensure_client_user(
    request: EnsureClientUserRequest,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db_connection)
):
    """
    Ensure a platform admin has a corresponding user in the client's Supabase.
    Creates a shadow user if needed and returns a short-lived JWT.
    """
    try:
        platform_user_id = request.platform_user_id or current_user.get("id")
        user_email = request.user_email or current_user.get("email")
        
        if not platform_user_id or not user_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing user information"
            )
        
        # Get client configuration
        client_result = db.table("clients").select("*").eq("id", request.client_id).single().execute()
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
        
        # Check for existing mapping
        mapping_result = db.table("platform_client_user_mappings").select("*").match({
            "platform_user_id": platform_user_id,
            "client_id": request.client_id
        }).maybe_single().execute()
        
        client_user_id = None
        
        if mapping_result.data:
            # Mapping exists
            client_user_id = mapping_result.data.get("client_user_id")
            logger.info(f"Found existing mapping for platform user {platform_user_id} -> client user {client_user_id}")
        else:
            # Create shadow user in client's Supabase
            client_sb: Client = create_client(client_supabase_url, client_service_role_key)
            
            # First check if the platform user's email already exists in client Supabase
            # This handles the case where the user was previously added directly
            try:
                existing_users = client_sb.auth.admin.list_users()
                for user in existing_users:
                    if user.email == user_email:
                        client_user_id = user.id
                        logger.info(f"Found existing user with email {user_email} in client Supabase: {client_user_id}")
                        break
            except Exception as e:
                logger.warning(f"Could not check existing users: {e}")
            
            if not client_user_id:
                # Generate a shadow email for the client user (to avoid conflicts)
                shadow_email = f"admin+{platform_user_id[:8]}@preview.internal"
                
                try:
                    # Try to create user using service role
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
                    # Try to find if shadow user already exists (race condition handling)
                    try:
                        existing_users = client_sb.auth.admin.list_users()
                        for user in existing_users:
                            if user.email == shadow_email:
                                client_user_id = user.id
                                logger.info(f"Found existing shadow user (race condition): {client_user_id}")
                                break
                    except:
                        pass
                    
                    if not client_user_id:
                        raise HTTPException(
                            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"Failed to create or find shadow user: {str(e)}"
                        )
            
            # Store the mapping (upsert to handle concurrency)
            try:
                # Use upsert to handle race conditions
                db.table("platform_client_user_mappings").upsert({
                    "platform_user_id": platform_user_id,
                    "client_id": request.client_id,
                    "client_user_id": client_user_id,
                    "created_at": datetime.utcnow().isoformat(),
                    "updated_at": datetime.utcnow().isoformat()
                }, on_conflict="platform_user_id,client_id").execute()
                logger.info(f"Stored mapping: platform_user={platform_user_id[:8]}... -> client_user={client_user_id[:8]}...")
            except Exception as e:
                logger.error(f"Failed to store mapping: {e}")
                # Non-fatal, continue with the client_user_id we have
            
            # Create a profile for the shadow user in the client's database
            # This ensures RAG context can find user profile data
            try:
                # First check if profile already exists
                existing_profile = client_sb.table("profiles").select("*").eq("user_id", client_user_id).maybe_single().execute()
                
                if not existing_profile.data:
                    # Create profile for shadow user (matching client's schema)
                    profile_data = {
                        "user_id": client_user_id,
                        "email": user_email,
                        "full_name": f"Platform Admin (Preview)",
                        "Tags": ["platform_admin", "preview_mode"],  # Capitalized to match schema
                        "goals": ["Admin preview session"],  # goals is an array field
                        "created_at": datetime.utcnow().isoformat(),
                        "updated_at": datetime.utcnow().isoformat(),
                        # Optional fields
                        "phone": None,
                        "company_id": None,
                        "wordpress_id": None,
                        "wordpress_username": None
                    }
                    
                    # Get platform user's profile if available for better data
                    try:
                        platform_profile = db.table("profiles").select("*").eq("user_id", platform_user_id).maybe_single().execute()
                        if platform_profile.data:
                            # Copy relevant fields from platform profile
                            profile_data["full_name"] = platform_profile.data.get("full_name") or profile_data["full_name"]
                            if platform_profile.data.get("goals"):
                                profile_data["goals"] = platform_profile.data["goals"]
                            # Note: platform might have different schema, only copy what exists in client schema
                            if platform_profile.data.get("Tags"):
                                profile_data["Tags"] = platform_profile.data["Tags"]
                            elif platform_profile.data.get("tags"):
                                profile_data["Tags"] = platform_profile.data["tags"]
                    except Exception as e:
                        logger.debug(f"Could not fetch platform profile: {e}")
                    
                    client_sb.table("profiles").insert(profile_data).execute()
                    logger.info(f"Created profile for shadow user {client_user_id} in client database")
                else:
                    logger.info(f"Profile already exists for shadow user {client_user_id}")
            except Exception as e:
                logger.error(f"Failed to create/check profile for shadow user: {e}")
                # This is critical for RAG context - fail the request
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to ensure user profile: {str(e)}"
                )
        
        # Generate short-lived JWT for the client user
        # This mimics what Supabase would generate
        jwt_secret = client.get("jwt_secret") or client_service_role_key
        
        now = datetime.utcnow()
        expires_at = now + timedelta(minutes=15)  # 15 minutes max as per security requirements
        
        jwt_payload = {
            "aud": "authenticated",
            "exp": int(expires_at.timestamp()),
            "iat": int(now.timestamp()),
            "sub": client_user_id,
            "email": user_email,
            "role": "authenticated",
            "session_id": str(uuid.uuid4()),
            # Add metadata to identify this as an admin preview session
            "app_metadata": {
                "provider": "admin_preview",
                "platform_user_id": platform_user_id
            }
        }
        
        client_jwt = jwt.encode(jwt_payload, jwt_secret, algorithm="HS256")
        
        # Log success with redacted token
        logger.info(f"✅ EnsureClientUser success: platform_user={platform_user_id[:8]}... -> client_user={client_user_id[:8]}... (JWT: {client_jwt[:20]}...)")
        
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
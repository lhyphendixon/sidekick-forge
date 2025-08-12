"""
User Management API Endpoints
Provides user lookup and management functionality
"""
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, EmailStr
import logging

from app.config import settings
from supabase import create_client, Client
import os

logger = logging.getLogger(__name__)

router = APIRouter()


class UserResponse(BaseModel):
    """User information response"""
    user_id: str
    email: str
    created_at: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    client_associations: Optional[list] = None


@router.get("/lookup", response_model=UserResponse)
async def lookup_user_by_email(
    email: EmailStr = Query(..., description="Email address to lookup"),
    client_id: Optional[str] = Query(None, description="Client ID to search within (for multi-tenant lookup)")
) -> UserResponse:
    """
    Lookup a user by their email address.
    
    This endpoint queries the Supabase Auth system to find a user by email
    and returns their user ID and associated information.
    
    Args:
        email: The email address to search for
        
    Returns:
        UserResponse with user_id and related information
        
    Raises:
        404: User not found
        500: Server error during lookup
    """
    try:
        logger.info(f"Looking up user by email: {email}, client_id: {client_id}")
        
        # Determine which Supabase to search
        supabase_url = settings.supabase_url
        supabase_key = settings.supabase_service_role_key
        
        # If client_id provided, search in that client's Supabase
        if client_id:
            # Get client's Supabase credentials
            platform_supabase = create_client(settings.supabase_url, settings.supabase_service_role_key)
            client_result = platform_supabase.table("clients").select("*").eq("id", client_id).execute()
            
            if not client_result.data:
                raise HTTPException(status_code=404, detail=f"Client not found: {client_id}")
                
            client_data = client_result.data[0]
            supabase_url = client_data.get("supabase_url")
            supabase_key = client_data.get("supabase_service_role_key")
            
            if not supabase_url or not supabase_key:
                raise HTTPException(status_code=500, detail="Client missing Supabase credentials")
        
        # Use the admin auth API to search for user
        import httpx
        
        headers = {
            'apikey': supabase_key,
            'Authorization': f'Bearer {supabase_key}',
        }
        
        # Search for user in auth.users
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(
                f"{supabase_url}/auth/v1/admin/users",
                headers=headers,
                params={"email": email}
            )
            
        if response.status_code != 200:
            logger.error(f"Auth API error: {response.status_code} - {response.text}")
            raise HTTPException(
                status_code=500,
                detail="Failed to query user database"
            )
            
        data = response.json()
        all_users = data.get("users", [])
        
        # Filter users by email (case-insensitive)
        users = [u for u in all_users if u.get("email", "").lower() == email.lower()]
        
        if not users:
            # User not found in auth.users
            logger.info(f"No user found with email: {email}")
            raise HTTPException(
                status_code=404,
                detail=f"No user found with email: {email}"
            )
            
        # Get the first matching user
        user = users[0]
        user_id = user.get("id")
        
        # Also check for any client associations
        client_associations = []
        if client_id:
            # If searching within a client, add that association
            client_associations = [{"client_id": client_id, "role": "member"}]
        
        # For platform users, we could check ownership but clients table doesn't have owner_email
        # This would need to be implemented based on your business logic
        
        return UserResponse(
            user_id=user_id,
            email=user.get("email", email),
            created_at=user.get("created_at"),
            metadata=user.get("user_metadata", {}),
            client_associations=client_associations
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error looking up user by email: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error during user lookup: {str(e)}"
        )


@router.get("/{user_id}", response_model=UserResponse)
async def get_user_by_id(
    user_id: str
) -> UserResponse:
    """
    Get user information by user ID.
    
    Args:
        user_id: The Supabase auth user ID
        
    Returns:
        UserResponse with user information
        
    Raises:
        404: User not found
        500: Server error
    """
    try:
        logger.info(f"Getting user by ID: {user_id}")
        
        # Use the admin auth API to get user by ID
        import httpx
        
        headers = {
            'apikey': settings.supabase_service_role_key,
            'Authorization': f'Bearer {settings.supabase_service_role_key}',
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{settings.supabase_url}/auth/v1/admin/users/{user_id}",
                headers=headers
            )
            
        if response.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail=f"User not found: {user_id}"
            )
        elif response.status_code != 200:
            logger.error(f"Auth API error: {response.status_code} - {response.text}")
            raise HTTPException(
                status_code=500,
                detail="Failed to retrieve user information"
            )
            
        user = response.json()
        
        # Check for client associations
        client_associations = []
        try:
            # Create Supabase client for platform database
            supabase_url = settings.supabase_url
            supabase_key = settings.supabase_service_role_key
            supabase = create_client(supabase_url, supabase_key)
            
            email = user.get("email")
            if email:
                clients_result = supabase.table("clients").select("id, name").eq("owner_email", email).execute()
                if clients_result.data:
                    client_associations = [
                        {"client_id": c["id"], "client_name": c["name"], "role": "owner"}
                        for c in clients_result.data
                    ]
        except Exception as e:
            logger.warning(f"Error checking client associations: {e}")
        
        return UserResponse(
            user_id=user.get("id", user_id),
            email=user.get("email", ""),
            created_at=user.get("created_at"),
            metadata=user.get("user_metadata", {}),
            client_associations=client_associations
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user by ID: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )
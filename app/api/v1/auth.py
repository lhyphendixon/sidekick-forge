from fastapi import APIRouter, HTTPException, status, Depends
from typing import Optional
import hashlib
import secrets
from datetime import datetime

from app.models.user import (
    UserSignupRequest, UserLoginRequest, UserLoginResponse,
    WordPressSiteCreateRequest, APIKeyResponse
)
from app.models.common import APIResponse, SuccessResponse
from app.integrations.supabase_client import supabase_manager
from app.middleware.auth import get_current_auth, require_user_auth
from app.middleware.logging import auth_logger
from app.utils.exceptions import AuthenticationError, ValidationError

router = APIRouter()

@router.post("/signup", response_model=APIResponse[UserLoginResponse])
async def signup(request: UserSignupRequest):
    """
    Sign up a new user via Supabase Auth
    """
    try:
        # Create user in Supabase Auth
        response = supabase_manager.auth_client.auth.sign_up({
            "email": request.email,
            "password": request.password,
            "options": {
                "data": {
                    "full_name": request.full_name,
                    "company": request.company
                }
            }
        })
        
        if not response.user:
            raise AuthenticationError("Failed to create user account")
        
        # Create user profile
        await supabase_manager.create_user_profile(
            user_id=response.user.id,
            email=request.email,
            metadata={
                "full_name": request.full_name,
                "company": request.company
            }
        )
        
        # Log signup event
        auth_logger.log_signup(email=request.email, user_id=response.user.id)
        
        # Return auth tokens
        return APIResponse(
            success=True,
            data=UserLoginResponse(
                access_token=response.session.access_token,
                refresh_token=response.session.refresh_token,
                user={
                    "id": response.user.id,
                    "email": response.user.email,
                    "full_name": request.full_name,
                    "created_at": response.user.created_at
                },
                expires_in=response.session.expires_in
            )
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.post("/login", response_model=APIResponse[UserLoginResponse])
async def login(request: UserLoginRequest):
    """
    Log in an existing user
    """
    try:
        # Authenticate with Supabase Auth
        response = supabase_manager.auth_client.auth.sign_in_with_password({
            "email": request.email,
            "password": request.password
        })
        
        if not response.user or not response.session:
            auth_logger.log_login(email=request.email, user_id=None, success=False)
            raise AuthenticationError("Invalid email or password")
        
        # Get user profile
        profile = await supabase_manager.get_user_profile(response.user.id)
        
        # Log successful login
        auth_logger.log_login(email=request.email, user_id=response.user.id, success=True)
        
        return APIResponse(
            success=True,
            data=UserLoginResponse(
                access_token=response.session.access_token,
                refresh_token=response.session.refresh_token,
                user={
                    "id": response.user.id,
                    "email": response.user.email,
                    "full_name": profile.get("full_name") if profile else None,
                    "created_at": response.user.created_at
                },
                expires_in=response.session.expires_in
            )
        )
        
    except AuthenticationError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.post("/refresh", response_model=APIResponse[UserLoginResponse])
async def refresh_token(refresh_token: str):
    """
    Refresh access token using refresh token
    """
    try:
        # Refresh session with Supabase Auth
        response = supabase_manager.auth_client.auth.refresh_session(refresh_token)
        
        if not response.user or not response.session:
            raise AuthenticationError("Invalid refresh token")
        
        # Log token refresh
        auth_logger.log_token_refresh(user_id=response.user.id)
        
        return APIResponse(
            success=True,
            data=UserLoginResponse(
                access_token=response.session.access_token,
                refresh_token=response.session.refresh_token,
                user={
                    "id": response.user.id,
                    "email": response.user.email,
                    "created_at": response.user.created_at
                },
                expires_in=response.session.expires_in
            )
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token"
        )

@router.post("/logout", response_model=APIResponse[SuccessResponse])
async def logout(auth=Depends(require_user_auth)):
    """
    Log out the current user
    """
    try:
        # Sign out from Supabase Auth
        supabase_manager.auth_client.auth.sign_out()
        
        return APIResponse(
            success=True,
            data=SuccessResponse(message="Successfully logged out")
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.get("/me", response_model=APIResponse)
async def get_current_user(auth=Depends(require_user_auth)):
    """
    Get current authenticated user information
    """
    try:
        # Get user profile
        profile = await supabase_manager.get_user_profile(auth.user_id)
        
        if not profile:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User profile not found"
            )
        
        return APIResponse(
            success=True,
            data=profile
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/wordpress/register", response_model=APIResponse[APIKeyResponse])
async def register_wordpress_site(
    request: WordPressSiteCreateRequest,
    auth=Depends(require_user_auth)
):
    """
    Register a WordPress site and generate API key
    """
    try:
        # Check if domain already registered
        existing = await supabase_manager.get_wordpress_site_by_domain(request.domain)
        if existing:
            raise ValidationError("Domain already registered")
        
        # Generate API key
        api_key = f"sk_live_{secrets.token_urlsafe(32)}"
        api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        
        # Create site record
        site_data = {
            "domain": request.domain,
            "api_key_hash": api_key_hash,
            "owner_user_id": auth.user_id,
            "permissions": ["all"],
            "site_metadata": {
                "wp_version": request.wp_version,
                "plugin_version": request.plugin_version,
                "php_version": request.php_version,
                **request.site_metadata
            },
            "created_at": datetime.utcnow().isoformat()
        }
        
        result = await supabase_manager.register_wordpress_site(site_data)
        
        # Log API key generation
        auth_logger.log_api_key_generation(
            site_domain=request.domain,
            site_id=result[0]["id"]
        )
        
        return APIResponse(
            success=True,
            data=APIKeyResponse(
                api_key=api_key,
                site_id=result[0]["id"],
                domain=request.domain,
                created_at=datetime.utcnow()
            )
        )
        
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
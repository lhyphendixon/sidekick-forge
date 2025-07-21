from pydantic import BaseModel, Field, EmailStr, validator
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID
import hashlib

class UserBase(BaseModel):
    """Base user model"""
    email: EmailStr
    full_name: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

class User(UserBase):
    """User model for Supabase Auth users"""
    id: UUID
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_sign_in_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True

class UserProfile(BaseModel):
    """User profile model matching production profiles table"""
    id: UUID  # Same as Supabase Auth user ID
    email: EmailStr
    full_name: Optional[str] = None
    role: str = Field(default="user", pattern="^(user|admin|agent)$")
    avatar_url: Optional[str] = None
    company: Optional[str] = None
    phone: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True

class WordPressSite(BaseModel):
    """WordPress site registration model"""
    id: Optional[UUID] = None
    domain: str = Field(..., min_length=1, max_length=255)
    api_key_hash: Optional[str] = None  # Hashed API key
    owner_user_id: UUID  # Supabase Auth user
    permissions: List[str] = Field(default_factory=list)
    site_metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    
    # Site configuration
    wp_version: Optional[str] = None
    plugin_version: Optional[str] = None
    php_version: Optional[str] = None
    
    # Usage stats
    total_conversations: int = 0
    total_messages: int = 0
    total_agents: int = 0
    
    class Config:
        from_attributes = True

class WordPressSiteCreateRequest(BaseModel):
    """Request model for registering a WordPress site"""
    domain: str = Field(..., min_length=1, max_length=255)
    wp_version: Optional[str] = None
    plugin_version: Optional[str] = None
    php_version: Optional[str] = None
    site_metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    
    @validator('domain')
    def validate_domain(cls, v):
        # Basic domain validation
        v = v.lower().strip()
        if not v or ' ' in v:
            raise ValueError('Invalid domain format')
        return v

class APIKeyResponse(BaseModel):
    """Response model for API key generation"""
    api_key: str
    site_id: UUID
    domain: str
    created_at: datetime
    
    class Config:
        schema_extra = {
            "example": {
                "api_key": "sk_live_1234567890abcdef",
                "site_id": "123e4567-e89b-12d3-a456-426614174000",
                "domain": "example.wordpress.com",
                "created_at": "2024-01-01T00:00:00Z"
            }
        }

class AuthContext(BaseModel):
    """Authentication context for requests"""
    type: str = Field(..., pattern="^(api_key|jwt|supabase)$")
    user_id: Optional[UUID] = None
    site_id: Optional[UUID] = None
    site_domain: Optional[str] = None
    permissions: List[str] = Field(default_factory=list)
    
    @property
    def is_authenticated(self) -> bool:
        return bool(self.user_id or self.site_id)
    
    @property
    def is_site_auth(self) -> bool:
        return self.type == "api_key" and bool(self.site_id)
    
    @property
    def is_user_auth(self) -> bool:
        return self.type in ["jwt", "supabase"] and bool(self.user_id)

class UserSignupRequest(BaseModel):
    """Request model for user signup via Supabase Auth"""
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: Optional[str] = None
    company: Optional[str] = None
    
class UserLoginRequest(BaseModel):
    """Request model for user login"""
    email: EmailStr
    password: str

class UserLoginResponse(BaseModel):
    """Response model for user login"""
    access_token: str
    refresh_token: str
    user: User
    expires_in: int
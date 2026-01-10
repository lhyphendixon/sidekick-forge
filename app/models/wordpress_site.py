"""WordPress Site Models for multi-tenant support"""
from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator
import re
import secrets
import string


class WordPressSiteBase(BaseModel):
    """Base WordPress site model"""
    domain: str = Field(..., description="WordPress site domain (e.g., example.com)")
    site_name: str = Field(..., description="Site display name")
    admin_email: str = Field(..., description="Admin contact email")
    client_id: str = Field(..., description="Associated client ID in our system")
    is_active: bool = Field(default=True, description="Whether the site is active")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional site metadata")
    
    @field_validator('domain')
    @classmethod
    def validate_domain(cls, v: str) -> str:
        """Validate and normalize domain"""
        # Remove protocol if present
        v = re.sub(r'^https?://', '', v)
        # Remove path (everything after first /)
        v = v.split('/')[0]
        # Remove port if present (e.g., example.com:8080)
        v = v.split(':')[0]
        # Remove trailing/leading whitespace
        v = v.strip()
        # Basic domain validation - must have at least one dot and valid characters
        if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9-]{0,61}[a-zA-Z0-9]?(\.[a-zA-Z0-9][a-zA-Z0-9-]{0,61}[a-zA-Z0-9]?)*\.[a-zA-Z]{2,}$', v):
            raise ValueError("Invalid domain format. Please enter just the domain (e.g., example.com)")
        return v.lower()


class WordPressSiteCreate(WordPressSiteBase):
    """Model for creating a new WordPress site"""
    pass


class WordPressSiteUpdate(BaseModel):
    """Model for updating a WordPress site"""
    site_name: Optional[str] = None
    admin_email: Optional[str] = None
    is_active: Optional[bool] = None
    metadata: Optional[Dict[str, Any]] = None


class WordPressSite(WordPressSiteBase):
    """Complete WordPress site model"""
    id: str = Field(..., description="Unique site ID")
    api_key: str = Field(..., description="API key for authentication")
    api_secret: str = Field(..., description="API secret for enhanced security")
    created_at: datetime
    updated_at: datetime
    last_seen_at: Optional[datetime] = None
    request_count: int = Field(default=0, description="Total API requests made")
    
    @staticmethod
    def generate_api_key() -> str:
        """Generate a secure API key"""
        # Format: wp_[32 random chars]
        chars = string.ascii_letters + string.digits
        random_part = ''.join(secrets.choice(chars) for _ in range(32))
        return f"wp_{random_part}"
    
    @staticmethod
    def generate_api_secret() -> str:
        """Generate a secure API secret"""
        # 64 character secret
        chars = string.ascii_letters + string.digits + "!@#$%^&*"
        return ''.join(secrets.choice(chars) for _ in range(64))


class WordPressSiteAuth(BaseModel):
    """Model for WordPress site authentication"""
    api_key: str
    api_secret: Optional[str] = None  # Optional for backward compatibility
    
    
class WordPressSiteStats(BaseModel):
    """Statistics for a WordPress site"""
    site_id: str
    domain: str
    request_count: int
    last_seen_at: Optional[datetime]
    active_users: int
    total_conversations: int
    total_messages: int
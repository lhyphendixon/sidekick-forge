"""
Platform Client model for Sidekick Forge

This is a simplified client model for the platform database,
which stores client configurations differently than the full Client model.
"""
from typing import Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


class APIKeys(BaseModel):
    """API keys for various AI providers"""
    # LLM Providers
    openai_api_key: Optional[str] = None
    groq_api_key: Optional[str] = None
    deepinfra_api_key: Optional[str] = None
    replicate_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    
    # Embedding Providers
    novita_api_key: Optional[str] = None
    cohere_api_key: Optional[str] = None
    
    # Voice/Speech Providers
    deepgram_api_key: Optional[str] = None
    elevenlabs_api_key: Optional[str] = None
    cartesia_api_key: Optional[str] = None
    speechify_api_key: Optional[str] = None
    
    # Reranking Providers
    siliconflow_api_key: Optional[str] = None
    jina_api_key: Optional[str] = None


class PlatformClientSettings(BaseModel):
    """Simplified settings for platform clients"""
    api_keys: APIKeys = Field(default_factory=APIKeys)
    livekit_config: Optional[Dict[str, str]] = None
    additional_settings: Dict[str, Any] = Field(default_factory=dict)


class PlatformClient(BaseModel):
    """Platform client model for multi-tenant support"""
    id: str = Field(..., description="Unique client identifier (UUID)")
    name: str = Field(..., description="Client name")
    
    # Supabase credentials for the client's own database
    supabase_project_url: Optional[str] = Field(None, description="Client's Supabase project URL")
    supabase_service_role_key: Optional[str] = Field(None, description="Client's Supabase service role key")
    
    # Settings
    settings: PlatformClientSettings = Field(default_factory=PlatformClientSettings)
    
    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class PlatformClientCreate(BaseModel):
    """Model for creating a new platform client"""
    name: str = Field(..., description="Client name")
    supabase_project_url: str = Field(..., description="Client's Supabase project URL")
    supabase_service_role_key: str = Field(..., description="Client's Supabase service role key")
    settings: Optional[PlatformClientSettings] = None


class PlatformClientUpdate(BaseModel):
    """Model for updating a platform client"""
    name: Optional[str] = None
    supabase_project_url: Optional[str] = None
    supabase_service_role_key: Optional[str] = None
    settings: Optional[PlatformClientSettings] = None
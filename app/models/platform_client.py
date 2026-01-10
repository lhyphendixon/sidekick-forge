"""
Platform Client model for Sidekick Forge

This is a simplified client model for the platform database,
which stores client configurations differently than the full Client model.
"""
from typing import Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, field_serializer


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

    # Avatar/Video Providers
    bithuman_api_secret: Optional[str] = None
    bey_api_key: Optional[str] = None


class PlatformSupabaseConfig(BaseModel):
    """Minimal Supabase config for platform clients"""
    url: Optional[str] = None
    anon_key: Optional[str] = None
    service_role_key: Optional[str] = None


class PlatformClientSettings(BaseModel):
    """Simplified settings for platform clients"""
    api_keys: APIKeys = Field(default_factory=APIKeys)
    livekit_config: Optional[Dict[str, str]] = None
    additional_settings: Dict[str, Any] = Field(default_factory=dict)
    supabase: Optional[PlatformSupabaseConfig] = None


class PlatformClient(BaseModel):
    """Platform client model for multi-tenant support"""
    model_config = {"extra": "allow"}  # Allow dynamic attributes for compatibility
    
    id: str = Field(..., description="Unique client identifier (UUID)")
    name: str = Field(..., description="Client name")
    
    # Supabase credentials for the client's own database
    supabase_project_url: Optional[str] = Field(None, description="Client's Supabase project URL")
    supabase_url: Optional[str] = Field(None, description="Alias for supabase_project_url (compatibility)")
    supabase_service_role_key: Optional[str] = Field(None, description="Client's Supabase service role key")
    supabase_project_ref: Optional[str] = Field(None, description="Supabase project reference for management API calls")
    supabase_anon_key: Optional[str] = Field(None, description="Client's Supabase anon key")
    
    # Settings
    settings: PlatformClientSettings = Field(default_factory=PlatformClientSettings)
    
    provisioning_status: str = Field(default="ready", description="Onboarding status for this client")
    provisioning_error: Optional[str] = Field(None, description="Last provisioning error message, if any")
    schema_version: Optional[str] = Field(None, description="Latest schema version applied to the tenant database")
    provisioning_started_at: Optional[datetime] = Field(None, description="Timestamp when provisioning began")
    provisioning_completed_at: Optional[datetime] = Field(None, description="Timestamp when provisioning finished")
    auto_provision: bool = Field(default=False, description="Whether the client was provisioned automatically")

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    @field_serializer(
        'created_at',
        'updated_at',
        'provisioning_started_at',
        'provisioning_completed_at'
    )
    def serialize_datetimes(
        self,
        value: Optional[datetime],
        info
    ) -> Optional[str]:
        # Be defensive: Pydantic may pass a SerializationInfo object as "value"
        if isinstance(value, datetime):
            return value.isoformat()
        if hasattr(value, "isoformat"):
            try:
                return value.isoformat()
            except Exception:
                pass
        return value if value is None else str(value)


class PlatformClientCreate(BaseModel):
    """Model for creating a new platform client"""
    name: str = Field(..., description="Client name")
    supabase_project_url: Optional[str] = Field(None, description="Client's Supabase project URL")
    supabase_service_role_key: Optional[str] = Field(None, description="Client's Supabase service role key")
    auto_provision: bool = Field(default=False, description="Provision a new Supabase project automatically")
    settings: Optional[PlatformClientSettings] = None


class PlatformClientUpdate(BaseModel):
    """Model for updating a platform client"""
    name: Optional[str] = None
    supabase_project_url: Optional[str] = None
    supabase_service_role_key: Optional[str] = None
    settings: Optional[PlatformClientSettings] = None
    supabase_project_ref: Optional[str] = None
    supabase_anon_key: Optional[str] = None
    provisioning_status: Optional[str] = None
    provisioning_error: Optional[str] = None
    schema_version: Optional[str] = None
    auto_provision: Optional[bool] = None

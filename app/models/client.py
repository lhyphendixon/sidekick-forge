"""
Client model for multi-tenant support
"""
from typing import Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, field_validator, field_serializer


class SupabaseConfig(BaseModel):
    """Supabase configuration for a client"""
    url: str = Field(..., description="Supabase project URL")
    anon_key: str = Field(..., description="Supabase anonymous key")
    service_role_key: str = Field(..., description="Supabase service role key")
    
    @field_validator('url')
    def validate_url(cls, v):
        # Allow empty URLs for clients without Supabase configured
        if v and not v.startswith(('http://', 'https://')):
            raise ValueError('URL must start with http:// or https://')
        return v


class LiveKitConfig(BaseModel):
    """LiveKit configuration for a client"""
    server_url: str = Field(..., description="LiveKit server URL")
    api_key: str = Field(..., description="LiveKit API key")
    api_secret: str = Field(..., description="LiveKit API secret")
    
    @field_validator('server_url')
    def validate_url(cls, v):
        if not v.startswith(('http://', 'https://', 'wss://', 'ws://')):
            raise ValueError('URL must start with http://, https://, ws://, or wss://')
        return v


class APIKeys(BaseModel):
    """API keys for various AI providers"""
    # LLM Providers
    openai_api_key: Optional[str] = None
    groq_api_key: Optional[str] = None
    cerebras_api_key: Optional[str] = None
    deepinfra_api_key: Optional[str] = None
    replicate_api_key: Optional[str] = None
    
    # Embedding Providers
    novita_api_key: Optional[str] = None
    cohere_api_key: Optional[str] = None
    perplexity_api_key: Optional[str] = None

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


class EmbeddingSettings(BaseModel):
    """Embedding configuration"""
    provider: str = Field(default="novita", description="Embedding provider")
    document_model: str = Field(default="Qwen/Qwen2.5-72B-Instruct", description="Document embedding model")
    conversation_model: str = Field(default="Qwen/Qwen2.5-72B-Instruct", description="Conversation embedding model")
    dimension: Optional[int] = Field(default=None, description="Embedding dimension (e.g., 1024 for Qwen/Qwen3-Embedding-0.6B, 4096 for Qwen/Qwen3-Embedding-8B)")


class RerankSettings(BaseModel):
    """Reranking configuration"""
    enabled: bool = Field(default=False, description="Enable reranking")
    provider: Optional[str] = Field(default="siliconflow", description="Rerank provider")
    model: Optional[str] = Field(default="BAAI/bge-reranker-base", description="Rerank model")
    top_k: int = Field(default=3, description="Top K results to return")
    candidates: int = Field(default=20, description="Number of candidates to rerank")


class TelegramChannelSettings(BaseModel):
    """Telegram channel configuration for a client."""
    enabled: bool = Field(default=False, description="Enable Telegram channel for this client")
    bot_token: Optional[str] = Field(default=None, description="Bot token (optional override per client)")
    webhook_secret: Optional[str] = Field(default=None, description="Secret token to validate incoming webhooks")
    default_agent_slug: Optional[str] = Field(default=None, description="Default agent slug for Telegram")
    reply_mode: str = Field(default="auto", description="auto|text|voice_on_voice")
    transcribe_voice: bool = Field(default=True, description="Transcribe inbound voice notes to text for processing")


class ChannelSettings(BaseModel):
    """All channel configurations for a client."""
    telegram: TelegramChannelSettings = Field(default_factory=TelegramChannelSettings)


class ClientSettings(BaseModel):
    """All client-specific settings"""
    supabase: SupabaseConfig
    livekit: Optional[LiveKitConfig] = None
    api_keys: APIKeys = Field(default_factory=APIKeys)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    rerank: RerankSettings = Field(default_factory=RerankSettings)
    channels: ChannelSettings = Field(default_factory=ChannelSettings)
    performance_monitoring: bool = Field(default=False, description="Enable performance monitoring")
    license_key: Optional[str] = None


class Client(BaseModel):
    """Client model for multi-tenant support"""
    id: str = Field(..., description="Unique client identifier")
    slug: Optional[str] = Field(None, description="URL-friendly identifier", pattern=r"^[a-z0-9\-]+$")
    name: str = Field(..., description="Client name")
    description: Optional[str] = Field(None, description="Client description")
    domain: Optional[str] = Field(None, description="Client's primary domain")
    settings: ClientSettings = Field(..., description="Client-specific settings")
    active: bool = Field(default=True, description="Whether client is active")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    additional_settings: Dict[str, Any] = Field(default_factory=dict, description="Additional client-specific settings")
    perplexity_api_key: Optional[str] = Field(None, description="Perplexity API key at the client level")
    supertab_client_id: Optional[str] = Field(None, description="Supertab client ID for voice chat paywall")
    firecrawl_api_key: Optional[str] = Field(None, description="Firecrawl API key for web scraping in Knowledge Base")

    @field_serializer('created_at', 'updated_at')
    def serialize_datetimes(self, value: Optional[datetime], info) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        if hasattr(value, "isoformat"):
            try:
                return value.isoformat()
            except Exception:
                pass
        return value if value is None else str(value)


class ClientCreate(BaseModel):
    """Create a new client"""
    id: Optional[str] = Field(None, description="Optional client identifier UUID. If not provided, will be auto-generated")
    slug: str = Field(..., description="URL-friendly identifier", pattern=r"^[a-z0-9\-]+$")
    name: str = Field(..., description="Client name")
    description: Optional[str] = None
    domain: Optional[str] = None
    settings: ClientSettings
    perplexity_api_key: Optional[str] = None


class ClientUpdate(BaseModel):
    """Update client information"""
    slug: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    domain: Optional[str] = None
    settings: Optional[ClientSettings] = None
    active: Optional[bool] = None
    perplexity_api_key: Optional[str] = None
    usersense_enabled: Optional[bool] = None
    supertab_client_id: Optional[str] = Field(None, description="Supertab client ID for voice chat paywall")
    firecrawl_api_key: Optional[str] = Field(None, description="Firecrawl API key for web scraping")


class ClientInDB(Client):
    """Client stored in database"""
    pass

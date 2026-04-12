"""
Agent model for multi-tenant AI agent management
"""
from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, Field, field_serializer
from enum import Enum
from app.models.client import ChannelSettings  # Reuse channel schema


class ProviderType(str, Enum):
    """Voice provider types"""
    LIVEKIT = "livekit"
    OPENAI = "openai"
    ELEVENLABS = "elevenlabs"
    CARTESIA = "cartesia"
    DEEPGRAM = "deepgram"
    SPEECHIFY = "speechify"
    INWORLD = "inworld"
    FISH_AUDIO = "fish_audio"


class VoiceSettings(BaseModel):
    """Voice configuration settings"""
    provider: ProviderType = Field(default=ProviderType.LIVEKIT)
    voice_id: Optional[str] = Field(default="alloy", description="Voice ID for the provider")
    temperature: float = Field(default=0.7, ge=0.0, le=1.0, description="Creativity level")
    
    # LLM and STT settings
    llm_provider: Optional[str] = Field(None, description="LLM provider (openai, groq, etc)")
    llm_model: Optional[str] = Field(None, description="LLM model to use")
    stt_provider: Optional[str] = Field(None, description="STT provider (deepgram, groq, etc)")
    stt_language: Optional[str] = Field(default="en", description="STT language")
    
    # TTS provider setting (missing earlier; required for voice flow)
    tts_provider: Optional[str] = Field(None, description="TTS provider (openai, elevenlabs, cartesia, etc)")
    
    # TTS provider-specific settings
    model: Optional[str] = Field(None, description="TTS model (for providers that support multiple models)")
    output_format: Optional[str] = Field(None, description="Output format (for Cartesia)")
    stability: Optional[float] = Field(None, description="Voice stability (for ElevenLabs)")
    similarity_boost: Optional[float] = Field(None, description="Voice similarity boost (for ElevenLabs)")
    loudness_normalization: Optional[bool] = Field(None, description="Loudness normalization (for Speechify)")
    text_normalization: Optional[bool] = Field(None, description="Text normalization (for Speechify)")
    
    # Provider-specific settings
    provider_config: Dict[str, Any] = Field(default_factory=dict)
    cartesia_emotions_enabled: Optional[bool] = Field(
        default=False, description="Enable Cartesia Sonic-3 emotion tagging"
    )
    fish_emotions_enabled: Optional[bool] = Field(
        default=True, description="Enable Fish Audio emotion tags in LLM output for expressive TTS"
    )
    cartesia_emotion_style: Optional[str] = Field(
        default=None, description="Default emotion style for Cartesia Sonic-3"
    )
    cartesia_emotion_intensity: Optional[int] = Field(
        default=None, description="Default intensity (1-5) when using emotion tags"
    )
    cartesia_emotion_volume: Optional[str] = Field(
        default=None, description="Default volume hint for Cartesia Sonic-3 emotion tags"
    )
    cartesia_emotion_speed: Optional[str] = Field(
        default=None, description="Default speed hint for Cartesia Sonic-3 emotion tags"
    )

    # Avatar/Video settings
    avatar_provider: Optional[str] = Field(
        default=None, description="Avatar provider (bithuman, beyondpresence, liveavatar, ken_burns)"
    )
    avatar_model_path: Optional[str] = Field(
        default=None, description="Path to local IMX model file (Bithuman)"
    )
    avatar_image_url: Optional[str] = Field(
        default=None, description="URL to avatar image (legacy)"
    )
    avatar_model_type: Optional[str] = Field(
        default=None, description="Avatar model type for Bithuman (expression, etc)"
    )
    avatar_id: Optional[str] = Field(
        default=None, description="Avatar ID for Beyond Presence"
    )
    liveavatar_avatar_id: Optional[str] = Field(
        default=None, description="Avatar ID for HeyGen LiveAvatar"
    )
    video_provider: Optional[str] = Field(
        default=None, description="Video provider type (ken_burns, etc)"
    )
    kenburns_style: Optional[str] = Field(
        default=None, description="Ken Burns visual style preset"
    )
    kenburns_duration: Optional[int] = Field(
        default=None, description="Ken Burns animation duration in seconds"
    )
    kenburns_auto_interval: Optional[int] = Field(
        default=None, description="Ken Burns auto-generate interval in seconds"
    )
    kenburns_starting_image: Optional[str] = Field(
        default=None, description="Ken Burns starting image URL"
    )
    tts_speed: Optional[float] = Field(
        default=None, description="TTS playback speed"
    )

    class Config:
        extra = "allow"  # Allow extra fields not defined in the model


class WebhookSettings(BaseModel):
    """Webhook configuration"""
    voice_context_webhook_url: Optional[str] = None
    text_context_webhook_url: Optional[str] = None


class Agent(BaseModel):
    """Agent model for AI assistants"""
    id: Optional[str] = Field(None, description="Unique agent ID from Supabase")
    slug: str = Field(..., description="URL-friendly identifier", pattern="^[a-z0-9\\-]+$")
    name: str = Field(..., description="Agent display name")
    description: Optional[str] = Field(None, description="Agent description")
    client_id: str = Field(..., description="Client this agent belongs to")
    
    # Appearance
    agent_image: Optional[str] = Field(None, description="Background image URL for chat interface")
    
    # Behavior
    system_prompt: str = Field(..., description="System prompt defining agent behavior")
    
    # Voice settings
    voice_settings: VoiceSettings = Field(default_factory=VoiceSettings)
    
    # Webhooks
    webhooks: WebhookSettings = Field(default_factory=WebhookSettings)
    
    # Status
    enabled: bool = Field(default=True, description="Whether agent is active")
    
    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    channels: Optional[ChannelSettings] = Field(default=None, description="Per-agent channel settings")
    
    # Tools configuration (stored as JSON)
    tools_config: Optional[Dict[str, Any]] = Field(None, description="Agent-specific tools configuration")

    # Sound settings for voice/video chat (thinking sounds, ambient sounds)
    sound_settings: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Sound settings for thinking and ambient sounds")

    # Citations feature flag
    show_citations: bool = Field(default=True, description="Whether to show RAG citations in responses")

    # Email channel
    email_address: Optional[str] = Field(default=None, description="Sidekick email address (e.g. slug@sidekickforge.com)")

    # Chat mode feature flags
    text_chat_enabled: bool = Field(default=True, description="Whether text chat is enabled")
    voice_chat_enabled: bool = Field(default=True, description="Whether voice chat is enabled")
    video_chat_enabled: bool = Field(default=False, description="Whether video chat is enabled")

    # Generation model + context retention
    model: Optional[str] = Field(default="gpt-4o-mini", description="LLM model to use for responses")
    context_retention_minutes: Optional[int] = Field(default=30, description="How long to retain context for voice sessions")
    max_context_messages: Optional[int] = Field(default=50, description="Number of past messages to keep in short-term memory")
    rag_results_limit: Optional[int] = Field(default=5, description="Number of knowledge base results to include in RAG context")
    
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


class AgentCreate(BaseModel):
    """Create a new agent"""
    slug: str = Field(..., pattern="^[a-z0-9\\-]+$")
    name: str
    description: Optional[str] = None
    client_id: Optional[str] = None  # Made optional since it's set from URL
    agent_image: Optional[str] = None
    system_prompt: str
    voice_settings: Optional[VoiceSettings] = None
    webhooks: Optional[WebhookSettings] = None
    enabled: bool = True
    tools_config: Optional[Dict[str, Any]] = None
    show_citations: Optional[bool] = None
    model: Optional[str] = Field(default="gpt-4o-mini", description="LLM model to use for this agent")
    context_retention_minutes: Optional[int] = Field(default=30, description="How long to retain conversation context")
    max_context_messages: Optional[int] = Field(default=50, description="Max short-term memory length")
    rag_results_limit: Optional[int] = Field(default=5, description="Number of knowledge base results to include in RAG context")


class AgentUpdate(BaseModel):
    """Update agent information"""
    name: Optional[str] = None
    description: Optional[str] = None
    agent_image: Optional[str] = None
    system_prompt: Optional[str] = None
    voice_settings: Optional[VoiceSettings] = None
    webhooks: Optional[WebhookSettings] = None
    enabled: Optional[bool] = None
    tools_config: Optional[Dict[str, Any]] = None
    show_citations: Optional[bool] = None
    model: Optional[str] = None
    context_retention_minutes: Optional[int] = None
    max_context_messages: Optional[int] = None
    channels: Optional[ChannelSettings] = None
    rag_results_limit: Optional[int] = None
    email_address: Optional[str] = None
    # Chat mode toggles
    voice_chat_enabled: Optional[bool] = None
    text_chat_enabled: Optional[bool] = None
    video_chat_enabled: Optional[bool] = None


class AgentInDB(Agent):
    """Agent as stored in database"""
    pass


class AgentWithClient(Agent):
    """Agent with client information included"""
    client_name: str
    client_domain: Optional[str] = None

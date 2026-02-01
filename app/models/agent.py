"""
Agent model for multi-tenant AI agent management
"""
from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, Field, field_serializer, model_validator
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
    tts_speed: Optional[float] = Field(
        default=None,
        ge=0.5,
        le=2.0,
        description="TTS speech speed (0.5-2.0, 1.0 is normal). For Cartesia sonic-3: 0.6-2.0"
    )
    output_format: Optional[str] = Field(None, description="Output format (for Cartesia)")
    stability: Optional[float] = Field(None, description="Voice stability (for ElevenLabs)")
    similarity_boost: Optional[float] = Field(None, description="Voice similarity boost (for ElevenLabs)")
    loudness_normalization: Optional[bool] = Field(None, description="Loudness normalization (for Speechify)")
    text_normalization: Optional[bool] = Field(None, description="Text normalization (for Speechify)")
    
    # Provider-specific settings
    provider_config: Dict[str, Any] = Field(default_factory=dict)
    cartesia_emotions_enabled: Optional[bool] = Field(
        default=False, description="Enable Cartesia dynamic emotion expression - agent chooses emotions based on context"
    )

    # Video avatar settings
    avatar_provider: Optional[str] = Field(
        default="bithuman", description="Avatar provider: 'bithuman', 'beyondpresence', or 'liveavatar'"
    )
    avatar_image_url: Optional[str] = Field(
        default=None, description="DEPRECATED - Bithuman cloud mode removed. Use avatar_model_path instead."
    )
    avatar_model_path: Optional[str] = Field(
        default=None, description="Supabase storage path to .imx model file (format: supabase://bucket/path)"
    )
    avatar_model_type: Optional[str] = Field(
        default="expression", description="Avatar model type for Bithuman: 'expression' or 'essence'"
    )
    avatar_id: Optional[str] = Field(
        default=None, description="Avatar ID for Beyond Presence (pre-built avatar selection)"
    )
    liveavatar_avatar_id: Optional[str] = Field(
        default=None, description="Avatar ID for HeyGen LiveAvatar"
    )


class SoundSettings(BaseModel):
    """Sound configuration for voice/video chat"""
    thinking_sound: Optional[str] = Field(
        default=None, description="Sound to play during RAG/tool processing: 'keyboard', 'none', or null for silent"
    )
    thinking_volume: Optional[float] = Field(
        default=0.3, ge=0.0, le=1.0, description="Volume for thinking sound (0.0-1.0)"
    )
    ambient_sound: Optional[str] = Field(
        default="none", description="Background ambient sound: 'none', 'office', 'forest', 'city', 'crowded_room'"
    )
    ambient_volume: Optional[float] = Field(
        default=0.15, ge=0.0, le=1.0, description="Volume for ambient sound (0.0-1.0)"
    )


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

    # Sound settings for voice/video chat
    sound_settings: SoundSettings = Field(default_factory=SoundSettings)

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
    
    # Citations feature flag
    show_citations: bool = Field(default=True, description="Whether to show RAG citations in responses")
    
    # Generation model + context retention
    model: Optional[str] = Field(default="gpt-4o-mini", description="LLM model to use for responses")
    context_retention_minutes: Optional[int] = Field(default=30, description="How long to retain context for voice sessions")
    max_context_messages: Optional[int] = Field(default=50, description="Number of past messages to keep in short-term memory")
    rag_results_limit: Optional[int] = Field(default=5, description="Number of knowledge base results to include in RAG context")

    # Supertab paywall settings
    supertab_enabled: Optional[bool] = Field(default=False, description="Enable Supertab paywall for voice chat")
    supertab_experience_id: Optional[str] = Field(default=None, description="Supertab offering ID for pricing")

    # Chat mode settings
    voice_chat_enabled: bool = Field(default=True, description="Whether voice chat is enabled for this agent")
    text_chat_enabled: bool = Field(default=True, description="Whether text chat is enabled for this agent")
    video_chat_enabled: bool = Field(default=False, description="Whether video chat with avatar is enabled for this agent")

    @model_validator(mode='after')
    def validate_at_least_one_chat_mode(self):
        """Ensure at least one chat mode (voice, text, or video) is enabled."""
        if not self.voice_chat_enabled and not self.text_chat_enabled and not self.video_chat_enabled:
            raise ValueError("At least one chat mode (voice, text, or video) must be enabled")
        return self

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
    sound_settings: Optional[SoundSettings] = None
    webhooks: Optional[WebhookSettings] = None
    enabled: Optional[bool] = None
    tools_config: Optional[Dict[str, Any]] = None
    show_citations: Optional[bool] = None
    model: Optional[str] = None
    context_retention_minutes: Optional[int] = None
    max_context_messages: Optional[int] = None
    channels: Optional[ChannelSettings] = None
    rag_results_limit: Optional[int] = None
    supertab_enabled: Optional[bool] = Field(None, description="Enable Supertab paywall for voice chat")
    supertab_experience_id: Optional[str] = Field(None, description="Supertab experience ID for pricing")
    voice_chat_enabled: Optional[bool] = Field(None, description="Whether voice chat is enabled")
    text_chat_enabled: Optional[bool] = Field(None, description="Whether text chat is enabled")
    video_chat_enabled: Optional[bool] = Field(None, description="Whether video chat with avatar is enabled")


class AgentInDB(Agent):
    """Agent as stored in database"""
    pass


class AgentWithClient(Agent):
    """Agent with client information included"""
    client_name: str
    client_domain: Optional[str] = None

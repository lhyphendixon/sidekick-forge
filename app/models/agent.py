"""
Agent model for multi-tenant AI agent management
"""
from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, Field, validator
from enum import Enum


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
    
    # TTS provider-specific settings
    model: Optional[str] = Field(None, description="TTS model (for providers that support multiple models)")
    output_format: Optional[str] = Field(None, description="Output format (for Cartesia)")
    stability: Optional[float] = Field(None, description="Voice stability (for ElevenLabs)")
    similarity_boost: Optional[float] = Field(None, description="Voice similarity boost (for ElevenLabs)")
    loudness_normalization: Optional[bool] = Field(None, description="Loudness normalization (for Speechify)")
    text_normalization: Optional[bool] = Field(None, description="Text normalization (for Speechify)")
    
    # Provider-specific settings
    provider_config: Dict[str, Any] = Field(default_factory=dict)


class WebhookSettings(BaseModel):
    """Webhook configuration"""
    voice_context_webhook_url: Optional[str] = None
    text_context_webhook_url: Optional[str] = None


class RAGSettings(BaseModel):
    """RAG (Retrieval-Augmented Generation) configuration"""
    enabled: bool = Field(default=True, description="Enable RAG for this agent")
    embedding_provider: str = Field(default="siliconflow", description="Embedding provider (siliconflow, novita, openai)")
    document_embedding_model: str = Field(default="BAAI/bge-large-en-v1.5", description="Model for document embeddings")
    conversation_embedding_model: str = Field(default="BAAI/bge-large-en-v1.5", description="Model for conversation embeddings")
    embedding_dimension: Optional[int] = Field(default=1024, description="Embedding dimension")
    rerank_enabled: bool = Field(default=True, description="Enable reranking")
    rerank_provider: str = Field(default="siliconflow", description="Rerank provider")
    rerank_model: str = Field(default="BAAI/bge-reranker-v2-m3", description="Rerank model")
    search_limit: int = Field(default=5, description="Number of documents to retrieve")
    conversation_window: int = Field(default=50, description="Number of recent messages to consider")


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
    
    # RAG settings
    rag_settings: RAGSettings = Field(default_factory=RAGSettings)
    
    # Status
    enabled: bool = Field(default=True, description="Whether agent is active")
    
    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Tools configuration (stored as JSON)
    tools_config: Optional[Dict[str, Any]] = Field(None, description="Agent-specific tools configuration")
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class AgentCreate(BaseModel):
    """Create a new agent"""
    slug: str = Field(..., pattern="^[a-z0-9\\-]+$")
    name: str
    description: Optional[str] = None
    client_id: str
    agent_image: Optional[str] = None
    system_prompt: str
    voice_settings: Optional[VoiceSettings] = None
    webhooks: Optional[WebhookSettings] = None
    enabled: bool = True
    tools_config: Optional[Dict[str, Any]] = None


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


class AgentInDB(Agent):
    """Agent as stored in database"""
    pass


class AgentWithClient(Agent):
    """Agent with client information included"""
    client_name: str
    client_domain: Optional[str] = None
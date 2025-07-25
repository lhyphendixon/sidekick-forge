import os
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class AgentConfig:
    """Configuration for the client agent, loaded from environment variables."""
    # Site and container identification
    site_id: Optional[str] = field(default_factory=lambda: os.getenv("SITE_ID"))
    container_name: Optional[str] = field(default_factory=lambda: os.getenv("CONTAINER_NAME"))

    # LiveKit connection details
    livekit_url: Optional[str] = field(default_factory=lambda: os.getenv("LIVEKIT_URL"))
    livekit_api_key: Optional[str] = field(default_factory=lambda: os.getenv("LIVEKIT_API_KEY"))
    livekit_api_secret: Optional[str] = field(default_factory=lambda: os.getenv("LIVEKIT_API_SECRET"))

    # Agent personality and behavior
    agent_slug: Optional[str] = field(default_factory=lambda: os.getenv("AGENT_SLUG"))
    agent_name: Optional[str] = field(default_factory=lambda: os.getenv("AGENT_NAME"))
    system_prompt: Optional[str] = field(default_factory=lambda: os.getenv("SYSTEM_PROMPT", "You are a friendly voice assistant."))
    
    # Language model configuration
    model: Optional[str] = field(default_factory=lambda: os.getenv("MODEL", "gpt-4-turbo-preview"))
    temperature: float = field(default_factory=lambda: float(os.getenv("TEMPERATURE", 0.7)))
    max_tokens: int = field(default_factory=lambda: int(os.getenv("MAX_TOKENS", 4096)))

    # Voice configuration
    voice_id: Optional[str] = field(default_factory=lambda: os.getenv("VOICE_ID", "alloy"))
    stt_provider: Optional[str] = field(default_factory=lambda: os.getenv("STT_PROVIDER", "deepgram"))
    stt_model: Optional[str] = field(default_factory=lambda: os.getenv("STT_MODEL"))
    tts_provider: Optional[str] = field(default_factory=lambda: os.getenv("TTS_PROVIDER", "openai"))
    tts_model: Optional[str] = field(default_factory=lambda: os.getenv("TTS_MODEL"))

    # API Keys for various services
    openai_api_key: Optional[str] = field(default_factory=lambda: os.getenv("OPENAI_API_KEY"))
    groq_api_key: Optional[str] = field(default_factory=lambda: os.getenv("GROQ_API_KEY"))
    elevenlabs_api_key: Optional[str] = field(default_factory=lambda: os.getenv("ELEVENLABS_API_KEY"))
    deepgram_api_key: Optional[str] = field(default_factory=lambda: os.getenv("DEEPGRAM_API_KEY"))

    # Webhooks for context
    voice_context_webhook_url: Optional[str] = field(default_factory=lambda: os.getenv("VOICE_CONTEXT_WEBHOOK_URL"))
    text_context_webhook_url: Optional[str] = field(default_factory=lambda: os.getenv("TEXT_CONTEXT_WEBHOOK_URL"))

    # Backend communication
    backend_url: Optional[str] = field(default_factory=lambda: os.getenv("BACKEND_URL"))
    backend_api_key: Optional[str] = field(default_factory=lambda: os.getenv("BACKEND_API_KEY"))

    # Monitoring and logging
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    enable_metrics: bool = field(default_factory=lambda: os.getenv("ENABLE_METRICS", "true").lower() == "true")

    def validate(self):
        """Validate required configuration"""
        required_fields = [
            "site_id", "agent_slug", "livekit_url", 
            "livekit_api_key", "livekit_api_secret"
        ]
        
        missing = []
        for field in required_fields:
            if not getattr(self, field):
                missing.append(field)
        
        if missing:
            raise ValueError(f"Missing required configuration: {', '.join(missing)}")
        
        # Validate API keys based on providers
        if self.stt_provider == "groq" and not self.groq_api_key:
            raise ValueError("Groq API key required for Groq STT")
        
        if self.tts_provider == "openai" and not self.openai_api_key:
            raise ValueError("OpenAI API key required for OpenAI TTS")
        
        if self.model.startswith("gpt") and not self.openai_api_key:
            raise ValueError("OpenAI API key required for GPT models")
        
        if self.model.startswith("claude") and not self.anthropic_api_key:
            raise ValueError("Anthropic API key required for Claude models")
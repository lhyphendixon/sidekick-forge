import os
from dataclasses import dataclass
from typing import Optional

@dataclass
class AgentConfig:
    """Configuration for client-specific agent container"""
    
    # Client identification
    site_id: str = os.getenv("SITE_ID", "")
    site_domain: str = os.getenv("SITE_DOMAIN", "")
    agent_slug: str = os.getenv("AGENT_SLUG", "")
    container_name: str = os.getenv("CONTAINER_NAME", "")
    
    # LiveKit configuration
    livekit_url: str = os.getenv("LIVEKIT_URL", "")
    livekit_api_key: str = os.getenv("LIVEKIT_API_KEY", "")
    livekit_api_secret: str = os.getenv("LIVEKIT_API_SECRET", "")
    
    # Agent configuration
    agent_name: str = os.getenv("AGENT_NAME", "Assistant")
    system_prompt: str = os.getenv("SYSTEM_PROMPT", "You are a helpful assistant.")
    model: str = os.getenv("MODEL", "gpt-4-turbo-preview")
    temperature: float = float(os.getenv("TEMPERATURE", "0.7"))
    max_tokens: int = int(os.getenv("MAX_TOKENS", "4096"))
    
    # Voice configuration
    voice_id: str = os.getenv("VOICE_ID", "alloy")
    stt_provider: str = os.getenv("STT_PROVIDER", "groq")
    stt_model: str = os.getenv("STT_MODEL", "whisper-large-v3-turbo")
    tts_provider: str = os.getenv("TTS_PROVIDER", "openai")
    tts_model: str = os.getenv("TTS_MODEL", "tts-1")
    
    # API Keys
    openai_api_key: Optional[str] = os.getenv("OPENAI_API_KEY")
    anthropic_api_key: Optional[str] = os.getenv("ANTHROPIC_API_KEY")
    groq_api_key: Optional[str] = os.getenv("GROQ_API_KEY")
    elevenlabs_api_key: Optional[str] = os.getenv("ELEVENLABS_API_KEY")
    cartesia_api_key: Optional[str] = os.getenv("CARTESIA_API_KEY")
    deepgram_api_key: Optional[str] = os.getenv("DEEPGRAM_API_KEY")
    
    # Webhooks
    voice_context_webhook_url: Optional[str] = os.getenv("VOICE_CONTEXT_WEBHOOK_URL")
    text_context_webhook_url: Optional[str] = os.getenv("TEXT_CONTEXT_WEBHOOK_URL")
    
    # Backend communication
    backend_url: str = os.getenv("BACKEND_URL", "http://fastapi:8000")
    backend_api_key: str = os.getenv("BACKEND_API_KEY", "")
    
    # Monitoring
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    enable_metrics: bool = os.getenv("ENABLE_METRICS", "true").lower() == "true"
    
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
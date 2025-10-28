from pydantic_settings import BaseSettings
from pydantic import validator, Field
from typing import List, Optional
import os
import json
from pathlib import Path
from dotenv import dotenv_values

class Settings(BaseSettings):
    # Application Settings
    app_name: str = Field(default="sidekick-forge", env="APP_NAME")
    platform_name: str = Field(default="Sidekick Forge", env="PLATFORM_NAME")
    app_env: str = Field(default="production", env="APP_ENV")
    debug: bool = Field(default=False, env="DEBUG")
    log_level: str = Field(default="INFO", env="LOG_LEVEL")
    
    # API Configuration
    api_host: str = Field(default="0.0.0.0", env="API_HOST")
    api_port: int = Field(default=8000, env="API_PORT")
    api_workers: int = Field(default=4, env="API_WORKERS")
    
    # Security
    secret_key: str = Field(default="dev-secret-key", env="SECRET_KEY")
    jwt_secret_key: str = Field(default="dev-jwt-secret", env="JWT_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", env="JWT_ALGORITHM")
    jwt_expiration_minutes: int = Field(default=1440, env="JWT_EXPIRATION_MINUTES")
    
    # Supabase Configuration (CRITICAL: Both service and anon keys needed)
    # IMPORTANT: No defaults - must be loaded from environment to avoid credential mismatches
    supabase_url: str = Field(..., env="SUPABASE_URL")
    supabase_service_role_key: str = Field(..., env="SUPABASE_SERVICE_ROLE_KEY")
    supabase_anon_key: str = Field(..., env="SUPABASE_ANON_KEY")
    
    # Supabase Auth Configuration
    supabase_auth_enabled: bool = Field(default=True, env="SUPABASE_AUTH_ENABLED")
    supabase_jwt_secret: str = Field(default="demo-jwt", env="SUPABASE_JWT_SECRET")
    
    # Database (using Supabase)
    database_url: Optional[str] = Field(None, env="DATABASE_URL")
    
    # LiveKit Configuration (primary platform)
    # IMPORTANT: No defaults - credentials are loaded dynamically from database if not in env
    livekit_url: Optional[str] = Field(None, env="LIVEKIT_URL")
    livekit_api_key: Optional[str] = Field(None, env="LIVEKIT_API_KEY")
    livekit_api_secret: Optional[str] = Field(None, env="LIVEKIT_API_SECRET")
    livekit_agent_name: str = Field(default="sidekick-agent", env=["LIVEKIT_AGENT_NAME", "AGENT_NAME"])
    
    # AI Provider API Keys
    openai_api_key: Optional[str] = Field(None, env="OPENAI_API_KEY")
    anthropic_api_key: Optional[str] = Field(None, env="ANTHROPIC_API_KEY")
    groq_api_key: Optional[str] = Field(None, env="GROQ_API_KEY")
    
    # Voice Provider API Keys
    elevenlabs_api_key: Optional[str] = Field(None, env="ELEVENLABS_API_KEY")
    cartesia_api_key: Optional[str] = Field(None, env="CARTESIA_API_KEY")
    deepgram_api_key: Optional[str] = Field(None, env="DEEPGRAM_API_KEY")
    
    # Tool Webhooks (n8n integration)
    n8n_text_webhook_url: Optional[str] = Field(None, env="N8N_TEXT_WEBHOOK_URL")
    n8n_rag_webhook_url: Optional[str] = Field(None, env="N8N_RAG_WEBHOOK_URL")
    
    # Redis Configuration
    redis_host: str = Field(default="localhost", env="REDIS_HOST")
    redis_port: int = Field(default=6379, env="REDIS_PORT")
    redis_db: int = Field(default=0, env="REDIS_DB")
    
    # CORS Settings
    cors_allowed_origins: List[str] = Field(
        default=["http://localhost:3000"],
        env="CORS_ALLOWED_ORIGINS"
    )
    
    # Rate Limiting
    rate_limit_per_minute: int = Field(default=60, env="RATE_LIMIT_PER_MINUTE")
    rate_limit_per_hour: int = Field(default=1000, env="RATE_LIMIT_PER_HOUR")
    
    # Feature Flags
    enable_transcripts: bool = Field(default=True, env="ENABLE_TRANSCRIPTS")
    enable_supabase: bool = Field(default=True, env="ENABLE_SUPABASE")
    benchmark_enabled: bool = Field(default=False, env="BENCHMARK_ENABLED")
    performance_monitoring: bool = Field(default=False, env="PERFORMANCE_MONITORING")
    
    # SSL/Domain Configuration
    domain_name: str = Field(env="DOMAIN_NAME")  # Required - no default
    ssl_email: str = Field(default="admin@sidekickforge.com", env="SSL_EMAIL")
    
    # Monitoring
    sentry_dsn: Optional[str] = Field(None, env="SENTRY_DSN")
    prometheus_enabled: bool = Field(default=False, env="PROMETHEUS_ENABLED")

    # Mailjet transactional email
    mailjet_api_key: Optional[str] = Field(default=None, env="MAILJET_API_KEY")
    mailjet_api_secret: Optional[str] = Field(default=None, env="MAILJET_API_SECRET")
    mailjet_sender_email: Optional[str] = Field(default=None, env="MAILJET_SENDER_EMAIL")
    mailjet_sender_name: Optional[str] = Field(default=None, env="MAILJET_SENDER_NAME")
    mailjet_notification_recipients_raw: Optional[str] = Field(default=None, env="MAILJET_NOTIFICATION_RECIPIENTS")

    # Perplexity MCP container configuration
    perplexity_mcp_image: str = Field(default="perplexity-mcp:latest", env="PERPLEXITY_MCP_IMAGE")
    perplexity_mcp_container_name: str = Field(default="perplexity-mcp", env="PERPLEXITY_MCP_CONTAINER_NAME")
    perplexity_mcp_port: int = Field(default=8081, env="PERPLEXITY_MCP_PORT")
    perplexity_mcp_host: str = Field(default="perplexity-mcp", env="PERPLEXITY_MCP_HOST")
    perplexity_mcp_network: Optional[str] = Field(default=None, env="PERPLEXITY_MCP_NETWORK")

    # Asana OAuth configuration
    asana_oauth_client_id: Optional[str] = Field(default=None, env="ASANA_OAUTH_CLIENT_ID")
    asana_oauth_client_secret: Optional[str] = Field(default=None, env="ASANA_OAUTH_CLIENT_SECRET")
    asana_oauth_redirect_uri: Optional[str] = Field(default=None, env="ASANA_OAUTH_REDIRECT_URI")
    asana_oauth_scopes: str = Field(default="default", env="ASANA_OAUTH_SCOPES")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # Ignore extra fields to prevent validation errors
        
    @validator('app_name', pre=True, always=True)
    def normalize_app_name(cls, v):
        if isinstance(v, str) and v.strip():
            return v.strip()
        return "sidekick-forge"

    @validator('perplexity_mcp_network', pre=True, always=True)
    def normalize_perplexity_network(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @validator('supabase_anon_key')
    def validate_supabase_anon_key(cls, v):
        if not v:
            raise ValueError('Supabase anon key is required for Supabase Auth')
        return v
    
    @validator('cors_allowed_origins', pre=True)
    def parse_cors_origins(cls, v):
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(',')]
        return v
    
    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def perplexity_mcp_network_name(self) -> str:
        if self.perplexity_mcp_network:
            return self.perplexity_mcp_network
        return f"{self.app_name}-network"

    @property
    def perplexity_mcp_server_url(self) -> str:
        return f"http://{self.perplexity_mcp_host}:{self.perplexity_mcp_port}/mcp/sse"

    @property
    def mailjet_is_configured(self) -> bool:
        recipients = self.mailjet_notification_recipients
        return all(
            [
                self.mailjet_api_key,
                self.mailjet_api_secret,
                self.mailjet_sender_email,
                recipients,
            ]
        )

    @staticmethod
    def _coerce_recipient_list(value: Optional[str]) -> List[str]:
        if not value:
            return []
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            except json.JSONDecodeError:
                pass
        return [item.strip() for item in raw.split(",") if item.strip()]

    @property
    def mailjet_notification_recipients(self) -> List[str]:
        raw = self.mailjet_notification_recipients_raw
        if not raw:
            raw = os.getenv("MAILJET_NOTIFICATION_RECIPIENTS")
        if not raw:
            env_file = getattr(self.Config, "env_file", ".env")
            try:
                path = Path(env_file)
                if not path.is_absolute():
                    path = Path(os.getcwd()) / path
                if path.exists():
                    raw = dotenv_values(path).get("MAILJET_NOTIFICATION_RECIPIENTS")
            except Exception:
                raw = None
        return self._coerce_recipient_list(raw)

# Create settings instance
settings = Settings()

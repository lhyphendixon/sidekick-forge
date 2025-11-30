from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator, Field, AliasChoices
from typing import List, Optional
import os
import json
from pathlib import Path
from dotenv import dotenv_values

class Settings(BaseSettings):
    # Application Settings
    app_name: str = Field(default="sidekick-forge")
    platform_name: str = Field(default="Sidekick Forge")
    app_env: str = Field(default="production")
    debug: bool = Field(default=False)
    log_level: str = Field(default="INFO")
    
    # API Configuration
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)
    api_workers: int = Field(default=4)
    
    # Security
    secret_key: str = Field(default="dev-secret-key")
    jwt_secret_key: str = Field(default="dev-jwt-secret")
    jwt_algorithm: str = Field(default="HS256")
    jwt_expiration_minutes: int = Field(default=1440)
    wordpress_bridge_secret: Optional[str] = Field(default=None)
    wordpress_bridge_max_skew: int = Field(default=300)
    
    # Supabase Configuration (CRITICAL: Both service and anon keys needed)
    # IMPORTANT: No defaults - must be loaded from environment to avoid credential mismatches
    supabase_url: str = Field(...)
    supabase_service_role_key: str = Field(...)
    supabase_anon_key: str = Field(...)
    
    # Supabase Auth Configuration
    supabase_auth_enabled: bool = Field(default=True)
    supabase_jwt_secret: str = Field(default="demo-jwt")
    
    # Database (using Supabase)
    database_url: Optional[str] = Field(None)
    
    # LiveKit Configuration (primary platform)
    # IMPORTANT: No defaults - credentials are loaded dynamically from database if not in env
    livekit_url: Optional[str] = Field(None)
    livekit_api_key: Optional[str] = Field(None)
    livekit_api_secret: Optional[str] = Field(None)
    livekit_agent_name: str = Field(
        default="sidekick-agent",
        validation_alias=AliasChoices("LIVEKIT_AGENT_NAME", "AGENT_NAME"),
    )
    
    # AI Provider API Keys
    openai_api_key: Optional[str] = Field(None)
    anthropic_api_key: Optional[str] = Field(None)
    groq_api_key: Optional[str] = Field(None)
    
    # Voice Provider API Keys
    elevenlabs_api_key: Optional[str] = Field(None)
    cartesia_api_key: Optional[str] = Field(None)
    deepgram_api_key: Optional[str] = Field(None)
    
    # Tool Webhooks (n8n integration)
    n8n_text_webhook_url: Optional[str] = Field(None)
    n8n_rag_webhook_url: Optional[str] = Field(None)
    
    # Redis Configuration
    redis_host: str = Field(default="localhost")
    redis_port: int = Field(default=6379)
    redis_db: int = Field(default=0)
    
    # CORS Settings
    cors_allowed_origins: List[str] = Field(
        default=["http://localhost:3000"]
    )
    
    # Rate Limiting
    rate_limit_per_minute: int = Field(default=60)
    rate_limit_per_hour: int = Field(default=1000)
    
    # Feature Flags
    enable_transcripts: bool = Field(default=True)
    enable_supabase: bool = Field(default=True)
    benchmark_enabled: bool = Field(default=False)
    performance_monitoring: bool = Field(default=False)
    enable_livekit_text_dispatch: bool = Field(default=True)
    
    # SSL/Domain Configuration
    domain_name: str = Field(...)
    ssl_email: str = Field(default="admin@sidekickforge.com")
    
    # Monitoring
    sentry_dsn: Optional[str] = Field(None)
    prometheus_enabled: bool = Field(default=False)

    # Mailjet transactional email
    mailjet_api_key: Optional[str] = Field(default=None)
    mailjet_api_secret: Optional[str] = Field(default=None)
    mailjet_sender_email: Optional[str] = Field(default=None)
    mailjet_sender_name: Optional[str] = Field(default=None)
    mailjet_notification_recipients_raw: Optional[str] = Field(default=None)

    # Perplexity MCP container configuration
    perplexity_mcp_image: str = Field(default="perplexity-mcp:latest")
    perplexity_mcp_container_name: str = Field(default="perplexity-mcp")
    perplexity_mcp_port: int = Field(default=8081)
    perplexity_mcp_host: str = Field(default="perplexity-mcp")
    perplexity_mcp_network: Optional[str] = Field(default=None)

    # Asana OAuth configuration
    asana_oauth_client_id: Optional[str] = Field(default=None)
    asana_oauth_client_secret: Optional[str] = Field(default=None)
    asana_oauth_redirect_uri: Optional[str] = Field(default=None)
    asana_oauth_scopes: str = Field(default="default")
    asana_token_preferred_store: str = Field(default="platform")
    asana_token_mirror_stores: bool = Field(default=False)
    asana_token_refresh_margin_seconds: int = Field(default=300)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
        
    @field_validator('app_name', mode='before')
    def normalize_app_name(cls, v):
        if isinstance(v, str) and v.strip():
            return v.strip()
        return "sidekick-forge"

    @field_validator('perplexity_mcp_network', mode='before')
    def normalize_perplexity_network(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator('asana_token_preferred_store', mode='before')
    def normalize_asana_token_store(cls, v):
        if not isinstance(v, str):
            return "platform"
        candidate = v.strip().lower()
        if candidate not in {"platform", "primary"}:
            return "platform"
        return candidate

    @field_validator('asana_token_refresh_margin_seconds', mode='before')
    def clamp_refresh_margin(cls, v):
        try:
            value = int(v)
        except (TypeError, ValueError):
            return 300
        return max(0, value)

    @field_validator('supabase_anon_key')
    def validate_supabase_anon_key(cls, v):
        if not v:
            raise ValueError('Supabase anon key is required for Supabase Auth')
        return v
    
    @field_validator('cors_allowed_origins', mode='before')
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

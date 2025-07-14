from pydantic_settings import BaseSettings
from pydantic import validator, Field
from typing import List, Optional
import os

class Settings(BaseSettings):
    # Application Settings
    app_name: str = "autonomite-saas"
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
    supabase_url: str = Field(default="https://yuowazxcxwhczywurmmw.supabase.co", env="SUPABASE_URL")
    supabase_service_role_key: str = Field(default="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY", env="SUPABASE_SERVICE_KEY")
    supabase_anon_key: str = Field(default="demo-anon", env="SUPABASE_ANON_KEY")
    
    # Supabase Auth Configuration
    supabase_auth_enabled: bool = Field(default=False, env="SUPABASE_AUTH_ENABLED")
    supabase_jwt_secret: str = Field(default="demo-jwt", env="SUPABASE_JWT_SECRET")
    
    # Database (using Supabase)
    database_url: Optional[str] = Field(None, env="DATABASE_URL")
    
    # LiveKit Configuration (primary platform)
    livekit_url: str = Field(default="wss://demo.livekit.cloud", env="LIVEKIT_URL")
    livekit_api_key: str = Field(default="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY", env="LIVEKIT_API_KEY")
    livekit_api_secret: str = Field(default="demo-secret", env="LIVEKIT_API_SECRET")
    
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
    domain_name: str = Field(default="agents.autonomite.net", env="DOMAIN_NAME")
    ssl_email: str = Field(default="admin@autonomite.net", env="SSL_EMAIL")
    
    # Monitoring
    sentry_dsn: Optional[str] = Field(None, env="SENTRY_DSN")
    prometheus_enabled: bool = Field(default=False, env="PROMETHEUS_ENABLED")
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # Ignore extra fields to prevent validation errors
        
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

# Create settings instance
settings = Settings()
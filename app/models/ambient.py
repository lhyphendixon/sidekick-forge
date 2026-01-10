"""
Models for Ambient Abilities - background processes that run after sessions or on schedule.
"""

from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from uuid import UUID


class ExecutionPhase(str, Enum):
    """When an ability runs"""
    ACTIVE = "active"      # During conversation (called by LLM)
    AMBIENT = "ambient"    # Background (post-session, scheduled)


class AmbientTriggerType(str, Enum):
    """What triggers an ambient ability"""
    POST_SESSION = "post_session"  # After conversation ends
    SCHEDULED = "scheduled"        # Cron-based schedule


class AmbientRunStatus(str, Enum):
    """Status of an ambient ability run"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TriggerConfig(BaseModel):
    """Configuration for ambient ability triggers"""
    trigger: AmbientTriggerType
    min_messages: Optional[int] = Field(default=3, description="Minimum messages for post_session trigger")
    delay_seconds: Optional[int] = Field(default=30, description="Delay before execution")
    agents: Optional[List[str]] = Field(default=None, description="Specific agent slugs (null = all)")
    cron: Optional[str] = Field(default=None, description="Cron expression for scheduled triggers")
    timezone: Optional[str] = Field(default="UTC", description="Timezone for scheduled triggers")

    class Config:
        use_enum_values = True


class AmbientAbilityConfig(BaseModel):
    """Configuration specific to an ambient ability"""
    model: Optional[str] = Field(default="claude-sonnet-4-20250514", description="LLM model to use")
    max_tokens: Optional[int] = Field(default=1500, description="Max tokens for LLM response")
    webhook_url: Optional[str] = Field(default=None, description="Webhook URL for webhook type")
    webhook_timeout: Optional[int] = Field(default=30, description="Webhook timeout in seconds")
    reflection_prompt_template: Optional[str] = Field(default=None, description="Template name for reflection prompt")


class AmbientAbilityRun(BaseModel):
    """A single execution of an ambient ability"""
    id: UUID
    ability_id: UUID
    ability_slug: Optional[str] = None
    ability_type: Optional[str] = None
    ability_config: Optional[Dict[str, Any]] = None
    trigger_config: Optional[TriggerConfig] = None
    client_id: UUID
    user_id: Optional[UUID] = None
    conversation_id: Optional[UUID] = None
    session_id: Optional[UUID] = None
    trigger_type: str
    status: AmbientRunStatus = AmbientRunStatus.PENDING
    input_context: Optional[Dict[str, Any]] = None
    output_result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    notification_shown: bool = False
    notification_message: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        use_enum_values = True


class AmbientAbilityRunCreate(BaseModel):
    """Request to create/queue an ambient ability run"""
    ability_id: UUID
    client_id: UUID
    user_id: Optional[UUID] = None
    conversation_id: Optional[UUID] = None
    session_id: Optional[UUID] = None
    trigger_type: AmbientTriggerType
    input_context: Optional[Dict[str, Any]] = None
    notification_message: Optional[str] = None

    class Config:
        use_enum_values = True


class QueuePostSessionRequest(BaseModel):
    """Request to queue post-session ambient abilities"""
    client_id: str
    user_id: str
    conversation_id: str
    session_id: Optional[str] = None
    message_count: int
    agent_slug: Optional[str] = None


class AmbientNotification(BaseModel):
    """Notification to show user about ambient ability completion"""
    id: UUID
    ability_slug: str
    notification_message: str
    output_result: Optional[Dict[str, Any]] = None
    completed_at: datetime


class UserOverviewUpdate(BaseModel):
    """A single update to apply to user overview"""
    section: str  # identity, goals, working_style, important_context, relationship_history
    action: str   # set, append, remove
    key: str
    value: Any


class UserSenseResult(BaseModel):
    """Result from UserSense reflection"""
    updates: List[UserOverviewUpdate] = Field(default_factory=list)
    summary: str = Field(default="", description="Brief description of what was learned")
    sections_updated: List[str] = Field(default_factory=list)
    confidence: Optional[float] = Field(default=None, description="Confidence in updates (0-1)")

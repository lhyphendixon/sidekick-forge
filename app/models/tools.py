from __future__ import annotations

from typing import Optional, Literal, Any, Dict, List
from pydantic import BaseModel, Field


ToolType = Literal["mcp", "n8n", "sidekick", "code"]
ToolScope = Literal["global", "client"]


class ToolBase(BaseModel):
    name: str = Field(..., description="Human-readable name of the tool")
    slug: str = Field(..., description="URL-safe slug; unique within scope")
    description: Optional[str] = Field(None, description="What this tool does")
    type: ToolType
    scope: ToolScope = Field("client", description="Tool scope: global or client")
    icon_url: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict, description="Type-specific configuration")
    enabled: bool = True


class ToolCreate(ToolBase):
    client_id: Optional[str] = Field(None, description="Client ID for client-scoped tools; null for global")


class ToolUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    icon_url: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None
    scope: Optional[ToolScope] = None


class ToolOut(ToolBase):
    id: str
    client_id: Optional[str] = None


class ToolAssignmentRequest(BaseModel):
    tool_ids: List[str] = Field(..., description="List of tool IDs to assign to the agent (replaces existing)")


class ToolExecutionLog(BaseModel):
    id: str
    tool_id: str
    agent_id: str
    conversation_id: Optional[str]
    user_id: Optional[str]
    status: Literal["success", "error", "timeout"]
    request: Optional[Dict[str, Any]]
    response: Optional[Dict[str, Any]]
    error: Optional[str]
    duration_ms: Optional[int]



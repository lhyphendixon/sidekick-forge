from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime
from uuid import UUID

class ContainerDeployRequest(BaseModel):
    """Request to deploy an agent container"""
    agent_slug: str
    cpu_limit: float = Field(default=1.0, ge=0.1, le=4.0)
    memory_limit: str = Field(default="1g", pattern="^[0-9]+[mg]$")
    
class ContainerInfo(BaseModel):
    """Container information"""
    id: str
    name: str
    status: str
    created: str
    labels: Dict[str, str]
    stats: Dict[str, Any]
    health: Dict[str, Any]

class ContainerListItem(BaseModel):
    """Container list item"""
    name: str
    status: str
    agent_slug: Optional[str]
    created_at: Optional[str]

class ContainerStats(BaseModel):
    """Container resource statistics"""
    cpu_percent: float
    memory_usage_mb: float
    memory_limit_mb: float
    memory_percent: float

class ContainerHealth(BaseModel):
    """Container health status"""
    status: str = Field(..., pattern="^(starting|healthy|unhealthy|none|unknown)$")
    failing_streak: int = 0

class ContainerLogsRequest(BaseModel):
    """Request for container logs"""
    lines: int = Field(default=100, ge=1, le=1000)
    since: Optional[datetime] = None

class ContainerScaleRequest(BaseModel):
    """Request to scale container resources"""
    cpu_limit: float = Field(default=1.0, ge=0.1, le=4.0)
    memory_limit: str = Field(default="1g", pattern="^[0-9]+[mg]$")

class ClientContainerStatus(BaseModel):
    """Overall container status for a client"""
    site_id: str
    total_containers: int
    running_containers: int
    stopped_containers: int
    total_cpu_usage: float
    total_memory_usage_mb: float
    containers: List[ContainerListItem]
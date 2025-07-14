from pydantic import BaseModel, Field, validator
from typing import Optional, Dict, List, Any
from datetime import datetime
from uuid import UUID

class ToolDefinition(BaseModel):
    """Tool definition following OpenAI function calling schema"""
    name: str = Field(..., min_length=1, max_length=100)
    description: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    
    @validator('parameters')
    def validate_parameters(cls, v):
        # Ensure parameters follow JSON Schema format
        if v and 'type' not in v:
            v['type'] = 'object'
        return v
    
    class Config:
        schema_extra = {
            "example": {
                "name": "get_weather",
                "description": "Get current weather for a location",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "City and state"
                        }
                    },
                    "required": ["location"]
                }
            }
        }

class ToolConfigurationBase(BaseModel):
    """Base tool configuration model"""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    category: str = Field(default="custom", pattern="^(custom|builtin|integration)$")
    tool_type: str = Field(default="api", pattern="^(api|webhook|function)$")
    enabled: bool = False
    configuration: Dict[str, Any] = Field(default_factory=dict)
    tool_definition: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)

class ToolConfiguration(ToolConfigurationBase):
    """Tool configuration model matching production autonomite_tools table"""
    id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    created_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True

class AgentToolConfiguration(BaseModel):
    """Agent-specific tool configuration"""
    id: Optional[UUID] = None
    agent_id: UUID
    tool_id: UUID
    enabled: bool = True
    configuration_override: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True

class ToolCreateRequest(ToolConfigurationBase):
    """Request model for creating a tool"""
    webhook_url: Optional[str] = None
    api_key: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    
    @validator('tool_definition')
    def validate_tool_definition(cls, v, values):
        if values.get('tool_type') in ['api', 'webhook'] and not v:
            raise ValueError('tool_definition is required for API/webhook tools')
        return v

class ToolUpdateRequest(BaseModel):
    """Request model for updating a tool"""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    category: Optional[str] = Field(None, pattern="^(custom|builtin|integration)$")
    enabled: Optional[bool] = None
    configuration: Optional[Dict[str, Any]] = None
    tool_definition: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None

class ToolExecuteRequest(BaseModel):
    """Request model for executing a tool"""
    tool_id: UUID
    agent_id: Optional[UUID] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    context: Optional[Dict[str, Any]] = None

class ToolExecuteResponse(BaseModel):
    """Response model for tool execution"""
    success: bool
    result: Optional[Any] = None
    error: Optional[str] = None
    execution_time_ms: float
    
class ToolListResponse(BaseModel):
    """Response model for tool list"""
    tools: List[ToolConfiguration]
    total: int
    page: int = 1
    per_page: int = 20

class AgentToolListResponse(BaseModel):
    """Response model for agent tool configurations"""
    agent_id: UUID
    tools: List[Dict[str, Any]]  # Combined tool and configuration data
    total: int
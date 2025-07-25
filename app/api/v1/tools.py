from fastapi import APIRouter, HTTPException, status, Depends, Query
from typing import List, Optional
from uuid import UUID
import httpx
import json
from datetime import datetime

from app.models.tool import (
    ToolConfiguration, ToolCreateRequest, ToolUpdateRequest,
    ToolExecuteRequest, ToolExecuteResponse, ToolListResponse,
    AgentToolListResponse
)
from app.models.common import APIResponse, SuccessResponse, DeleteResponse
from app.middleware.auth import get_current_auth, require_user_auth
from app.integrations.supabase_client import supabase_manager
from app.utils.exceptions import NotFoundError, ValidationError, ServiceUnavailableError

router = APIRouter()

@router.get("/", response_model=APIResponse[ToolListResponse])
async def list_tools(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    category: Optional[str] = Query(None, pattern="^(custom|builtin|integration)$"),
    enabled_only: bool = Query(False),
    auth=Depends(get_current_auth)
):
    """
    List available tools
    """
    try:
        # Build query
        query = supabase_manager.admin_client.table("autonomite_tools").select("*")
        
        # Filter by user if user auth
        if auth.is_user_auth:
            query = query.eq("user_id", auth.user_id)
        
        if category:
            query = query.eq("category", category)
        if enabled_only:
            query = query.eq("enabled", True)
        
        # Pagination
        offset = (page - 1) * per_page
        query = query.order("created_at", desc=True).limit(per_page).offset(offset)
        
        # Execute query
        result = await supabase_manager.execute_query(query)
        
        return APIResponse(
            success=True,
            data=ToolListResponse(
                tools=result,
                total=len(result),
                page=page,
                per_page=per_page
            )
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/", response_model=APIResponse[ToolConfiguration])
async def create_tool(
    request: ToolCreateRequest,
    auth=Depends(require_user_auth)
):
    """
    Create a new tool configuration
    """
    try:
        # Validate tool definition
        if request.tool_type in ["api", "webhook"] and not request.tool_definition:
            raise ValidationError("tool_definition is required for API/webhook tools")
        
        # Prepare configuration
        configuration = request.configuration.copy()
        if request.webhook_url:
            configuration["webhook_url"] = request.webhook_url
        if request.api_key:
            configuration["api_key"] = request.api_key
        if request.headers:
            configuration["headers"] = request.headers
        
        # Create tool data
        tool_data = {
            "user_id": auth.user_id,
            "name": request.name,
            "description": request.description,
            "category": request.category,
            "tool_type": request.tool_type,
            "enabled": request.enabled,
            "configuration": configuration,
            "tool_definition": request.tool_definition,
            "metadata": request.metadata,
            "created_at": datetime.utcnow().isoformat()
        }
        
        # Create tool
        result = await supabase_manager.execute_query(
            supabase_manager.admin_client.table("autonomite_tools").insert(tool_data)
        )
        
        return APIResponse(
            success=True,
            data=result[0]
        )
        
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/{tool_id}", response_model=APIResponse[ToolConfiguration])
async def get_tool(
    tool_id: UUID,
    auth=Depends(get_current_auth)
):
    """
    Get tool configuration details
    """
    try:
        query = supabase_manager.admin_client.table("autonomite_tools").select("*").eq("id", str(tool_id))
        
        # Filter by user if user auth
        if auth.is_user_auth:
            query = query.eq("user_id", auth.user_id)
        
        result = await supabase_manager.execute_query(query)
        
        if not result:
            raise NotFoundError("Tool not found")
        
        return APIResponse(
            success=True,
            data=result[0]
        )
        
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool not found"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.put("/{tool_id}", response_model=APIResponse[ToolConfiguration])
async def update_tool(
    tool_id: UUID,
    request: ToolUpdateRequest,
    auth=Depends(require_user_auth)
):
    """
    Update tool configuration
    """
    try:
        # Check tool exists
        query = supabase_manager.admin_client.table("autonomite_tools").select("*").eq("id", str(tool_id)).eq("user_id", auth.user_id)
        result = await supabase_manager.execute_query(query)
        
        if not result:
            raise NotFoundError("Tool not found")
        
        # Update tool
        update_data = request.dict(exclude_unset=True)
        
        result = await supabase_manager.execute_query(
            supabase_manager.admin_client.table("autonomite_tools")
            .update(update_data)
            .eq("id", str(tool_id))
        )
        
        return APIResponse(
            success=True,
            data=result[0]
        )
        
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool not found"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.delete("/{tool_id}", response_model=APIResponse[DeleteResponse])
async def delete_tool(
    tool_id: UUID,
    auth=Depends(require_user_auth)
):
    """
    Delete a tool configuration
    """
    try:
        # Check tool exists
        query = supabase_manager.admin_client.table("autonomite_tools").select("*").eq("id", str(tool_id)).eq("user_id", auth.user_id)
        result = await supabase_manager.execute_query(query)
        
        if not result:
            raise NotFoundError("Tool not found")
        
        # Delete agent-tool associations first
        await supabase_manager.execute_query(
            supabase_manager.admin_client.table("autonomite_agent_tools")
            .delete()
            .eq("tool_id", str(tool_id))
        )
        
        # Delete tool
        await supabase_manager.execute_query(
            supabase_manager.admin_client.table("autonomite_tools")
            .delete()
            .eq("id", str(tool_id))
        )
        
        return APIResponse(
            success=True,
            data=DeleteResponse(
                deleted_id=str(tool_id),
                deleted_at=datetime.utcnow()
            )
        )
        
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool not found"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/execute", response_model=APIResponse[ToolExecuteResponse])
async def execute_tool(
    request: ToolExecuteRequest,
    auth=Depends(get_current_auth)
):
    """
    Execute a tool (webhook proxy)
    """
    try:
        # Get tool configuration
        query = supabase_manager.admin_client.table("autonomite_tools").select("*").eq("id", str(request.tool_id))
        result = await supabase_manager.execute_query(query)
        
        if not result:
            raise NotFoundError("Tool not found")
        
        tool = result[0]
        
        if not tool["enabled"]:
            raise ValidationError("Tool is not enabled")
        
        if tool["tool_type"] not in ["api", "webhook"]:
            raise ValidationError(f"Cannot execute tool of type {tool['tool_type']}")
        
        # Get webhook URL
        webhook_url = tool["configuration"].get("webhook_url")
        if not webhook_url:
            raise ValidationError("Tool has no webhook URL configured")
        
        # Prepare headers
        headers = tool["configuration"].get("headers", {})
        if tool["configuration"].get("api_key"):
            headers["Authorization"] = f"Bearer {tool['configuration']['api_key']}"
        
        # Execute webhook
        start_time = datetime.utcnow()
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    webhook_url,
                    json={
                        "tool_id": str(request.tool_id),
                        "parameters": request.parameters,
                        "context": request.context,
                        "agent_id": str(request.agent_id) if request.agent_id else None
                    },
                    headers=headers,
                    timeout=30.0
                )
                
                response.raise_for_status()
                result_data = response.json()
                
                execution_time = (datetime.utcnow() - start_time).total_seconds() * 1000
                
                return APIResponse(
                    success=True,
                    data=ToolExecuteResponse(
                        success=True,
                        result=result_data,
                        execution_time_ms=execution_time
                    )
                )
                
            except httpx.HTTPError as e:
                execution_time = (datetime.utcnow() - start_time).total_seconds() * 1000
                
                return APIResponse(
                    success=False,
                    data=ToolExecuteResponse(
                        success=False,
                        error=str(e),
                        execution_time_ms=execution_time
                    )
                )
        
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool not found"
        )
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/agent/{agent_id}", response_model=APIResponse[AgentToolListResponse])
async def get_agent_tools(
    agent_id: UUID,
    auth=Depends(get_current_auth)
):
    """
    Get tools configured for a specific agent
    """
    try:
        # Get agent-tool configurations with tool details
        tools = await supabase_manager.get_tools_for_agent(str(agent_id))
        
        # Format response
        formatted_tools = []
        for tool_config in tools:
            if tool_config.get("autonomite_tools"):
                tool_data = tool_config["autonomite_tools"]
                tool_data["enabled_for_agent"] = tool_config["enabled"]
                tool_data["configuration_override"] = tool_config.get("configuration_override", {})
                formatted_tools.append(tool_data)
        
        return APIResponse(
            success=True,
            data=AgentToolListResponse(
                agent_id=agent_id,
                tools=formatted_tools,
                total=len(formatted_tools)
            )
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/agent/{agent_id}/configure", response_model=APIResponse[SuccessResponse])
async def configure_agent_tools(
    agent_id: UUID,
    tool_ids: List[UUID],
    auth=Depends(require_user_auth)
):
    """
    Configure which tools are enabled for an agent
    """
    try:
        # Remove existing configurations
        await supabase_manager.execute_query(
            supabase_manager.admin_client.table("autonomite_agent_tools")
            .delete()
            .eq("agent_id", str(agent_id))
        )
        
        # Add new configurations
        if tool_ids:
            configs = [
                {
                    "agent_id": str(agent_id),
                    "tool_id": str(tool_id),
                    "enabled": True,
                    "created_at": datetime.utcnow().isoformat()
                }
                for tool_id in tool_ids
            ]
            
            await supabase_manager.execute_query(
                supabase_manager.admin_client.table("autonomite_agent_tools")
                .insert(configs)
            )
        
        return APIResponse(
            success=True,
            data=SuccessResponse(
                message=f"Configured {len(tool_ids)} tools for agent"
            )
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
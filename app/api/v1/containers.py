from fastapi import APIRouter, HTTPException, status, Depends, Query
from typing import List, Optional
from uuid import UUID

from app.models.container import (
    ContainerDeployRequest, ContainerInfo, ContainerListItem,
    ContainerLogsRequest, ContainerScaleRequest, ClientContainerStatus
)
from app.models.common import APIResponse, SuccessResponse
from app.middleware.auth import get_current_auth, require_site_auth
from app.services.container_manager import container_manager
from app.integrations.supabase_client import supabase_manager
from app.utils.exceptions import NotFoundError, ServiceUnavailableError

router = APIRouter()

@router.post("/deploy", response_model=APIResponse[ContainerInfo])
async def deploy_container(
    request: ContainerDeployRequest,
    auth=Depends(require_site_auth)
):
    """
    Deploy an agent container for the authenticated site
    """
    try:
        # Get agent configuration
        agent_config = await supabase_manager.get_agent_configuration(request.agent_slug)
        if not agent_config:
            raise NotFoundError(f"Agent '{request.agent_slug}' not found")
        
        # Get site configuration
        site_result = await supabase_manager.execute_query(
            supabase_manager.admin_client.table("wordpress_sites")
            .select("*")
            .eq("id", auth.site_id)
            .single()
        )
        
        # Deploy container
        container_info = await container_manager.deploy_agent_container(
            site_id=auth.site_id,
            agent_slug=request.agent_slug,
            agent_config=agent_config,
            site_config=site_result or {}
        )
        
        if not container_info:
            raise ServiceUnavailableError("Failed to deploy container")
        
        return APIResponse(
            success=True,
            data=ContainerInfo(**container_info)
        )
        
    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except ServiceUnavailableError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/", response_model=APIResponse[ClientContainerStatus])
async def list_containers(auth=Depends(require_site_auth)):
    """
    List all containers for the authenticated site
    """
    try:
        containers = await container_manager.list_client_containers(auth.site_id)
        
        # Calculate aggregated stats
        running_containers = sum(1 for c in containers if c["status"] == "running")
        stopped_containers = len(containers) - running_containers
        
        # Get detailed stats for running containers
        total_cpu = 0.0
        total_memory = 0.0
        
        for container in containers:
            if container["status"] == "running":
                info = await container_manager.get_container_info(container["name"])
                if info and info.get("stats"):
                    total_cpu += info["stats"].get("cpu_percent", 0)
                    total_memory += info["stats"].get("memory_usage_mb", 0)
        
        return APIResponse(
            success=True,
            data=ClientContainerStatus(
                site_id=auth.site_id,
                total_containers=len(containers),
                running_containers=running_containers,
                stopped_containers=stopped_containers,
                total_cpu_usage=round(total_cpu, 2),
                total_memory_usage_mb=round(total_memory, 2),
                containers=containers
            )
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/{agent_slug}", response_model=APIResponse[ContainerInfo])
async def get_container_info(
    agent_slug: str,
    auth=Depends(require_site_auth)
):
    """
    Get detailed information about a specific container
    """
    try:
        container_name = container_manager.get_container_name(auth.site_id, agent_slug)
        container_info = await container_manager.get_container_info(container_name)
        
        if not container_info:
            raise NotFoundError(f"Container for agent '{agent_slug}' not found")
        
        return APIResponse(
            success=True,
            data=ContainerInfo(**container_info)
        )
        
    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/{agent_slug}/stop", response_model=APIResponse[SuccessResponse])
async def stop_container(
    agent_slug: str,
    auth=Depends(require_site_auth)
):
    """
    Stop a running container
    """
    try:
        container_name = container_manager.get_container_name(auth.site_id, agent_slug)
        success = await container_manager.stop_container(container_name)
        
        if not success:
            raise NotFoundError(f"Container for agent '{agent_slug}' not found")
        
        return APIResponse(
            success=True,
            data=SuccessResponse(message=f"Container {container_name} stopped successfully")
        )
        
    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/{agent_slug}/restart", response_model=APIResponse[SuccessResponse])
async def restart_container(
    agent_slug: str,
    auth=Depends(require_site_auth)
):
    """
    Restart a container
    """
    try:
        container_name = container_manager.get_container_name(auth.site_id, agent_slug)
        success = await container_manager.restart_container(container_name)
        
        if not success:
            raise NotFoundError(f"Container for agent '{agent_slug}' not found")
        
        return APIResponse(
            success=True,
            data=SuccessResponse(message=f"Container {container_name} restarted successfully")
        )
        
    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.delete("/{agent_slug}", response_model=APIResponse[SuccessResponse])
async def remove_container(
    agent_slug: str,
    auth=Depends(require_site_auth)
):
    """
    Remove a container
    """
    try:
        container_name = container_manager.get_container_name(auth.site_id, agent_slug)
        success = await container_manager.remove_container(container_name)
        
        if not success:
            raise NotFoundError(f"Container for agent '{agent_slug}' not found")
        
        return APIResponse(
            success=True,
            data=SuccessResponse(message=f"Container {container_name} removed successfully")
        )
        
    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/{agent_slug}/logs", response_model=APIResponse)
async def get_container_logs(
    agent_slug: str,
    request: ContainerLogsRequest,
    auth=Depends(require_site_auth)
):
    """
    Get container logs
    """
    try:
        container_name = container_manager.get_container_name(auth.site_id, agent_slug)
        logs = await container_manager.get_container_logs(
            container_name,
            lines=request.lines,
            since=request.since
        )
        
        if not logs:
            raise NotFoundError(f"Container for agent '{agent_slug}' not found")
        
        return APIResponse(
            success=True,
            data={
                "container_name": container_name,
                "logs": logs,
                "lines": request.lines
            }
        )
        
    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/{agent_slug}/scale", response_model=APIResponse[SuccessResponse])
async def scale_container(
    agent_slug: str,
    request: ContainerScaleRequest,
    auth=Depends(require_site_auth)
):
    """
    Update container resource limits
    """
    try:
        container_name = container_manager.get_container_name(auth.site_id, agent_slug)
        success = await container_manager.scale_container(
            container_name,
            cpu_limit=request.cpu_limit,
            memory_limit=request.memory_limit
        )
        
        if not success:
            raise NotFoundError(f"Container for agent '{agent_slug}' not found")
        
        return APIResponse(
            success=True,
            data=SuccessResponse(
                message=f"Container {container_name} scaled to CPU={request.cpu_limit}, Memory={request.memory_limit}"
            )
        )
        
    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
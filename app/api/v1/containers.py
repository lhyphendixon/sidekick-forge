from fastapi import APIRouter, HTTPException, status, Depends
from typing import List, Dict, Any
import docker

from app.models.common import APIResponse
from app.middleware.auth import require_site_auth

router = APIRouter()

@router.get("/info")
async def get_container_info(auth=Depends(require_site_auth)):
    """
    Get information about the worker pool architecture
    """
    return APIResponse(
        success=True,
        data={
            "architecture": "worker-pool",
            "message": "Agents now use a shared worker pool instead of individual containers",
            "benefits": [
                "Better resource utilization",
                "Faster agent startup times",
                "Automatic load balancing",
                "No container management needed"
            ],
            "worker_status": await get_worker_pool_status()
        }
    )

@router.get("/workers")
async def list_workers():
    """
    List worker pool status
    """
    return APIResponse(
        success=True,
        data=await get_worker_pool_status()
    )

async def get_worker_pool_status() -> Dict[str, Any]:
    """Get status of the worker pool"""
    try:
        # Try to get Docker client to check workers
        client = docker.from_env()
        
        # Find agent worker containers
        workers = []
        for container in client.containers.list():
            if "agent-worker" in container.name:
                workers.append({
                    "name": container.name,
                    "status": container.status,
                    "created": container.attrs['Created'],
                    "id": container.short_id
                })
        
        return {
            "total_workers": len(workers),
            "active_workers": sum(1 for w in workers if w["status"] == "running"),
            "workers": workers
        }
    except Exception as e:
        # Docker not available or other error
        return {
            "total_workers": 0,
            "active_workers": 0,
            "workers": [],
            "error": f"Unable to get worker status: {str(e)}"
        }

# Legacy endpoints that return deprecation notices
@router.post("/deploy")
async def deploy_container_deprecated(auth=Depends(require_site_auth)):
    """Deprecated - containers are no longer individually deployed"""
    return APIResponse(
        success=False,
        message="Container deployment is deprecated. Agents now use a shared worker pool.",
        data=None
    )

@router.post("/{agent_slug}/stop")
async def stop_container_deprecated(agent_slug: str, auth=Depends(require_site_auth)):
    """Deprecated - containers are managed by the worker pool"""
    return APIResponse(
        success=False,
        message="Container management is deprecated. Worker pool handles agent lifecycle.",
        data=None
    )

@router.get("/{agent_slug}/logs")
async def get_logs_deprecated(agent_slug: str, auth=Depends(require_site_auth)):
    """Deprecated - logs are centralized"""
    return APIResponse(
        success=False,
        message="Individual container logs are deprecated. Check centralized worker logs.",
        data=None
    )
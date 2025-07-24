from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any
import logging

from app.middleware.auth import get_current_auth, require_user_auth
from app.services.container_manager import container_manager
from app.models.user import User, AuthContext

router = APIRouter(prefix="/maintenance", tags=["maintenance"])
logger = logging.getLogger(__name__)

@router.post("/health-check-pools")
async def health_check_pools(
    auth: AuthContext = Depends(require_user_auth)
) -> Dict[str, Any]:
    """Manually trigger health check on all container pools"""
    # For now, allow any authenticated user (admin check can be added later)
    
    logger.info(f"User {auth.user_id} triggered manual pool health check")
    results = await container_manager.health_check_pool()
    return results

@router.post("/scale-to-zero-check")
async def scale_to_zero_check(
    auth: AuthContext = Depends(require_user_auth)
) -> Dict[str, Any]:
    """Manually trigger scale-to-zero check on idle containers"""
    # For now, allow any authenticated user (admin check can be added later)
    
    logger.info(f"User {auth.user_id} triggered manual scale-to-zero check")
    results = await container_manager.scale_to_zero_check()
    return results

@router.get("/pool-status")
async def get_pool_status(
    auth: AuthContext = Depends(require_user_auth)
) -> Dict[str, Any]:
    """Get current status of all container pools"""
    # For now, allow any authenticated user (admin check can be added later)
    
    # Get pool status from Redis if available
    if container_manager.redis_client:
        try:
            pool_status = {}
            
            # Get all idle pools
            idle_keys = await container_manager.redis_client.keys("client:*:container_pool:idle")
            for key in idle_keys:
                client_id = key.split(":")[1]
                idle_containers = await container_manager.redis_client.smembers(key)
                
                # Get busy containers for this client
                busy_key = f"client:{client_id}:container_pool:busy"
                busy_containers = await container_manager.redis_client.smembers(busy_key)
                
                # Get detailed info for each container
                containers_info = []
                for container_name in idle_containers.union(busy_containers):
                    info_key = f"container:{container_name}:info"
                    info = await container_manager.redis_client.hgetall(info_key)
                    containers_info.append({
                        "name": container_name,
                        "status": info.get("status", "unknown"),
                        "worker_id": info.get("worker_id"),
                        "idle_since": info.get("idle_since"),
                        "last_used": info.get("last_used")
                    })
                
                pool_status[client_id] = {
                    "idle_count": len(idle_containers),
                    "busy_count": len(busy_containers),
                    "total_count": len(idle_containers) + len(busy_containers),
                    "containers": containers_info
                }
            
            return {
                "pools": pool_status,
                "config": {
                    "max_pool_size": container_manager.max_pool_size,
                    "min_pool_size": container_manager.min_pool_size,
                    "idle_timeout_minutes": container_manager.idle_timeout_minutes
                }
            }
            
        except Exception as e:
            logger.error(f"Failed to get pool status from Redis: {e}")
            return {"error": str(e)}
    else:
        # Fallback to in-memory pools
        pool_status = {}
        for client_id, containers in container_manager.pools.items():
            pool_status[client_id] = {
                "idle_count": len(containers),
                "busy_count": 0,  # Can't track busy in memory-only mode
                "total_count": len(containers)
            }
        
        return {
            "pools": pool_status,
            "config": {
                "max_pool_size": container_manager.max_pool_size,
                "min_pool_size": container_manager.min_pool_size,
                "idle_timeout_minutes": container_manager.idle_timeout_minutes
            },
            "note": "Using in-memory state (Redis not available)"
        }
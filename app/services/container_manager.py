import docker
import asyncio
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
import os
import json
from collections import defaultdict
import redis.asyncio as redis

from app.config import settings
from app.utils.exceptions import ServiceUnavailableError, ValidationError
from app.utils.deployment_logger import ContainerDeploymentLogger

logger = logging.getLogger(__name__)
deployment_logger = ContainerDeploymentLogger()

class ContainerManager:
    """Manages Docker containers for client-specific agent instances with pooling support"""
    
    def __init__(self):
        self.docker_client = None
        self.network_name = "autonomite-agents-network"
        self.agent_image = "autonomite/agent-runtime:chatcontent-fix"  # Fixed ChatContent issue
        self._initialized = False
        
        # Redis client for container state management
        self.redis_client = None
        
        # Container pooling configuration
        self.pools = defaultdict(list)  # client_id -> list of available container names
        self.active_containers = defaultdict(dict)  # client_id -> {session_id: container_name}
        self.container_reuse_counts = {}  # container_name -> reuse count
        self.max_pool_size = 5  # Allow up to 5 containers per client in pool
        self.max_reuse_count = 10  # Max reuses before container is recycled
        
        # Scale-to-zero configuration
        self.idle_timeout_minutes = int(os.getenv("CONTAINER_IDLE_TIMEOUT_MINUTES", "30"))  # Default 30 minutes
        self.min_pool_size = int(os.getenv("CONTAINER_MIN_POOL_SIZE", "0"))  # Can scale to zero
        
        # Redis key patterns
        self.REDIS_IDLE_POOL_KEY = "client:{client_id}:container_pool:idle"
        self.REDIS_BUSY_POOL_KEY = "client:{client_id}:container_pool:busy"
        self.REDIS_CONTAINER_INFO_KEY = "container:{container_name}:info"
    
    async def initialize(self):
        """Initialize Docker client, Redis client, and ensure network exists"""
        if self._initialized:
            return
        
        try:
            self.docker_client = docker.from_env()
            
            # Initialize Redis client
            try:
                self.redis_client = await redis.from_url(
                    settings.redis_url or "redis://localhost:6379",
                    decode_responses=True
                )
                await self.redis_client.ping()
                logger.info("✅ Connected to Redis for container state management")
            except Exception as e:
                logger.warning(f"Redis not available, using in-memory state: {e}")
                self.redis_client = None
            
            # Ensure agent network exists
            try:
                self.docker_client.networks.get(self.network_name)
            except docker.errors.NotFound:
                self.docker_client.networks.create(
                    self.network_name,
                    driver="bridge",
                    labels={"managed_by": "autonomite-saas"}
                )
            
            # Restore warm pools from existing containers
            await self._restore_container_pools()
            
            self._initialized = True
            logger.info("Container manager initialized successfully with warm pool support")
            
        except Exception as e:
            logger.warning(f"Docker not available, container management disabled: {e}")
            self.docker_client = None
            self._initialized = True  # Still initialize but without Docker
    
    async def _restore_container_pools(self):
        """Restore container pools from existing running containers"""
        if not self.docker_client:
            return
            
        try:
            containers = self.docker_client.containers.list(filters={"name": "agent_"})
            for container in containers:
                if container.status == "running":
                    # Extract client_id from container name
                    parts = container.name.split("_")
                    if len(parts) >= 3:
                        client_id = parts[1]
                        # Add to idle pool if container is healthy
                        if container.attrs.get("State", {}).get("Health", {}).get("Status") == "healthy":
                            await self._add_to_idle_pool(client_id, container.name)
                            logger.info(f"♻️ Restored container {container.name} to warm pool for client {client_id}")
        except Exception as e:
            logger.error(f"Failed to restore container pools: {e}")
    
    async def _add_to_idle_pool(self, client_id: str, container_name: str, worker_id: Optional[str] = None):
        """Add a container to the idle pool"""
        if self.redis_client:
            idle_key = self.REDIS_IDLE_POOL_KEY.format(client_id=client_id)
            await self.redis_client.sadd(idle_key, container_name)
            # Set container info
            info_key = self.REDIS_CONTAINER_INFO_KEY.format(container_name=container_name)
            await self.redis_client.hset(info_key, "last_used", datetime.now().isoformat())
            await self.redis_client.hset(info_key, "idle_since", datetime.now().isoformat())
            await self.redis_client.hset(info_key, "status", "idle")
            if worker_id:
                await self.redis_client.hset(info_key, "worker_id", worker_id)
        else:
            # Fallback to in-memory
            self.pools[client_id].append(container_name)
    
    async def _get_from_idle_pool(self, client_id: str) -> Optional[str]:
        """Get a container from the idle pool"""
        if self.redis_client:
            idle_key = self.REDIS_IDLE_POOL_KEY.format(client_id=client_id)
            container_name = await self.redis_client.spop(idle_key)
            if container_name:
                # Mark as busy
                busy_key = self.REDIS_BUSY_POOL_KEY.format(client_id=client_id)
                await self.redis_client.sadd(busy_key, container_name)
                info_key = self.REDIS_CONTAINER_INFO_KEY.format(container_name=container_name)
                await self.redis_client.hset(info_key, "status", "busy")
                await self.redis_client.hset(info_key, "acquired_at", datetime.now().isoformat())
            return container_name
        else:
            # Fallback to in-memory
            if self.pools[client_id]:
                return self.pools[client_id].pop(0)
            return None
    
    async def release_container_to_pool(self, client_id: str, container_name: str, worker_id: Optional[str] = None):
        """Release a container back to the idle pool"""
        if self.redis_client:
            # Remove from busy set
            busy_key = self.REDIS_BUSY_POOL_KEY.format(client_id=client_id)
            await self.redis_client.srem(busy_key, container_name)
            # Add to idle set with worker ID
            await self._add_to_idle_pool(client_id, container_name, worker_id)
            logger.info(f"♻️ Released container {container_name} back to idle pool for client {client_id}")
        else:
            # Fallback to in-memory
            if container_name not in self.pools[client_id]:
                self.pools[client_id].append(container_name)
                logger.info(f"♻️ Released container {container_name} back to idle pool for client {client_id}")
    
    async def get_container_worker_id(self, container_name: str) -> Optional[str]:
        """Get the LiveKit worker ID for a container"""
        if self.redis_client:
            info_key = self.REDIS_CONTAINER_INFO_KEY.format(container_name=container_name)
            return await self.redis_client.hget(info_key, "worker_id")
        return None
    
    async def verify_worker_registration(self, container_name: str, max_attempts: int = 5) -> bool:
        """
        Verify that a container's worker has registered with LiveKit.
        This is critical for dispatch to work properly.
        """
        logger.info(f"🔍 Verifying worker registration for container {container_name}")
        
        for attempt in range(max_attempts):
            try:
                # Check if container is running
                container = self.docker_client.containers.get(container_name)
                if container.status != "running":
                    logger.warning(f"Container {container_name} is not running (status: {container.status})")
                    return False
                
                # Check container logs for registration confirmation
                logs = container.logs(tail=50).decode('utf-8')
                
                # Look for registration patterns in logs
                registration_patterns = [
                    "Worker registered with LiveKit",
                    "Successfully registered worker",
                    "Worker ID:",
                    "connected to LiveKit server",
                    "Worker ready",
                    "Registered with server"
                ]
                
                for pattern in registration_patterns:
                    if pattern.lower() in logs.lower():
                        logger.info(f"✅ Worker registration confirmed for {container_name}: found '{pattern}'")
                        return True
                
                # Also check Redis for worker ID if available
                if self.redis_client:
                    info_key = self.REDIS_CONTAINER_INFO_KEY.format(container_name=container_name)
                    worker_id = await self.redis_client.hget(info_key, "worker_id")
                    if worker_id:
                        logger.info(f"✅ Worker registration confirmed via Redis: {worker_id}")
                        return True
                
                if attempt < max_attempts - 1:
                    logger.info(f"⏳ Waiting for worker registration... (attempt {attempt + 1}/{max_attempts})")
                    await asyncio.sleep(1)
                    
            except Exception as e:
                logger.error(f"Error checking worker registration: {e}")
                if attempt < max_attempts - 1:
                    await asyncio.sleep(1)
        
        logger.warning(f"❌ Worker registration not confirmed for {container_name} after {max_attempts} attempts")
        return False
    
    async def health_check_pool(self) -> Dict[str, Any]:
        """Perform health checks on all containers in idle pools"""
        if not self.docker_client:
            return {"status": "skipped", "reason": "Docker not available"}
        
        results = {
            "checked": 0,
            "healthy": 0,
            "unhealthy": 0,
            "removed": []
        }
        
        try:
            if self.redis_client:
                # Get all clients with pools
                all_keys = await self.redis_client.keys("client:*:container_pool:idle")
                
                for key in all_keys:
                    client_id = key.split(":")[1]
                    idle_containers = await self.redis_client.smembers(key)
                    
                    for container_name in idle_containers:
                        results["checked"] += 1
                        
                        try:
                            container = self.docker_client.containers.get(container_name)
                            
                            # Check if container is running
                            if container.status != "running":
                                logger.warning(f"⚠️ Container {container_name} is not running, removing from pool")
                                await self.redis_client.srem(key, container_name)
                                results["unhealthy"] += 1
                                results["removed"].append(container_name)
                                continue
                            
                            # Check container health status
                            health = container.attrs.get("State", {}).get("Health", {}).get("Status")
                            if health == "healthy":
                                results["healthy"] += 1
                            else:
                                logger.warning(f"⚠️ Container {container_name} is unhealthy: {health}")
                                results["unhealthy"] += 1
                                
                                # Remove unhealthy containers from pool
                                await self.redis_client.srem(key, container_name)
                                results["removed"].append(container_name)
                                
                                # Stop and remove the container
                                try:
                                    container.stop(timeout=5)
                                    container.remove(force=True)
                                except:
                                    pass
                                    
                        except docker.errors.NotFound:
                            # Container doesn't exist, remove from pool
                            logger.warning(f"⚠️ Container {container_name} not found, removing from pool")
                            await self.redis_client.srem(key, container_name)
                            results["unhealthy"] += 1
                            results["removed"].append(container_name)
            
            else:
                # In-memory pool check
                for client_id, containers in self.pools.items():
                    for container_name in containers[:]:  # Copy list to modify during iteration
                        results["checked"] += 1
                        
                        try:
                            container = self.docker_client.containers.get(container_name)
                            if container.status == "running":
                                results["healthy"] += 1
                            else:
                                results["unhealthy"] += 1
                                self.pools[client_id].remove(container_name)
                                results["removed"].append(container_name)
                        except:
                            results["unhealthy"] += 1
                            self.pools[client_id].remove(container_name)
                            results["removed"].append(container_name)
            
            logger.info(f"🏥 Pool health check complete: {results}")
            return results
            
        except Exception as e:
            logger.error(f"Failed to perform pool health check: {e}")
            return {"status": "error", "error": str(e)}
    
    async def scale_to_zero_check(self) -> Dict[str, Any]:
        """Check for idle containers and scale down after timeout period"""
        if not self.docker_client:
            return {"status": "skipped", "reason": "Docker not available"}
        
        results = {
            "checked": 0,
            "scaled_down": 0,
            "containers_stopped": []
        }
        
        try:
            current_time = datetime.now()
            
            if self.redis_client:
                # Get all clients with idle pools
                all_keys = await self.redis_client.keys("client:*:container_pool:idle")
                
                for key in all_keys:
                    client_id = key.split(":")[1]
                    idle_containers = await self.redis_client.smembers(key)
                    
                    # Skip if pool is already at minimum size
                    if len(idle_containers) <= self.min_pool_size:
                        continue
                    
                    containers_to_remove = []
                    
                    for container_name in idle_containers:
                        results["checked"] += 1
                        
                        # Get container idle time
                        info_key = self.REDIS_CONTAINER_INFO_KEY.format(container_name=container_name)
                        idle_since = await self.redis_client.hget(info_key, "idle_since")
                        
                        if idle_since:
                            idle_time = current_time - datetime.fromisoformat(idle_since)
                            idle_minutes = idle_time.total_seconds() / 60
                            
                            if idle_minutes > self.idle_timeout_minutes:
                                # Check if removing this container would go below minimum
                                remaining = len(idle_containers) - len(containers_to_remove) - 1
                                if remaining >= self.min_pool_size:
                                    containers_to_remove.append(container_name)
                    
                    # Stop and remove idle containers
                    for container_name in containers_to_remove:
                        try:
                            container = self.docker_client.containers.get(container_name)
                            container.stop(timeout=10)
                            container.remove(force=True)
                            
                            # Remove from pool
                            await self.redis_client.srem(key, container_name)
                            
                            # Clean up container info
                            info_key = self.REDIS_CONTAINER_INFO_KEY.format(container_name=container_name)
                            await self.redis_client.delete(info_key)
                            
                            results["scaled_down"] += 1
                            results["containers_stopped"].append(container_name)
                            
                            logger.info(f"📉 Scaled down idle container: {container_name} (client: {client_id})")
                            
                        except Exception as e:
                            logger.error(f"Failed to stop container {container_name}: {e}")
            
            else:
                # In-memory pool check (simplified)
                for client_id, containers in self.pools.items():
                    if len(containers) > self.min_pool_size:
                        # For in-memory, we can't track idle time precisely
                        # So we'll just remove excess containers
                        excess = len(containers) - self.min_pool_size
                        for i in range(excess):
                            if containers:
                                container_name = containers.pop()
                                try:
                                    container = self.docker_client.containers.get(container_name)
                                    container.stop(timeout=10)
                                    container.remove(force=True)
                                    results["scaled_down"] += 1
                                    results["containers_stopped"].append(container_name)
                                except:
                                    pass
            
            if results["scaled_down"] > 0:
                logger.info(f"♻️ Scale-to-zero check complete: {results}")
            
            return results
            
        except Exception as e:
            logger.error(f"Failed to perform scale-to-zero check: {e}")
            return {"status": "error", "error": str(e)}
    
    async def _update_container_session(self, container_name: str, room_name: str, session_id: str):
        """Update container environment for new session"""
        try:
            # Write session info to a file that the container can read
            session_info = {
                "room_name": room_name,
                "session_id": session_id,
                "timestamp": datetime.now().isoformat()
            }
            
            # Store in Redis if available
            if self.redis_client:
                info_key = self.REDIS_CONTAINER_INFO_KEY.format(container_name=container_name)
                await self.redis_client.hset(info_key, "current_room", room_name)
                await self.redis_client.hset(info_key, "current_session", session_id)
            
            # Also write to a file the container can read
            session_file = f"/tmp/{container_name}_session.json"
            with open(session_file, 'w') as f:
                json.dump(session_info, f)
            
            logger.info(f"📝 Updated container {container_name} with session {session_id} for room {room_name}")
        except Exception as e:
            logger.error(f"Failed to update container session: {e}")
    
    def get_container_name(self, site_id: str, agent_slug: str, session_id: str = None) -> str:
        """Generate container name for a client's agent, optionally with session"""
        # Clean the inputs to be container-name safe
        safe_site_id = site_id.replace("-", "").lower()[:8]
        safe_agent_slug = agent_slug.replace("-", "_").lower()
        
        if session_id:
            # Include session ID for unique containers per session
            safe_session = session_id.replace("-", "").lower()[:8]
            return f"agent_{safe_site_id}_{safe_agent_slug}_{safe_session}"
        else:
            # Legacy format for backwards compatibility
            return f"agent_{safe_site_id}_{safe_agent_slug}"
    
    async def get_or_create_container(
        self,
        site_id: str,
        agent_slug: str,
        agent_config: Dict[str, Any],
        site_config: Dict[str, Any],
        session_id: str,
        room_name: str
    ) -> str:
        """Get a container from warm pool or create a new persistent one"""
        logger.info(f"🏊 Checking warm pool for client {site_id}, agent {agent_slug}")
        
        # Try to get a container from the idle pool first
        container_name = await self._get_from_idle_pool(site_id)
        
        if container_name:
            try:
                # Verify the container is still running and healthy
                container = self.docker_client.containers.get(container_name)
                if container.status == "running":
                    logger.info(f"✅ Acquired warm container {container_name} from pool for client {site_id}")
                    
                    # Update container with new session info
                    await self._update_container_session(container_name, room_name, session_id)
                    
                    # Track active container
                    self.active_containers[site_id][session_id] = container_name
                    
                    # Increment reuse count
                    self.container_reuse_counts[container_name] = self.container_reuse_counts.get(container_name, 0) + 1
                    logger.info(f"📊 Container {container_name} reused {self.container_reuse_counts[container_name]} times")
                    
                    return container_name
                else:
                    logger.warning(f"⚠️ Pooled container {container_name} is not running, removing from pool")
                    # Remove unhealthy container
                    try:
                        container.remove(force=True)
                    except:
                        pass
            except docker.errors.NotFound:
                logger.warning(f"⚠️ Pooled container {container_name} no longer exists")
        
        # No container in pool, need to create a new one
        logger.info(f"🏊 No warm containers available for client {site_id}, creating new persistent container")
        
        # First check if there's already a container for this room
        try:
            containers = self.docker_client.containers.list(filters={"name": "agent_"})
            for container in containers:
                # Check environment variables
                env_vars = container.attrs['Config']['Env']
                for env in env_vars:
                    if env.startswith(f'ROOM_NAME={room_name}'):
                        logger.warning(f"⚠️ Container {container.name} already exists for room {room_name}")
                        # Return the existing container instead of creating a new one
                        self.active_containers[site_id][session_id] = container.name
                        return container.name
                        
            # For preview rooms, stop any existing containers for the same client/agent
            # This prevents multiple containers from the same preview session
            if room_name.startswith("preview_"):
                target_prefix = f"agent_{site_id}_{agent_slug}_"
                containers_to_stop = []
                for container in containers:
                    if container.name.startswith(target_prefix):
                        # Check if it's a preview container
                        env_vars = container.attrs['Config']['Env']
                        for env in env_vars:
                            if env.startswith('ROOM_NAME=preview_'):
                                containers_to_stop.append(container)
                                break
                
                # Stop old preview containers for this client/agent
                for container in containers_to_stop:
                    try:
                        logger.info(f"🛑 Stopping old preview container {container.name} for new preview session")
                        container.stop(timeout=5)
                        container.remove(force=True)
                    except Exception as e:
                        logger.warning(f"Failed to stop old preview container {container.name}: {e}")
                        
        except Exception as e:
            logger.error(f"Error checking existing containers: {e}")
        
        # Check if we have a pooled container for this client
        # TEMPORARILY DISABLED: Pooling causes LiveKit connection issues
        if False and self.pools[site_id]:
            # Get a container from the pool
            container_name = self.pools[site_id].pop(0)
            logger.info(f"♻️ Reusing pooled container {container_name} for client {site_id}")
            
            # Reset the container for new session
            await self.reset_container(container_name, session_id, room_name, agent_config)
            
            # Track reuse
            self.container_reuse_counts[container_name] = self.container_reuse_counts.get(container_name, 0) + 1
            logger.info(f"📊 Container {container_name} reused {self.container_reuse_counts[container_name]} times")
            
            # Record active container
            self.active_containers[site_id][session_id] = container_name
            
            # Log deployment as reused
            deployment_logger.log_deployment(container_name, "pooled_reuse", {
                "reuse_count": self.container_reuse_counts[container_name],
                "client_id": site_id,
                "session_id": session_id
            })
            
            return container_name
        else:
            # No container in pool, create new one
            logger.info(f"🆕 No pooled containers for client {site_id}, creating new one")
            
            # Deploy new container
            container_info = await self.deploy_agent_container(
                site_id=site_id,
                agent_slug=agent_slug,
                agent_config=agent_config,
                site_config=site_config,
                session_id=session_id
            )
            
            container_name = container_info["name"]
            
            # Initialize reuse count
            self.container_reuse_counts[container_name] = 1
            
            # Record active container
            self.active_containers[site_id][session_id] = container_name
            
            return container_name
    
    async def reset_container(self, container_name: str, session_id: str, room_name: str, agent_config: Dict[str, Any]) -> bool:
        """Reset container state for reuse with new session"""
        try:
            container = await self.get_container(container_name)
            if not container:
                logger.error(f"Container {container_name} not found for reset")
                return False
            
            logger.info(f"🧹 Resetting container {container_name} for session {session_id}")
            
            # Execute reset commands inside container
            reset_commands = [
                # Clear conversation memory
                "rm -rf /tmp/conversations/*",
                # Clear any cached data
                "rm -rf /tmp/cache/*",
                # Clear metadata files
                "rm -rf /tmp/job_metadata_*.json",
                # Reset Python environment (if agent has a reset script)
                "if [ -f /app/reset.py ]; then python /app/reset.py; fi"
            ]
            
            for cmd in reset_commands:
                try:
                    container.exec_run(cmd, detach=False)
                except Exception as e:
                    logger.warning(f"Reset command failed: {cmd} - {e}")
            
            # Update environment variables for new session
            # Note: We can't update env vars on running container, so we update via metadata file
            metadata = {
                "session_id": session_id,
                "room_name": room_name,
                "reset_at": datetime.utcnow().isoformat()
            }
            
            metadata_path = f"/tmp/container_metadata_{container_name}.json"
            with open(metadata_path, 'w') as f:
                json.dump(metadata, f)
            
            logger.info(f"✅ Container {container_name} reset completed")
            return True
            
        except Exception as e:
            logger.error(f"Failed to reset container {container_name}: {e}")
            return False
    
    async def return_container(self, site_id: str, session_id: str) -> bool:
        """Return a container to the pool or destroy if over reuse limit"""
        try:
            # Find the container for this session
            container_name = self.active_containers[site_id].get(session_id)
            if not container_name:
                logger.warning(f"No active container found for client {site_id}, session {session_id}")
                return False
            
            # Remove from active containers
            del self.active_containers[site_id][session_id]
            
            # Check reuse count
            reuse_count = self.container_reuse_counts.get(container_name, 0)
            
            # Check if container is still healthy
            container = await self.get_container(container_name)
            if not container or container.status != "running":
                logger.warning(f"Container {container_name} not healthy, removing")
                await self.remove_container(container_name)
                del self.container_reuse_counts[container_name]
                return True
            
            # Decide whether to pool or destroy
            current_pool_size = len(self.pools[site_id])
            
            if reuse_count >= self.max_reuse_count:
                # Container has been reused too many times, destroy it
                logger.info(f"🗑️ Container {container_name} reached max reuse ({reuse_count}), destroying")
                await self.stop_container(container_name)
                await self.remove_container(container_name)
                del self.container_reuse_counts[container_name]
            elif current_pool_size >= self.max_pool_size:
                # Pool is full, destroy this container
                logger.info(f"🗑️ Pool full for client {site_id}, destroying container {container_name}")
                await self.stop_container(container_name)
                await self.remove_container(container_name)
                del self.container_reuse_counts[container_name]
            else:
                # Temporarily disable pooling - always destroy containers to ensure clean state
                logger.info(f"🛑 Destroying container {container_name} (pooling disabled for LiveKit cleanup)")
                await self.stop_container(container_name)
                await self.remove_container(container_name)
                if container_name in self.container_reuse_counts:
                    del self.container_reuse_counts[container_name]
                # Old pooling code (disabled):
                # logger.info(f"🏊 Returning container {container_name} to pool for client {site_id}")
                # self.pools[site_id].append(container_name)
                
                deployment_logger.log_deployment(container_name, "returned_to_pool", {
                    "reuse_count": reuse_count,
                    "pool_size": current_pool_size + 1,
                    "client_id": site_id
                })
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to return container for client {site_id}, session {session_id}: {e}")
            return False
    
    async def cleanup_client_pool(self, site_id: str) -> int:
        """Clean up all pooled containers for a client"""
        cleaned = 0
        
        # Stop and remove all pooled containers
        while self.pools[site_id]:
            container_name = self.pools[site_id].pop()
            try:
                await self.stop_container(container_name)
                await self.remove_container(container_name)
                if container_name in self.container_reuse_counts:
                    del self.container_reuse_counts[container_name]
                cleaned += 1
            except Exception as e:
                logger.error(f"Failed to cleanup container {container_name}: {e}")
        
        logger.info(f"🧹 Cleaned up {cleaned} pooled containers for client {site_id}")
        return cleaned
    
    async def deploy_agent_container(
        self,
        site_id: str,
        agent_slug: str,
        agent_config: Dict[str, Any],
        site_config: Dict[str, Any],
        session_id: str = None
    ) -> Dict[str, Any]:
        """Deploy a new agent container for a specific client"""
        if not self.docker_client:
            raise ServiceUnavailableError("Docker not available - container deployment disabled")
        
        container_name = self.get_container_name(site_id, agent_slug, session_id)
        
        try:
            # Log deployment start
            deployment_logger.log_deployment(container_name, "build", {
                "image": self.agent_image,
                "site_id": site_id,
                "agent_slug": agent_slug,
                "tier": site_config.get("tier", "basic")
            })
            
            # Check if container already exists
            existing = await self.get_container(container_name)
            if existing and existing.status == "running":
                # Check if existing container is for the same room
                existing_room = None
                try:
                    inspect = self.docker_client.api.inspect_container(container_name)
                    for env in inspect.get("Config", {}).get("Env", []):
                        if env.startswith("ROOM_NAME="):
                            existing_room = env.split("=", 1)[1]
                            break
                except Exception as e:
                    logger.warning(f"Could not inspect existing container: {e}")
                
                requested_room = agent_config.get("room_name", "")
                if existing_room and existing_room != requested_room:
                    logger.warning(f"⚠️ Container {container_name} exists but is for room '{existing_room}', not '{requested_room}'")
                    logger.info(f"🔄 Stopping existing container to create new one for correct room")
                    await self.stop_container(container_name)
                    await self.remove_container(container_name)
                    # Continue to create new container
                else:
                    logger.info(f"✅ Container {container_name} already running for room '{existing_room or 'unknown'}'")
                    deployment_logger.log_deployment(container_name, "reused", {"status": "already_running", "room": existing_room})
                    return await self.get_container_info(container_name)
            
            # Validate LiveKit credentials are present
            if not agent_config.get("livekit_url") or not agent_config.get("livekit_api_key") or not agent_config.get("livekit_api_secret"):
                logger.error(f"❌ Missing LiveKit credentials for client {site_id}")
                logger.error(f"   - URL: {'Present' if agent_config.get('livekit_url') else 'MISSING'}")
                logger.error(f"   - API Key: {'Present' if agent_config.get('livekit_api_key') else 'MISSING'}")
                logger.error(f"   - API Secret: {'Present' if agent_config.get('livekit_api_secret') else 'MISSING'}")
                raise ValidationError("Client must have LiveKit credentials configured. No fallbacks allowed.")
            
            # Prepare environment variables
            logger.info(f"Deploying container with site_id: {site_id}")
            
            # Debug: Log what LiveKit credentials we're receiving
            logger.info(f"🔍 DEBUG: LiveKit credentials in agent_config:")
            logger.info(f"   - URL from agent_config: {agent_config.get('livekit_url', 'MISSING')}")
            logger.info(f"   - API Key from agent_config: {agent_config.get('livekit_api_key', 'MISSING')[:20]}..." if agent_config.get('livekit_api_key') else "   - API Key: MISSING")
            logger.info(f"   - API Secret from agent_config: {'SET' if agent_config.get('livekit_api_secret') else 'MISSING'}")
            
            env_vars = {
                # Client identification
                "SITE_ID": site_id,
                "CLIENT_ID": site_id,  # Also set CLIENT_ID for compatibility
                "SITE_DOMAIN": site_config.get("domain", ""),
                "AGENT_SLUG": agent_slug,
                "CONTAINER_NAME": container_name,
                "ROOM_NAME": agent_config.get("room_name", ""),  # Room the agent should join
                
                # Backend URL for pool release
                "BACKEND_URL": f"http://host.docker.internal:8000",  # Use Docker host networking
                
                # LiveKit configuration - Use backend credentials for thin client architecture
                "LIVEKIT_URL": settings.livekit_url,
                "LIVEKIT_API_KEY": settings.livekit_api_key,
                "LIVEKIT_API_SECRET": settings.livekit_api_secret,
                
                # Agent configuration
                "AGENT_NAME": agent_config.get("agent_name", "Assistant"),
                "SYSTEM_PROMPT": agent_config.get("system_prompt", ""),
                "MODEL": agent_config.get("model", "gpt-4-turbo-preview"),
                "TEMPERATURE": str(agent_config.get("temperature", 0.7)),
                "MAX_TOKENS": str(agent_config.get("max_tokens", 4096)),
                
                # Voice configuration
                "VOICE_ID": agent_config.get("voice_id", "alloy"),
                "STT_PROVIDER": agent_config.get("stt_provider", "groq"),
                "STT_MODEL": agent_config.get("stt_model", "whisper-large-v3-turbo"),
                "TTS_PROVIDER": agent_config.get("tts_provider", "openai"),
                "TTS_MODEL": agent_config.get("tts_model", ""),
                
                # Supabase configuration (CRITICAL for agent operation and RAG)
                "SUPABASE_URL": settings.supabase_url,
                "SUPABASE_KEY": settings.supabase_service_role_key,  # For RAG system
                "SUPABASE_ANON_KEY": settings.supabase_anon_key,
                "SUPABASE_SERVICE_ROLE_KEY": settings.supabase_service_role_key,
                
                # API Keys (client-specific ONLY - no fallbacks)
                "OPENAI_API_KEY": agent_config.get("openai_api_key", ""),
                "ANTHROPIC_API_KEY": agent_config.get("anthropic_api_key", ""),
                "GROQ_API_KEY": agent_config.get("groq_api_key", ""),
                "DEEPGRAM_API_KEY": agent_config.get("deepgram_api_key", ""),
                "CARTESIA_API_KEY": agent_config.get("cartesia_api_key", ""),
                "ELEVEN_API_KEY": agent_config.get("elevenlabs_api_key", ""),
                
                # Embedding providers
                "NOVITA_API_KEY": agent_config.get("novita_api_key", ""),
                "SILICONFLOW_API_KEY": agent_config.get("siliconflow_api_key", ""),
                
                # Webhooks
                "VOICE_CONTEXT_WEBHOOK_URL": agent_config.get("voice_context_webhook_url", ""),
                "TEXT_CONTEXT_WEBHOOK_URL": agent_config.get("text_context_webhook_url", ""),
                
                # Backend communication
                "BACKEND_URL": f"http://fastapi:8000",
                "BACKEND_API_KEY": self._generate_internal_api_key(site_id),
                
                # Monitoring
                "LOG_LEVEL": settings.log_level,
                "ENABLE_METRICS": "true"
            }
            
            # Debug API keys
            cartesia_key = env_vars.get("CARTESIA_API_KEY")
            if cartesia_key:
                logger.info(f"📝 Cartesia API key in env_vars: length={len(cartesia_key)}, value={repr(cartesia_key)}")
            else:
                logger.info("❌ No Cartesia API key in env_vars")
            
            # Get resource limits based on client tier
            client_tier = site_config.get("tier", "basic").lower()
            resource_limits = self._get_resource_limits_for_tier(client_tier)
            
            # Container configuration
            container_config = {
                "image": self.agent_image,
                "name": container_name,
                "environment": env_vars,
                "network": self.network_name,
                # Apply patch and start
                "command": [
                    "/bin/bash", "-c",
                    "python3 /patch_chatmessage.py && ./start_agent.sh"
                ],
                "labels": {
                    "autonomite.site_id": site_id,
                    "autonomite.agent_slug": agent_slug,
                    "autonomite.managed": "true",
                    "autonomite.tier": client_tier,
                    "autonomite.created_at": datetime.utcnow().isoformat()
                },
                "restart_policy": {"Name": "unless-stopped"},
                "mem_limit": resource_limits["memory"],
                "cpu_quota": resource_limits["cpu_quota"],
                "cpu_period": 100000,  # Period for CPU quota (100ms)
                "detach": True,
                # Mount /tmp directory to share metadata files and patch script
                "volumes": {
                    "/tmp": {
                        "bind": "/tmp",
                        "mode": "rw"
                    },
                    "/opt/autonomite-saas/agent-runtime/patch_chatmessage.py": {
                        "bind": "/patch_chatmessage.py",
                        "mode": "ro"
                    }
                }
                # Health check disabled temporarily
                # "healthcheck": {
                #     "test": ["CMD", "curl", "-f", "http://localhost:8080/health"],
                #     "interval": 30000000000,  # 30s in nanoseconds
                #     "timeout": 10000000000,   # 10s in nanoseconds
                #     "retries": 3
                # }
            }
            
            # Remove existing stopped container if any
            if existing:
                await self.remove_container(container_name)
            
            # Log configuration
            deployment_logger.log_deployment(container_name, "configure", {
                "env_vars_count": len(env_vars),
                "resource_limits": resource_limits,
                "has_api_keys": {
                    "cartesia": bool(env_vars.get("CARTESIA_API_KEY")),
                    "deepgram": bool(env_vars.get("DEEPGRAM_API_KEY")),
                    "groq": bool(env_vars.get("GROQ_API_KEY"))
                }
            })
            
            # Create and start container with retry logic
            max_retries = 3
            retry_count = 0
            container = None
            
            while retry_count < max_retries:
                try:
                    container = self.docker_client.containers.run(**container_config)
                    logger.info(f"Deployed agent container: {container_name} (attempt {retry_count + 1})")
                    break
                except docker.errors.APIError as e:
                    retry_count += 1
                    if retry_count >= max_retries:
                        raise
                    logger.warning(f"Container creation failed (attempt {retry_count}): {e}")
                    await asyncio.sleep(2)  # Wait before retry
            
            if not container:
                raise ServiceUnavailableError("Failed to create container after retries")
            
            deployment_logger.log_deployment(container_name, "started", {
                "container_id": container.id[:12],
                "status": container.status,
                "retries": retry_count
            })
            
            # Wait for container to be running
            await self._wait_for_container_running(container_name, timeout=30)
            
            # Check if container has basic connectivity
            if await self._check_container_basic_health(container_name):
                logger.info(f"✅ Container {container_name} is healthy")
            else:
                logger.warning(f"⚠️ Container {container_name} may have issues")
            
            return await self.get_container_info(container_name)
            
        except Exception as e:
            logger.error(f"Failed to deploy container {container_name}: {e}")
            raise ServiceUnavailableError(f"Failed to deploy agent container: {str(e)}")
    
    async def get_container(self, container_name: str) -> Optional[Any]:
        """Get container by name"""
        if not self.docker_client:
            return None
        
        try:
            return self.docker_client.containers.get(container_name)
        except docker.errors.NotFound:
            return None
        except Exception as e:
            logger.error(f"Error getting container {container_name}: {e}")
            return None
    
    async def get_container_info(self, container_name: str) -> Dict[str, Any]:
        """Get detailed container information"""
        container = await self.get_container(container_name)
        if not container:
            return None
        
        return {
            "id": container.id,
            "name": container.name,
            "status": container.status,
            "created": container.attrs["Created"],
            "labels": container.labels,
            # Skip stats collection - it's too slow
            # "stats": await self._get_container_stats(container),
            # "health": await self._get_container_health(container)
        }
    
    async def stop_container(self, container_name: str) -> bool:
        """Stop a running container"""
        try:
            container = await self.get_container(container_name)
            if container:
                container.stop(timeout=30)
                logger.info(f"Stopped container: {container_name}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to stop container {container_name}: {e}")
            return False
    
    async def remove_container(self, container_name: str) -> bool:
        """Remove a container"""
        try:
            container = await self.get_container(container_name)
            if container:
                container.remove(force=True)
                logger.info(f"Removed container: {container_name}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to remove container {container_name}: {e}")
            return False
    
    async def list_client_containers(self, site_id: str) -> List[Dict[str, Any]]:
        """List all containers for a specific client"""
        try:
            containers = self.docker_client.containers.list(
                all=True,
                filters={
                    "label": f"autonomite.site_id={site_id}"
                }
            )
            
            return [
                {
                    "name": c.name,
                    "status": c.status,
                    "agent_slug": c.labels.get("autonomite.agent_slug"),
                    "created_at": c.labels.get("autonomite.created_at")
                }
                for c in containers
            ]
        except Exception as e:
            logger.error(f"Failed to list containers for site {site_id}: {e}")
            return []
    
    async def get_container_logs(
        self,
        container_name: str,
        lines: int = 100,
        since: Optional[datetime] = None
    ) -> str:
        """Get container logs"""
        try:
            container = await self.get_container(container_name)
            if not container:
                return ""
            
            kwargs = {"tail": lines}
            if since:
                kwargs["since"] = since
            
            return container.logs(**kwargs).decode("utf-8")
            
        except Exception as e:
            logger.error(f"Failed to get logs for {container_name}: {e}")
            return ""
    
    async def restart_container(self, container_name: str) -> bool:
        """Restart a container"""
        try:
            container = await self.get_container(container_name)
            if container:
                container.restart(timeout=30)
                logger.info(f"Restarted container: {container_name}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to restart container {container_name}: {e}")
            return False
    
    async def scale_container(
        self,
        container_name: str,
        cpu_limit: float = 1.0,
        memory_limit: str = "1g"
    ) -> bool:
        """Update container resource limits"""
        try:
            container = await self.get_container(container_name)
            if not container:
                return False
            
            # Update container resources
            container.update(
                cpu_quota=int(cpu_limit * 100000),
                mem_limit=memory_limit
            )
            
            logger.info(f"Updated resources for {container_name}: CPU={cpu_limit}, Memory={memory_limit}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to scale container {container_name}: {e}")
            return False
    
    async def _wait_for_healthy(self, container_name: str, timeout: int = 60) -> bool:
        """Wait for container to become healthy"""
        start_time = datetime.utcnow()
        
        while (datetime.utcnow() - start_time).total_seconds() < timeout:
            container = await self.get_container(container_name)
            if not container:
                return False
            
            health = await self._get_container_health(container)
            if health.get("status") == "healthy":
                return True
            
            await asyncio.sleep(2)
        
        return False
    
    async def _get_container_stats(self, container) -> Dict[str, Any]:
        """Get container resource statistics"""
        try:
            stats = container.stats(stream=False)
            
            # Calculate CPU percentage
            cpu_delta = stats["cpu_stats"]["cpu_usage"]["total_usage"] - \
                       stats["precpu_stats"]["cpu_usage"]["total_usage"]
            system_delta = stats["cpu_stats"]["system_cpu_usage"] - \
                          stats["precpu_stats"]["system_cpu_usage"]
            cpu_percent = (cpu_delta / system_delta) * 100.0 if system_delta > 0 else 0.0
            
            # Memory usage
            memory_usage = stats["memory_stats"]["usage"]
            memory_limit = stats["memory_stats"]["limit"]
            memory_percent = (memory_usage / memory_limit) * 100.0 if memory_limit > 0 else 0.0
            
            return {
                "cpu_percent": round(cpu_percent, 2),
                "memory_usage_mb": round(memory_usage / 1024 / 1024, 2),
                "memory_limit_mb": round(memory_limit / 1024 / 1024, 2),
                "memory_percent": round(memory_percent, 2)
            }
        except Exception:
            return {}
    
    async def _get_container_health(self, container) -> Dict[str, str]:
        """Get container health status"""
        try:
            health = container.attrs.get("State", {}).get("Health", {})
            return {
                "status": health.get("Status", "unknown"),
                "failing_streak": health.get("FailingStreak", 0)
            }
        except Exception:
            return {"status": "unknown", "failing_streak": 0}
    
    def _get_resource_limits_for_tier(self, tier: str) -> Dict[str, Any]:
        """
        Get resource limits based on client tier
        
        Returns memory limit and CPU quota based on tier:
        - Basic: 512MB RAM, 0.5 CPU
        - Pro: 1GB RAM, 1.0 CPU
        - Enterprise: 2GB RAM, 2.0 CPU
        """
        tier_limits = {
            "basic": {
                "memory": "512m",
                "cpu_quota": 50000,  # 0.5 CPU (50% of 100ms period)
            },
            "pro": {
                "memory": "1g", 
                "cpu_quota": 100000,  # 1.0 CPU (100% of 100ms period)
            },
            "enterprise": {
                "memory": "2g",
                "cpu_quota": 200000,  # 2.0 CPU (200% of 100ms period)
            }
        }
        
        # Default to basic if tier not recognized
        return tier_limits.get(tier, tier_limits["basic"])
    
    def _generate_internal_api_key(self, site_id: str) -> str:
        """Generate internal API key for container-to-backend communication"""
        import hashlib
        import secrets
        
        # Generate a deterministic but secure key that doesn't change daily
        # Use site_id and secret_key for consistency
        seed = f"{site_id}:{settings.secret_key}:autonomite-internal"
        return hashlib.sha256(seed.encode()).hexdigest()
    
    async def _wait_for_container_running(self, container_name: str, timeout: int = 30) -> bool:
        """Wait for container to be in running state"""
        start_time = asyncio.get_event_loop().time()
        
        while asyncio.get_event_loop().time() - start_time < timeout:
            container = await self.get_container(container_name)
            if container and container.status == "running":
                return True
            elif container and container.status in ["exited", "dead"]:
                # Container failed to start
                logs = container.logs(tail=100).decode('utf-8')
                logger.error(f"Container {container_name} failed to start. Last logs:\n{logs}")
                return False
            
            await asyncio.sleep(1)
        
        logger.warning(f"Container {container_name} did not start within {timeout} seconds")
        return False
    
    async def _check_container_basic_health(self, container_name: str) -> bool:
        """Check basic health of container"""
        try:
            container = await self.get_container(container_name)
            if not container:
                return False
            
            # Check if container is still running
            container.reload()
            if container.status != "running":
                return False
            
            # Check if main process is running
            top_result = container.top()
            if not top_result or 'Processes' not in top_result or not top_result['Processes']:
                logger.warning(f"No processes found in container {container_name}")
                return False
            
            # Check for python process
            processes = top_result['Processes']
            python_found = any('python' in ' '.join(proc) for proc in processes)
            if not python_found:
                logger.warning(f"Python process not found in container {container_name}")
                return False
            
            # Check logs for startup errors
            logs = container.logs(tail=50).decode('utf-8')
            error_indicators = [
                "ERROR",
                "Failed to",
                "Could not",
                "Missing required",
                "API key not found",
                "Authentication failed"
            ]
            
            for indicator in error_indicators:
                if indicator in logs:
                    logger.warning(f"Found error indicator '{indicator}' in container logs")
                    # Don't fail on errors, just warn
                    # return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error checking container health: {e}")
            return False
    
    async def stop_agent_for_room(self, room_name: str) -> Dict[str, Any]:
        """
        Stop agent containers associated with a specific room
        
        This method now returns containers to the pool instead of stopping them outright,
        unless they've exceeded reuse limits or the pool is full.
        """
        logger.info(f"🛑 Processing containers for room {room_name}")
        
        stopped_containers = []
        returned_to_pool = []
        errors = []
        
        try:
            # Get all agent containers
            containers = self.docker_client.containers.list(
                filters={"name": "agent_*"},
                all=True  # Include stopped containers
            )
            
            for container in containers:
                try:
                    # Check if this container was created for the specified room
                    # by looking at environment variables or metadata
                    env_vars = container.attrs.get("Config", {}).get("Env", [])
                    
                    # Look for room name in environment variables
                    container_room = None
                    site_id = None
                    for env in env_vars:
                        if env.startswith("ROOM_NAME="):
                            container_room = env.split("=", 1)[1]
                        elif env.startswith("SITE_ID="):
                            site_id = env.split("=", 1)[1]
                    
                    # Also check metadata file path for room name
                    if not container_room:
                        for env in env_vars:
                            if env.startswith("METADATA_FILE=") and room_name in env:
                                container_room = room_name
                                break
                    
                    # Process container if it matches the room
                    if container_room == room_name:
                        if container.status == "running" and site_id:
                            # Try to find the session ID from active containers
                            session_id = None
                            for sid, cname in self.active_containers.get(site_id, {}).items():
                                if cname == container.name:
                                    session_id = sid
                                    break
                            
                            if session_id:
                                # Return container to pool
                                logger.info(f"🏊 Attempting to return container {container.name} to pool")
                                returned = await self.return_container(site_id, session_id)
                                if returned:
                                    returned_to_pool.append({
                                        "container_id": container.id[:12],
                                        "container_name": container.name,
                                        "room_name": room_name,
                                        "action": "returned_to_pool"
                                    })
                                else:
                                    # If return failed, stop it
                                    container.stop(timeout=10)
                                    stopped_containers.append({
                                        "container_id": container.id[:12],
                                        "container_name": container.name,
                                        "room_name": room_name,
                                        "action": "stopped"
                                    })
                            else:
                                # No session ID found, just stop it
                                logger.warning(f"No session ID found for container {container.name}, stopping")
                                container.stop(timeout=10)
                                stopped_containers.append({
                                    "container_id": container.id[:12],
                                    "container_name": container.name,
                                    "room_name": room_name,
                                    "action": "stopped"
                                })
                        else:
                            logger.info(f"Container {container.name} already stopped for room {room_name}")
                            stopped_containers.append({
                                "container_id": container.id[:12],
                                "container_name": container.name,
                                "room_name": room_name,
                                "action": "already_stopped"
                            })
                
                except Exception as e:
                    error_msg = f"Error processing container {container.name}: {str(e)}"
                    logger.error(error_msg)
                    errors.append(error_msg)
            
            # Clean up metadata files for the room
            try:
                import glob
                metadata_files = glob.glob(f"/tmp/job_metadata_*{room_name}*.json")
                for file_path in metadata_files:
                    try:
                        os.remove(file_path)
                        logger.info(f"Cleaned up metadata file: {file_path}")
                    except Exception as e:
                        logger.error(f"Error removing metadata file {file_path}: {e}")
            except Exception as e:
                logger.error(f"Error cleaning up metadata files: {e}")
            
            return {
                "success": len(errors) == 0,
                "room_name": room_name,
                "stopped_containers": stopped_containers,
                "returned_to_pool": returned_to_pool,
                "errors": errors,
                "message": f"Processed {len(stopped_containers) + len(returned_to_pool)} containers for room {room_name} ({len(returned_to_pool)} returned to pool, {len(stopped_containers)} stopped)"
            }
            
        except Exception as e:
            error_msg = f"Fatal error stopping agents for room {room_name}: {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "room_name": room_name,
                "errors": [error_msg],
                "stopped_containers": [],
                "message": "Failed to stop agent containers"
            }

# Create singleton instance
container_manager = ContainerManager()
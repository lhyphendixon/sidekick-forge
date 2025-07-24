"""
Container Pool Manager for warm container pools

Implements pre-warmed container pools for instant agent availability,
replacing on-demand spawning with pool-based allocation.
"""
import asyncio
import logging
from typing import Dict, List, Optional, Set, Any
from datetime import datetime, timedelta
import uuid
from dataclasses import dataclass, field
from enum import Enum
import json

from app.config import settings
from app.services.container_manager import ContainerManager
from app.services.client_service_supabase import ClientService
from app.services.agent_service_supabase import AgentService

logger = logging.getLogger(__name__)


class ContainerState(str, Enum):
    """Container states in the pool"""
    IDLE = "idle"          # Available for allocation
    ALLOCATED = "allocated"  # Assigned to a session
    WARMING = "warming"    # Being prepared
    UNHEALTHY = "unhealthy"  # Failed health checks
    DRAINING = "draining"  # Being removed from pool


@dataclass
class PooledContainer:
    """Container in the warm pool"""
    container_name: str
    client_id: str
    agent_slug: str
    state: ContainerState
    created_at: datetime
    last_used: Optional[datetime] = None
    session_count: int = 0
    health_check_failures: int = 0
    allocated_to: Optional[str] = None  # room_name when allocated
    metadata: Dict[str, Any] = field(default_factory=dict)


class ContainerPoolManager:
    """
    Manages warm pools of containers for each client/agent combination
    
    Features:
    - Pre-warmed containers for instant availability
    - Automatic scaling based on demand
    - Health monitoring and container recycling
    - Session state isolation
    """
    
    def __init__(
        self,
        container_manager: ContainerManager,
        client_service: ClientService,
        agent_service: AgentService,
        min_pool_size: int = 2,
        max_pool_size: int = 10,
        max_sessions_per_container: int = 10,
        container_ttl_minutes: int = 60,
        health_check_interval: int = 30
    ):
        self.container_manager = container_manager
        self.client_service = client_service
        self.agent_service = agent_service
        
        # Pool configuration
        self.min_pool_size = min_pool_size
        self.max_pool_size = max_pool_size
        self.max_sessions_per_container = max_sessions_per_container
        self.container_ttl = timedelta(minutes=container_ttl_minutes)
        self.health_check_interval = health_check_interval
        
        # Pool state
        self.pools: Dict[str, List[PooledContainer]] = {}  # key: f"{client_id}:{agent_slug}"
        self.pool_locks: Dict[str, asyncio.Lock] = {}
        self._maintenance_task: Optional[asyncio.Task] = None
        self._started = False
        
        logger.info(f"🏊 Container Pool Manager initialized with pool size {min_pool_size}-{max_pool_size}")
    
    async def start(self):
        """Start the pool manager and maintenance tasks"""
        if self._started:
            return
            
        self._started = True
        
        # Restore pools from Redis if available
        await self._restore_pools_from_redis()
        
        self._maintenance_task = asyncio.create_task(self._maintenance_loop())
        logger.info("✅ Container Pool Manager started")
    
    async def stop(self):
        """Stop the pool manager and drain all pools"""
        if not self._started:
            return
            
        self._started = False
        
        # Cancel maintenance task
        if self._maintenance_task:
            self._maintenance_task.cancel()
            try:
                await self._maintenance_task
            except asyncio.CancelledError:
                pass
        
        # Drain all pools
        for pool_key in list(self.pools.keys()):
            await self._drain_pool(pool_key)
            
        logger.info("🛑 Container Pool Manager stopped")
    
    def _get_pool_key(self, client_id: str, agent_slug: str) -> str:
        """Get pool key for client/agent combination"""
        return f"{client_id}:{agent_slug}"
    
    async def _restore_pools_from_redis(self):
        """Restore container pools from Redis on startup"""
        if not hasattr(self, 'client_service') or not self.client_service:
            logger.warning("Client service not available for pool restoration")
            return
            
        try:
            # Find all idle pool keys in Redis
            import redis.asyncio as redis
            redis_client = await redis.from_url("redis://localhost:6379", decode_responses=True)
            
            pool_keys = await redis_client.keys("client:*:container_pool:idle")
            logger.info(f"🔄 Found {len(pool_keys)} pool keys in Redis to restore")
            
            for redis_key in pool_keys:
                # Extract client_id from key
                parts = redis_key.split(":")
                if len(parts) >= 2:
                    client_id = parts[1]
                    
                    # Get all containers in this pool
                    container_names = await redis_client.smembers(redis_key)
                    
                    for container_name in container_names:
                        # Extract agent_slug from container name
                        container_parts = container_name.split("_")
                        if len(container_parts) >= 3:
                            # Handle different container name formats:
                            # 1. agent_{client_id}_{agent_slug}_session_{session_id}
                            # 2. agent_{client_id}_{agent_slug}_{session_id}
                            # 3. agent_{client_id}_{agent_slug}
                            
                            # Find the "session" separator if it exists
                            session_index = -1
                            for i, part in enumerate(container_parts):
                                if part == "session":
                                    session_index = i
                                    break
                            
                            if session_index > 2:
                                # Everything between client_id and "session" is the agent_slug
                                agent_slug_parts = container_parts[2:session_index]
                                agent_slug = "_".join(agent_slug_parts)
                            elif len(container_parts) == 4 and len(container_parts[3]) == 8:
                                # Likely format: agent_{client_id}_{agent_slug}_{8-char-session}
                                agent_slug = container_parts[2]
                            elif len(container_parts) >= 4:
                                # For containers with compound agent slugs, try to determine the slug
                                # by looking at all parts between client_id and any session identifier
                                agent_slug = container_parts[2]
                            else:
                                agent_slug = container_parts[2]
                            
                            pool_key = self._get_pool_key(client_id, agent_slug)
                            
                            # Initialize pool if needed
                            if pool_key not in self.pools:
                                self.pools[pool_key] = []
                            
                            # Create PooledContainer object
                            container = PooledContainer(
                                container_name=container_name,
                                client_id=client_id,
                                agent_slug=agent_slug,
                                state=ContainerState.IDLE,
                                created_at=datetime.now(),
                                last_used=datetime.now()
                            )
                            
                            # Add to in-memory pool
                            self.pools[pool_key].append(container)
                            logger.info(f"✅ Restored container {container_name} to pool {pool_key}")
            
            await redis_client.close()
            
            # Log pool status
            total_containers = sum(len(pool) for pool in self.pools.values())
            logger.info(f"📊 Restored {total_containers} containers across {len(self.pools)} pools")
            
        except Exception as e:
            logger.error(f"Failed to restore pools from Redis: {e}")
    
    async def _get_pool_lock(self, pool_key: str) -> asyncio.Lock:
        """Get or create lock for pool"""
        if pool_key not in self.pool_locks:
            self.pool_locks[pool_key] = asyncio.Lock()
        return self.pool_locks[pool_key]
    
    async def allocate_container(
        self,
        client_id: str,
        agent_slug: str,
        room_name: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[PooledContainer]:
        """
        Allocate a container from the warm pool
        
        Returns immediately with a pre-warmed container if available,
        otherwise creates one on-demand while maintaining pool size.
        """
        pool_key = self._get_pool_key(client_id, agent_slug)
        pool_lock = await self._get_pool_lock(pool_key)
        
        async with pool_lock:
            # Initialize pool if needed
            if pool_key not in self.pools:
                self.pools[pool_key] = []
                asyncio.create_task(self._ensure_pool_size(pool_key, client_id, agent_slug))
            
            pool = self.pools[pool_key]
            
            # Find an idle container
            for container in pool:
                if container.state == ContainerState.IDLE:
                    # Allocate the container
                    container.state = ContainerState.ALLOCATED
                    container.allocated_to = room_name
                    container.last_used = datetime.now()
                    container.session_count += 1
                    
                    if metadata:
                        container.metadata.update(metadata)
                    
                    logger.info(f"✅ Allocated warm container {container.container_name} for {room_name}")
                    
                    # Trigger pool refill in background
                    asyncio.create_task(self._ensure_pool_size(pool_key, client_id, agent_slug))
                    
                    return container
            
            # No idle containers available - check if we can create more
            allocated_count = sum(1 for c in pool if c.state == ContainerState.ALLOCATED)
            total_count = len(pool)
            
            if total_count < self.max_pool_size:
                # Create on-demand container
                logger.info(f"🔄 No idle containers, creating on-demand for {room_name}")
                container = await self._create_container(client_id, agent_slug)
                if container:
                    container.state = ContainerState.ALLOCATED
                    container.allocated_to = room_name
                    container.last_used = datetime.now()
                    container.session_count = 1
                    
                    if metadata:
                        container.metadata.update(metadata)
                    
                    pool.append(container)
                    return container
            else:
                logger.warning(f"❌ Pool at max capacity ({self.max_pool_size}) for {pool_key}")
                return None
    
    async def release_container(
        self,
        client_id: str,
        agent_slug: str,
        container_name: str,
        force_recycle: bool = False
    ):
        """
        Release a container back to the pool
        
        Performs state cleanup and decides whether to recycle based on
        session count and health.
        """
        pool_key = self._get_pool_key(client_id, agent_slug)
        pool_lock = await self._get_pool_lock(pool_key)
        
        async with pool_lock:
            pool = self.pools.get(pool_key, [])
            
            for container in pool:
                if container.container_name == container_name:
                    # Clear allocation
                    container.allocated_to = None
                    
                    # Check if container should be recycled
                    should_recycle = (
                        force_recycle or
                        container.session_count >= self.max_sessions_per_container or
                        container.health_check_failures > 2 or
                        (datetime.now() - container.created_at) > self.container_ttl
                    )
                    
                    if should_recycle:
                        logger.info(f"♻️ Recycling container {container_name} after {container.session_count} sessions")
                        container.state = ContainerState.DRAINING
                        asyncio.create_task(self._recycle_container(pool_key, container))
                    else:
                        # Perform state cleanup
                        logger.info(f"🧹 Cleaning container {container_name} for reuse")
                        cleanup_success = await self._cleanup_container_state(container)
                        
                        if cleanup_success:
                            container.state = ContainerState.IDLE
                            logger.info(f"✅ Container {container_name} returned to pool")
                        else:
                            logger.warning(f"❌ Cleanup failed for {container_name}, marking for recycling")
                            container.state = ContainerState.DRAINING
                            asyncio.create_task(self._recycle_container(pool_key, container))
                    
                    break
    
    async def _create_container(self, client_id: str, agent_slug: str) -> Optional[PooledContainer]:
        """Create a new container for the pool"""
        try:
            # Get client and agent details
            client = await self.client_service.get_client(client_id)
            agent = await self.agent_service.get_agent(client_id, agent_slug)
            
            if not client or not agent:
                logger.error(f"Failed to get client/agent details for {client_id}/{agent_slug}")
                return None
            
            # Prepare agent configuration with client's LiveKit credentials
            agent_config = {
                "agent_slug": agent.slug,
                "agent_name": agent.name,
                "system_prompt": agent.system_prompt,
                "voice_id": agent.voice_settings.voice_id if agent.voice_settings else None,
                "voice_settings": agent.voice_settings.model_dump() if agent.voice_settings else {},
                "llm_settings": agent.llm_settings.model_dump() if hasattr(agent, 'llm_settings') and agent.llm_settings else {},
                "enable_rag": agent.enable_rag if hasattr(agent, 'enable_rag') else False,
                "files": agent.files if hasattr(agent, 'files') else [],
                "webhooks": agent.webhooks.model_dump() if agent.webhooks else {},
                "api_keys": client.settings.api_keys.model_dump() if client.settings and client.settings.api_keys else {}
            }
            
            # Add client's LiveKit credentials - CRITICAL for multi-tenant isolation
            if client.settings and client.settings.livekit:
                agent_config["livekit_url"] = client.settings.livekit.url
                agent_config["livekit_api_key"] = client.settings.livekit.api_key
                agent_config["livekit_api_secret"] = client.settings.livekit.api_secret
                
                # Explicit confirmation of client-specific credentials
                logger.info(f"🔐 CONFIRMED: Using CLIENT-SPECIFIC LiveKit credentials for container:")
                logger.info(f"   - Client: {client.name} (ID: {client_id})")
                logger.info(f"   - LiveKit URL: {agent_config['livekit_url']}")
                logger.info(f"   - API Key: {agent_config['livekit_api_key'][:20]}... (CLIENT-SPECIFIC)")
                logger.info(f"   - This ensures per-client billing, logging, and migration capabilities")
            else:
                logger.error(f"❌ Client {client_id} missing LiveKit credentials - container creation will fail")
            
            # Create container
            container_name = f"agent_{client_id}_{agent_slug}_{uuid.uuid4().hex[:8]}"
            
            success = await self.container_manager.create_container(
                agent_name=container_name,
                agent_config=agent_config,
                client_id=client_id,
                resource_limits={
                    "memory": client.tier_limits.get("memory_limit", "512m"),
                    "cpus": client.tier_limits.get("cpu_limit", "0.5")
                }
            )
            
            if success:
                container = PooledContainer(
                    container_name=container_name,
                    client_id=client_id,
                    agent_slug=agent_slug,
                    state=ContainerState.WARMING,
                    created_at=datetime.now()
                )
                
                # Wait for container to be ready
                ready = await self._wait_for_container_ready(container_name)
                if ready:
                    container.state = ContainerState.IDLE
                    logger.info(f"✅ Created warm container {container_name}")
                    return container
                else:
                    logger.error(f"Container {container_name} failed to become ready")
                    await self.container_manager.stop_container(container_name)
                    return None
            
        except Exception as e:
            logger.error(f"Failed to create container: {e}")
            return None
    
    async def _wait_for_container_ready(self, container_name: str, timeout: int = 30) -> bool:
        """Wait for container to be ready"""
        start_time = datetime.now()
        
        while (datetime.now() - start_time).seconds < timeout:
            info = await self.container_manager.get_container_info(container_name)
            if info and info.get("status") == "running":
                # Container is running, consider it ready
                return True
            await asyncio.sleep(1)
        
        return False
    
    async def _cleanup_container_state(self, container: PooledContainer) -> bool:
        """
        Clean up container state between sessions
        
        Sends a state reset command to the agent to clear:
        - Conversation history
        - User context
        - Any cached data
        """
        try:
            # Send state reset command to container
            reset_command = {
                "action": "reset_state",
                "timestamp": datetime.now().isoformat()
            }
            
            # Execute reset command in container
            exec_result = await self.container_manager.execute_in_container(
                container.container_name,
                f"echo '{json.dumps(reset_command)}' | python -m agent_runtime.state_reset"
            )
            
            if exec_result and exec_result.get("ExitCode") == 0:
                logger.info(f"✅ State reset successful for {container.container_name}")
                return True
            else:
                logger.error(f"State reset failed for {container.container_name}: {exec_result}")
                return False
                
        except Exception as e:
            logger.error(f"Error during state cleanup: {e}")
            return False
    
    async def _recycle_container(self, pool_key: str, container: PooledContainer):
        """Recycle a container by stopping it and creating a replacement"""
        try:
            # Stop the container
            await self.container_manager.stop_container(container.container_name)
            
            # Remove from pool
            pool_lock = await self._get_pool_lock(pool_key)
            async with pool_lock:
                pool = self.pools.get(pool_key, [])
                if container in pool:
                    pool.remove(container)
            
            logger.info(f"♻️ Recycled container {container.container_name}")
            
            # Trigger pool refill
            client_id, agent_slug = pool_key.split(":", 1)
            asyncio.create_task(self._ensure_pool_size(pool_key, client_id, agent_slug))
            
        except Exception as e:
            logger.error(f"Error recycling container: {e}")
    
    async def _ensure_pool_size(self, pool_key: str, client_id: str, agent_slug: str):
        """Ensure pool has minimum number of idle containers with SDK auto-scaling awareness"""
        pool_lock = await self._get_pool_lock(pool_key)
        
        async with pool_lock:
            pool = self.pools.get(pool_key, [])
            
            # Count containers by state
            idle_count = sum(1 for c in pool if c.state == ContainerState.IDLE)
            allocated_count = sum(1 for c in pool if c.state == ContainerState.ALLOCATED)
            total_count = len(pool)
            
            # Calculate demand-based scaling hints (LiveKit SDK pattern)
            utilization_rate = allocated_count / max(total_count, 1)
            high_demand = utilization_rate > 0.8  # Over 80% utilization
            
            # Adjust target based on demand signals
            target_idle = self.min_pool_size
            if high_demand and total_count < self.max_pool_size:
                # Scale up proactively when demand is high
                target_idle = min(self.min_pool_size + 2, self.max_pool_size - allocated_count)
                logger.info(f"📈 High demand detected ({utilization_rate:.0%} utilization) - scaling up pool")
            
            # Create containers to meet target
            while idle_count < target_idle and len(pool) < self.max_pool_size:
                logger.info(f"🔄 Creating container to maintain pool size for {pool_key} (target idle: {target_idle})")
                container = await self._create_container(client_id, agent_slug)
                if container:
                    pool.append(container)
                    idle_count += 1
                else:
                    break
            
            # Log pool health for SDK monitoring
            logger.info(f"📊 Pool {pool_key} status: {idle_count} idle, {allocated_count} allocated, {total_count} total")
    
    async def _health_check_container(self, container: PooledContainer) -> bool:
        """Perform health check on container"""
        try:
            info = await self.container_manager.get_container_info(container.container_name)
            
            if info and info.get("status") == "running":
                container.health_check_failures = 0
                return True
            else:
                container.health_check_failures += 1
                return False
                
        except Exception as e:
            logger.error(f"Health check failed for {container.container_name}: {e}")
            container.health_check_failures += 1
            return False
    
    async def _maintenance_loop(self):
        """Background maintenance of container pools"""
        while self._started:
            try:
                for pool_key, pool in list(self.pools.items()):
                    pool_lock = await self._get_pool_lock(pool_key)
                    
                    async with pool_lock:
                        # Health check idle containers
                        for container in pool:
                            if container.state == ContainerState.IDLE:
                                healthy = await self._health_check_container(container)
                                if not healthy and container.health_check_failures > 2:
                                    container.state = ContainerState.UNHEALTHY
                                    asyncio.create_task(self._recycle_container(pool_key, container))
                        
                        # Remove expired containers
                        now = datetime.now()
                        for container in pool:
                            if container.state == ContainerState.IDLE:
                                if (now - container.created_at) > self.container_ttl:
                                    logger.info(f"🕐 Container {container.container_name} expired")
                                    container.state = ContainerState.DRAINING
                                    asyncio.create_task(self._recycle_container(pool_key, container))
                        
                        # Ensure minimum pool size
                        client_id, agent_slug = pool_key.split(":", 1)
                        asyncio.create_task(self._ensure_pool_size(pool_key, client_id, agent_slug))
                
                # Wait before next maintenance cycle
                await asyncio.sleep(self.health_check_interval)
                
            except Exception as e:
                logger.error(f"Error in maintenance loop: {e}")
                await asyncio.sleep(self.health_check_interval)
    
    async def _drain_pool(self, pool_key: str):
        """Drain all containers from a pool"""
        pool_lock = await self._get_pool_lock(pool_key)
        
        async with pool_lock:
            pool = self.pools.get(pool_key, [])
            
            for container in pool:
                try:
                    await self.container_manager.stop_container(container.container_name)
                except Exception as e:
                    logger.error(f"Error stopping container during drain: {e}")
            
            self.pools[pool_key] = []
            logger.info(f"🚿 Drained pool {pool_key}")
    
    def get_pool_stats(self) -> Dict[str, Any]:
        """Get statistics about all pools"""
        stats = {
            "pools": {},
            "total_containers": 0,
            "idle_containers": 0,
            "allocated_containers": 0
        }
        
        for pool_key, pool in self.pools.items():
            idle_count = sum(1 for c in pool if c.state == ContainerState.IDLE)
            allocated_count = sum(1 for c in pool if c.state == ContainerState.ALLOCATED)
            
            stats["pools"][pool_key] = {
                "total": len(pool),
                "idle": idle_count,
                "allocated": allocated_count,
                "warming": sum(1 for c in pool if c.state == ContainerState.WARMING),
                "unhealthy": sum(1 for c in pool if c.state == ContainerState.UNHEALTHY),
                "draining": sum(1 for c in pool if c.state == ContainerState.DRAINING)
            }
            
            stats["total_containers"] += len(pool)
            stats["idle_containers"] += idle_count
            stats["allocated_containers"] += allocated_count
        
        return stats


# Singleton instance
_pool_manager: Optional[ContainerPoolManager] = None


def get_container_pool_manager() -> ContainerPoolManager:
    """Get the singleton container pool manager"""
    global _pool_manager
    
    if _pool_manager is None:
        from app.services.container_manager import container_manager
        from app.core.dependencies import get_client_service, get_agent_service
        
        _pool_manager = ContainerPoolManager(
            container_manager=container_manager,
            client_service=get_client_service(),
            agent_service=get_agent_service(),
            min_pool_size=settings.CONTAINER_POOL_MIN_SIZE if hasattr(settings, 'CONTAINER_POOL_MIN_SIZE') else 2,
            max_pool_size=settings.CONTAINER_POOL_MAX_SIZE if hasattr(settings, 'CONTAINER_POOL_MAX_SIZE') else 10
        )
    
    return _pool_manager
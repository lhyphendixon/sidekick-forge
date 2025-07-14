import docker
import asyncio
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime
import os
import json

from app.config import settings
from app.utils.exceptions import ServiceUnavailableError, ValidationError

logger = logging.getLogger(__name__)

class ContainerManager:
    """Manages Docker containers for client-specific agent instances"""
    
    def __init__(self):
        self.docker_client = None
        self.network_name = "autonomite-agents-network"
        self.agent_image = "autonomite/livekit-agent:latest"
        self._initialized = False
    
    async def initialize(self):
        """Initialize Docker client and ensure network exists"""
        if self._initialized:
            return
        
        try:
            self.docker_client = docker.from_env()
            
            # Ensure agent network exists
            try:
                self.docker_client.networks.get(self.network_name)
            except docker.errors.NotFound:
                self.docker_client.networks.create(
                    self.network_name,
                    driver="bridge",
                    labels={"managed_by": "autonomite-saas"}
                )
            
            self._initialized = True
            logger.info("Container manager initialized successfully")
            
        except Exception as e:
            logger.warning(f"Docker not available, container management disabled: {e}")
            self.docker_client = None
            self._initialized = True  # Still initialize but without Docker
    
    def get_container_name(self, site_id: str, agent_slug: str) -> str:
        """Generate consistent container name for a client's agent"""
        # Clean the inputs to be container-name safe
        safe_site_id = site_id.replace("-", "").lower()[:8]
        safe_agent_slug = agent_slug.replace("-", "_").lower()
        return f"agent_{safe_site_id}_{safe_agent_slug}"
    
    async def deploy_agent_container(
        self,
        site_id: str,
        agent_slug: str,
        agent_config: Dict[str, Any],
        site_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Deploy a new agent container for a specific client"""
        if not self.docker_client:
            raise ServiceUnavailableError("Docker not available - container deployment disabled")
        
        container_name = self.get_container_name(site_id, agent_slug)
        
        try:
            # Check if container already exists
            existing = await self.get_container(container_name)
            if existing and existing.status == "running":
                logger.info(f"Container {container_name} already running")
                return await self.get_container_info(container_name)
            
            # Prepare environment variables
            env_vars = {
                # Client identification
                "SITE_ID": site_id,
                "SITE_DOMAIN": site_config.get("domain", ""),
                "AGENT_SLUG": agent_slug,
                "CONTAINER_NAME": container_name,
                
                # LiveKit configuration
                "LIVEKIT_URL": agent_config.get("livekit_url", settings.livekit_url),
                "LIVEKIT_API_KEY": agent_config.get("livekit_api_key", settings.livekit_api_key),
                "LIVEKIT_API_SECRET": agent_config.get("livekit_api_secret", settings.livekit_api_secret),
                
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
                
                # API Keys (client-specific or fallback to defaults)
                "OPENAI_API_KEY": agent_config.get("openai_api_key", settings.openai_api_key),
                "ANTHROPIC_API_KEY": agent_config.get("anthropic_api_key", settings.anthropic_api_key),
                "GROQ_API_KEY": agent_config.get("groq_api_key", settings.groq_api_key),
                
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
            
            # Container configuration
            container_config = {
                "image": self.agent_image,
                "name": container_name,
                "environment": env_vars,
                "network": self.network_name,
                "labels": {
                    "autonomite.site_id": site_id,
                    "autonomite.agent_slug": agent_slug,
                    "autonomite.managed": "true",
                    "autonomite.created_at": datetime.utcnow().isoformat()
                },
                "restart_policy": {"Name": "unless-stopped"},
                "mem_limit": "1g",  # 1GB memory limit per container
                "cpu_quota": 100000,  # Equivalent to 1 CPU
                "detach": True,
                "healthcheck": {
                    "test": ["CMD", "curl", "-f", "http://localhost:8080/health"],
                    "interval": 30000000000,  # 30s in nanoseconds
                    "timeout": 10000000000,   # 10s in nanoseconds
                    "retries": 3
                }
            }
            
            # Remove existing stopped container if any
            if existing:
                await self.remove_container(container_name)
            
            # Create and start container
            container = self.docker_client.containers.run(**container_config)
            
            logger.info(f"Deployed agent container: {container_name}")
            
            # Wait for container to be healthy
            await self._wait_for_healthy(container_name, timeout=60)
            
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
            "stats": await self._get_container_stats(container),
            "health": await self._get_container_health(container)
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
    
    def _generate_internal_api_key(self, site_id: str) -> str:
        """Generate internal API key for container-to-backend communication"""
        import hashlib
        import secrets
        
        # Generate a deterministic but secure key that doesn't change daily
        # Use site_id and secret_key for consistency
        seed = f"{site_id}:{settings.secret_key}:autonomite-internal"
        return hashlib.sha256(seed.encode()).hexdigest()

# Create singleton instance
container_manager = ContainerManager()
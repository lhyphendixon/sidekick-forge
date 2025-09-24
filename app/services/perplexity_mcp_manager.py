from __future__ import annotations

import asyncio
import logging
import os
from functools import lru_cache
from typing import Any, Dict, Optional

import docker
from docker.errors import APIError, NotFound
from docker.models.containers import Container

from app.config import settings

LOGGER = logging.getLogger(__name__)


class PerplexityMCPManager:
    """Manage the shared Perplexity MCP container lifecycle."""

    def __init__(self, docker_client: Optional[docker.DockerClient] = None) -> None:
        self._docker = docker_client or docker.from_env()
        self._lock = asyncio.Lock()
        self._container_name = settings.perplexity_mcp_container_name
        self._image = settings.perplexity_mcp_image
        self._port = settings.perplexity_mcp_port
        self._host_alias = settings.perplexity_mcp_host
        self._network_name = settings.perplexity_mcp_network_name
        self._server_url = settings.perplexity_mcp_server_url

    @property
    def server_url(self) -> str:
        return self._server_url

    async def ensure_running(self) -> str:
        """Ensure the shared container is running and return its SSE endpoint."""
        async with self._lock:
            await asyncio.to_thread(self._ensure_running_sync)
        return self._server_url

    async def stop(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._stop_sync)

    # --- internal helpers -------------------------------------------------

    def _ensure_running_sync(self) -> None:
        try:
            container = self._docker.containers.get(self._container_name)
            container.reload()
            if container.status != "running":
                LOGGER.info("Starting Perplexity MCP container '%s'", self._container_name)
                container.start()
            self._ensure_network(container)
            return
        except NotFound:
            LOGGER.info(
                "Perplexity MCP container '%s' not found; creating using image '%s'",
                self._container_name,
                self._image,
            )
        except APIError as exc:
            raise RuntimeError(f"Docker API error while inspecting container: {exc}") from exc

        env: dict[str, str] = {}
        default_api_key = os.getenv("PERPLEXITY_API_KEY", "").strip()
        if default_api_key:
            env["PERPLEXITY_API_KEY"] = default_api_key

        run_kwargs: Dict[str, Any] = {
            "image": self._image,
            "name": self._container_name,
            "detach": True,
            "environment": env or None,
            "hostname": self._host_alias,
            "ports": {f"{self._port}/tcp": self._port},
            "restart_policy": {"Name": "unless-stopped"},
        }
        if self._network_name:
            run_kwargs["network"] = self._network_name

        try:
            container = self._docker.containers.run(**run_kwargs)
            LOGGER.info(
                "Started Perplexity MCP container '%s' on network '%s'",
                self._container_name,
                self._network_name,
            )
        except APIError as exc:
            raise RuntimeError(f"Failed to start Perplexity MCP container: {exc}") from exc

        self._ensure_network(container)

    def _stop_sync(self) -> None:
        try:
            container = self._docker.containers.get(self._container_name)
        except NotFound:
            return
        except APIError as exc:
            raise RuntimeError(f"Docker API error while locating container: {exc}") from exc

        try:
            LOGGER.info("Stopping Perplexity MCP container '%s'", self._container_name)
            container.stop(timeout=5)
        except APIError as exc:
            raise RuntimeError(f"Failed to stop Perplexity MCP container: {exc}") from exc

    def _ensure_network(self, container: Container) -> None:
        if not self._network_name:
            return
        try:
            network = self._docker.networks.get(self._network_name)
        except NotFound:
            LOGGER.warning(
                "Docker network '%s' not found; Perplexity MCP container may not be reachable",
                self._network_name,
            )
            return
        except APIError as exc:
            LOGGER.warning("Failed to inspect Docker network '%s': %s", self._network_name, exc)
            return

        try:
            attached = network.attrs.get("Containers", {}) or {}
            if container.id not in attached:
                LOGGER.info(
                    "Attaching Perplexity MCP container '%s' to network '%s'",
                    self._container_name,
                    self._network_name,
                )
                network.connect(container, aliases=[self._host_alias])
        except APIError as exc:
            LOGGER.warning(
                "Failed to connect Perplexity MCP container to network '%s': %s",
                self._network_name,
                exc,
            )


@lru_cache(maxsize=1)
def get_perplexity_mcp_manager() -> PerplexityMCPManager:
    return PerplexityMCPManager()

"""
Supabase Credential Management
Dynamically loads Supabase credentials from client configuration
"""
import logging
from typing import Optional, Tuple, Set

from app.config import settings

logger = logging.getLogger(__name__)


class SupabaseCredentialManager:
    """Manages Supabase credentials with dynamic loading"""

    @staticmethod
    async def get_service_credentials() -> Tuple[str, str, str]:
        """
        Get Supabase service credentials
        Returns: (url, anon_key, service_role_key)

        For the main Supabase instance, we need to bootstrap with env/config values,
        but should update to use the service role key from Autonomite client if available.
        """
        # Start with bootstrap values from config
        url = settings.supabase_url
        anon_key = settings.supabase_anon_key
        service_role_key = settings.supabase_service_role_key

        # Platform credentials are configured in .env and should not be dynamically loaded from clients
        # Each client has their own separate Supabase instance in multi-tenant architecture
        # The platform should use its own credentials, not client credentials

        return url, anon_key, service_role_key

    @staticmethod
    async def get_client_supabase_credentials(client_id: str) -> Optional[Tuple[str, str, str]]:
        """
        Get Supabase credentials for a specific client
        Returns: (url, anon_key, service_role_key) or None if client not found
        """
        try:
            from app.core.dependencies import get_client_service

            client_service = get_client_service()
            client = await client_service.get_client(client_id, auto_sync=False)

            if not client:
                logger.error(f"Client {client_id} not found")
                return None

            # Check if credentials are stored directly on client record (new structure)
            if isinstance(client, dict):
                url = client.get("supabase_url", "")
                service_key = client.get("supabase_service_role_key", "")
                anon_key = client.get("supabase_anon_key", "")
            else:
                url = getattr(client, "supabase_url", "")
                service_key = getattr(client, "supabase_service_role_key", "")
                anon_key = getattr(client, "supabase_anon_key", "")

            # Fall back to checking settings.supabase (old structure) if not found
            if not url or not service_key or not anon_key:
                client_settings = (
                    client.get("settings", {})
                    if isinstance(client, dict)
                    else getattr(client, "settings", {})
                )
                supabase_settings = (
                    client_settings.get("supabase", {})
                    if isinstance(client_settings, dict)
                    else getattr(client_settings, "supabase", {})
                )

                if not url:
                    url = (
                        supabase_settings.get("url", "")
                        if isinstance(supabase_settings, dict)
                        else getattr(supabase_settings, "url", "")
                    )
                if not service_key:
                    service_key = (
                        supabase_settings.get("service_role_key", "")
                        if isinstance(supabase_settings, dict)
                        else getattr(supabase_settings, "service_role_key", "")
                    )
                if not anon_key:
                    anon_key = (
                        supabase_settings.get("anon_key", "")
                        if isinstance(supabase_settings, dict)
                        else getattr(supabase_settings, "anon_key", "")
                    )

            if not url or not service_key:
                logger.warning(f"Client {client_id} missing Supabase credentials")
                return None

            return url, anon_key, service_key

        except Exception as e:
            logger.error(f"Error getting client Supabase credentials: {e}")
            return None

    @staticmethod
    async def get_frontend_credentials(
        client_id: Optional[str],
        allow_platform_ids: Optional[Set[str]] = None,
    ) -> Tuple[str, str]:
        """
        Return (url, anon_key) for browser contexts.
        Certain sentinel client IDs (global/debug flows) fall back to the platform credentials.
        """
        sentinel_ids: Set[str] = {"global"}
        if allow_platform_ids:
            sentinel_ids |= {sid for sid in allow_platform_ids if sid}

        if client_id is None or client_id in sentinel_ids:
            return settings.supabase_url, settings.supabase_anon_key

        creds = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
        if not creds:
            raise ValueError(f"Client {client_id} is missing Supabase configuration")

        url, anon_key, _ = creds
        if not url or not anon_key:
            raise ValueError(f"Client {client_id} is missing Supabase anon key")

        return url, anon_key

"""
Supabase Credential Management
Dynamically loads Supabase credentials from client configuration
"""
import os
import logging
from typing import Optional, Tuple
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
        
        # Try to get updated service role key from Autonomite client
        try:
            # We can use the anon key to query the client settings
            from supabase import create_client
            temp_client = create_client(url, anon_key)
            
            # Query Autonomite client settings
            response = temp_client.table('clients').select('settings').eq('id', 'df91fd06-816f-4273-a903-5a4861277040').single().execute()
            
            if response.data:
                client_settings = response.data.get('settings', {})
                supabase_config = client_settings.get('supabase', {})
                
                # If this client uses the same Supabase instance
                if supabase_config.get('url') == url:
                    new_service_key = supabase_config.get('service_role_key')
                    if new_service_key and new_service_key != service_role_key:
                        logger.info("Using updated service role key from Autonomite client settings")
                        service_role_key = new_service_key
                        # Update environment for consistency
                        os.environ['SUPABASE_SERVICE_ROLE_KEY'] = service_role_key
                        
        except Exception as e:
            logger.warning(f"Could not load updated service role key from client settings: {e}")
            # Continue with bootstrap values
        
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
            client = await client_service.get_client(client_id)
            
            if not client:
                logger.error(f"Client {client_id} not found")
                return None
            
            client_settings = client.get('settings', {}) if isinstance(client, dict) else getattr(client, 'settings', {})
            supabase_settings = client_settings.get('supabase', {}) if isinstance(client_settings, dict) else getattr(client_settings, 'supabase', {})
            
            url = supabase_settings.get('url', '') if isinstance(supabase_settings, dict) else getattr(supabase_settings, 'url', '')
            anon_key = supabase_settings.get('anon_key', '') if isinstance(supabase_settings, dict) else getattr(supabase_settings, 'anon_key', '')
            service_key = supabase_settings.get('service_role_key', '') if isinstance(supabase_settings, dict) else getattr(supabase_settings, 'service_role_key', '')
            
            if not all([url, service_key]):
                logger.warning(f"Client {client_id} missing Supabase credentials")
                return None
                
            return url, anon_key, service_key
            
        except Exception as e:
            logger.error(f"Error getting client Supabase credentials: {e}")
            return None
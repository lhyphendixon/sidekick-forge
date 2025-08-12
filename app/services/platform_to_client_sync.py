"""
Service to sync API keys from platform database to client databases
"""
import logging
from typing import Optional, Dict, Any
from supabase import create_client, Client as SupabaseClient

logger = logging.getLogger(__name__)


class PlatformToClientSync:
    """Syncs configuration from platform database to client databases"""
    
    @staticmethod
    async def sync_api_keys_to_client(
        client_id: str,
        platform_supabase: SupabaseClient,
        client_supabase_url: str,
        client_service_key: str
    ) -> bool:
        """
        Sync API keys from platform database to client's database
        
        Args:
            client_id: The client ID
            platform_supabase: Platform database client
            client_supabase_url: Client's Supabase URL
            client_service_key: Client's service role key
            
        Returns:
            bool: True if sync successful, False otherwise
        """
        try:
            logger.info(f"Starting platform-to-client sync for {client_id}")
            
            # Get API keys and settings from platform database
            result = platform_supabase.table('clients').select(
                'openai_api_key, groq_api_key, deepgram_api_key, elevenlabs_api_key, '
                'cartesia_api_key, speechify_api_key, deepinfra_api_key, replicate_api_key, '
                'novita_api_key, cohere_api_key, siliconflow_api_key, jina_api_key, additional_settings'
            ).eq('id', client_id).execute()
            
            if not result.data:
                logger.error(f"No client found with ID {client_id}")
                return False
                
            platform_keys = result.data[0]
            logger.info(f"Found {len([k for k, v in platform_keys.items() if v])} API keys in platform database")
            
            # Connect to client's database
            client_supabase = create_client(client_supabase_url, client_service_key)
            
            # Update global_settings table in client's database
            success_count = 0
            for key_name, key_value in platform_keys.items():
                if key_value and key_value != '<needs-actual-key>':
                    try:
                        # Check if setting exists
                        existing = client_supabase.table('global_settings').select('*').eq('setting_key', key_name).execute()
                        
                        if existing.data:
                            # Update existing
                            result = client_supabase.table('global_settings').update({
                                'setting_value': key_value
                            }).eq('setting_key', key_name).execute()
                            
                            if result.data:
                                logger.info(f"✅ Updated {key_name} in client's global_settings")
                                success_count += 1
                        else:
                            # Insert new
                            result = client_supabase.table('global_settings').insert({
                                'setting_key': key_name,
                                'setting_value': key_value
                            }).execute()
                            
                            if result.data:
                                logger.info(f"✅ Inserted {key_name} in client's global_settings")
                                success_count += 1
                                
                    except Exception as e:
                        logger.error(f"Failed to sync {key_name}: {e}")
            
            # Also sync embedding settings from additional_settings
            additional = result.data[0].get('additional_settings', {})
            if 'embedding' in additional:
                embedding = additional['embedding']
                # Update embedding provider in global_settings
                if embedding.get('provider'):
                    try:
                        existing = client_supabase.table('global_settings').select('*').eq('setting_key', 'embedding_provider').execute()
                        if existing.data:
                            client_supabase.table('global_settings').update({
                                'setting_value': embedding['provider']
                            }).eq('setting_key', 'embedding_provider').execute()
                        else:
                            client_supabase.table('global_settings').insert({
                                'setting_key': 'embedding_provider',
                                'setting_value': embedding['provider']
                            }).execute()
                        logger.info(f"✅ Synced embedding_provider: {embedding['provider']}")
                        success_count += 1
                    except Exception as e:
                        logger.error(f"Failed to sync embedding_provider: {e}")
                
                # Update embedding models
                for model_key in ['document_model', 'conversation_model']:
                    if embedding.get(model_key):
                        setting_key = f'embedding_model_{model_key.split("_")[0]}s'  # document_model -> embedding_model_documents
                        try:
                            existing = client_supabase.table('global_settings').select('*').eq('setting_key', setting_key).execute()
                            if existing.data:
                                client_supabase.table('global_settings').update({
                                    'setting_value': embedding[model_key]
                                }).eq('setting_key', setting_key).execute()
                            else:
                                client_supabase.table('global_settings').insert({
                                    'setting_key': setting_key,
                                    'setting_value': embedding[model_key]
                                }).execute()
                            logger.info(f"✅ Synced {setting_key}: {embedding[model_key]}")
                            success_count += 1
                        except Exception as e:
                            logger.error(f"Failed to sync {setting_key}: {e}")
            
            logger.info(f"Successfully synced {success_count} settings to client database")
            return success_count > 0
            
        except Exception as e:
            logger.error(f"Platform-to-client sync failed: {e}", exc_info=True)
            return False
    
    @staticmethod
    async def sync_after_update(client_id: str, client_service) -> bool:
        """
        Convenience method to sync after a client update
        
        Args:
            client_id: The client ID
            client_service: ClientService instance
            
        Returns:
            bool: True if sync successful, False otherwise
        """
        try:
            # Get client configuration
            client = await client_service.get_client(client_id, auto_sync=False)
            if not client or not client.settings or not client.settings.supabase:
                logger.error(f"Client {client_id} missing Supabase configuration")
                return False
                
            # Skip if placeholder URL
            if "pending.supabase.co" in client.settings.supabase.url:
                logger.info(f"Skipping sync for client {client_id} with placeholder URL")
                return False
                
            # Perform sync
            return await PlatformToClientSync.sync_api_keys_to_client(
                client_id=client_id,
                platform_supabase=client_service.supabase,
                client_supabase_url=client.settings.supabase.url,
                client_service_key=client.settings.supabase.service_role_key
            )
            
        except Exception as e:
            logger.error(f"Sync after update failed: {e}")
            return False
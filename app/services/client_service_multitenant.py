"""
Multi-tenant Client management service for Sidekick Forge Platform

This service manages client records in the platform database.
"""
from typing import List, Optional, Dict, Any
from datetime import datetime
from uuid import UUID
import logging
import json

from app.models.platform_client import PlatformClient as Client, PlatformClientCreate as ClientCreate, PlatformClientUpdate as ClientUpdate, APIKeys, PlatformClientSettings
from app.services.client_connection_manager import get_connection_manager, ClientConfigurationError

logger = logging.getLogger(__name__)


class ClientService:
    """Service for managing clients in the platform database"""
    
    def __init__(self):
        self.connection_manager = get_connection_manager()
        # Access platform database directly
        self.platform_db = self.connection_manager.platform_client
    
    def _parse_client_data(self, client_data: Dict[str, Any]) -> Client:
        """Parse client data from platform database"""
        # Parse API keys into APIKeys model
        api_keys = APIKeys(
            openai_api_key=client_data.get("openai_api_key"),
            groq_api_key=client_data.get("groq_api_key"),
            deepgram_api_key=client_data.get("deepgram_api_key"),
            elevenlabs_api_key=client_data.get("elevenlabs_api_key"),
            cartesia_api_key=client_data.get("cartesia_api_key"),
            speechify_api_key=client_data.get("speechify_api_key"),
            deepinfra_api_key=client_data.get("deepinfra_api_key"),
            replicate_api_key=client_data.get("replicate_api_key"),
            novita_api_key=client_data.get("novita_api_key"),
            cohere_api_key=client_data.get("cohere_api_key"),
            siliconflow_api_key=client_data.get("siliconflow_api_key"),
            jina_api_key=client_data.get("jina_api_key"),
        )
        
        # Add anthropic if it exists
        if hasattr(APIKeys, 'anthropic_api_key'):
            api_keys.anthropic_api_key = client_data.get("anthropic_api_key")
        
        # Parse additional settings
        additional_settings = client_data.get("additional_settings", {})
        if isinstance(additional_settings, str):
            try:
                additional_settings = json.loads(additional_settings)
            except json.JSONDecodeError:
                additional_settings = {}
        
        # Parse LiveKit credentials if available
        livekit_config = None
        if client_data.get("livekit_url"):
            livekit_config = {
                "url": client_data.get("livekit_url"),
                "api_key": client_data.get("livekit_api_key"),
                "api_secret": client_data.get("livekit_api_secret"),
            }
        
        # Create PlatformClientSettings
        settings = PlatformClientSettings(
            api_keys=api_keys,
            livekit_config=livekit_config,
            additional_settings=additional_settings
        )
        
        # Parse datetime fields
        created_at = client_data.get("created_at")
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            except ValueError:
                created_at = datetime.utcnow()
        
        updated_at = client_data.get("updated_at")
        if isinstance(updated_at, str):
            try:
                updated_at = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
            except ValueError:
                updated_at = datetime.utcnow()
        
        return Client(
            id=client_data["id"],
            name=client_data["name"],
            supabase_project_url=client_data.get("supabase_url"),
            supabase_service_role_key=client_data.get("supabase_service_role_key"),
            settings=settings,
            created_at=created_at,
            updated_at=updated_at
        )
    
    async def get_clients(self) -> List[Client]:
        """Get all clients from platform database"""
        try:
            result = self.platform_db.table("clients").select("*").execute()
            
            clients = []
            for client_data in result.data:
                try:
                    client = self._parse_client_data(client_data)
                    clients.append(client)
                except Exception as e:
                    logger.error(f"Error parsing client {client_data.get('name', 'unknown')}: {e}")
                    continue
            
            logger.info(f"Retrieved {len(clients)} clients from platform database")
            return clients
            
        except Exception as e:
            logger.error(f"Error fetching clients: {e}")
            return []
    
    async def get_client(self, client_id: str) -> Optional[Client]:
        """Get a specific client by ID"""
        try:
            result = self.platform_db.table("clients").select("*").eq("id", client_id).single().execute()
            
            if result.data:
                return self._parse_client_data(result.data)
            
            logger.warning(f"Client {client_id} not found")
            return None
            
        except Exception as e:
            logger.error(f"Error fetching client {client_id}: {e}")
            return None
    
    async def create_client(self, client_data: ClientCreate) -> Optional[Client]:
        """Create a new client in the platform database"""
        try:
            # Prepare data for insertion
            data = {
                "name": client_data.name,
                "supabase_url": client_data.supabase_project_url,
                "supabase_service_role_key": client_data.supabase_service_role_key,
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }
            
            # Add API keys if provided
            if client_data.settings and client_data.settings.api_keys:
                keys = client_data.settings.api_keys
                data.update({
                    "openai_api_key": keys.openai_api_key,
                    "groq_api_key": keys.groq_api_key,
                    "deepgram_api_key": keys.deepgram_api_key,
                    "elevenlabs_api_key": keys.elevenlabs_api_key,
                    "cartesia_api_key": keys.cartesia_api_key,
                    "speechify_api_key": keys.speechify_api_key,
                    "deepinfra_api_key": keys.deepinfra_api_key,
                    "replicate_api_key": keys.replicate_api_key,
                    "novita_api_key": keys.novita_api_key,
                    "cohere_api_key": keys.cohere_api_key,
                    "siliconflow_api_key": keys.siliconflow_api_key,
                    "jina_api_key": keys.jina_api_key,
                })
                if hasattr(keys, 'anthropic_api_key'):
                    data["anthropic_api_key"] = keys.anthropic_api_key
            
            # Add LiveKit config if provided
            if client_data.settings and client_data.settings.livekit_config:
                livekit = client_data.settings.livekit_config
                data.update({
                    "livekit_url": livekit.get("url"),
                    "livekit_api_key": livekit.get("api_key"),
                    "livekit_api_secret": livekit.get("api_secret"),
                })
            
            # Insert into platform database
            result = self.platform_db.table("clients").insert(data).execute()
            
            if result.data:
                logger.info(f"Created client {client_data.name}")
                # Clear cache for new client
                self.connection_manager.clear_cache()
                return self._parse_client_data(result.data[0])
            
            return None
            
        except Exception as e:
            logger.error(f"Error creating client: {e}")
            return None
    
    async def update_client(self, client_id: str, client_update: ClientUpdate) -> Optional[Client]:
        """Update an existing client"""
        try:
            # Prepare update data
            update_data = {"updated_at": datetime.utcnow().isoformat()}
            
            if client_update.name is not None:
                update_data["name"] = client_update.name
            
            if client_update.supabase_project_url is not None:
                update_data["supabase_url"] = client_update.supabase_project_url
            
            if client_update.supabase_service_role_key is not None:
                update_data["supabase_service_role_key"] = client_update.supabase_service_role_key
            
            # Update API keys if provided
            if client_update.settings and client_update.settings.api_keys:
                keys = client_update.settings.api_keys
                update_data.update({
                    "openai_api_key": keys.openai_api_key,
                    "groq_api_key": keys.groq_api_key,
                    "deepgram_api_key": keys.deepgram_api_key,
                    "elevenlabs_api_key": keys.elevenlabs_api_key,
                    "cartesia_api_key": keys.cartesia_api_key,
                    "speechify_api_key": keys.speechify_api_key,
                    "deepinfra_api_key": keys.deepinfra_api_key,
                    "replicate_api_key": keys.replicate_api_key,
                    "novita_api_key": keys.novita_api_key,
                    "cohere_api_key": keys.cohere_api_key,
                    "siliconflow_api_key": keys.siliconflow_api_key,
                    "jina_api_key": keys.jina_api_key,
                })
                if hasattr(keys, 'anthropic_api_key'):
                    update_data["anthropic_api_key"] = keys.anthropic_api_key
            
            # Update LiveKit config if provided
            if client_update.settings and client_update.settings.livekit_config:
                livekit = client_update.settings.livekit_config
                update_data.update({
                    "livekit_url": livekit.get("url"),
                    "livekit_api_key": livekit.get("api_key"),
                    "livekit_api_secret": livekit.get("api_secret"),
                })
            
            # Update in platform database
            result = self.platform_db.table("clients").update(update_data).eq("id", client_id).execute()
            
            if result.data:
                logger.info(f"Updated client {client_id}")
                # Clear cache for updated client
                self.connection_manager.clear_cache(UUID(client_id))
                return self._parse_client_data(result.data[0])
            
            return None
            
        except Exception as e:
            logger.error(f"Error updating client {client_id}: {e}")
            return None
    
    async def delete_client(self, client_id: str) -> bool:
        """Delete a client from the platform database"""
        try:
            result = self.platform_db.table("clients").delete().eq("id", client_id).execute()
            
            if result.data:
                logger.info(f"Deleted client {client_id}")
                # Clear cache for deleted client
                self.connection_manager.clear_cache(UUID(client_id))
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Error deleting client {client_id}: {e}")
            return False
    
    async def sync_from_supabase(self, client_id: str) -> Optional[Client]:
        """
        Sync client data from their own Supabase instance.
        This pulls settings from the client's database and updates the platform record.
        """
        try:
            # Get client connection
            client_db = self.connection_manager.get_client_db_client(UUID(client_id))
            
            # Try to fetch settings from client's database
            # This assumes they have a settings or config table
            try:
                result = client_db.table("settings").select("*").single().execute()
                if result.data:
                    # Update platform record with synced data
                    # Implementation depends on client's schema
                    logger.info(f"Synced settings for client {client_id}")
            except Exception as e:
                logger.debug(f"Could not sync settings from client database: {e}")
            
            # Return updated client
            return await self.get_client(client_id)
            
        except ClientConfigurationError as e:
            logger.error(f"Client configuration error during sync: {e}")
            return None
        except Exception as e:
            logger.error(f"Error syncing client {client_id}: {e}")
            return None
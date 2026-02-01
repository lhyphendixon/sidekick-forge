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
        if not additional_settings:
            additional_settings = {}
        if isinstance(additional_settings, str):
            try:
                additional_settings = json.loads(additional_settings)
            except json.JSONDecodeError:
                additional_settings = {}

        # Propagate top-level DB columns into additional_settings for downstream access
        if client_data.get("uses_platform_keys") is not None:
            additional_settings["uses_platform_keys"] = client_data["uses_platform_keys"]
        if client_data.get("hosting_type"):
            additional_settings["hosting_type"] = client_data["hosting_type"]
        if client_data.get("tier"):
            additional_settings["tier"] = client_data["tier"]
        
        # Parse LiveKit credentials if available
        livekit_config = None
        if client_data.get("livekit_url"):
            livekit_config = {
                "url": client_data.get("livekit_url"),
                "api_key": client_data.get("livekit_api_key"),
                "api_secret": client_data.get("livekit_api_secret"),
            }
        
        supabase_config = None
        if client_data.get("supabase_url") or client_data.get("supabase_service_role_key"):
            supabase_config = {
                "url": client_data.get("supabase_url"),
                "anon_key": client_data.get("supabase_anon_key"),
                "service_role_key": client_data.get("supabase_service_role_key"),
            }

        # Create PlatformClientSettings
        settings = PlatformClientSettings(
            api_keys=api_keys,
            livekit_config=livekit_config,
            additional_settings=additional_settings,
            supabase=supabase_config,
        )
        
        def _parse_datetime(value: Any) -> Optional[datetime]:
            if isinstance(value, datetime):
                return value
            if isinstance(value, str):
                try:
                    return datetime.fromisoformat(value.replace('Z', '+00:00'))
                except ValueError:
                    return None
            return value

        created_at = _parse_datetime(client_data.get("created_at")) or datetime.utcnow()
        updated_at = _parse_datetime(client_data.get("updated_at")) or datetime.utcnow()
        provisioning_started_at = _parse_datetime(client_data.get("provisioning_started_at"))
        provisioning_completed_at = _parse_datetime(client_data.get("provisioning_completed_at"))

        provisioning_status = client_data.get("provisioning_status") or "ready"
        auto_provision = bool(client_data.get("auto_provision"))

        # Legacy clients created before the provisioning rollout may have been
        # defaulted to "queued" even though they already have credentials.
        if not auto_provision and provisioning_status in (None, "", "queued", "pending"):
            provisioning_status = "ready"
            try:
                now_iso = datetime.utcnow().isoformat()
                self.platform_db.table("clients").update({
                    "provisioning_status": "ready",
                    "provisioning_error": None,
                    "provisioning_started_at": client_data.get("provisioning_started_at") or now_iso,
                    "provisioning_completed_at": client_data.get("provisioning_completed_at") or now_iso,
                }).eq("id", client_data["id"]).execute()
            except Exception as legacy_update_error:
                logger.debug(
                    "Unable to auto-promote provisioning status for legacy client %s: %s",
                    client_data.get("id"),
                    legacy_update_error,
                )

        return Client(
            id=client_data["id"],
            name=client_data["name"],
            supabase_project_url=client_data.get("supabase_url"),
            supabase_url=client_data.get("supabase_url"),
            supabase_service_role_key=client_data.get("supabase_service_role_key"),
            supabase_project_ref=client_data.get("supabase_project_ref"),
            supabase_anon_key=client_data.get("supabase_anon_key"),
            settings=settings,
            provisioning_status=provisioning_status,
            provisioning_error=client_data.get("provisioning_error"),
            schema_version=client_data.get("schema_version"),
            provisioning_started_at=provisioning_started_at,
            provisioning_completed_at=provisioning_completed_at,
            auto_provision=auto_provision,
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
            now_iso = datetime.utcnow().isoformat()
            auto_provision = getattr(client_data, "auto_provision", False)
            provisioning_status = "queued" if auto_provision else "ready"

            # Prepare data for insertion
            data = {
                "name": client_data.name,
                "supabase_url": client_data.supabase_project_url,
                "supabase_service_role_key": client_data.supabase_service_role_key,
                "auto_provision": auto_provision,
                "provisioning_status": provisioning_status,
                "created_at": now_iso,
                "updated_at": now_iso,
                "provisioning_started_at": now_iso,
                "provisioning_error": None,
            }

            if not auto_provision:
                data["provisioning_completed_at"] = now_iso

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
                if hasattr(keys, 'assemblyai_api_key'):
                    data["assemblyai_api_key"] = keys.assemblyai_api_key

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
                client_row = result.data[0]
                client_id = client_row["id"]
                logger.info(f"Created client {client_data.name}")

                if auto_provision:
                    try:
                        # Queue provisioning; worker design documented in docs/provisioning_worker_plan.md
                        self.platform_db.table("client_provisioning_jobs").upsert(
                            {
                                "client_id": client_id,
                                "job_type": "supabase_project",
                                "attempts": 0,
                                "claimed_at": None,
                                "last_error": None,
                            },
                            on_conflict="client_id,job_type",
                        ).execute()
                    except Exception as job_error:
                        logger.error(
                            f"Error queueing provisioning job for client {client_id}: {job_error}"
                        )
                        self.platform_db.table("clients").update(
                            {
                                "provisioning_status": "failed",
                                "provisioning_error": str(job_error),
                            }
                        ).eq("id", client_id).execute()
                        raise

                # Clear cache for new client
                self.connection_manager.clear_cache()
                return self._parse_client_data(client_row)

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

            if client_update.supabase_project_ref is not None:
                update_data["supabase_project_ref"] = client_update.supabase_project_ref

            if client_update.supabase_anon_key is not None:
                update_data["supabase_anon_key"] = client_update.supabase_anon_key

            if client_update.provisioning_status is not None:
                update_data["provisioning_status"] = client_update.provisioning_status

            if client_update.provisioning_error is not None:
                update_data["provisioning_error"] = client_update.provisioning_error

            if client_update.schema_version is not None:
                update_data["schema_version"] = client_update.schema_version

            if client_update.auto_provision is not None:
                update_data["auto_provision"] = client_update.auto_provision

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
                if hasattr(keys, 'assemblyai_api_key'):
                    update_data["assemblyai_api_key"] = keys.assemblyai_api_key

            # Update Firecrawl API key if provided
            if client_update.firecrawl_api_key is not None:
                update_data["firecrawl_api_key"] = client_update.firecrawl_api_key

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
            # Check if client exists first
            check = self.platform_db.table("clients").select("id").eq("id", client_id).execute()
            if not check.data:
                logger.warning(f"Client {client_id} not found for deletion")
                return False

            # Execute delete
            self.platform_db.table("clients").delete().eq("id", client_id).execute()

            # Verify deletion succeeded (Supabase delete may return empty result.data)
            verify = self.platform_db.table("clients").select("id").eq("id", client_id).execute()
            if verify.data:
                logger.error(f"Delete failed for client {client_id} - still exists")
                return False

            logger.info(f"Deleted client {client_id}")
            # Clear cache for deleted client
            self.connection_manager.clear_cache(UUID(client_id))
            return True

        except Exception as e:
            logger.error(f"Error deleting client {client_id}: {e}")
            return False

    async def retry_provisioning(self, client_id: str) -> bool:
        """Reset provisioning state and enqueue a fresh provisioning job."""
        try:
            now_iso = datetime.utcnow().isoformat()

            result = self.platform_db.table("clients").update({
                "provisioning_status": "queued",
                "provisioning_error": None,
                "provisioning_started_at": now_iso,
                "provisioning_completed_at": None,
            }).eq("id", client_id).execute()

            if not result.data:
                logger.warning(f"Cannot retry provisioning for client {client_id}: not found")
                return False

            self.platform_db.table("client_provisioning_jobs").upsert({
                "client_id": client_id,
                "job_type": "supabase_project",
                "attempts": 0,
                "claimed_at": None,
                "last_error": None,
            }, on_conflict="client_id,job_type").execute()

            self.connection_manager.clear_cache(UUID(client_id))
            logger.info(f"Re-queued provisioning for client {client_id}")
            return True
        except Exception as e:
            logger.error(f"Error retrying provisioning for client {client_id}: {e}")
            return False

    async def list_provisioning_jobs(self) -> List[Dict[str, Any]]:
        """Return provisioning job queue with associated client metadata."""
        try:
            job_result = (
                self.platform_db
                .table("client_provisioning_jobs")
                .select("*")
                .order("created_at")
                .execute()
            )

            jobs = job_result.data or []
            if not jobs:
                return []

            client_ids = list({job["client_id"] for job in jobs if job.get("client_id")})

            clients_map: Dict[str, Dict[str, Any]] = {}
            if client_ids:
                clients_result = (
                    self.platform_db
                    .table("clients")
                    .select("id,name,provisioning_status,provisioning_error,supabase_url")
                    .in_("id", client_ids)
                    .execute()
                )
                if clients_result.data:
                    clients_map = {row["id"]: row for row in clients_result.data}

            enriched = []
            for job in jobs:
                client_info = clients_map.get(job["client_id"], {})
                enriched.append(
                    {
                        "id": job.get("id"),
                        "client_id": job.get("client_id"),
                        "job_type": job.get("job_type"),
                        "attempts": job.get("attempts", 0),
                        "claimed_at": job.get("claimed_at"),
                        "last_error": job.get("last_error"),
                        "created_at": job.get("created_at"),
                        "updated_at": job.get("updated_at"),
                        "client_name": client_info.get("name"),
                        "client_status": client_info.get("provisioning_status"),
                        "client_error": client_info.get("provisioning_error"),
                        "client_supabase_url": client_info.get("supabase_url"),
                    }
                )

            return enriched

        except Exception as e:
            logger.error(f"Error listing provisioning jobs: {e}")
            return []
    
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

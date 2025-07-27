"""
Backend LiveKit Credential Sync Service
Automatically syncs LiveKit credentials from primary client (Autonomite) to backend
"""
import os
import logging
import asyncio
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

class BackendLiveKitSync:
    """Manages automatic synchronization of LiveKit credentials from Supabase to backend"""
    
    AUTONOMITE_CLIENT_ID = "df91fd06-816f-4273-a903-5a4861277040"
    ENV_FILE_PATH = "/root/sidekick-forge/.env"
    
    @classmethod
    async def sync_credentials(cls) -> bool:
        """Sync LiveKit credentials from Autonomite client to backend environment"""
        try:
            # Get client service
            from app.core.dependencies import get_client_service
            client_service = get_client_service()
            
            # Get Autonomite client
            client = await client_service.get_client(cls.AUTONOMITE_CLIENT_ID)
            if not client:
                logger.error(f"Autonomite client {cls.AUTONOMITE_CLIENT_ID} not found")
                return False
            
            # Extract LiveKit credentials
            if not client.settings or not hasattr(client.settings, 'livekit'):
                logger.error("No LiveKit configuration found in client settings")
                return False
            
            livekit_config = client.settings.livekit
            url = getattr(livekit_config, 'server_url', None)
            api_key = getattr(livekit_config, 'api_key', None)
            api_secret = getattr(livekit_config, 'api_secret', None)
            
            if not all([url, api_key, api_secret]):
                logger.error("Incomplete LiveKit configuration in client settings")
                return False
            
            # Check if credentials are the known invalid test credentials
            if api_key == "APIUtuiQ47BQBsk":
                logger.warning(f"Skipping sync of expired LiveKit credentials: {api_key}")
                return False
            
            # Update environment variables
            os.environ["LIVEKIT_URL"] = url
            os.environ["LIVEKIT_API_KEY"] = api_key
            os.environ["LIVEKIT_API_SECRET"] = api_secret
            
            # Update .env file for persistence
            cls._update_env_file(url, api_key, api_secret)
            
            logger.info(f"✅ Synced LiveKit credentials from Autonomite client")
            logger.info(f"   URL: {url}")
            logger.info(f"   API Key: {api_key[:8]}...{api_key[-4:]}")
            
            # Note: Worker restart is handled automatically on next deployment
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to sync LiveKit credentials: {e}")
            return False
    
    @classmethod
    def _update_env_file(cls, url: str, api_key: str, api_secret: str) -> None:
        """Update .env file with new credentials"""
        try:
            # Read existing .env
            lines = []
            if os.path.exists(cls.ENV_FILE_PATH):
                with open(cls.ENV_FILE_PATH, 'r') as f:
                    lines = f.readlines()
            
            # Update LiveKit values
            updated = False
            new_lines = []
            skip_until_next_section = False
            
            for i, line in enumerate(lines):
                if skip_until_next_section:
                    if line.strip().startswith('#') and 'Configuration' in line:
                        skip_until_next_section = False
                    elif not line.strip() or not line.strip().startswith('LIVEKIT'):
                        skip_until_next_section = False
                    else:
                        continue
                
                if line.strip().startswith('# LiveKit Configuration'):
                    new_lines.append(line)
                    new_lines.append(f'LIVEKIT_URL={url}\n')
                    new_lines.append(f'LIVEKIT_API_KEY={api_key}\n')
                    new_lines.append(f'LIVEKIT_API_SECRET={api_secret}\n')
                    skip_until_next_section = True
                    updated = True
                elif not line.strip().startswith('LIVEKIT_'):
                    new_lines.append(line)
            
            # Add if not found
            if not updated:
                new_lines.append('\n# LiveKit Configuration (Auto-synced from Supabase)\n')
                new_lines.append(f'LIVEKIT_URL={url}\n')
                new_lines.append(f'LIVEKIT_API_KEY={api_key}\n')
                new_lines.append(f'LIVEKIT_API_SECRET={api_secret}\n')
            
            # Write back
            with open(cls.ENV_FILE_PATH, 'w') as f:
                f.writelines(new_lines)
                
            logger.info(f"Updated {cls.ENV_FILE_PATH} with new LiveKit credentials")
            
        except Exception as e:
            logger.error(f"Failed to update .env file: {e}")
    
    @classmethod
    async def _restart_worker(cls) -> None:
        """Restart worker to apply new credentials"""
        try:
            import subprocess
            # Use docker-compose to restart the worker
            result = subprocess.run(
                ["docker-compose", "restart", "agent-worker"],
                cwd="/root/sidekick-forge",
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                logger.info("✅ Worker restarted successfully")
            else:
                logger.error(f"Failed to restart worker: {result.stderr}")
        except Exception as e:
            logger.error(f"Failed to restart worker: {e}")
    
    @classmethod
    async def start_sync_task(cls, interval_seconds: int = 300) -> None:
        """Start background task to periodically sync credentials"""
        while True:
            try:
                await cls.sync_credentials()
            except Exception as e:
                logger.error(f"Error in sync task: {e}")
            
            await asyncio.sleep(interval_seconds)
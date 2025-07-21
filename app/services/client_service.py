"""
Client management service
"""
import json
from typing import List, Optional, Dict, Any
from datetime import datetime
import redis
from fastapi import HTTPException

from app.models.client import Client, ClientCreate, ClientUpdate, ClientInDB


class ClientService:
    """Service for managing clients and their configurations"""
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.client_key_prefix = "client:"
        
    def _get_client_key(self, client_id: str) -> str:
        """Get Redis key for a client"""
        return f"{self.client_key_prefix}{client_id}"
    
    async def create_client(self, client_data: ClientCreate) -> ClientInDB:
        """Create a new client"""
        # Check if client already exists
        existing = await self.get_client(client_data.id)
        if existing:
            raise HTTPException(status_code=400, detail=f"Client with ID {client_data.id} already exists")
        
        # Create client object
        client = ClientInDB(
            **client_data.dict(),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        # Store in Redis
        key = self._get_client_key(client.id)
        self.redis.set(key, client.json())
        
        # Add to client list
        self.redis.sadd("clients", client.id)
        
        return client
    
    async def get_client(self, client_id: str) -> Optional[ClientInDB]:
        """Get a client by ID"""
        key = self._get_client_key(client_id)
        data = self.redis.get(key)
        
        if not data:
            return None
            
        return ClientInDB.parse_raw(data)
    
    async def get_all_clients(self) -> List[ClientInDB]:
        """Get all clients"""
        client_ids = self.redis.smembers("clients")
        clients = []
        
        for client_id in client_ids:
            client = await self.get_client(client_id.decode())
            if client:
                clients.append(client)
                
        # Sort by name
        clients.sort(key=lambda x: x.name)
        return clients
    
    async def update_client(self, client_id: str, update_data: ClientUpdate) -> ClientInDB:
        """Update a client"""
        client = await self.get_client(client_id)
        if not client:
            raise HTTPException(status_code=404, detail=f"Client {client_id} not found")
        
        # Update fields
        update_dict = update_data.dict(exclude_unset=True)
        if update_dict:
            for field, value in update_dict.items():
                setattr(client, field, value)
            client.updated_at = datetime.utcnow()
        
        # Save back to Redis
        key = self._get_client_key(client_id)
        self.redis.set(key, client.json())
        
        return client
    
    async def delete_client(self, client_id: str) -> bool:
        """Delete a client"""
        key = self._get_client_key(client_id)
        
        # Remove from Redis
        deleted = self.redis.delete(key)
        
        # Remove from client list
        self.redis.srem("clients", client_id)
        
        return deleted > 0
    
    async def get_active_clients(self) -> List[ClientInDB]:
        """Get all active clients"""
        all_clients = await self.get_all_clients()
        return [client for client in all_clients if client.active]
    
    async def get_client_by_domain(self, domain: str) -> Optional[ClientInDB]:
        """Get a client by domain"""
        all_clients = await self.get_all_clients()
        
        for client in all_clients:
            if client.domain and client.domain.lower() == domain.lower():
                return client
                
        return None
    
    async def validate_api_key(self, client_id: str, api_key: str) -> bool:
        """Validate an API key for a client"""
        client = await self.get_client(client_id)
        if not client:
            return False
            
        # Check if the provided API key matches any of the client's API keys
        # This is a simple implementation - you might want to use a separate API key system
        return client.settings.license_key == api_key
    
    async def initialize_default_clients(self):
        """Initialize default clients if they don't exist"""
        default_clients = [
            {
                "id": "autonomite-agent",
                "name": "Autonomite Agent",
                "description": "First-party agents by Autonomite",
                "domain": "autonomite.net",
                "settings": {
                    "supabase": {
                        "url": "https://YOUR_AUTONOMITE_PROJECT.supabase.co",
                        "anon_key": "YOUR_AUTONOMITE_ANON_KEY",
                        "service_role_key": "YOUR_AUTONOMITE_SERVICE_KEY"
                    },
                    "livekit": {
                        "server_url": "https://YOUR_LIVEKIT_SERVER",
                        "api_key": "YOUR_LIVEKIT_API_KEY",
                        "api_secret": "YOUR_LIVEKIT_API_SECRET"
                    }
                }
            },
            {
                "id": "live-free-academy",
                "name": "Live Free Academy",
                "description": "Live Free Academy client",
                "domain": "livefreeacademy.com",
                "settings": {
                    "supabase": {
                        "url": "https://YOUR_LFA_PROJECT.supabase.co",
                        "anon_key": "YOUR_LFA_ANON_KEY", 
                        "service_role_key": "YOUR_LFA_SERVICE_KEY"
                    },
                    "livekit": {
                        "server_url": "https://YOUR_LIVEKIT_SERVER",
                        "api_key": "YOUR_LFA_LIVEKIT_API_KEY",
                        "api_secret": "YOUR_LFA_LIVEKIT_API_SECRET"
                    }
                }
            }
        ]
        
        for client_data in default_clients:
            try:
                existing = await self.get_client(client_data["id"])
                if not existing:
                    await self.create_client(ClientCreate(**client_data))
                    print(f"Created default client: {client_data['name']}")
            except Exception as e:
                print(f"Error creating default client {client_data['id']}: {e}")
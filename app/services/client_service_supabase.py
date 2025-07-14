"""
Client management service using Supabase
"""
import json
from typing import List, Optional, Dict, Any
from datetime import datetime
from fastapi import HTTPException
from supabase import create_client, Client as SupabaseClient

from app.models.client import Client, ClientCreate, ClientUpdate, ClientInDB


class ClientService:
    """Service for managing clients and their configurations in Supabase"""
    
    def __init__(self, supabase_url: str, supabase_key: str):
        self.supabase: SupabaseClient = create_client(supabase_url, supabase_key)
        self.table_name = "clients"
        
    async def ensure_table_exists(self):
        """Ensure the clients table exists in Supabase"""
        # This would typically be done via migrations
        # For now, we'll document the expected schema:
        """
        CREATE TABLE IF NOT EXISTS clients (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            domain TEXT,
            settings JSONB NOT NULL,
            active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        
        CREATE INDEX idx_clients_domain ON clients(domain);
        CREATE INDEX idx_clients_active ON clients(active);
        """
        pass
        
    async def create_client(self, client_data: ClientCreate) -> ClientInDB:
        """Create a new client"""
        # Check if client already exists
        existing = await self.get_client(client_data.id)
        if existing:
            raise HTTPException(status_code=400, detail=f"Client with ID {client_data.id} already exists")
        
        # Create client object
        now = datetime.utcnow()
        client_dict = {
            **client_data.dict(),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat()
        }
        
        # Store in Supabase
        result = self.supabase.table(self.table_name).insert(client_dict).execute()
        
        if result.data:
            return ClientInDB(**result.data[0])
        else:
            raise HTTPException(status_code=500, detail="Failed to create client")
    
    async def get_client(self, client_id: str) -> Optional[ClientInDB]:
        """Get a client by ID"""
        result = self.supabase.table(self.table_name).select("*").eq("id", client_id).execute()
        
        if result.data and len(result.data) > 0:
            return ClientInDB(**result.data[0])
        
        return None
    
    async def get_all_clients(self) -> List[ClientInDB]:
        """Get all clients"""
        result = self.supabase.table(self.table_name).select("*").order("name").execute()
        
        if result.data:
            return [ClientInDB(**client) for client in result.data]
        
        return []
    
    async def update_client(self, client_id: str, update_data: ClientUpdate) -> ClientInDB:
        """Update a client"""
        client = await self.get_client(client_id)
        if not client:
            raise HTTPException(status_code=404, detail=f"Client {client_id} not found")
        
        # Update fields
        update_dict = update_data.dict(exclude_unset=True)
        if update_dict:
            update_dict["updated_at"] = datetime.utcnow().isoformat()
            
            result = self.supabase.table(self.table_name).update(update_dict).eq("id", client_id).execute()
            
            if result.data:
                return ClientInDB(**result.data[0])
            else:
                raise HTTPException(status_code=500, detail="Failed to update client")
        
        return client
    
    async def delete_client(self, client_id: str) -> bool:
        """Delete a client"""
        result = self.supabase.table(self.table_name).delete().eq("id", client_id).execute()
        
        return len(result.data) > 0 if result.data else False
    
    async def get_active_clients(self) -> List[ClientInDB]:
        """Get all active clients"""
        result = self.supabase.table(self.table_name).select("*").eq("active", True).order("name").execute()
        
        if result.data:
            return [ClientInDB(**client) for client in result.data]
        
        return []
    
    async def get_client_by_domain(self, domain: str) -> Optional[ClientInDB]:
        """Get a client by domain"""
        result = self.supabase.table(self.table_name).select("*").eq("domain", domain).execute()
        
        if result.data and len(result.data) > 0:
            return ClientInDB(**result.data[0])
        
        return None
    
    async def validate_api_key(self, client_id: str, api_key: str) -> bool:
        """Validate an API key for a client"""
        client = await self.get_client(client_id)
        if not client:
            return False
            
        # Check if the provided API key matches the client's license key
        # In production, you'd want a more sophisticated API key system
        return client.settings.license_key == api_key
    
    async def get_client_supabase_config(self, client_id: str) -> Optional[Dict[str, str]]:
        """Get Supabase configuration for a specific client"""
        client = await self.get_client(client_id)
        if not client:
            return None
            
        return {
            "url": str(client.settings.supabase.url),
            "anon_key": client.settings.supabase.anon_key,
            "service_role_key": client.settings.supabase.service_role_key
        }
    
    def get_client_supabase_client(self, client_id: str) -> Optional[SupabaseClient]:
        """Get a Supabase client instance for a specific client"""
        config = self.get_client_supabase_config(client_id)
        if not config:
            return None
            
        return create_client(config["url"], config["service_role_key"])
    
    async def initialize_default_clients(self):
        """Initialize default clients if they don't exist"""
        # Check if table exists first
        await self.ensure_table_exists()
        
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
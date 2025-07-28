from supabase import create_client, Client
from typing import Optional, Dict, Any, List
import logging
from datetime import datetime
import asyncio
from functools import lru_cache

from app.config import settings
from app.utils.exceptions import DatabaseError, AuthenticationError

logger = logging.getLogger(__name__)

class SupabaseManager:
    """Manages Supabase connections and operations"""
    
    def __init__(self):
        self.admin_client: Optional[Client] = None
        self.auth_client: Optional[Client] = None
        self._initialized = False
    
    async def initialize(self):
        """Initialize Supabase clients"""
        if self._initialized:
            return
        
        try:
            # Get credentials dynamically
            from app.utils.supabase_credentials import SupabaseCredentialManager
            url, anon_key, service_role_key = await SupabaseCredentialManager.get_service_credentials()
            
            # Service role client for admin operations
            self.admin_client = create_client(url, service_role_key)
            
            # Anon client for Supabase Auth operations
            self.auth_client = create_client(url, anon_key)
            
            self._initialized = True
            logger.info("Supabase clients initialized successfully with dynamic credentials")
            
        except Exception as e:
            logger.error(f"Failed to initialize Supabase clients: {e}")
            raise DatabaseError("Failed to connect to database")
    
    async def close(self):
        """Close Supabase connections"""
        # Supabase Python client doesn't require explicit closing
        self._initialized = False
    
    async def health_check(self) -> bool:
        """Check Supabase connection health"""
        try:
            # Try a simple query on the clients table (platform database)
            result = await self.execute_query(
                self.admin_client.table("clients").select("id").limit(1)
            )
            return True
        except Exception as e:
            logger.error(f"Supabase health check failed: {e}")
            return False
    
    async def check_database_connection(self) -> bool:
        """Check if database is accessible"""
        try:
            # Test database connection with a simple query on the clients table
            result = await self.execute_query(
                self.admin_client.table("clients").select("id").limit(1)
            )
            return True
        except Exception:
            return False
    
    def get_user_client(self, access_token: str) -> Client:
        """Get client authenticated with user's Supabase Auth token"""
        client = create_client(settings.supabase_url, settings.supabase_anon_key)
        client.auth.set_session(access_token, refresh_token=None)
        return client
    
    async def execute_query(self, query):
        """Execute a Supabase query with error handling"""
        try:
            result = query.execute()
            return result.data
        except Exception as e:
            logger.error(f"Database query failed: {e}")
            raise DatabaseError(f"Database operation failed: {str(e)}")
    
    # User Management
    async def verify_jwt_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Verify Supabase Auth JWT token"""
        try:
            user = self.auth_client.auth.get_user(token)
            if user and user.user:
                return {
                    "id": user.user.id,
                    "email": user.user.email,
                    "metadata": user.user.user_metadata
                }
            return None
        except Exception as e:
            logger.error(f"Token verification failed: {e}")
            return None
    
    async def create_user_profile(self, user_id: str, email: str, metadata: Dict[str, Any]):
        """Create profile after Supabase Auth signup"""
        profile_data = {
            "id": user_id,
            "email": email,
            "full_name": metadata.get("full_name", ""),
            "role": "user",
            "created_at": datetime.utcnow().isoformat()
        }
        
        return await self.execute_query(
            self.admin_client.table("profiles").insert(profile_data)
        )
    
    async def get_user_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user profile from Supabase"""
        result = await self.execute_query(
            self.admin_client.table("profiles")
            .select("*")
            .eq("id", user_id)
            .single()
        )
        return result
    
    # Agent Management
    async def get_agent_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        """Get agent by slug"""
        result = await self.execute_query(
            self.admin_client.table("agents")
            .select("*")
            .eq("slug", slug)
            .single()
        )
        return result
    
    async def get_agent_configuration(self, agent_slug: str) -> Optional[Dict[str, Any]]:
        """Get agent runtime configuration"""
        result = await self.execute_query(
            self.admin_client.table("agent_configurations")
            .select("*")
            .eq("agent_slug", agent_slug)
            .single()
        )
        return result
    
    async def list_agents(self, enabled_only: bool = True, limit: int = 100) -> List[Dict[str, Any]]:
        """List agents with optional filtering"""
        query = self.admin_client.table("agents").select("*")
        
        if enabled_only:
            query = query.eq("enabled", True)
        
        query = query.limit(limit).order("created_at", desc=True)
        
        return await self.execute_query(query)
    
    # Conversation Management
    async def create_conversation(self, conversation_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new conversation"""
        return await self.execute_query(
            self.admin_client.table("conversations").insert(conversation_data)
        )
    
    async def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Get conversation by ID"""
        result = await self.execute_query(
            self.admin_client.table("conversations")
            .select("*")
            .eq("id", conversation_id)
            .single()
        )
        return result
    
    async def add_conversation_message(self, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """Add message to conversation"""
        return await self.execute_query(
            self.admin_client.table("conversation_transcripts").insert(message_data)
        )
    
    async def get_conversation_messages(
        self,
        conversation_id: str,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Get messages for a conversation"""
        return await self.execute_query(
            self.admin_client.table("conversation_transcripts")
            .select("*")
            .eq("conversation_id", conversation_id)
            .order("created_at", desc=False)
            .limit(limit)
            .offset(offset)
        )
    
    # Document Management
    async def create_document(self, document_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create document record"""
        return await self.execute_query(
            self.admin_client.table("documents").insert(document_data)
        )
    
    async def update_document(self, document_id: str, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update document record"""
        return await self.execute_query(
            self.admin_client.table("documents")
            .update(update_data)
            .eq("id", document_id)
        )
    
    async def create_document_chunks(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Create multiple document chunks"""
        return await self.execute_query(
            self.admin_client.table("document_chunks").insert(chunks)
        )
    
    # Tool Management
    async def get_tools_for_agent(self, agent_id: str) -> List[Dict[str, Any]]:
        """Get all tools configured for an agent"""
        # Get agent-tool configurations
        agent_tools = await self.execute_query(
            self.admin_client.table("autonomite_agent_tools")
            .select("*, sidekick_tools(*)")
            .eq("agent_id", agent_id)
            .eq("enabled", True)
        )
        
        return agent_tools
    
    # WordPress Site Management
    async def register_wordpress_site(self, site_data: Dict[str, Any]) -> Dict[str, Any]:
        """Register a new WordPress site"""
        return await self.execute_query(
            self.admin_client.table("wordpress_sites").insert(site_data)
        )
    
    async def get_wordpress_site_by_domain(self, domain: str) -> Optional[Dict[str, Any]]:
        """Get WordPress site by domain"""
        result = await self.execute_query(
            self.admin_client.table("wordpress_sites")
            .select("*")
            .eq("domain", domain)
            .single()
        )
        return result
    
    async def verify_api_key(self, api_key_hash: str) -> Optional[Dict[str, Any]]:
        """Verify WordPress site API key"""
        result = await self.execute_query(
            self.admin_client.table("wordpress_sites")
            .select("*")
            .eq("api_key_hash", api_key_hash)
            .single()
        )
        return result

# Create singleton instance
supabase_manager = SupabaseManager()
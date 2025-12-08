from supabase import create_client, Client
from typing import Optional, Dict, Any, List
import logging
from datetime import datetime
import asyncio
import uuid
from functools import lru_cache
import httpx

from app.config import settings
from app.utils.exceptions import DatabaseError, AuthenticationError

logger = logging.getLogger(__name__)

class SupabaseManager:
    """Manages Supabase connections and operations"""
    
    def __init__(self):
        self.admin_client: Optional[Client] = None
        self.auth_client: Optional[Client] = None
        self._initialized = False
        # Allowed columns for profiles table writes
        self._profile_allowed_columns = {
            "user_id",
            "email",
            "full_name",
            "phone",
            "avatar_url",
            "role",
        }
    
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
            "user_id": user_id,
            "email": email,
            "full_name": metadata.get("full_name", ""),
            "role": "user",
            "created_at": datetime.utcnow().isoformat()
        }
        
        return await self.execute_query(
            self.admin_client.table("profiles").insert(profile_data)
        )
    
    async def get_user_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user profile from auth metadata (primary) and optionally profiles table."""
        merged: Dict[str, Any] = {}
        try:
            auth_user = await self.get_auth_user(user_id)
            if auth_user:
                meta = getattr(auth_user, "user_metadata", None) or {}
                merged["user_id"] = getattr(auth_user, "id", user_id)
                merged["email"] = getattr(auth_user, "email", None)
                for key, value in meta.items():
                    merged[key] = value
        except Exception as e:
            logger.debug(f"Auth metadata lookup failed for {user_id}: {e}")

        # Best-effort merge from profiles table (if it exists)
        if self.admin_client:
            try:
                profile_row = (
                    self.admin_client.table("profiles")
                    .select("*")
                    .eq("user_id", user_id)
                    .single()
                    .execute()
                )
                data = getattr(profile_row, "data", None)
                if isinstance(data, dict):
                    for k, v in data.items():
                        if v is not None and merged.get(k) is None:
                            merged[k] = v
            except Exception as e:
                logger.debug(f"profiles lookup failed for {user_id}: {e}")

        return merged if merged else None

    async def upsert_telegram_link(self, user_id: str, username: Optional[str], telegram_user_id: str) -> None:
        """Persist Telegram binding."""
        if not user_id or not telegram_user_id:
            return
        payload = {
            "user_id": user_id,
            "telegram_user_id": int(telegram_user_id),
            "telegram_username": username,
            "verified_at": datetime.utcnow().isoformat(),
            "status": "verified",
        }
        try:
            self.admin_client.table("user_telegram_links").upsert(payload, on_conflict="user_id").execute()
        except Exception as e:
            logger.warning(f"Failed to upsert telegram link for {user_id}: {e}")

    def _is_valid_uuid(self, value: str) -> bool:
        try:
            uuid.UUID(str(value))
            return True
        except Exception:
            return False

    async def update_user_profile(self, user_id: Optional[str], updates: Dict[str, Any], email: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Upsert a user profile by user_id or email; auth metadata is authoritative."""
        if not updates:
            return None
        profile: Dict[str, Any] = {k: v for k, v in updates.items() if v is not None}
        if email:
            profile.setdefault("email", email)
        if user_id:
            profile["user_id"] = user_id

        # Resolve target auth user id
        target_id = None
        if user_id and self._is_valid_uuid(user_id):
            target_id = user_id
        elif email:
            try:
                auth_user = await self.find_auth_user_by_email(email)
                if auth_user:
                    target_id = getattr(auth_user, "id", None) or auth_user.get("id")
                    if target_id:
                        profile["user_id"] = target_id
            except Exception:
                target_id = None

        # Best-effort profiles table write (only if UUID and table exists)
        if target_id and self._is_valid_uuid(target_id):
            db_payload = {
                k: v
                for k, v in profile.items()
                if v is not None and k in self._profile_allowed_columns
            }
            db_payload["user_id"] = target_id
            try:
                result = (
                    self.admin_client.table("profiles")
                    .upsert(db_payload, on_conflict="user_id")
                    .execute()
                )
                data = getattr(result, "data", None) or []
                if isinstance(data, list) and data:
                    profile.update(data[0])
            except Exception as e:
                logger.warning("Profile upsert skipped (auth metadata only): %s", e)

        # Auth metadata update (preferred for extended fields)
        try:
            user_metadata = {}
            for key in ("full_name", "company", "phone", "telegram_username"):
                if key in profile and profile[key] is not None:
                    user_metadata[key] = profile[key]
            if user_metadata and target_id:
                # Try REST update first (allows arbitrary metadata); fallback to SDK
                updated = await self._update_auth_metadata(target_id, user_metadata)
                if not updated:
                    try:
                        self.admin_client.auth.admin.update_user_by_id(target_id, user_metadata=user_metadata)
                        updated = True
                    except Exception:
                        pass
                if not updated:
                    logger.warning(f"Auth metadata update failed for user {target_id}")
        except Exception as e:
            logger.warning(f"Auth metadata update failed for user {target_id or email}: {e}")

        return profile

    async def upsert_profile_by_email(self, email: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Upsert profile using email when id is unavailable."""
        if not email:
            return None
            
        # Try to resolve user_id from email first
        try:
            auth_user = await self.find_auth_user_by_email(email)
            user_id = None
            if auth_user:
                user_id = getattr(auth_user, "id", None) or auth_user.get("id")
                
            if user_id:
                # If we have a user_id, delegate to update_user_profile which handles it correctly
                return await self.update_user_profile(user_id, updates)
        except Exception as e:
            logger.warning(f"Failed to resolve auth user for {email}: {e}")

        # If we still don't have a user_id, we can't safely write to profiles table
        # because user_id is a required foreign key.
        logger.warning(f"Cannot upsert profile for {email}: User not found in Auth, skipping DB write.")
        
        # Return a constructed profile so the UI doesn't break, but don't persist to DB.
        # This handles "virtual" admins (like hardcoded credentials) that don't exist in DB.
        payload = {k: v for k, v in updates.items() if v is not None}
        payload["email"] = email
        return payload
    
    async def get_auth_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Fetch auth user (admin scope)."""
        try:
            res = self.admin_client.auth.admin.get_user_by_id(user_id)
            return res.user if hasattr(res, "user") else None
        except Exception as e:
            logger.warning(f"Failed to fetch auth user {user_id}: {e}")
            return None

    async def find_auth_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Search auth users by email via admin REST (more reliable than pagination)."""
        if not email:
            return None
        email_lower = email.strip().lower()
        try:
            url = f"{settings.supabase_url}/auth/v1/admin/users"
            headers = {
                "apikey": settings.supabase_service_role_key,
                "Authorization": f"Bearer {settings.supabase_service_role_key}",
            }
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(url, params={"email": email_lower}, headers=headers)
                resp.raise_for_status()
                data = resp.json() or {}
                users = data.get("users") or []
                for u in users:
                    u_email = (u.get("email") or "").lower()
                    if u_email == email_lower:
                        return u
        except Exception as e:
            logger.warning(f"Auth user search by email failed for {email}: {e}")
        return None

    async def _update_auth_metadata(self, user_id: str, user_metadata: Dict[str, Any]) -> bool:
        """Update auth user metadata via admin REST (more reliable for arbitrary fields)."""
        if not user_id or not user_metadata:
            return False
        try:
            url = f"{settings.supabase_url}/auth/v1/admin/users/{user_id}"
            headers = {
                "apikey": settings.supabase_service_role_key,
                "Authorization": f"Bearer {settings.supabase_service_role_key}",
                "Content-Type": "application/json",
            }
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.put(url, headers=headers, json={"user_metadata": user_metadata})
                resp.raise_for_status()
            return True
        except Exception as e:
            logger.warning(f"Auth metadata REST update failed for user {user_id}: {e}")
            return False

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

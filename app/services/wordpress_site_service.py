"""Service for managing WordPress sites"""
import json
from datetime import datetime
from typing import List, Optional, Dict, Any
import redis
from supabase import create_client, Client
import httpx

from app.models.wordpress_site import (
    WordPressSite, WordPressSiteCreate, WordPressSiteUpdate,
    WordPressSiteAuth, WordPressSiteStats
)
import logging

logger = logging.getLogger(__name__)


class WordPressSiteService:
    """Service for WordPress site management with hybrid storage"""
    
    def __init__(self, supabase_url: str, supabase_key: str, redis_client: redis.Redis):
        self.supabase = create_client(supabase_url, supabase_key)
        self.redis = redis_client
        self.table_name = "wordpress_sites"
        self.cache_prefix = "wp_site:"
        self.cache_ttl = 3600  # 1 hour
        
    def _get_from_cache(self, site_id: str) -> Optional[Dict[str, Any]]:
        """Get WordPress site from Redis cache"""
        try:
            data = self.redis.get(f"{self.cache_prefix}{site_id}")
            if data:
                return json.loads(data)
        except Exception as e:
            logger.error(f"Error reading from cache: {e}")
        return None
        
    def _set_cache(self, site_id: str, data: Dict[str, Any]):
        """Set WordPress site in Redis cache"""
        try:
            self.redis.setex(
                f"{self.cache_prefix}{site_id}",
                self.cache_ttl,
                json.dumps(data, default=str)
            )
        except Exception as e:
            logger.error(f"Error writing to cache: {e}")
            
    def _invalidate_cache(self, site_id: str):
        """Invalidate cache for a WordPress site"""
        try:
            self.redis.delete(f"{self.cache_prefix}{site_id}")
            self.redis.delete(f"{self.cache_prefix}by_domain:*")
            self.redis.delete(f"{self.cache_prefix}by_api_key:*")
        except Exception as e:
            logger.error(f"Error invalidating cache: {e}")
            
    def create_site(self, site_data: WordPressSiteCreate) -> WordPressSite:
        """Create a new WordPress site registration"""
        site_dict = site_data.model_dump()
        
        # Generate unique ID, API key and secret
        import uuid
        site_id = str(uuid.uuid4())
        api_key = WordPressSite.generate_api_key()
        api_secret = WordPressSite.generate_api_secret()
        
        # Add generated fields
        site_dict.update({
            "id": site_id,
            "api_key": api_key,
            "api_secret": api_secret,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "request_count": 0
        })
        
        # Try to store in Supabase first
        try:
            result = self.supabase.table(self.table_name).insert(site_dict).execute()
            logger.info(f"WordPress site {site_id} created in Supabase")
        except Exception as e:
            logger.error(f"Error creating site in Supabase: {e}")
            logger.info("Storing in Redis only")
            
        # Always store in Redis for fast access
        self._set_cache(site_id, site_dict)
        
        # Also cache by domain and API key for lookups
        self.redis.setex(
            f"{self.cache_prefix}by_domain:{site_dict['domain']}",
            self.cache_ttl,
            site_id
        )
        self.redis.setex(
            f"{self.cache_prefix}by_api_key:{api_key}",
            self.cache_ttl,
            site_id
        )
        
        return WordPressSite(**site_dict)
        
    def get_site(self, site_id: str) -> Optional[WordPressSite]:
        """Get a WordPress site by ID"""
        # Check cache first
        cached = self._get_from_cache(site_id)
        if cached:
            return WordPressSite(**cached)
            
        # Try Supabase
        try:
            result = self.supabase.table(self.table_name).select("*").eq("id", site_id).execute()
            if result.data:
                site_dict = result.data[0]
                self._set_cache(site_id, site_dict)
                return WordPressSite(**site_dict)
        except Exception as e:
            logger.error(f"Error fetching site from Supabase: {e}")
            
        return None
        
    def get_site_by_domain(self, domain: str) -> Optional[WordPressSite]:
        """Get a WordPress site by domain"""
        # Normalize domain
        domain = domain.lower().strip('/')
        if domain.startswith('http://') or domain.startswith('https://'):
            domain = domain.split('://', 1)[1]
            
        # Check cache for site ID
        site_id = self.redis.get(f"{self.cache_prefix}by_domain:{domain}")
        if site_id:
            return self.get_site(site_id)
            
        # Try Supabase
        try:
            result = self.supabase.table(self.table_name).select("*").eq("domain", domain).execute()
            if result.data:
                site_dict = result.data[0]
                self._set_cache(site_dict['id'], site_dict)
                self.redis.setex(
                    f"{self.cache_prefix}by_domain:{domain}",
                    self.cache_ttl,
                    site_dict['id']
                )
                return WordPressSite(**site_dict)
        except Exception as e:
            logger.error(f"Error fetching site by domain from Supabase: {e}")
            
        return None
        
    def get_site_by_api_key(self, api_key: str) -> Optional[WordPressSite]:
        """Get a WordPress site by API key"""
        # Check cache for site ID
        site_id = self.redis.get(f"{self.cache_prefix}by_api_key:{api_key}")
        if site_id:
            return self.get_site(site_id)
            
        # Try Supabase
        try:
            result = self.supabase.table(self.table_name).select("*").eq("api_key", api_key).execute()
            if result.data:
                site_dict = result.data[0]
                self._set_cache(site_dict['id'], site_dict)
                self.redis.setex(
                    f"{self.cache_prefix}by_api_key:{api_key}",
                    self.cache_ttl,
                    site_dict['id']
                )
                return WordPressSite(**site_dict)
        except Exception as e:
            logger.error(f"Error fetching site by API key from Supabase: {e}")
            
        return None
        
    def update_site(self, site_id: str, update_data: WordPressSiteUpdate) -> Optional[WordPressSite]:
        """Update a WordPress site"""
        # Get current site
        site = self.get_site(site_id)
        if not site:
            return None
            
        # Prepare update
        update_dict = update_data.model_dump(exclude_unset=True)
        update_dict["updated_at"] = datetime.utcnow().isoformat()
        
        # Try to update in Supabase
        try:
            result = self.supabase.table(self.table_name).update(update_dict).eq("id", site_id).execute()
            logger.info(f"WordPress site {site_id} updated in Supabase")
        except Exception as e:
            logger.error(f"Error updating site in Supabase: {e}")
            
        # Update cache
        site_dict = site.model_dump()
        site_dict.update(update_dict)
        self._set_cache(site_id, site_dict)
        self._invalidate_cache(site_id)
        
        return WordPressSite(**site_dict)
        
    def validate_api_key(self, api_key: str, api_secret: Optional[str] = None) -> Optional[WordPressSite]:
        """Validate API key and optionally secret"""
        site = self.get_site_by_api_key(api_key)
        if not site or not site.is_active:
            return None
            
        # If secret provided, validate it
        if api_secret and site.api_secret != api_secret:
            return None
            
        # Update last seen
        self.update_last_seen(site.id)
        
        return site
        
    def update_last_seen(self, site_id: str):
        """Update last seen timestamp and increment request count"""
        try:
            # Update in Supabase
            update_data = {
                "last_seen_at": datetime.utcnow().isoformat(),
                "request_count": self.supabase.rpc("increment", {"x": 1, "row_id": site_id}).execute()
            }
            self.supabase.table(self.table_name).update(update_data).eq("id", site_id).execute()
        except Exception as e:
            logger.error(f"Error updating last seen: {e}")
            
        # Update cache
        cached = self._get_from_cache(site_id)
        if cached:
            cached["last_seen_at"] = datetime.utcnow().isoformat()
            cached["request_count"] = cached.get("request_count", 0) + 1
            self._set_cache(site_id, cached)
            
    def list_sites(self, client_id: Optional[str] = None, is_active: Optional[bool] = None) -> List[WordPressSite]:
        """List WordPress sites with optional filters"""
        sites = []
        
        # Try Supabase
        try:
            query = self.supabase.table(self.table_name).select("*")
            if client_id:
                query = query.eq("client_id", client_id)
            if is_active is not None:
                query = query.eq("is_active", is_active)
                
            result = query.execute()
            sites = [WordPressSite(**site) for site in result.data]
        except Exception as e:
            logger.error(f"Error listing sites from Supabase: {e}")
            
            # Fallback to Redis scan
            cursor = 0
            pattern = f"{self.cache_prefix}*"
            while True:
                cursor, keys = self.redis.scan(cursor, match=pattern, count=100)
                for key in keys:
                    if not key.decode().startswith(f"{self.cache_prefix}by_"):
                        data = self.redis.get(key)
                        if data:
                            site_dict = json.loads(data)
                            if (not client_id or site_dict.get("client_id") == client_id) and \
                               (is_active is None or site_dict.get("is_active") == is_active):
                                sites.append(WordPressSite(**site_dict))
                if cursor == 0:
                    break
                    
        return sites
        
    def get_site_stats(self, site_id: str) -> Optional[WordPressSiteStats]:
        """Get statistics for a WordPress site"""
        site = self.get_site(site_id)
        if not site:
            return None
            
        # Get additional stats from Supabase
        stats = {
            "site_id": site.id,
            "domain": site.domain,
            "request_count": site.request_count,
            "last_seen_at": site.last_seen_at,
            "active_users": 0,
            "total_conversations": 0,
            "total_messages": 0
        }
        
        try:
            # Count active users
            users_result = self.supabase.table("profiles").select("id", count="exact").eq("wordpress_site_id", site_id).execute()
            stats["active_users"] = users_result.count or 0
            
            # Count conversations
            conv_result = self.supabase.table("conversations").select("id", count="exact").eq("wordpress_site_id", site_id).execute()
            stats["total_conversations"] = conv_result.count or 0
            
            # Count messages
            msg_result = self.supabase.table("conversation_transcripts").select("id", count="exact").eq("wordpress_site_id", site_id).execute()
            stats["total_messages"] = msg_result.count or 0
        except Exception as e:
            logger.error(f"Error fetching site stats: {e}")
            
        return WordPressSiteStats(**stats)
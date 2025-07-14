"""Service for managing WordPress sites using Supabase only"""
import json
from datetime import datetime
from typing import List, Optional, Dict, Any
from supabase import create_client, Client
import httpx
import uuid

from app.models.wordpress_site import (
    WordPressSite, WordPressSiteCreate, WordPressSiteUpdate,
    WordPressSiteAuth, WordPressSiteStats
)
import logging

logger = logging.getLogger(__name__)


class WordPressSiteService:
    """Service for WordPress site management with Supabase storage"""
    
    def __init__(self, supabase_url: str, supabase_key: str):
        self.supabase = create_client(supabase_url, supabase_key)
        self.table_name = "wordpress_sites"
        
    async def ensure_table_exists(self):
        """Ensure the wordpress_sites table exists in Supabase"""
        # This would typically be done via migrations
        # Schema should include indexes on domain and api_key for fast lookups
        pass
        
    def create_site(self, site_data: WordPressSiteCreate) -> WordPressSite:
        """Create a new WordPress site registration"""
        site_dict = site_data.model_dump()
        
        # Generate unique ID, API key and secret
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
        
        # Store in Supabase
        try:
            result = self.supabase.table(self.table_name).insert(site_dict).execute()
            if result.data:
                logger.info(f"WordPress site {site_id} created in Supabase")
                return WordPressSite(**result.data[0])
            else:
                raise Exception("Failed to create site in Supabase")
        except Exception as e:
            logger.error(f"Error creating site in Supabase: {e}")
            raise
        
    def get_site(self, site_id: str) -> Optional[WordPressSite]:
        """Get a WordPress site by ID"""
        try:
            result = self.supabase.table(self.table_name).select("*").eq("id", site_id).execute()
            if result.data:
                return WordPressSite(**result.data[0])
        except Exception as e:
            logger.error(f"Error fetching site from Supabase: {e}")
            
        return None
        
    def get_site_by_domain(self, domain: str) -> Optional[WordPressSite]:
        """Get a WordPress site by domain"""
        # Normalize domain
        domain = domain.lower().strip('/')
        if domain.startswith('http://') or domain.startswith('https://'):
            domain = domain.split('://', 1)[1]
            
        try:
            result = self.supabase.table(self.table_name).select("*").eq("domain", domain).execute()
            if result.data:
                return WordPressSite(**result.data[0])
        except Exception as e:
            logger.error(f"Error fetching site by domain from Supabase: {e}")
            
        return None
        
    def get_site_by_api_key(self, api_key: str) -> Optional[WordPressSite]:
        """Get a WordPress site by API key"""
        try:
            result = self.supabase.table(self.table_name).select("*").eq("api_key", api_key).execute()
            if result.data:
                return WordPressSite(**result.data[0])
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
        
        # Update in Supabase
        try:
            result = self.supabase.table(self.table_name).update(update_dict).eq("id", site_id).execute()
            if result.data:
                logger.info(f"WordPress site {site_id} updated in Supabase")
                return WordPressSite(**result.data[0])
        except Exception as e:
            logger.error(f"Error updating site in Supabase: {e}")
            return None
        
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
            # Get current request count
            result = self.supabase.table(self.table_name).select("request_count").eq("id", site_id).execute()
            current_count = result.data[0]["request_count"] if result.data else 0
            
            # Update with incremented count
            update_data = {
                "last_seen_at": datetime.utcnow().isoformat(),
                "request_count": current_count + 1
            }
            self.supabase.table(self.table_name).update(update_data).eq("id", site_id).execute()
        except Exception as e:
            logger.error(f"Error updating last seen: {e}")
            
    def list_sites(self, client_id: Optional[str] = None, is_active: Optional[bool] = None) -> List[WordPressSite]:
        """List WordPress sites with optional filters"""
        sites = []
        
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
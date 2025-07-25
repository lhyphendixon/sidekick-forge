"""
Service to manage Supabase projects using the Management API
This eliminates the need for a separate clients table by using organization projects directly
"""
import httpx
import os
import json
from typing import List, Dict, Any, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class SupabaseProjectService:
    """Service to manage Supabase projects via Management API"""
    
    def __init__(self, access_token: str = None, organization_id: str = None):
        self.access_token = access_token or os.getenv("SUPABASE_ACCESS_TOKEN")
        self.organization_id = organization_id or os.getenv("SUPABASE_ORG_ID")
        self.base_url = "https://api.supabase.com/v1"
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        
        if not self.access_token:
            logger.warning("No Supabase access token provided. Generate one at https://supabase.com/dashboard/account/tokens")
    
    async def get_all_projects(self) -> List[Dict[str, Any]]:
        """Get all projects from the organization"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/projects",
                    headers=self.headers
                )
                
                if response.status_code == 200:
                    projects = response.json()
                    # Filter by organization if specified
                    if self.organization_id:
                        projects = [p for p in projects if p.get("organization_id") == self.organization_id]
                    
                    # Convert to our client format
                    clients = []
                    for project in projects:
                        client = await self._project_to_client(project)
                        clients.append(client)
                    
                    return clients
                else:
                    logger.error(f"Failed to fetch projects: {response.status_code} - {response.text}")
                    return []
                    
        except Exception as e:
            logger.error(f"Error fetching projects: {e}")
            return []
    
    async def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific project by ID"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/projects/{project_id}",
                    headers=self.headers
                )
                
                if response.status_code == 200:
                    project = response.json()
                    return await self._project_to_client(project)
                else:
                    logger.error(f"Failed to fetch project {project_id}: {response.status_code} - {response.text}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error fetching project {project_id}: {e}")
            return None
    
    async def _project_to_client(self, project: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a Supabase project to our client format"""
        # Generate project URL
        project_ref = project.get("id", "")
        project_url = f"https://{project_ref}.supabase.co"
        
        # Get project keys (would need to be fetched separately or configured)
        project_keys = await self._get_project_keys(project_ref)
        
        # Determine domain from project name or use default
        domain = self._generate_domain(project.get("name", ""))
        
        # Convert to client format
        client = {
            "id": project.get("id"),
            "name": project.get("name"),
            "description": f"Supabase project in {project.get('region', 'unknown')} region",
            "domain": domain,
            "active": project.get("status") == "ACTIVE",
            "status": "active" if project.get("status") == "ACTIVE" else "inactive",
            "created_at": project.get("created_at"),
            "updated_at": project.get("created_at"),  # Projects don't have updated_at
            "container_status": "unknown",
            "agent_count": 0,  # Would need to be counted from project
            "settings": {
                "supabase": {
                    "url": project_url,
                    "anon_key": project_keys.get("anon_key", ""),
                    "service_role_key": project_keys.get("service_role_key", "")
                },
                "livekit": {
                    "server_url": "wss://litebridge-hw6srhvi.livekit.cloud",
                    "api_key": "APIUtuiQ47BQBsk",
                    "api_secret": "rVdSevKfORf5hNfvrsek4joeyrwjhdbkC1HIBsdfQcjM"
                },
                "status": "connected" if project.get("status") == "ACTIVE" else "disconnected"
            }
        }
        
        return client
    
    async def _get_project_keys(self, project_ref: str) -> Dict[str, str]:
        """Get project API keys - these would need to be configured separately"""
        # For now, return empty keys as these need to be configured per project
        # In a real implementation, you'd either:
        # 1. Store these in environment variables per project
        # 2. Have a separate configuration system
        # 3. Use the Management API to fetch them (if available)
        
        # Check for environment variables
        anon_key = os.getenv(f"SUPABASE_{project_ref.upper()}_ANON_KEY", "")
        service_key = os.getenv(f"SUPABASE_{project_ref.upper()}_SERVICE_KEY", "")
        
        # If this is the master project, use the known keys
        if project_ref == "yuowazxcxwhczywurmmw":
            anon_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzU3ODQ1NzMsImV4cCI6MjA1MTM2MDU3M30.SmqTIWrScKQWkJ2_PICWVJYpRSKfvqkRcjMMt0ApH1U"
            service_key = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl1b3dhenhjeHdoY3p5d3VybW13Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTczNTc4NDU3MywiZXhwIjoyMDUxMzYwNTczfQ.cAnluEEhLdSkAatKyxX_lR-acWOYXW6w2hPZaC1fZxY"
        
        return {
            "anon_key": anon_key,
            "service_role_key": service_key
        }
    
    def _generate_domain(self, project_name: str) -> str:
        """Generate a domain from project name"""
        if not project_name:
            return "unknown.local"
        
        # Convert project name to domain-like format
        domain = project_name.lower().replace(" ", "").replace("-", "")
        
        # Known mappings
        domain_mappings = {
            "autonomite": "autonomite.ai",
            "livefreeacademy": "livefreeacademy.com",
            "lfa": "livefreeacademy.com"
        }
        
        return domain_mappings.get(domain, f"{domain}.local")
    
    async def get_project_agents(self, project_id: str) -> List[Dict[str, Any]]:
        """Get agents from a specific project by querying its agents table"""
        client = await self.get_project(project_id)
        if not client:
            return []
            
        supabase_url = client["settings"]["supabase"]["url"]
        service_key = client["settings"]["supabase"]["service_role_key"]
        
        if not service_key:
            logger.warning(f"No service key for project {project_id}")
            return []
            
        try:
            async with httpx.AsyncClient() as http_client:
                response = await http_client.get(
                    f"{supabase_url}/rest/v1/agents",
                    headers={
                        "apikey": service_key,
                        "Authorization": f"Bearer {service_key}"
                    }
                )
                
                if response.status_code == 200:
                    agents = response.json()
                    # Add client info to each agent
                    for agent in agents:
                        agent["client_id"] = project_id
                        agent["client_name"] = client["name"]
                    return agents
                else:
                    logger.warning(f"Failed to fetch agents from project {project_id}: {response.status_code}")
                    return []
                    
        except Exception as e:
            logger.error(f"Error fetching agents from project {project_id}: {e}")
            return []
    
    async def get_all_agents(self) -> List[Dict[str, Any]]:
        """Get all agents from all projects"""
        projects = await self.get_all_projects()
        all_agents = []
        
        for project in projects:
            agents = await self.get_project_agents(project["id"])
            all_agents.extend(agents)
            
        return all_agents
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Return cache stats (empty since we don't use caching)"""
        return {
            "type": "project-based",
            "cache_enabled": False,
            "projects_cached": 0,
            "last_refresh": datetime.utcnow().isoformat()
        }
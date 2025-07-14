"""
Agents API endpoints for multi-tenant agent management (Supabase only)
"""
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.models.agent import Agent, AgentCreate, AgentUpdate, AgentInDB, AgentWithClient
from app.services.agent_service_supabase import AgentService
from app.services.client_service_supabase import ClientService
from app.core.dependencies import get_client_service, get_agent_service

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentResponse(BaseModel):
    """Response model for agent operations"""
    success: bool
    message: str
    data: Optional[Any] = None


@router.get("/", response_model=List[Dict[str, Any]])
async def get_all_agents(
    service: AgentService = Depends(get_agent_service)
) -> List[Dict[str, Any]]:
    """Get all agents across all clients with client info"""
    return await service.get_all_agents_with_clients()


@router.get("/client/{client_id}", response_model=List[AgentInDB])
async def get_client_agents(
    client_id: str,
    service: AgentService = Depends(get_agent_service)
) -> List[AgentInDB]:
    """Get all agents for a specific client"""
    return await service.get_client_agents(client_id)


@router.post("/client/{client_id}", response_model=AgentInDB)
async def create_agent(
    client_id: str,
    agent_data: AgentCreate,
    service: AgentService = Depends(get_agent_service)
) -> AgentInDB:
    """Create a new agent for a client"""
    # Ensure client_id matches
    agent_data.client_id = client_id
    return await service.create_agent(client_id, agent_data)


@router.get("/client/{client_id}/{agent_slug}", response_model=AgentInDB)
async def get_agent(
    client_id: str,
    agent_slug: str,
    service: AgentService = Depends(get_agent_service)
) -> AgentInDB:
    """Get a specific agent by slug"""
    agent = await service.get_agent(client_id, agent_slug)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent {agent_slug} not found")
    return agent


@router.put("/client/{client_id}/{agent_slug}", response_model=AgentInDB)
async def update_agent(
    client_id: str,
    agent_slug: str,
    update_data: AgentUpdate,
    service: AgentService = Depends(get_agent_service)
) -> AgentInDB:
    """Update an agent"""
    return await service.update_agent(client_id, agent_slug, update_data)


@router.delete("/client/{client_id}/{agent_slug}")
async def delete_agent(
    client_id: str,
    agent_slug: str,
    service: AgentService = Depends(get_agent_service)
) -> AgentResponse:
    """Delete an agent"""
    success = await service.delete_agent(client_id, agent_slug)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete agent")
    
    return AgentResponse(
        success=True,
        message=f"Agent {agent_slug} deleted successfully"
    )


@router.post("/client/{client_id}/sync")
async def sync_agents(
    client_id: str,
    service: AgentService = Depends(get_agent_service)
) -> AgentResponse:
    """Force sync agents from client's Supabase"""
    count = await service.sync_agents_from_supabase(client_id)
    
    return AgentResponse(
        success=True,
        message=f"Synced {count} agents from Supabase",
        data={"count": count}
    )


# Demo endpoints for testing without real Supabase
@router.post("/demo/create-defaults/{client_id}")
async def create_default_agents(
    client_id: str,
    service: AgentService = Depends(get_agent_service)
) -> AgentResponse:
    """Create default demo agents for testing"""
    demo_agents = [
        AgentCreate(
            slug="support-agent",
            name="Customer Support Agent",
            description="Helps with customer inquiries and support tickets",
            client_id=client_id,
            system_prompt="You are a helpful customer support agent. Be professional, empathetic, and solution-oriented.",
            voice_settings={
                "provider": "livekit",
                "voice_id": "alloy",
                "temperature": 0.7
            },
            enabled=True
        ),
        AgentCreate(
            slug="sales-assistant",
            name="Sales Assistant",
            description="Assists with sales inquiries and product information",
            client_id=client_id,
            system_prompt="You are a knowledgeable sales assistant. Help customers find the right products and answer their questions.",
            voice_settings={
                "provider": "livekit",
                "voice_id": "echo",
                "temperature": 0.8
            },
            enabled=True
        ),
        AgentCreate(
            slug="tech-helper",
            name="Technical Helper",
            description="Provides technical support and troubleshooting",
            client_id=client_id,
            system_prompt="You are a technical support specialist. Help users solve technical problems step by step.",
            voice_settings={
                "provider": "livekit",
                "voice_id": "fable",
                "temperature": 0.5
            },
            enabled=True
        )
    ]
    
    created_count = 0
    for agent_data in demo_agents:
        try:
            await service.create_agent(client_id, agent_data)
            created_count += 1
        except HTTPException as e:
            if "already exists" not in str(e.detail):
                raise
    
    return AgentResponse(
        success=True,
        message=f"Created {created_count} default agents",
        data={"created": created_count}
    )
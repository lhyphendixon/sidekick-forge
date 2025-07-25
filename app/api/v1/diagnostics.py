"""
Voice Chat Diagnostics API
Comprehensive diagnostics for debugging voice chat issues
"""
import asyncio
import logging
import subprocess
from typing import Dict, Any, List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import get_agent_service, get_client_service
from app.integrations.livekit_client import livekit_manager
# Container manager removed - using worker pool architecture
import docker

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])
logger = logging.getLogger(__name__)


@router.get("/voice-chat/{client_id}/{agent_slug}")
async def diagnose_voice_chat(
    client_id: str,
    agent_slug: str,
    agent_service = Depends(get_agent_service),
    client_service = Depends(get_client_service)
) -> Dict[str, Any]:
    """
    Comprehensive voice chat diagnostics to identify issues
    """
    diagnostics = {
        "timestamp": datetime.now().isoformat(),
        "client_id": client_id,
        "agent_slug": agent_slug,
        "checks": {},
        "recommendations": []
    }
    
    # 1. Check client exists and is configured
    try:
        client = await client_service.get_client(client_id)
        if not client:
            diagnostics["checks"]["client"] = {
                "status": "error",
                "message": f"Client {client_id} not found"
            }
            diagnostics["recommendations"].append("Ensure client exists in the system")
        else:
            diagnostics["checks"]["client"] = {
                "status": "ok",
                "name": client.name,
                "livekit_configured": bool(client.settings.livekit.api_key),
                "livekit_url": client.settings.livekit.server_url if client.settings.livekit.server_url else "Not configured"
            }
            
            if not client.settings.livekit.api_key:
                diagnostics["recommendations"].append("Configure LiveKit credentials for this client")
    except Exception as e:
        diagnostics["checks"]["client"] = {
            "status": "error",
            "message": str(e)
        }
    
    # 2. Check agent exists and configuration
    try:
        agent = await agent_service.get_agent(client_id, agent_slug)
        if not agent:
            diagnostics["checks"]["agent"] = {
                "status": "error", 
                "message": f"Agent {agent_slug} not found for client {client_id}"
            }
            diagnostics["recommendations"].append(f"Create agent '{agent_slug}' or use correct slug")
        else:
            diagnostics["checks"]["agent"] = {
                "status": "ok",
                "name": agent.name,
                "enabled": agent.enabled,
                "voice_provider": agent.voice_settings.get("provider", "Not configured"),
                "llm_provider": agent.voice_settings.get("llm_provider", "Not configured"),
                "stt_provider": agent.voice_settings.get("stt_provider", "Not configured")
            }
            
            if not agent.enabled:
                diagnostics["recommendations"].append("Enable the agent")
            
            if not agent.voice_settings.get("provider"):
                diagnostics["recommendations"].append("Configure voice provider for agent")
    except Exception as e:
        diagnostics["checks"]["agent"] = {
            "status": "error",
            "message": str(e)
        }
    
    # 3. Check backend LiveKit connectivity
    try:
        # Test backend LiveKit connection
        rooms = await livekit_manager.list_rooms()
        diagnostics["checks"]["backend_livekit"] = {
            "status": "ok",
            "url": livekit_manager.url,
            "active_rooms": len(rooms),
            "can_connect": True
        }
    except Exception as e:
        diagnostics["checks"]["backend_livekit"] = {
            "status": "error",
            "message": str(e),
            "url": livekit_manager.url
        }
        diagnostics["recommendations"].append("Check backend LiveKit credentials and connectivity")
    
    # 4. Check worker pool status
    try:
        # Check for agent workers in the pool
        client = docker.from_env()
        containers = client.containers.list()
        
        agent_workers = []
        for container in containers:
            if "agent-worker" in container.name or "agent_" in container.name:
                agent_workers.append({
                    "name": container.name,
                    "status": container.status,
                    "id": container.short_id
                })
        
        diagnostics["checks"]["worker_pool"] = {
            "status": "ok" if agent_workers else "warning",
            "total_workers": len(agent_workers),
            "running_workers": sum(1 for w in agent_workers if w["status"] == "running"),
            "worker_names": [w["name"] for w in agent_workers]
        }
        
        if not agent_workers:
            diagnostics["recommendations"].append("No workers found - ensure agent-worker service is running")
    except Exception as e:
        diagnostics["checks"]["worker_pool"] = {
            "status": "error",
            "message": str(e)
        }
        diagnostics["recommendations"].append("Check Docker daemon is running")
    
    # 5. Check agent processes
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True
        )
        
        agent_processes = []
        for line in result.stdout.split('\n'):
            if 'minimal_agent' in line or 'backend_worker' in line or 'autonomite_agent' in line:
                parts = line.split()
                if len(parts) > 10:
                    agent_processes.append({
                        "pid": parts[1],
                        "cpu": parts[2],
                        "mem": parts[3],
                        "command": ' '.join(parts[10:])[:100]
                    })
        
        diagnostics["checks"]["agent_processes"] = {
            "status": "ok" if agent_processes else "warning",
            "count": len(agent_processes),
            "processes": agent_processes[:5]  # Limit to 5 for readability
        }
        
        if not agent_processes:
            diagnostics["recommendations"].append("No agent processes found - ensure agent containers are running")
    except Exception as e:
        diagnostics["checks"]["agent_processes"] = {
            "status": "error",
            "message": str(e)
        }
    
    # 6. Test room creation capability
    try:
        test_room_name = f"diagnostic_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        room_info = await livekit_manager.create_room(
            name=test_room_name,
            empty_timeout=60,  # 1 minute timeout
            max_participants=2
        )
        
        # Clean up test room
        await livekit_manager.delete_room(test_room_name)
        
        diagnostics["checks"]["room_creation"] = {
            "status": "ok",
            "test_room": test_room_name,
            "can_create_rooms": True
        }
    except Exception as e:
        diagnostics["checks"]["room_creation"] = {
            "status": "error",
            "message": str(e)
        }
        diagnostics["recommendations"].append("Unable to create LiveKit rooms - check permissions")
    
    # 7. Check recent worker logs for errors
    if "worker_pool" in diagnostics["checks"] and diagnostics["checks"]["worker_pool"].get("worker_names"):
        try:
            # Get logs from first available worker
            worker_name = diagnostics["checks"]["worker_pool"]["worker_names"][0]
            worker_container = client.containers.get(worker_name)
            logs = worker_container.logs(tail=50).decode('utf-8')
            
            error_count = logs.lower().count("error")
            warning_count = logs.lower().count("warning")
            
            diagnostics["checks"]["worker_logs"] = {
                "status": "warning" if error_count > 0 else "ok",
                "worker": worker_name,
                "error_count": error_count,
                "warning_count": warning_count,
                "recent_errors": []
            }
            
            # Extract recent errors
            for line in logs.split('\n'):
                if 'error' in line.lower():
                    diagnostics["checks"]["worker_logs"]["recent_errors"].append(line[:200])
                    if len(diagnostics["checks"]["worker_logs"]["recent_errors"]) >= 5:
                        break
            
            if error_count > 0:
                diagnostics["recommendations"].append(f"Worker has {error_count} errors - check logs for details")
        except Exception as e:
            diagnostics["checks"]["worker_logs"] = {
                "status": "error",
                "message": str(e)
            }
    
    # Overall status
    error_count = sum(1 for check in diagnostics["checks"].values() if check.get("status") == "error")
    warning_count = sum(1 for check in diagnostics["checks"].values() if check.get("status") == "warning")
    
    diagnostics["overall_status"] = {
        "healthy": error_count == 0,
        "errors": error_count,
        "warnings": warning_count,
        "message": "System healthy" if error_count == 0 else f"Found {error_count} errors"
    }
    
    # Add specific recommendations based on common issues
    if diagnostics["checks"].get("agent_processes", {}).get("count", 0) == 0:
        diagnostics["recommendations"].insert(0, "CRITICAL: No agent processes running. Start agent workers.")
    
    if diagnostics["checks"].get("backend_livekit", {}).get("status") == "error":
        diagnostics["recommendations"].insert(0, "CRITICAL: Cannot connect to LiveKit. Check credentials.")
    
    return diagnostics


@router.post("/voice-chat/test-trigger/{client_id}/{agent_slug}")
async def test_voice_trigger(
    client_id: str,
    agent_slug: str,
    agent_service = Depends(get_agent_service),
    client_service = Depends(get_client_service)
) -> Dict[str, Any]:
    """
    Test triggering a voice agent with detailed logging
    """
    from app.api.v1.trigger import TriggerAgentRequest, TriggerMode, trigger_agent
    import uuid
    
    # Generate test room name
    room_name = f"diagnostic_{agent_slug}_{uuid.uuid4().hex[:8]}"
    
    # Create trigger request
    trigger_request = TriggerAgentRequest(
        agent_slug=agent_slug,
        client_id=client_id,
        mode=TriggerMode.VOICE,
        room_name=room_name,
        user_id="diagnostic_user",
        session_id=f"diag_session_{uuid.uuid4().hex[:8]}",
        conversation_id=f"diag_conv_{uuid.uuid4().hex[:8]}"
    )
    
    try:
        # Trigger the agent
        result = await trigger_agent(trigger_request, agent_service=agent_service)
        
        # Add diagnostic info
        return {
            "success": result.success,
            "trigger_result": result.dict(),
            "diagnostic_info": {
                "room_name": room_name,
                "timestamp": datetime.now().isoformat(),
                "recommendations": []
            }
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "diagnostic_info": {
                "room_name": room_name,
                "timestamp": datetime.now().isoformat(),
                "recommendations": [
                    "Check agent configuration",
                    "Ensure LiveKit credentials are valid",
                    "Verify agent workers are running"
                ]
            }
        }


@router.get("/voice-chat/active-rooms")
async def get_active_rooms() -> Dict[str, Any]:
    """
    List all active LiveKit rooms with participant info
    """
    try:
        rooms = await livekit_manager.list_rooms()
        
        room_details = []
        for room in rooms:
            participants = await livekit_manager.list_participants(room.name)
            room_details.append({
                "name": room.name,
                "created_at": room.creation_time,
                "participants": len(participants),
                "participant_details": [
                    {
                        "identity": p.identity,
                        "name": p.name,
                        "joined_at": p.joined_at
                    }
                    for p in participants
                ]
            })
        
        return {
            "total_rooms": len(rooms),
            "rooms": room_details,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }
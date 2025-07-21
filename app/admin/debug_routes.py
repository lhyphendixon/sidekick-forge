"""
Debug routes for the admin dashboard
"""
from fastapi import APIRouter, WebSocket, Depends, HTTPException
from fastapi.responses import JSONResponse
from typing import Dict, Any, List
import asyncio
import logging
import json
from datetime import datetime

from app.admin.dependencies import get_admin_user
from app.utils.diagnostics import agent_diagnostics
# from app.tests.test_voice_agent_e2e import VoiceAgentE2ETest  # Commented out for production

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/debug", tags=["debug"])

# WebSocket connections for real-time updates
active_connections: List[WebSocket] = []


async def broadcast_debug_message(message: Dict[str, Any]):
    """Broadcast a debug message to all connected clients"""
    disconnected = []
    for connection in active_connections:
        try:
            await connection.send_json(message)
        except Exception:
            disconnected.append(connection)
    
    # Remove disconnected clients
    for conn in disconnected:
        active_connections.remove(conn)


@router.websocket("/stream")
async def debug_stream(websocket: WebSocket):
    """WebSocket endpoint for real-time debug updates"""
    await websocket.accept()
    active_connections.append(websocket)
    
    try:
        # Send initial connection message
        await websocket.send_json({
            "type": "connected",
            "message": "Debug stream connected",
            "timestamp": datetime.utcnow().isoformat()
        })
        
        # Keep connection alive
        while True:
            # Wait for messages (or use as heartbeat)
            await asyncio.sleep(30)
            await websocket.send_json({"type": "heartbeat"})
            
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        active_connections.remove(websocket)


@router.get("/backend-logs")
async def get_backend_logs(admin_user: Dict[str, Any] = Depends(get_admin_user)):
    """Get recent backend logs"""
    try:
        # In production, this would read from a log aggregator
        # For now, return formatted recent logs
        logs = []
        
        # Simulate recent log entries
        log_entries = [
            {"level": "INFO", "message": "Health check passed", "timestamp": datetime.utcnow().isoformat()},
            {"level": "DEBUG", "message": "Processing agent trigger request", "timestamp": datetime.utcnow().isoformat()},
        ]
        
        html = ""
        for entry in log_entries:
            level_class = {
                "ERROR": "text-red-400",
                "WARNING": "text-yellow-400",
                "INFO": "text-blue-400",
                "DEBUG": "text-gray-400"
            }.get(entry["level"], "")
            
            html += f"""
            <div class="mb-1">
                <span class="text-dark-text-secondary">[{entry['timestamp']}]</span>
                <span class="{level_class}">{entry['level']}</span>
                {entry['message']}
            </div>
            """
            
        return html or '<div class="text-dark-text-secondary">No recent logs</div>'
        
    except Exception as e:
        logger.error(f"Error getting backend logs: {e}")
        return '<div class="text-red-500">Error loading logs</div>'


@router.get("/containers")
async def get_containers(admin_user: Dict[str, Any] = Depends(get_admin_user)):
    """Get list of agent containers"""
    try:
        import docker
        client = docker.from_env()
        
        containers = []
        for container in client.containers.list(all=True):
            if "agent_" in container.name:
                containers.append({
                    "name": container.name,
                    "status": container.status,
                    "id": container.short_id,
                    "created": container.attrs['Created']
                })
                
        return containers
        
    except Exception as e:
        logger.error(f"Error getting containers: {e}")
        return []


@router.get("/container-logs/{container_name}")
async def get_container_logs(
    container_name: str,
    admin_user: Dict[str, Any] = Depends(get_admin_user)
):
    """Get logs for a specific container"""
    try:
        import docker
        client = docker.from_env()
        
        container = client.containers.get(container_name)
        logs = container.logs(tail=100, timestamps=True).decode('utf-8')
        log_lines = logs.strip().split('\n')
        
        return {"logs": log_lines}
        
    except Exception as e:
        logger.error(f"Error getting container logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Commented out E2E tests for production - pytest not available
# @router.post("/run-e2e-tests")
# async def run_e2e_tests(admin_user: Dict[str, Any] = Depends(get_admin_user)):
#     """Run end-to-end tests"""
#     try:
#         # Broadcast test start
#         await broadcast_debug_message({
#             "type": "test_start",
#             "message": "Starting E2E tests",
#             "timestamp": datetime.utcnow().isoformat()
#         })
#         
#         tester = VoiceAgentE2ETest()
#         results = await tester.run_all_tests()
#         
#         # Broadcast test completion
#         await broadcast_debug_message({
#             "type": "test_complete",
#             "results": results,
#             "timestamp": datetime.utcnow().isoformat()
#         })
#         
#         return results
#         
#     except Exception as e:
#         logger.error(f"Error running E2E tests: {e}")
#         raise HTTPException(status_code=500, detail=str(e))


@router.get("/test-livekit")
async def test_livekit(admin_user: Dict[str, Any] = Depends(get_admin_user)):
    """Test LiveKit connection"""
    result = await agent_diagnostics.test_livekit_connection(
        server_url="wss://litebridge-hw6srhvi.livekit.cloud",
        api_key="APIUtuiQ47BQBsk",
        api_secret="rVdSevKfORf5hNfvrsek4joeyrwjhdbkC1HIBsdfQcjM"
    )
    return result


@router.get("/active-operations")
async def get_active_operations(admin_user: Dict[str, Any] = Depends(get_admin_user)):
    """Get active diagnostic operations"""
    try:
        operations = agent_diagnostics.active_diagnostics
        
        if not operations:
            return '<div class="text-dark-text-secondary">No active operations</div>'
            
        html = '<div class="space-y-2">'
        for op_id, diag in operations.items():
            elapsed = int((datetime.utcnow().timestamp() - diag.start_time) * 1000)
            html += f"""
            <div class="p-3 bg-dark-elevated rounded">
                <div class="flex justify-between items-center">
                    <span class="font-medium">{diag.operation}</span>
                    <span class="text-xs text-dark-text-secondary">{elapsed}ms</span>
                </div>
                <div class="text-sm text-dark-text-secondary mt-1">
                    Events: {len(diag.events)} | Errors: {len(diag.errors)}
                </div>
            </div>
            """
        html += '</div>'
        
        return html
        
    except Exception as e:
        logger.error(f"Error getting active operations: {e}")
        return '<div class="text-red-500">Error loading operations</div>'


# Add custom logging handler to broadcast logs
class DebugBroadcastHandler(logging.Handler):
    """Custom logging handler that broadcasts to WebSocket clients"""
    
    def emit(self, record):
        try:
            # Only broadcast important logs
            if record.levelno >= logging.INFO and "[DIAG]" in record.getMessage():
                asyncio.create_task(broadcast_debug_message({
                    "type": "log",
                    "source": "backend",
                    "level": record.levelname,
                    "message": record.getMessage(),
                    "timestamp": datetime.utcnow().isoformat()
                }))
        except Exception:
            pass


# Add the broadcast handler to the logger
debug_handler = DebugBroadcastHandler()
debug_handler.setLevel(logging.INFO)
logging.getLogger("app").addHandler(debug_handler)
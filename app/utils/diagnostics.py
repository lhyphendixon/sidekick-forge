"""
Comprehensive diagnostics and logging system for debugging agent issues
"""
import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Dict, Any, List, Optional
from contextlib import asynccontextmanager
import traceback

logger = logging.getLogger(__name__)


class DiagnosticContext:
    """Context manager for tracking diagnostic information through the request lifecycle"""
    
    def __init__(self, operation: str, context: Dict[str, Any] = None):
        self.operation = operation
        self.context = context or {}
        self.start_time = time.time()
        self.events: List[Dict[str, Any]] = []
        self.errors: List[Dict[str, Any]] = []
        self.checkpoints: Dict[str, float] = {}
        
    def add_event(self, event_type: str, message: str, data: Dict[str, Any] = None):
        """Add an event to the diagnostic log"""
        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "elapsed_ms": int((time.time() - self.start_time) * 1000),
            "type": event_type,
            "message": message,
            "data": data or {}
        }
        self.events.append(event)
        logger.info(f"[DIAG] {self.operation} - {event_type}: {message}", extra={"diagnostic_data": data})
        
    def add_error(self, error: Exception, context: str = None):
        """Add an error to the diagnostic log"""
        error_info = {
            "timestamp": datetime.utcnow().isoformat(),
            "elapsed_ms": int((time.time() - self.start_time) * 1000),
            "type": type(error).__name__,
            "message": str(error),
            "context": context,
            "traceback": traceback.format_exc()
        }
        self.errors.append(error_info)
        logger.error(f"[DIAG] {self.operation} - ERROR in {context}: {error}", exc_info=True)
        
    def checkpoint(self, name: str):
        """Mark a checkpoint in the diagnostic timeline"""
        elapsed = time.time() - self.start_time
        self.checkpoints[name] = elapsed
        self.add_event("checkpoint", f"Reached checkpoint: {name}", {"elapsed_seconds": elapsed})
        
    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of the diagnostic session"""
        total_time = time.time() - self.start_time
        return {
            "operation": self.operation,
            "context": self.context,
            "start_time": datetime.fromtimestamp(self.start_time).isoformat(),
            "total_time_ms": int(total_time * 1000),
            "events_count": len(self.events),
            "errors_count": len(self.errors),
            "checkpoints": self.checkpoints,
            "success": len(self.errors) == 0,
            "events": self.events,
            "errors": self.errors
        }


class AgentDiagnostics:
    """Diagnostic tools for agent operations"""
    
    def __init__(self):
        self.active_diagnostics: Dict[str, DiagnosticContext] = {}
        
    def start_diagnostic(self, operation_id: str, operation_type: str, context: Dict[str, Any] = None) -> DiagnosticContext:
        """Start a new diagnostic session"""
        diag = DiagnosticContext(operation_type, context)
        self.active_diagnostics[operation_id] = diag
        diag.add_event("start", f"Starting {operation_type} diagnostic", context)
        return diag
        
    def get_diagnostic(self, operation_id: str) -> Optional[DiagnosticContext]:
        """Get an active diagnostic session"""
        return self.active_diagnostics.get(operation_id)
        
    def end_diagnostic(self, operation_id: str) -> Dict[str, Any]:
        """End a diagnostic session and return summary"""
        if operation_id in self.active_diagnostics:
            diag = self.active_diagnostics.pop(operation_id)
            summary = diag.get_summary()
            logger.info(f"[DIAG] Completed {diag.operation} - Success: {summary['success']}, Time: {summary['total_time_ms']}ms")
            return summary
        return None
        
    async def test_livekit_connection(self, server_url: str, api_key: str, api_secret: str) -> Dict[str, Any]:
        """Test LiveKit connection and configuration"""
        diag = DiagnosticContext("livekit_connection_test")
        
        try:
            from livekit import api
            
            # Test API connection
            diag.add_event("test", "Testing LiveKit API connection", {
                "server_url": server_url,
                "api_key": api_key[:10] + "..."
            })
            
            lk_api = api.LiveKitAPI(server_url, api_key, api_secret)
            
            # List rooms to test connection
            diag.checkpoint("api_initialized")
            rooms = await lk_api.room.list_rooms(api.ListRoomsRequest())
            
            diag.add_event("success", f"Connected to LiveKit - found {len(rooms.rooms)} rooms")
            diag.checkpoint("rooms_listed")
            
            return {
                "success": True,
                "server_reachable": True,
                "api_working": True,
                "rooms_count": len(rooms.rooms),
                "diagnostics": diag.get_summary()
            }
            
        except Exception as e:
            diag.add_error(e, "livekit_connection")
            return {
                "success": False,
                "error": str(e),
                "diagnostics": diag.get_summary()
            }
            
    async def test_agent_container(self, container_name: str) -> Dict[str, Any]:
        """Test agent container health and logs"""
        diag = DiagnosticContext("container_test", {"container": container_name})
        
        try:
            import docker
            client = docker.from_env()
            
            # Get container
            diag.add_event("test", f"Checking container: {container_name}")
            container = client.containers.get(container_name)
            
            # Check status
            status = container.status
            diag.add_event("status", f"Container status: {status}", {"status": status})
            
            # Get recent logs
            logs = container.logs(tail=50, timestamps=True).decode('utf-8')
            log_lines = logs.strip().split('\n')
            
            # Analyze logs for common issues
            issues = []
            for line in log_lines:
                if "error" in line.lower():
                    issues.append({"type": "error", "line": line})
                elif "warning" in line.lower():
                    issues.append({"type": "warning", "line": line})
                elif "failed" in line.lower():
                    issues.append({"type": "failure", "line": line})
                    
            diag.add_event("logs_analyzed", f"Found {len(issues)} potential issues", {
                "issues_count": len(issues),
                "recent_logs": log_lines[-10:]
            })
            
            # Check container health
            health = container.attrs.get('State', {}).get('Health', {})
            health_status = health.get('Status', 'unknown')
            diag.add_event("health", f"Container health: {health_status}", health)
            
            return {
                "success": status == "running",
                "status": status,
                "health": health_status,
                "issues": issues,
                "recent_logs": log_lines[-20:],
                "diagnostics": diag.get_summary()
            }
            
        except Exception as e:
            diag.add_error(e, "container_test")
            return {
                "success": False,
                "error": str(e),
                "diagnostics": diag.get_summary()
            }
            
    async def test_agent_trigger_flow(self, agent_slug: str, client_id: str) -> Dict[str, Any]:
        """Test the complete agent trigger flow"""
        operation_id = f"trigger_test_{int(time.time())}"
        diag = self.start_diagnostic(operation_id, "agent_trigger_flow", {
            "agent_slug": agent_slug,
            "client_id": client_id
        })
        
        try:
            # Import required modules
            from app.api.v1.trigger import TriggerAgentRequest, TriggerMode, trigger_agent
            from app.core.dependencies import get_agent_service
            
            # Create trigger request
            diag.add_event("prepare", "Creating trigger request")
            trigger_request = TriggerAgentRequest(
                agent_slug=agent_slug,
                client_id=client_id,
                mode=TriggerMode.VOICE,
                room_name=f"diag_test_{operation_id}",
                user_id="diagnostic_test"
            )
            
            # Get agent service
            diag.checkpoint("getting_agent_service")
            agent_service = get_agent_service()
            
            # Trigger agent with timeout
            diag.add_event("trigger", "Triggering agent")
            trigger_task = asyncio.create_task(
                trigger_agent(trigger_request, agent_service=agent_service)
            )
            
            # Wait with timeout
            try:
                result = await asyncio.wait_for(trigger_task, timeout=30.0)
                diag.add_event("triggered", "Agent triggered successfully", {
                    "success": result.success,
                    "room_name": result.data.get("room_name") if result.data else None
                })
                
                return {
                    "success": True,
                    "result": result.data if hasattr(result, 'data') else None,
                    "diagnostics": self.end_diagnostic(operation_id)
                }
                
            except asyncio.TimeoutError:
                diag.add_error(Exception("Trigger timeout after 30 seconds"), "trigger_timeout")
                return {
                    "success": False,
                    "error": "Trigger timeout",
                    "diagnostics": self.end_diagnostic(operation_id)
                }
                
        except Exception as e:
            diag.add_error(e, "trigger_flow")
            return {
                "success": False,
                "error": str(e),
                "diagnostics": self.end_diagnostic(operation_id)
            }


# Global diagnostics instance
agent_diagnostics = AgentDiagnostics()


@asynccontextmanager
async def diagnostic_context(operation: str, **kwargs):
    """Context manager for diagnostic tracking"""
    operation_id = f"{operation}_{int(time.time() * 1000)}"
    diag = agent_diagnostics.start_diagnostic(operation_id, operation, kwargs)
    
    try:
        yield diag
    except Exception as e:
        diag.add_error(e, "operation_failed")
        raise
    finally:
        summary = agent_diagnostics.end_diagnostic(operation_id)
        if summary and not summary['success']:
            logger.warning(f"Operation {operation} completed with errors: {len(summary['errors'])} errors")
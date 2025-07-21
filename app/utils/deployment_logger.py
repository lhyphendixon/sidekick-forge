"""
Enhanced Deployment Logger
Addresses oversight agent's requirement for runtime proof in all operations
"""
import logging
import json
import time
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
import inspect
import functools

class DeploymentLogger:
    """Logger that captures runtime proof for all critical operations"""
    
    def __init__(self, name: str):
        self.logger = logging.getLogger(name)
        self.deployment_evidence = {}
        
    def log_with_proof(self, level: str, message: str, evidence: Dict[str, Any]):
        """Log with runtime proof evidence"""
        timestamp = datetime.now().isoformat()
        caller = inspect.stack()[1]
        
        proof_entry = {
            "timestamp": timestamp,
            "level": level,
            "message": message,
            "evidence": evidence,
            "caller": {
                "function": caller.function,
                "line": caller.lineno,
                "file": caller.filename
            }
        }
        
        # Store evidence
        operation_id = f"{caller.function}_{int(time.time() * 1000)}"
        self.deployment_evidence[operation_id] = proof_entry
        
        # Log with evidence
        getattr(self.logger, level)(f"{message} | Evidence: {json.dumps(evidence, default=str)}")
        
        return operation_id
    
    def get_deployment_proof(self, operation_id: Optional[str] = None) -> Dict:
        """Retrieve deployment proof for verification"""
        if operation_id:
            return self.deployment_evidence.get(operation_id, {})
        return self.deployment_evidence
    
    def deployment_verification(self, func):
        """Decorator to automatically capture deployment proof"""
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            evidence = {
                "function": func.__name__,
                "args": str(args)[:100],  # Truncate for readability
                "kwargs": str(kwargs)[:100]
            }
            
            try:
                result = await func(*args, **kwargs)
                evidence["success"] = True
                evidence["duration"] = time.time() - start_time
                
                # Extract key evidence from result
                if isinstance(result, dict):
                    evidence["result_keys"] = list(result.keys())
                    if "container_id" in result:
                        evidence["container_id"] = result["container_id"]
                    if "status" in result:
                        evidence["status"] = result["status"]
                
                self.log_with_proof("info", f"{func.__name__} completed successfully", evidence)
                return result
                
            except Exception as e:
                evidence["success"] = False
                evidence["error"] = str(e)
                evidence["duration"] = time.time() - start_time
                
                self.log_with_proof("error", f"{func.__name__} failed", evidence)
                raise
        
        return wrapper


# Agent-specific logging enhancements
class AgentEventLogger:
    """Specialized logger for agent event verification"""
    
    def __init__(self, agent_name: str, room_name: str):
        self.agent_name = agent_name
        self.room_name = room_name
        self.events = []
        self.logger = logging.getLogger(f"agent.{agent_name}")
        
    def log_event(self, event_type: str, data: Dict[str, Any]):
        """Log agent event with full context"""
        event = {
            "timestamp": datetime.now().isoformat(),
            "agent": self.agent_name,
            "room": self.room_name,
            "type": event_type,
            "data": data
        }
        
        self.events.append(event)
        
        # Special handling for critical events
        if event_type in ["user_speech_committed", "agent_speech_committed"]:
            self.logger.info(f"🎯 CRITICAL EVENT: {event_type} | Data: {json.dumps(data, default=str)}")
        else:
            self.logger.info(f"📌 Event: {event_type} | Data: {json.dumps(data, default=str)}")
        
        return event
    
    def get_event_timeline(self) -> List[Dict]:
        """Get complete event timeline for verification"""
        return self.events
    
    def verify_event_sequence(self, expected_sequence: List[str]) -> Tuple[bool, str]:
        """Verify events occurred in expected sequence"""
        actual_sequence = [e["type"] for e in self.events]
        
        for expected in expected_sequence:
            if expected not in actual_sequence:
                return False, f"Missing event: {expected}"
        
        # Check order
        indices = [actual_sequence.index(e) for e in expected_sequence if e in actual_sequence]
        if indices != sorted(indices):
            return False, f"Events out of order. Expected: {expected_sequence}, Got: {actual_sequence}"
        
        return True, "Event sequence verified"


# Container deployment logger
class ContainerDeploymentLogger:
    """Logger for container deployment verification"""
    
    def __init__(self):
        self.logger = logging.getLogger("container.deployment")
        self.deployments = {}
        
    def log_deployment(self, container_name: str, stage: str, details: Dict[str, Any]):
        """Log container deployment stage with evidence"""
        if container_name not in self.deployments:
            self.deployments[container_name] = {
                "stages": [],
                "created": datetime.now().isoformat()
            }
        
        stage_entry = {
            "timestamp": datetime.now().isoformat(),
            "stage": stage,
            "details": details
        }
        
        self.deployments[container_name]["stages"].append(stage_entry)
        
        self.logger.info(f"Container {container_name} - Stage: {stage} | Details: {json.dumps(details, default=str)}")
        
        # Verify critical stages
        if stage == "started" and "container_id" not in details:
            self.logger.error(f"❌ Missing container_id in deployment proof for {container_name}")
        
        return stage_entry
    
    def get_deployment_proof(self, container_name: str) -> Dict:
        """Get complete deployment proof for a container"""
        return self.deployments.get(container_name, {})
    
    def verify_deployment(self, container_name: str) -> Tuple[bool, str]:
        """Verify container was properly deployed"""
        if container_name not in self.deployments:
            return False, "No deployment record found"
        
        stages = [s["stage"] for s in self.deployments[container_name]["stages"]]
        required_stages = ["build", "configure", "started", "health_check"]
        
        missing = [s for s in required_stages if s not in stages]
        if missing:
            return False, f"Missing deployment stages: {missing}"
        
        # Check for errors
        for stage in self.deployments[container_name]["stages"]:
            if "error" in stage["details"]:
                return False, f"Error in stage {stage['stage']}: {stage['details']['error']}"
        
        return True, "Deployment verified successfully"
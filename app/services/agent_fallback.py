"""
Agent fallback service for handling startup failures
"""
import logging
from typing import Dict, Any, Optional
import asyncio
from datetime import datetime, timedelta

from app.utils.exceptions import ServiceUnavailableError

logger = logging.getLogger(__name__)


class AgentFallbackService:
    """Service to handle agent startup failures and provide fallback options"""
    
    def __init__(self):
        self.failure_counts = {}  # Track failures per client/agent
        self.failure_window = timedelta(minutes=5)  # Window for counting failures
        self.max_failures = 3  # Max failures before escalation
    
    async def handle_startup_failure(
        self,
        client_id: str,
        agent_slug: str,
        container_name: str,
        error: Exception,
        logs: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Handle agent startup failure with escalating responses
        
        Returns:
            Dict with recommended action and details
        """
        failure_key = f"{client_id}:{agent_slug}"
        current_time = datetime.utcnow()
        
        # Track failure
        if failure_key not in self.failure_counts:
            self.failure_counts[failure_key] = []
        
        # Clean old failures outside window
        self.failure_counts[failure_key] = [
            (ts, err) for ts, err in self.failure_counts[failure_key]
            if current_time - ts < self.failure_window
        ]
        
        # Add current failure
        self.failure_counts[failure_key].append((current_time, str(error)))
        failure_count = len(self.failure_counts[failure_key])
        
        logger.error(f"Agent startup failure #{failure_count} for {failure_key}: {error}")
        
        # Analyze error for specific issues
        error_analysis = self._analyze_error(str(error), logs)
        
        # Determine action based on failure count and error type
        if error_analysis["type"] == "missing_api_key":
            return {
                "action": "configuration_error",
                "retry": False,
                "message": f"Missing required API key: {error_analysis['details']}",
                "user_message": "Your agent is missing required API keys. Please configure them in your settings.",
                "resolution": "configure_api_keys"
            }
        
        elif error_analysis["type"] == "resource_limit":
            return {
                "action": "resource_error",
                "retry": True,
                "delay": 30,  # Wait 30 seconds before retry
                "message": "Container resource limits exceeded",
                "user_message": "The system is currently under heavy load. Please try again in a moment.",
                "resolution": "retry_with_delay"
            }
        
        elif failure_count >= self.max_failures:
            return {
                "action": "escalate",
                "retry": False,
                "message": f"Agent failed {failure_count} times in {self.failure_window.total_seconds()}s",
                "user_message": "We're experiencing technical difficulties. Our team has been notified.",
                "resolution": "contact_support",
                "notification": {
                    "type": "critical",
                    "subject": f"Agent startup failures for {client_id}",
                    "details": {
                        "client_id": client_id,
                        "agent_slug": agent_slug,
                        "failure_count": failure_count,
                        "recent_errors": [err for _, err in self.failure_counts[failure_key]],
                        "container_logs": logs[-500:] if logs else None
                    }
                }
            }
        
        else:
            # Standard retry logic
            retry_delay = min(10 * failure_count, 60)  # Exponential backoff, max 60s
            return {
                "action": "retry",
                "retry": True,
                "delay": retry_delay,
                "attempt": failure_count,
                "message": f"Agent startup failed, retrying in {retry_delay}s",
                "user_message": "Starting your agent... please wait.",
                "resolution": "automatic_retry"
            }
    
    def _analyze_error(self, error_str: str, logs: Optional[str] = None) -> Dict[str, Any]:
        """Analyze error to determine type and details"""
        error_lower = error_str.lower()
        logs_lower = (logs or "").lower()
        
        # Check for API key issues
        api_key_patterns = [
            ("openai", "openai api key"),
            ("groq", "groq api key"),
            ("cartesia", "cartesia api key"),
            ("deepgram", "deepgram api key"),
            ("elevenlabs", "elevenlabs api key"),
        ]
        
        for provider, pattern in api_key_patterns:
            if pattern in error_lower or pattern in logs_lower:
                return {
                    "type": "missing_api_key",
                    "details": provider,
                    "provider": provider
                }
        
        # Check for resource issues
        if any(term in error_lower for term in ["resource", "memory", "cpu", "limit"]):
            return {
                "type": "resource_limit",
                "details": "Container resource limits exceeded"
            }
        
        # Check for network issues
        if any(term in error_lower for term in ["connection", "network", "timeout"]):
            return {
                "type": "network_error",
                "details": "Network connectivity issue"
            }
        
        # Default unknown error
        return {
            "type": "unknown",
            "details": error_str[:200]
        }
    
    async def get_fallback_response(
        self,
        client_id: str,
        agent_slug: str,
        user_message: str
    ) -> Dict[str, Any]:
        """
        Provide a fallback response when agent is unavailable
        """
        return {
            "type": "fallback_response",
            "message": "I apologize, but I'm temporarily unavailable. Your message has been saved and I'll respond as soon as possible.",
            "user_message": user_message,
            "saved": True,
            "timestamp": datetime.utcnow().isoformat()
        }
    
    def clear_failure_history(self, client_id: str, agent_slug: str):
        """Clear failure history for a client/agent after successful startup"""
        failure_key = f"{client_id}:{agent_slug}"
        if failure_key in self.failure_counts:
            del self.failure_counts[failure_key]
            logger.info(f"Cleared failure history for {failure_key}")


# Create singleton instance
agent_fallback_service = AgentFallbackService()
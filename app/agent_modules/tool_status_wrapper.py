"""
Tool Status Update Wrapper

Provides verbal status updates during long-running tool executions.
Based on LiveKit documentation: https://docs.livekit.io/agents/build/external-data/#-thinking-sounds
"""

import asyncio
import logging
from typing import Any, Callable, Optional
from functools import wraps

logger = logging.getLogger(__name__)


def with_status_updates(
    tool_func: Callable,
    tool_name: str,
    delay_seconds: float = 2.0,
) -> Callable:
    """
    Wrap a tool function to provide verbal status updates during long-running operations.
    
    Args:
        tool_func: The tool function to wrap
        tool_name: User-friendly name of the tool for status messages
        delay_seconds: How long to wait before providing a status update (default: 2.0 seconds)
    
    Returns:
        Wrapped function that provides status updates
    """
    
    @wraps(tool_func)
    async def wrapper(*args, **kwargs):
        # Extract RunContext if provided (for voice chat)
        context = kwargs.get('context')
        
        # Create status update task
        status_update_task = None
        
        if context and hasattr(context, 'session'):
            async def _speak_status_update():
                try:
                    await asyncio.sleep(delay_seconds)
                    # Generate a brief status update
                    await context.session.generate_reply(
                        instructions=f"""
                        You are currently working on: {tool_name}.
                        It's taking a moment. Briefly acknowledge this to the user in a natural way.
                        Keep your response under 10 words.
                        """
                    )
                    logger.info(f"🗣️ Spoke status update for {tool_name}")
                except asyncio.CancelledError:
                    pass  # Normal cancellation when task completes quickly
                except Exception as exc:
                    logger.warning(f"Failed to speak status update for {tool_name}: {exc}")
            
            status_update_task = asyncio.create_task(_speak_status_update())
        
        try:
            # Execute the actual tool
            if asyncio.iscoroutinefunction(tool_func):
                result = await tool_func(*args, **kwargs)
            else:
                result = tool_func(*args, **kwargs)
                # If it returned a coroutine, await it
                if asyncio.iscoroutine(result):
                    result = await result
            
            return result
        finally:
            # Cancel status update if it hasn't fired yet
            if status_update_task and not status_update_task.done():
                status_update_task.cancel()
                try:
                    await status_update_task
                except asyncio.CancelledError:
                    pass
    
    return wrapper


def get_tool_friendly_name(tool_def: dict) -> str:
    """
    Extract a user-friendly name from a tool definition.
    
    Args:
        tool_def: Tool definition dictionary
    
    Returns:
        User-friendly tool name
    """
    slug = tool_def.get("slug") or tool_def.get("name") or "this task"
    
    # Convert slug to friendly name
    friendly_name = slug.replace("_", " ").replace("-", " ").title()
    
    # Map common tool slugs to better names
    name_map = {
        "Asana Tasks": "checking your Asana tasks",
        "Perplexity Search": "searching for information",
        "Perplexity Search N8N": "searching for information",  # Legacy name fallback
        "Search Knowledge Base": "searching the knowledge base",
        "Knowledge Base": "searching the knowledge base",
        "Rag Search": "searching for relevant information",
        "Content Catalyst": "generating your articles",
        "Prediction Market": "checking prediction markets",
    }
    
    return name_map.get(friendly_name, f"working on {friendly_name}")


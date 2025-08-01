"""
Context-aware LLM wrapper that injects dynamic RAG context before each LLM call.
This ensures the agent always has access to conversation history and documents.
"""
import logging
from typing import List, Dict, Any, Optional
from livekit import agents
from livekit.agents import llm
import asyncio

logger = logging.getLogger(__name__)


class ContextAwareLLM:
    """
    Wraps an LLM plugin to inject dynamic context before each generation.
    This ensures RAG context is always fresh and available to the agent.
    """
    
    def __init__(self, base_llm, context_manager, user_id: str):
        """
        Initialize the context-aware LLM wrapper.
        
        Args:
            base_llm: The underlying LLM plugin (OpenAI, Groq, etc.)
            context_manager: AgentContextManager instance for building context
            user_id: The user ID for context retrieval
        """
        self.base_llm = base_llm
        self.context_manager = context_manager
        self.user_id = user_id
        self._wrapped_attrs = set()
        
        # Copy all attributes from base_llm to maintain compatibility
        for attr in dir(base_llm):
            if not attr.startswith('_') and attr not in ['chat', 'achat']:
                try:
                    setattr(self, attr, getattr(base_llm, attr))
                    self._wrapped_attrs.add(attr)
                except AttributeError:
                    pass
    
    def __getattr__(self, name):
        """Forward any unknown attributes to the base LLM."""
        return getattr(self.base_llm, name)
    
    async def chat(self, messages: List[llm.ChatMessage], **kwargs) -> Any:
        """
        Intercept chat calls to inject dynamic context.
        
        Args:
            messages: List of chat messages
            **kwargs: Additional arguments passed to the base LLM
            
        Returns:
            The response from the base LLM
        """
        try:
            # Extract the latest user message
            user_message = None
            for msg in reversed(messages):
                if msg.role == "user":
                    user_message = msg.content
                    break
            
            # Build dynamic context if we have a user message and context manager
            if user_message and self.context_manager:
                logger.info(f"Building dynamic context for user message: '{user_message[:100]}...'")
                
                try:
                    # Build complete context including conversation history and documents
                    context_result = await self.context_manager.build_complete_context(
                        user_message=user_message,
                        user_id=self.user_id
                    )
                    
                    enhanced_prompt = context_result.get("enhanced_system_prompt")
                    if enhanced_prompt:
                        logger.info("✅ Dynamic context built successfully")
                        
                        # Find the last system message index
                        last_system_idx = -1
                        for i, msg in enumerate(messages):
                            if msg.role == "system":
                                last_system_idx = i
                        
                        # Create enhanced messages list
                        enhanced_messages = messages.copy()
                        
                        # Insert the enhanced context after the last system message
                        # but before any user/assistant messages
                        if last_system_idx >= 0:
                            # Insert after the last system message
                            enhanced_messages.insert(
                                last_system_idx + 1, 
                                llm.ChatMessage(role="system", content=enhanced_prompt)
                            )
                        else:
                            # No system messages, insert at the beginning
                            enhanced_messages.insert(
                                0,
                                llm.ChatMessage(role="system", content=enhanced_prompt)
                            )
                        
                        # Log context metadata
                        context_metadata = context_result.get('context_metadata', {})
                        logger.info(f"Context metadata: {context_metadata}")
                        
                        # Use enhanced messages for the LLM call
                        messages = enhanced_messages
                    else:
                        logger.warning("Context manager returned no enhanced prompt")
                        
                except Exception as e:
                    logger.error(f"Failed to build dynamic context: {e}", exc_info=True)
                    # Continue without enhanced context rather than failing
            
            # Call the base LLM with potentially enhanced messages
            logger.debug(f"Calling base LLM with {len(messages)} messages")
            return await self.base_llm.chat(messages, **kwargs)
            
        except Exception as e:
            logger.error(f"Error in ContextAwareLLM.chat: {e}", exc_info=True)
            raise
    
    # Provide synchronous wrapper if needed
    def chat_sync(self, messages: List[llm.ChatMessage], **kwargs) -> Any:
        """Synchronous version of chat for compatibility."""
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(self.chat(messages, **kwargs))
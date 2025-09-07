import logging
from typing import AsyncIterable, Optional, List, Dict, Any
import uuid
import asyncio
from datetime import datetime

from livekit import rtc
from livekit.agents import llm
from livekit.agents import ModelSettings
from livekit.agents import Agent as CoreAgent
from livekit.agents import voice


logger = logging.getLogger(__name__)


class SidekickAgent(voice.Agent):
    """
    LiveKit-compliant Agent that injects RAG context at the documented node
    `on_user_turn_completed`, and provides explicit logging around the LLM node.
    Now includes citation tracking for RAG responses.
    """

    def __init__(
        self,
        *,
        instructions: Optional[str] = None,
        stt=None,
        llm=None,
        tts=None,
        vad=None,
        context_manager=None,
        user_id: Optional[str] = None,
        client_id: Optional[str] = None,
        agent_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(instructions=instructions, stt=stt, llm=llm, tts=tts, vad=vad)
        self._context_manager = context_manager
        self._user_id = user_id
        self._client_id = client_id
        self._agent_config = agent_config or {}
        
        # Citation tracking
        self._current_citations: List[Dict[str, Any]] = []
        self._current_message_id: Optional[str] = None
        
        # Feature flag for citations (can be configured per agent)
        self._citations_enabled = self._agent_config.get('show_citations', True)
        
        # Transcript tracking
        self._current_user_transcript = ""
        self._current_assistant_transcript = ""
        self._transcript_enabled = True
        self._supabase_client = None
        self._conversation_id = None
        self._agent_id = None

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        try:
            # Generate unique message ID for this turn
            self._current_message_id = str(uuid.uuid4())
            self._current_citations = []
            
            # Debug log the turn context to find the user's message (reduced logging)
            
            # Try to get the last user message from the turn context
            user_text = None
            if hasattr(turn_ctx, 'messages'):
                # Look for the last user message in the context
                for msg in reversed(turn_ctx.messages):
                    if msg.role == "user":
                        if isinstance(msg.content, str):
                            user_text = msg.content
                        elif isinstance(msg.content, list):
                            # Check if it's a list of strings (as seen in logs)
                            if msg.content and isinstance(msg.content[0], str):
                                user_text = " ".join(msg.content)
                                logger.info(f"DEBUG: Extracted user text from turn_ctx string list: {user_text[:100]}")
                            else:
                                # Handle structured content (list of dicts)
                                for part in msg.content:
                                    if isinstance(part, dict) and part.get("type") == "text":
                                        user_text = part.get("text")
                                        break
                        if user_text:
                            logger.info(f"DEBUG: Found user text from turn_ctx: {user_text[:100]}")
                            break
            
            
            # If we didn't find user text in turn_ctx, try new_message
            if not user_text:
                # Prefer the helper if available
                if hasattr(new_message, "text_content") and callable(getattr(new_message, "text_content")):
                    user_text = new_message.text_content()
                else:
                    # Fallback: handle simple string or structured content
                    content = getattr(new_message, "content", None)
                    if isinstance(content, str):
                        user_text = content
                    elif isinstance(content, list):
                        # Check if it's a list of strings (as seen in logs)
                        if content and isinstance(content[0], str):
                            user_text = " ".join(content)  # Join all strings in the list
                            logger.info(f"DEBUG: Extracted user text from string list: {user_text[:100]}")
                        else:
                            # Handle structured content (list of dicts)
                            for part in content:
                                if isinstance(part, dict) and part.get("type") == "text":
                                    user_text = part.get("text")
                                    break

            if not user_text:
                # Try to read latest text captured on session or agent (populated by event handler)
                try:
                    # Check agent session first
                    if hasattr(self, "_agent_session") and hasattr(self._agent_session, "latest_user_text") and isinstance(self._agent_session.latest_user_text, str):
                        user_text = self._agent_session.latest_user_text
                        logger.info(f"on_user_turn_completed: using captured _agent_session.latest_user_text: '{user_text[:120]}'")
                    # Check session property
                    elif hasattr(self, "session") and hasattr(self.session, "latest_user_text") and isinstance(self.session.latest_user_text, str):
                        user_text = self.session.latest_user_text
                        logger.info(f"on_user_turn_completed: using captured session.latest_user_text: '{user_text[:120]}'")
                    # Check agent directly as fallback
                    elif hasattr(self, "latest_user_text") and isinstance(self.latest_user_text, str):
                        user_text = self.latest_user_text
                        logger.info(f"on_user_turn_completed: using captured agent.latest_user_text: '{user_text[:120]}'")
                        
                    # If we found text, try to set it on new_message
                    if user_text and not new_message.content:
                        try:
                            new_message.content = user_text
                            logger.info("on_user_turn_completed: populated empty new_message.content from captured text")
                        except Exception as e:
                            logger.warning(f"on_user_turn_completed: failed to set new_message.content: {type(e).__name__}: {e}")
                except Exception as e:
                    logger.warning(f"Failed to check for latest_user_text: {e}")

            if not user_text:
                logger.info("on_user_turn_completed: no user text to enrich; skipping RAG injection (LLM may skip reply)")
                return
            
            # Store user transcript
            self._current_user_transcript = user_text
            logger.info(f"Captured user text for transcript: {user_text[:100]}...")
            await self._handle_user_transcript(user_text)

            if not self._context_manager:
                logger.info("on_user_turn_completed: context manager not available; skipping RAG injection")
                return

            # Perform RAG retrieval with citations if enabled
            if self._citations_enabled and self._client_id:
                try:
                    await self._retrieve_with_citations(user_text)
                except Exception as e:
                    logger.error(f"Citation retrieval failed: {e}")
                    # Continue with regular RAG if citations fail (graceful degradation)

            logger.info("on_user_turn_completed: building RAG context for current turn")
            ctx = await self._context_manager.build_complete_context(
                user_message=user_text, user_id=self._user_id or "unknown"
            )

            enhanced = ctx.get("enhanced_system_prompt") if isinstance(ctx, dict) else None
            if enhanced:
                # Inject context as a system message so LLM treats it as instructions/context
                turn_ctx.add_message(
                    role="system",
                    content=enhanced,
                )
                logger.info("✅ RAG context injected into turn_ctx for this turn")
            else:
                logger.info("on_user_turn_completed: no enhanced prompt returned; nothing injected")
        except Exception as e:
            logger.error(f"on_user_turn_completed: RAG injection failed: {type(e).__name__}: {e}")

    async def _retrieve_with_citations(self, user_text: str) -> None:
        """
        Perform RAG retrieval with citation tracking.
        This method populates self._current_citations for use in the response.
        """
        try:
            # Use local citations service
            from citations_service import initialize_citations_service
            
            # Initialize the service if needed
            if not hasattr(self, '_citations_service_initialized'):
                # Use the context manager's supabase client and embedder if available
                if self._context_manager and hasattr(self._context_manager, 'supabase'):
                    # Get embedder from context manager if available
                    embedder = None
                    if hasattr(self._context_manager, 'embedder'):
                        embedder = self._context_manager.embedder
                    
                    # Get agent_slug from agent config
                    agent_slug = self._agent_config.get('agent_slug') or self._agent_config.get('agent_id')
                    
                    initialize_citations_service(
                        self._context_manager.supabase,
                        embedder=embedder,
                        agent_slug=agent_slug
                    )
                    self._citations_service_initialized = True
                else:
                    logger.warning("No Supabase client available for citations service")
                    return
            
            from citations_service import rag_citations_service
            
            # Get agent_slug for this agent
            agent_slug = self._agent_config.get('agent_slug') or self._agent_config.get('agent_id')
            if not agent_slug:
                logger.info("No agent_slug configured for citations")
                return
            
            # Perform RAG retrieval with citations
            result = await rag_citations_service.retrieve_with_citations(
                query=user_text,
                client_id=self._client_id,
                agent_slug=agent_slug,
                top_k=12,
                similarity_threshold=0.5,
                max_documents=4,
                max_chunks=8
            )
            
            # Store citations for inclusion in the final response
            self._current_citations = [
                {
                    "chunk_id": citation.chunk_id,
                    "doc_id": citation.doc_id,
                    "title": citation.title,
                    "source_url": citation.source_url,
                    "source_type": citation.source_type,
                    "chunk_index": citation.chunk_index,
                    "page_number": citation.page_number,
                    "char_start": citation.char_start,
                    "char_end": citation.char_end,
                    "similarity": citation.similarity
                }
                for citation in result.citations
            ]
            
            logger.info(f"Retrieved {len(self._current_citations)} citations for message {self._current_message_id}")
            
        except Exception as e:
            logger.error(f"Citations retrieval failed: {e}")
            logger.error(f"Error type: {type(e).__name__}")
            # Don't silently fail - let the error propagate if it's critical
            if "embedder" in str(e).lower() or "no agent_slug" in str(e).lower():
                logger.error("Critical configuration error in citations service - cannot proceed")
                raise
            # For other errors, continue without citations
            self._current_citations = []

    def get_current_citations(self) -> List[Dict[str, Any]]:
        """Get citations for the current message"""
        return self._current_citations.copy()
    
    def get_current_message_id(self) -> Optional[str]:
        """Get the current message ID"""
        return self._current_message_id

    def setup_transcript_storage(self, room: rtc.Room) -> None:
        """Set up room reference and metadata for transcript storage."""
        # Set up room reference for transcript storage
        self._room = room
        
        # Note: The metadata (_conversation_id, _supabase_client, _agent_id) 
        # are already set directly by entrypoint.py before this method is called,
        # so we don't need to extract them here
    
    async def _handle_user_transcript(self, text: str) -> None:
        """Store user transcript in real-time."""
        try:
            self._current_user_transcript = text
            
            # Log what we have available
            logger.info(f"📝 User transcript captured: {text[:100]}...")
            logger.info(f"   - Has supabase_client: {self._supabase_client is not None}")
            logger.info(f"   - Has conversation_id: {self._conversation_id is not None}")
            logger.info(f"   - Has agent_id: {self._agent_id is not None}")
            
            if self._supabase_client and self._conversation_id:
                await self._store_transcript(
                    role="user",
                    content=text,
                    citations=None
                )
                logger.info(f"✅ Stored user transcript to database")
            else:
                logger.warning(f"Cannot store transcript - missing: supabase={self._supabase_client is None}, conv_id={self._conversation_id is None}")
        except Exception as e:
            logger.error(f"Failed to store user transcript: {e}", exc_info=True)
    
    async def _handle_assistant_transcript(self, text: str) -> None:
        """Store assistant transcript with citations if available."""
        try:
            self._current_assistant_transcript = text
            
            # Include citations if available
            citations = self._current_citations if self._citations_enabled else None
            
            if self._supabase_client and self._conversation_id:
                await self._store_transcript(
                    role="assistant",
                    content=text,
                    citations=citations
                )
                logger.info(f"📝 Stored assistant transcript with {len(citations) if citations else 0} citations")
        except Exception as e:
            logger.error(f"Failed to store assistant transcript: {e}")
    
    async def _store_transcript(
        self,
        role: str,
        content: str,
        citations: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        """Store a transcript entry in the database."""
        if not self._supabase_client:
            return
        
        try:
            ts = datetime.utcnow().isoformat()
            row = {
                "conversation_id": self._conversation_id,
                "agent_id": self._agent_id or self._agent_config.get('id'),
                "user_id": self._user_id,
                # Generate a stable session_id per conversation to satisfy NOT NULL constraint
                "session_id": str(self._conversation_id),
                "role": role,
                "content": content,
                "transcript": content,
                "created_at": ts,
                # "source": "voice",  # Column doesn't exist yet in client DB
            }
            
            # Add citations if available (for assistant role)
            if role == "assistant" and citations:
                row["citations"] = citations
            
            # Use asyncio.to_thread to properly await the sync operation
            await asyncio.to_thread(
                lambda: self._supabase_client.table("conversation_transcripts").insert(row).execute()
            )
            
        except Exception as e:
            logger.error(f"Failed to store {role} transcript: {e}")
    
    async def llm_node(
        self,
        chat_ctx: llm.ChatContext,
        tools: list[llm.FunctionTool],
        model_settings: ModelSettings,
    ) -> AsyncIterable[llm.ChatChunk]:
        logger.info("📥 llm_node: invoked; delegating to default implementation")
        async for chunk in CoreAgent.default.llm_node(self, chat_ctx, tools, model_settings):
            yield chunk
    
    async def on_assistant_response(self, message: str) -> None:
        """Called when assistant generates a response - override this to capture transcripts"""
        logger.info(f"Assistant response captured for transcript: {message[:100]}...")
        await self._handle_assistant_transcript(message)



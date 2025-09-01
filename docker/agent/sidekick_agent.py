import logging
from typing import AsyncIterable, Optional, List, Dict, Any
import uuid

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

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        try:
            # Generate unique message ID for this turn
            self._current_message_id = str(uuid.uuid4())
            self._current_citations = []
            
            user_text = None
            # Prefer the helper if available
            if hasattr(new_message, "text_content") and callable(getattr(new_message, "text_content")):
                user_text = new_message.text_content()
            else:
                # Fallback: handle simple string or structured content
                content = getattr(new_message, "content", None)
                if isinstance(content, str):
                    user_text = content
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            user_text = part.get("text")
                            break

            if not user_text:
                # Try to read latest text captured on session (populated by event handler)
                try:
                    if hasattr(self.session, "latest_user_text") and isinstance(self.session.latest_user_text, str):
                        user_text = self.session.latest_user_text
                        logger.info(f"on_user_turn_completed: using captured latest_user_text: '{user_text[:120]}'")
                        # If new_message has no content, set it so the pipeline proceeds to LLM
                        try:
                            new_message.content = user_text
                            logger.info("on_user_turn_completed: populated empty new_message.content from latest_user_text")
                        except Exception as e:
                            logger.warning(f"on_user_turn_completed: failed to set new_message.content: {type(e).__name__}: {e}")
                except Exception:
                    pass

            if not user_text:
                logger.info("on_user_turn_completed: no user text to enrich; skipping RAG injection (LLM may skip reply)")
                return

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
                # Per docs: add an additional message to the turn context.
                turn_ctx.add_message(
                    role="assistant",
                    content=f"Additional information relevant to the user's next message: {enhanced}",
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
            from app.integrations.rag.citations_service import rag_citations_service
            
            # Get dataset IDs for this agent (would come from agent config)
            dataset_ids = self._agent_config.get('dataset_ids', [])
            if not dataset_ids:
                logger.info("No dataset IDs configured for citations")
                return
            
            # Perform RAG retrieval with citations
            result = await rag_citations_service.retrieve_with_citations(
                query=user_text,
                client_id=self._client_id,
                dataset_ids=dataset_ids,
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
            self._current_citations = []

    def get_current_citations(self) -> List[Dict[str, Any]]:
        """Get citations for the current message"""
        return self._current_citations.copy()
    
    def get_current_message_id(self) -> Optional[str]:
        """Get the current message ID"""
        return self._current_message_id

    async def llm_node(
        self,
        chat_ctx: llm.ChatContext,
        tools: list[llm.FunctionTool],
        model_settings: ModelSettings,
    ) -> AsyncIterable[llm.ChatChunk]:
        logger.info("📥 llm_node: invoked; delegating to default implementation")
        async for chunk in CoreAgent.default.llm_node(self, chat_ctx, tools, model_settings):
            yield chunk



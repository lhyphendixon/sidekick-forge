import logging
import re
import unicodedata
from typing import Optional, List, Dict, Any, AsyncIterable, AsyncGenerator
import uuid
import asyncio
from datetime import datetime

from livekit import rtc
from livekit.agents import llm
from livekit.agents import voice
from livekit.agents.llm import StopResponse

try:
    # livekit-agents >= 1.2.18
    from livekit.agents.voice.io import TimedString
except ImportError:  # pragma: no cover - fallback for older SDKs
    from livekit.agents.voice.agent import TimedString


logger = logging.getLogger(__name__)


def _normalize_text(value: str) -> str:
    """Normalize text for comparison (used for deduplication)."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value)
    text = text.replace("\u2019", "'")
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = " ".join(text.split())
    return text


def _strip_ssml_tags(text: str) -> str:
    """
    Strip Cartesia SSML emotion tags and laughter markers from transcript text.

    These tags are meant for TTS rendering only and should not appear in
    the displayed transcript.

    Strips:
    - <emotion value="..." /> (self-closing emotion tags)
    - <emotion value="...">...</emotion> (wrapping emotion tags, just in case)
    - [laughter] markers
    """
    if not text:
        return text

    # Strip self-closing emotion tags: <emotion value="..." />
    text = re.sub(r'<emotion\s+value="[^"]*"\s*/>', '', text)

    # Strip wrapping emotion tags: <emotion value="...">text</emotion>
    # Just remove the tags, keep the content inside
    text = re.sub(r'<emotion\s+value="[^"]*">', '', text)
    text = re.sub(r'</emotion>', '', text)

    # Strip [laughter] markers
    text = re.sub(r'\[laughter\]', '', text, flags=re.IGNORECASE)

    # Clean up any double spaces left behind
    text = re.sub(r'  +', ' ', text)

    return text.strip()


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
        tools: Optional[List[Any]] = None,
        chat_ctx=None,  # Initial chat context for conversation history
        context_manager=None,
        user_id: Optional[str] = None,
        client_id: Optional[str] = None,
        agent_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(instructions=instructions, stt=stt, llm=llm, tts=tts, vad=vad, tools=tools, chat_ctx=chat_ctx)
        self._context_manager = context_manager
        self._user_id = user_id
        self._client_id = client_id
        self._agent_config = agent_config or {}
        
        # Citation tracking
        self._current_citations: List[Dict[str, Any]] = []
        self._current_message_id: Optional[str] = None
        self._current_rerank_info: Dict[str, Any] = {}
        self._current_rag_context: str = ""  # RAG context text for LLM injection
        
        # Feature flag for citations (can be configured per agent)
        self._citations_enabled = self._agent_config.get('show_citations', True)
        # Wizard mode flag - skip RAG processing entirely
        self._is_wizard_mode = self._agent_config.get('is_wizard_mode', False)

        # GLM reasoning toggle - disabled by default for fast voice responses
        # When using GLM-4.7 models, reasoning can be enabled on-demand for complex tasks
        self._reasoning_enabled: bool = False
        self._is_glm_model: bool = False
        self._glm_model_name: str = ""
        
        # Transcript tracking
        self._current_user_transcript = ""
        self._current_assistant_transcript = ""
        self._transcript_enabled = True
        self._supabase_client = None
        self._conversation_id = None
        self._client_conversation_id = None
        self._agent_id = None
        self._current_turn_id: Optional[str] = None
        # Separate user turn tracking to persist across pauses until assistant completes
        # This allows user speech chunks to be merged even if user pauses mid-sentence
        self._user_turn_id: Optional[str] = None
        self._latest_tool_results: List[Dict[str, Any]] = []
        # Strategy: store final assistant transcript once per turn via session events
        self._suppress_on_assistant_transcript = True
        self._last_assistant_commit: str = ""
        self._last_committed_text: str = ""
        self._last_user_commit: str = ""
        self._pending_user_commit: bool = False
        
        # TTS-aligned transcript streaming
        self._streaming_transcript_row_id: Optional[str] = None
        self._streaming_transcript_text: str = ""
        # Raw LLM output (before TTS sanitization) for markdown-preserved transcripts
        self._raw_llm_text: str = ""
        self._raw_llm_chunks: List[str] = []
        # Text-only mode response capture
        self._text_mode_enabled: bool = False
        self._text_response_collector: Optional[Any] = None

    def attach_text_response_collector(self, collector: Any) -> None:
        """Enable text-only mode response capture."""
        self._text_mode_enabled = True
        self._text_response_collector = collector

    def configure_for_glm_model(self, model_name: str) -> None:
        """
        Configure agent for GLM model with dynamic reasoning toggle.

        GLM-4.7 supports a reasoning mode that can be enabled/disabled per request.
        By default, reasoning is DISABLED for fast voice responses.
        The agent can enable reasoning on-demand for complex tasks via a system tool.
        """
        self._is_glm_model = True
        self._glm_model_name = model_name
        self._reasoning_enabled = False  # Default: fast responses for voice chat
        logger.info(f"🧠 GLM model configured ({model_name}), reasoning disabled by default for fast voice responses")

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        try:
            # EARLY EXIT: Check for echo (agent's own speech picked up by mic)
            # The SDK's internal pipeline may bypass our on_user_input_transcribed handler
            try:
                user_text_raw = None
                if hasattr(new_message, "text_content") and callable(getattr(new_message, "text_content")):
                    user_text_raw = new_message.text_content()
                elif hasattr(new_message, "content"):
                    content = new_message.content
                    if isinstance(content, str):
                        user_text_raw = content
                    elif isinstance(content, list) and content and isinstance(content[0], str):
                        user_text_raw = max(content, key=len)

                if user_text_raw:
                    # Normalize for comparison
                    user_norm = _normalize_text(user_text_raw).lower().strip()

                    # Check against recent greeting
                    recent_greeting = getattr(self, '_agent_session', None)
                    if recent_greeting:
                        greeting_norm = getattr(recent_greeting, '_recent_greeting_norm', '')
                        if greeting_norm and greeting_norm in user_norm:
                            logger.info(f"🚫 on_user_turn_completed: Blocking echo - user text matches greeting: '{user_text_raw[:50]}'")
                            raise StopResponse()

                    # Check against last assistant commit
                    last_assistant = getattr(self, '_last_assistant_commit', '')
                    if last_assistant:
                        assistant_norm = _normalize_text(last_assistant).lower().strip()
                        # Check if the user text is substantially similar to the last assistant response
                        # Must be a substantial overlap, not just a single word match
                        if assistant_norm and len(user_norm) > 5:
                            if user_norm in assistant_norm or assistant_norm in user_norm:
                                logger.info(f"🚫 on_user_turn_completed: Blocking echo - user text matches last assistant: '{user_text_raw[:50]}'")
                                raise StopResponse()
                        # Only block common greeting echoes if user text is EXACTLY the greeting phrase
                        # (with minor variations). Don't block legitimate user greetings like "Hello?"
                        # We only want to block if the STT picked up the TTS audio
                        recent_greeting_text = getattr(recent_greeting, '_recent_greeting_norm', '') if recent_greeting else ''
                        if recent_greeting_text and user_norm == recent_greeting_text:
                            logger.info(f"🚫 on_user_turn_completed: Blocking exact greeting echo: '{user_text_raw[:50]}'")
                            raise StopResponse()
            except StopResponse:
                raise  # Re-raise to exit
            except Exception as echo_check_err:
                logger.debug(f"Echo check failed (continuing): {echo_check_err}")

            # Generate unique message ID for this turn
            self._current_message_id = str(uuid.uuid4())
            self._current_citations = []

            # DEBUG: Log full conversation context being sent to LLM
            try:
                items = getattr(turn_ctx, 'items', None) or getattr(turn_ctx, 'messages', [])
                logger.info(f"📊 DEBUG: turn_ctx has {len(items)} items")
                for i, item in enumerate(items[-5:]):  # Log last 5 items
                    item_role = getattr(item, 'role', 'unknown')
                    item_type = getattr(item, 'type', 'unknown')
                    item_content = getattr(item, 'content', None)
                    content_preview = ""
                    if isinstance(item_content, str):
                        content_preview = item_content[:80]
                    elif isinstance(item_content, list):
                        content_preview = f"list({len(item_content)} items): {str(item_content)[:80]}"
                    logger.info(f"📊 turn_ctx[{i}]: role={item_role}, type={item_type}, content={content_preview}")
                # Also log the new_message structure
                logger.info(f"📊 new_message: role={new_message.role}, content_type={type(new_message.content)}, content={str(new_message.content)[:100]}")
            except Exception as ctx_log_err:
                logger.debug(f"Could not log turn_ctx: {ctx_log_err}")

            # Try to get user text from new_message first (most reliable)
            user_text = None
            
            # 1. Try new_message (helper method)
            if hasattr(new_message, "text_content") and callable(getattr(new_message, "text_content")):
                user_text = new_message.text_content()
                if user_text:
                    logger.info(f"DEBUG: Extracted user text from new_message.text_content(): {user_text[:100]}")

            # 2. Try new_message content directly
            if not user_text:
                content = getattr(new_message, "content", None)
                if isinstance(content, str):
                    user_text = content
                elif isinstance(content, list):
                    # Check if it's a list of strings
                    if content and isinstance(content[0], str):
                        # Take the longest string - STT often sends overlapping chunks
                        # where later chunks contain the full text plus fragments
                        user_text = max(content, key=len) if content else ""
                        try:
                            # IMPORTANT: Set content as a list with a single string, not a bare string
                            # ChatMessage.content expects list[ChatContent] (list of strings/ImageContent/AudioContent)
                            new_message.content = [user_text]
                            logger.info(f"DEBUG: Coerced new_message.content to single-item list")
                        except Exception:
                            logger.debug("Unable to coerce new_message.content to list")
                        logger.info(f"DEBUG: Extracted user text from string list (longest): {user_text[:100]}")
                    else:
                        # Handle structured content (list of dicts)
                        for part in content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                user_text = part.get("text")
                                break
            
            # 3. Fallback to turn_ctx only if new_message failed
            if not user_text and hasattr(turn_ctx, 'messages'):
                # Look for the last user message in the context
                for msg in reversed(turn_ctx.messages):
                    if msg.role == "user":
                        if isinstance(msg.content, str):
                            user_text = msg.content
                        elif isinstance(msg.content, list):
                            # Check if it's a list of strings
                            if msg.content and isinstance(msg.content[0], str):
                                # UPDATED: Use same logic as new_message (longest string) instead of joining with space
                                # Joining with space caused "s p a c e d" text if chunks were characters/tokens
                                user_text = max(msg.content, key=len) if msg.content else ""
                                try:
                                    # IMPORTANT: Set content as a list with a single string, not a bare string
                                    msg.content = [user_text]
                                except Exception:
                                    logger.debug("Unable to coerce turn_ctx message content to list")
                                logger.info(f"DEBUG: Extracted user text from turn_ctx string list (longest): {user_text[:100]}")
                            else:
                                # Handle structured content (list of dicts)
                                for part in msg.content:
                                    if isinstance(part, dict) and part.get("type") == "text":
                                        user_text = part.get("text")
                                        break
                        if user_text:
                            logger.info(f"DEBUG: Found user text from turn_ctx: {user_text[:100]}")
                            break

            session_last = None
            try:
                if hasattr(self, "_agent_session"):
                    session_last = getattr(self._agent_session, "_last_committed_text", None)
            except Exception:
                session_last = None

            # Only use committed_candidates as fallback if we don't have text from new_message
            # This prevents old turn's text from overriding new turn's message
            if not user_text:
                committed_candidates: List[str] = []
                if session_last:
                    committed_candidates.append(session_last)
                if self._last_committed_text:
                    committed_candidates.append(self._last_committed_text)

                if committed_candidates:
                    best_candidate = max(committed_candidates, key=len)
                    user_text = best_candidate
                    logger.info(f"DEBUG: Using committed candidate as fallback: {user_text[:100]}")
                    try:
                        if hasattr(new_message, "content") and isinstance(best_candidate, str):
                            new_message.content = [best_candidate]
                    except Exception:
                        logger.debug("Unable to set new_message.content from committed candidate")

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
            normalized_text = self._normalize_spelled_words(user_text)
            if normalized_text != user_text:
                logger.info(f"Normalized spelled sequence: '{user_text[:120]}' -> '{normalized_text[:120]}'")
                user_text = normalized_text
                try:
                    if hasattr(new_message, "content"):
                        # IMPORTANT: Set content as a list with a single string, not a bare string
                        new_message.content = [normalized_text]
                except Exception:
                    logger.debug("Failed to update new_message.content with normalized text")

            self._last_committed_text = user_text
            self._current_user_transcript = user_text
            logger.info(f"Captured user text for transcript (context only): {user_text[:100]}...")

            if not self._context_manager:
                logger.info("on_user_turn_completed: context manager not available; skipping RAG injection")
                return

            # WIZARD MODE: Skip all RAG processing - wizard has no documents
            if self._is_wizard_mode:
                logger.info("🧙 on_user_turn_completed: wizard mode - skipping RAG injection entirely")
                return

            # Perform RAG retrieval with citations if enabled
            # Empty RAG context is acceptable for conversational queries that don't match documents
            logger.info(f"📚 [RAG-CHECK] citations_enabled={self._citations_enabled}, client_id={bool(self._client_id)}")
            if self._citations_enabled and self._client_id:
                logger.info(f"📚 [RAG-CHECK] Starting RAG retrieval for: '{user_text[:50]}...'")
                await self._retrieve_with_citations(user_text)
                logger.info(f"📚 [RAG-CHECK] RAG retrieval complete: {len(self._current_citations)} citations retrieved")
                # Check if we got any RAG context
                rag_context = getattr(self, "_current_rag_context", "") or ""
                if not rag_context:
                    # Empty context is OK for conversational queries - agent can respond without RAG
                    logger.info("📚 RAG context retrieval returned empty - continuing without document context (conversational query)")

            logger.info("on_user_turn_completed: building RAG context for current turn")
            # Skip knowledge RAG if citations_service already did it (avoid duplicate searches)
            skip_knowledge = self._citations_enabled and self._client_id
            # Reuse cached embedding from citations_service if available (saves ~1s API call)
            cached_embedding = getattr(self, '_cached_query_embedding', None) if skip_knowledge else None
            # Pass top document intelligence from RAG result (DocumentSense - only the #1 ranked document)
            top_doc_intel = getattr(self, '_top_document_intelligence', None) if skip_knowledge else None
            ctx = await self._context_manager.build_complete_context(
                user_message=user_text,
                user_id=self._user_id or "unknown",
                skip_knowledge_rag=skip_knowledge,
                cached_query_embedding=cached_embedding,
                top_document_intelligence=top_doc_intel
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
                # No enhanced prompt - this is OK for conversational queries
                # The agent can respond using just its system prompt without RAG context
                logger.info("📚 No enhanced prompt returned - continuing with base system prompt (conversational query)")
        except Exception as e:
            # Log RAG errors but continue - agent can still respond conversationally
            logger.warning(f"⚠️ on_user_turn_completed: RAG processing issue: {type(e).__name__}: {e}")
            # Don't re-raise - allow the agent to continue and respond without RAG context

    async def _retrieve_with_citations(self, user_text: str) -> None:
        """
        Perform RAG retrieval with citation tracking.
        This method populates self._current_citations for use in the response.
        """
        try:
            # Debug: Log agent_config state
            logger.info(f"_retrieve_with_citations: agent_config type={type(self._agent_config)}, is_none={self._agent_config is None}")

            # Ensure agent_config is a dict
            if not isinstance(self._agent_config, dict):
                logger.warning(f"_retrieve_with_citations: agent_config is not a dict, skipping citations")
                self._current_citations = []
                return

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
            
            # Collect optional dataset constraints (deprecated — match_documents uses agent_slug)
            dataset_ids = []
            try:
                if isinstance(self._agent_config, dict) and self._agent_config.get('dataset_ids'):
                    if isinstance(self._agent_config['dataset_ids'], list):
                        dataset_ids = self._agent_config['dataset_ids']
            except Exception:
                dataset_ids = []

            # Determine retrieval limits from agent config (defaults: 10 with rerank safe limits)
            rag_results_limit = 10
            try:
                rag_results_limit = int(self._agent_config.get('rag_results_limit', rag_results_limit))
            except Exception:
                rag_results_limit = 10
            if rag_results_limit < 1:
                rag_results_limit = 1
            if rag_results_limit > 50:
                rag_results_limit = 50

            rerank_cfg = self._agent_config.get("rerank", {}) if isinstance(self._agent_config, dict) else {}
            # Ensure rerank_cfg is a dict (could be None if key exists with None value)
            if not isinstance(rerank_cfg, dict):
                rerank_cfg = {}
            rerank_enabled = rerank_cfg.get("enabled", True)
            # Default to the agent's rag_results_limit for both candidates and top_k so we don't silently truncate to 5.
            rerank_candidates = rerank_cfg.get("candidates", rag_results_limit)
            rerank_top_k = rerank_cfg.get("top_k")
            if not rerank_top_k or rerank_top_k < 1:
                rerank_top_k = rerank_candidates if rerank_candidates else rag_results_limit
            rerank_provider = rerank_cfg.get("provider")
            rerank_model = rerank_cfg.get("model")
            rerank_fallback_info = {
                "enabled": bool(rerank_enabled),
                "provider": rerank_provider,
                "model": rerank_model,
                "candidates_configured": rerank_candidates,
                "top_k_configured": rerank_top_k,
            }

            api_keys = {}
            try:
                if self._context_manager and hasattr(self._context_manager, "api_keys"):
                    api_keys = self._context_manager.api_keys or {}
            except Exception:
                api_keys = {}
            if not api_keys and isinstance(self._agent_config, dict):
                api_keys = self._agent_config.get("api_keys", {}) or {}

            # Use configured values - don't override with hardcoded multipliers
            # rerank_candidates: how many chunks to fetch for reranking
            # rerank_top_k: how many to return after reranking
            # rag_results_limit: final limit on results
            if rerank_enabled:
                # Use configured rerank_candidates, or default to rag_results_limit if not set
                match_count = rerank_candidates if rerank_candidates else rag_results_limit
                rerank_top_k = rerank_top_k if rerank_top_k else rag_results_limit
                max_docs = rag_results_limit
            else:
                # No rerank: just fetch what we need
                match_count = rag_results_limit
                rerank_candidates = None
                rerank_top_k = None
                max_docs = rag_results_limit

            # Perform RAG retrieval with citations
            # Determine hosting type for shared pool tenant isolation
            hosting_type = None
            if isinstance(self._agent_config, dict):
                hosting_type = self._agent_config.get("hosting_type")

            result = await rag_citations_service.retrieve_with_citations(
                query=user_text,
                client_id=self._client_id,
                agent_slug=agent_slug,
                dataset_ids=dataset_ids,
                top_k=match_count,
                similarity_threshold=0.2,  # widen recall for sparse results
                max_documents=max_docs,
                max_chunks=match_count,
                rerank_enabled=rerank_enabled,
                rerank_candidates=rerank_candidates,
                rerank_top_k=rerank_top_k,
                rerank_provider=rerank_provider,
                rerank_model=rerank_model,
                api_keys=api_keys,
                hosting_type=hosting_type,
            )
            
            # Store RAG context for LLM injection (this is the actual document content)
            self._current_rag_context = result.context_for_llm or ""

            # Cache the query embedding for reuse in conversation RAG (saves ~1s API call)
            self._cached_query_embedding = result.query_embedding

            # Store top document intelligence for context injection (DocumentSense)
            self._top_document_intelligence = result.top_document_intelligence

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

            # Store rerank info for downstream metadata
            try:
                self._current_rerank_info = result.rerank_info or rerank_fallback_info
            except Exception:
                self._current_rerank_info = rerank_fallback_info

            logger.info(f"Retrieved {len(self._current_citations)} citations for message {self._current_message_id} (context: {len(self._current_rag_context)} chars)")
            
        except Exception as e:
            import traceback
            logger.error(f"Citations retrieval failed: {e}")
            logger.error(f"Error type: {type(e).__name__}")
            logger.error(f"Full traceback:\n{traceback.format_exc()}")
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

    # ------------------------------------------------------------------
    # LLM node override for GLM reasoning toggle
    # ------------------------------------------------------------------

    async def llm_node(
        self,
        chat_ctx: llm.ChatContext,
        tools: List[Any],
        model_settings: Any
    ) -> AsyncIterable[llm.ChatChunk]:
        """
        Override llm_node to inject disable_reasoning parameter for GLM models.

        When using GLM-4.7, we pass `disable_reasoning: True/False` via extra_body
        to control whether the model uses its extended reasoning capabilities.
        Reasoning is disabled by default for fast voice responses.

        This follows LiveKit's activity pattern for proper integration.
        """
        from livekit.agents.voice import Agent as BaseAgent
        from livekit.agents import NOT_GIVEN

        # For non-GLM models, use the default implementation
        if not self._is_glm_model:
            async for chunk in BaseAgent.default.llm_node(self, chat_ctx, tools, model_settings):
                yield chunk
            return

        # For GLM models, inject the disable_reasoning parameter
        # Build extra_kwargs with reasoning toggle
        extra_body = {"disable_reasoning": not self._reasoning_enabled}

        if self._reasoning_enabled:
            logger.info(f"🧠 GLM: Reasoning ENABLED for this request (model: {self._glm_model_name})")
        else:
            logger.info(f"🧠 GLM: Reasoning DISABLED for fast response (model: {self._glm_model_name})")

        # Use LiveKit's activity pattern for proper integration
        try:
            activity = self._get_activity_or_raise()
            assert activity.llm is not None, "llm_node called but no LLM is available"

            tool_choice = model_settings.tool_choice if model_settings else NOT_GIVEN
            conn_options = activity.session.conn_options.llm_conn_options

            async with activity.llm.chat(
                chat_ctx=chat_ctx,
                tools=tools,
                tool_choice=tool_choice,
                conn_options=conn_options,
                extra_kwargs={"extra_body": extra_body},
            ) as stream:
                async for chunk in stream:
                    yield chunk
        except Exception as e:
            logger.error(f"🧠 GLM llm_node error: {e}")
            raise

    # ------------------------------------------------------------------
    # Text formatting for display
    # ------------------------------------------------------------------

    @staticmethod
    def _enhance_text_for_display(text: str) -> str:
        """
        Add markdown formatting to plain speech text for better display.
        Applies formatting progressively as text streams in.

        Key formatting:
        - Strip malformed tool calls that LLMs sometimes output inline
        - Double line breaks between paragraphs (every 2 sentences)
        - Bold for key terms and transition words
        - Proper list formatting
        """
        if not text:
            return text

        enhanced = text

        # Strip malformed tool call text that some LLMs output inline
        # Pattern: "tool_call: function_name: xxx arguments: yyy response_id: zzz"
        enhanced = re.sub(
            r"tool_call:\s*function_name:\s*\S+\s*arguments:\s*[^T]*?response_id:\s*\S+",
            "",
            enhanced,
            flags=re.IGNORECASE | re.DOTALL
        )
        # Also strip variations like "tool_call: {...}" JSON format
        enhanced = re.sub(r"tool_call:\s*\{[^}]+\}", "", enhanced, flags=re.IGNORECASE)
        # Strip "function_call:" patterns as well
        enhanced = re.sub(r"function_call:\s*\{[^}]+\}", "", enhanced, flags=re.IGNORECASE)

        # Normalize multiple spaces to single space
        enhanced = re.sub(r'  +', ' ', enhanced)

        # =================================================================
        # PARAGRAPH BREAKS - Every 2 sentences gets a double newline
        # =================================================================

        # Add paragraph breaks after sentence-ending punctuation followed by space and capital
        # Also handle cases with no space (just punctuation followed by capital)
        enhanced = re.sub(r'([.!?])\s*([A-Z])', r'\1\n\n\2', enhanced)

        # =================================================================
        # LISTS - Format numbered and bullet lists
        # =================================================================

        # Numbered lists: "1. " "2. " etc - ensure they're on their own line
        enhanced = re.sub(r'\n\n(\d+)\.\s+', r'\n\n\1. ', enhanced)
        enhanced = re.sub(r'^(\d+)\.\s+', r'\1. ', enhanced)

        # Bullet points
        enhanced = re.sub(r'\n\n[-•]\s+', r'\n\n- ', enhanced)

        # =================================================================
        # BOLD - Emphasize key terms and phrases
        # =================================================================

        # Bold transition words ANYWHERE in text (not just after paragraph breaks)
        # Match: sentence boundary or paragraph start, then transition word, then comma or space
        bold_transitions = (
            r'Additionally|Moreover|However|Furthermore|Nevertheless|'
            r'Consequently|Therefore|Meanwhile|Alternatively|'
            r'First|Second|Third|Finally|Lastly|Next|'
            r'For example|For instance|In summary|In conclusion|In fact|As a result|'
            r'Essentially|Specifically|Importantly|Interestingly|Notably'
        )
        # Bold these words when they appear after newlines or at start
        enhanced = re.sub(
            rf'(^|\n\n)({bold_transitions})(,?\s)',
            r'\1**\2,** ',
            enhanced
        )

        # Bold "Label:" patterns (e.g., "Key Point:" or "Note:")
        enhanced = re.sub(
            r'(^|\n\n)([A-Z][a-zA-Z\s]{2,20}):\s+',
            r'\1**\2:** ',
            enhanced
        )

        # Bold the FIRST key phrase at the start of each paragraph
        # This creates visual anchors for skimming
        # Match: after \n\n, capture 2-4 words before the first comma/colon or end of first clause
        enhanced = re.sub(
            r'(\n\n)([A-Z][a-z]+(?:\s+[a-z]+){0,3})([,:]|\s+(?:is|are|was|were|can|could|would|will|has|have|had|involves?|means?|refers?))',
            r'\1**\2**\3',
            enhanced
        )

        # Also bold opening phrase of the text if not already bold
        if not enhanced.startswith('**'):
            enhanced = re.sub(
                r'^([A-Z][a-z]+(?:\s+[a-z]+){0,3})([,:]|\s+(?:is|are|was|were|can|could|would|will|has|have|had|involves?|means?|refers?))',
                r'**\1**\2',
                enhanced
            )

        # Bold quoted terms (e.g., "remote viewing" -> "**remote viewing**")
        enhanced = re.sub(
            r'"([^"]{3,30})"',
            r'"**\1**"',
            enhanced
        )

        # =================================================================
        # CLEANUP
        # =================================================================

        # Clean up triple+ newlines to double
        enhanced = re.sub(r'\n{3,}', '\n\n', enhanced)

        # Remove leading newlines
        enhanced = enhanced.lstrip('\n')

        # Ensure no double-bold (from multiple passes)
        enhanced = re.sub(r'\*\*\*\*', '**', enhanced)

        # Fix any malformed bold (like **word** ** or ** **word**)
        enhanced = re.sub(r'\*\*\s+\*\*', '** **', enhanced)

        # Log final result (after all transformations)
        has_double_newline = '\n\n' in enhanced
        logger.info(f"📝 _enhance_text_for_display: in_len={len(text)}, out_len={len(enhanced)}, has_newlines={has_double_newline}")
        if has_double_newline:
            logger.debug(f"📝 _enhance output sample: {repr(enhanced[:150])}")

        return enhanced

    # ------------------------------------------------------------------
    # Speech output sanitization
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_tts_text(text: str) -> str:
        """Remove Markdown asterisks and malformed tool calls so TTS engines don't verbalize them."""
        if not text:
            return text

        # Strip malformed tool call text that some LLMs output inline
        # Pattern: "tool_call: function_name: xxx arguments: yyy response_id: zzz"
        # This can appear when LLMs don't properly use tool calling API
        text = re.sub(
            r"tool_call:\s*function_name:\s*\S+\s*arguments:\s*[^T]*?response_id:\s*\S+",
            "",
            text,
            flags=re.IGNORECASE | re.DOTALL
        )

        # Also strip variations like "tool_call: {...}" JSON format
        text = re.sub(
            r"tool_call:\s*\{[^}]+\}",
            "",
            text,
            flags=re.IGNORECASE
        )

        # Strip "function_call:" patterns as well
        text = re.sub(
            r"function_call:\s*\{[^}]+\}",
            "",
            text,
            flags=re.IGNORECASE
        )

        # Collapse Markdown emphasis markers while keeping the inner content
        # Note: Use r"\1" (single backslash) for proper backreference, not r"\\1"
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
        text = re.sub(r"\*(.+?)\*", r"\1", text)

        # Convert bullet markers to a hyphen separator that reads naturally
        text = re.sub(r"(^|\n)\s*\*\s+", r"\1- ", text)

        # Strip any other stray asterisks that might remain
        text = text.replace("*", "")

        return text

    def tts_node(self, text: AsyncIterable[str], model_settings):
        async def _apply_sanitizer():
            async for chunk in text:
                yield self._sanitize_tts_text(chunk)

        return super().tts_node(_apply_sanitizer(), model_settings)

    async def transcription_node(
        self, text: AsyncIterable[str | TimedString], model_settings
    ) -> AsyncGenerator[str | TimedString, None]:
        """
        Intercept the TTS-aligned transcript stream to write incremental updates.
        This enables word-by-word streaming on the frontend.
        """
        import traceback
        call_id = str(uuid.uuid4())[:8]
        caller_info = ''.join(traceback.format_stack()[-4:-1])  # Get caller info
        logger.info("📝 transcription_node STARTED [%s] - streaming_row_id=%s, streaming_text_len=%d",
                   call_id,
                   self._streaming_transcript_row_id[:8] if self._streaming_transcript_row_id else None,
                   len(self._streaming_transcript_text) if self._streaming_transcript_text else 0)
        logger.debug("📝 transcription_node caller stack [%s]:\n%s", call_id, caller_info)

        # ALWAYS clear previous streaming state when starting a new response
        # This handles the case where a previous response was interrupted mid-stream
        if self._streaming_transcript_row_id:
            logger.info("📝 transcription_node: Clearing stale row_id=%s from previous (possibly interrupted) response [%s]",
                       self._streaming_transcript_row_id[:8] if self._streaming_transcript_row_id else None, call_id)
            self._streaming_transcript_row_id = None

        # Clear any previous transcript text for the new response
        if self._streaming_transcript_text:
            logger.info("📝 transcription_node: Clearing previous transcript text (%d chars) for new response",
                       len(self._streaming_transcript_text))
            self._streaming_transcript_text = ""

        # NOTE: We NO LONGER reset _current_turn_id here.
        # The user turn_id should persist until the assistant COMPLETES their response.
        # This allows user speech chunks to be merged even if the user pauses mid-sentence
        # and the assistant starts responding before the user finishes.
        # The _user_turn_id is reset in agent_speech_committed handler (when turn truly ends).
        if self._current_turn_id:
            logger.info("📝 transcription_node: Preserving user turn_id=%s (will reset after assistant completes)",
                       self._current_turn_id[:8] if self._current_turn_id else None)

        accumulated_text = ""

        async for chunk in text:
            # Extract text from chunk (TimedString or plain str)
            if isinstance(chunk, TimedString):
                chunk_text = str(chunk)
            else:
                chunk_text = chunk
            
            # Accumulate text
            accumulated_text += chunk_text

            # Apply formatting incrementally for better UX
            # This formats the text as it streams rather than waiting for the end
            formatted_text = self._enhance_text_for_display(accumulated_text)

            # Write to database incrementally with formatted text
            # DEBUG: Log the condition check
            logger.debug(f"📝 DB write check: supabase={bool(self._supabase_client)}, conv_id={bool(self._conversation_id)}, agent_id={bool(self._agent_id)}")
            if self._supabase_client and self._conversation_id and self._agent_id:
                try:
                    timestamp = datetime.utcnow().isoformat()

                    if not self._streaming_transcript_row_id:
                        # First chunk: INSERT a new row
                        # Use _user_turn_id to link assistant response to user utterance
                        # This ensures the UI can group user + assistant in the same turn
                        if not self._current_turn_id:
                            self._current_turn_id = self._user_turn_id or str(uuid.uuid4())

                        # Ensure conversation record exists before first INSERT (FK constraint)
                        try:
                            existing = await asyncio.to_thread(
                                lambda: self._supabase_client
                                    .table("conversations")
                                    .select("id")
                                    .eq("id", self._conversation_id)
                                    .limit(1)
                                    .execute()
                            )
                            if not existing or not getattr(existing, "data", None):
                                conv_payload = {
                                    "id": self._conversation_id,
                                    "agent_id": self._agent_id,
                                    "user_id": self._user_id,
                                    "channel": "voice",
                                    "created_at": timestamp,
                                    "updated_at": timestamp,
                                }
                                if self._client_id:
                                    conv_payload["client_id"] = self._client_id
                                # Try INSERT with fallback for dedicated tenant schemas (no client_id column)
                                try:
                                    await asyncio.to_thread(
                                        lambda: self._supabase_client
                                            .table("conversations")
                                            .insert(conv_payload)
                                            .execute()
                                    )
                                except Exception as conv_insert_exc:
                                    # If insert failed due to client_id column, retry without it
                                    if self._client_id and ("client_id" in str(conv_insert_exc).lower() or "column" in str(conv_insert_exc).lower()):
                                        logger.info(f"📝 Retrying conversation insert without client_id (dedicated tenant schema)")
                                        conv_payload.pop("client_id", None)
                                        await asyncio.to_thread(
                                            lambda: self._supabase_client
                                                .table("conversations")
                                                .insert(conv_payload)
                                                .execute()
                                        )
                                    else:
                                        raise
                                logger.info(f"📝 Created conversation record: {self._conversation_id[:8]}")
                        except Exception as conv_err:
                            logger.warning(f"📝 Failed to ensure conversation exists: {conv_err}")

                        # DIAGNOSTIC: Log INSERT operation
                        logger.info(f"📝 INSERT transcript: call_id={call_id}, turn_id={self._current_turn_id[:8]}, text='{formatted_text[:50]}...'")

                        row = {
                            "conversation_id": self._conversation_id,
                            "session_id": self._conversation_id,
                            "agent_id": self._agent_id,
                            "user_id": self._user_id,
                            "role": "assistant",
                            "content": formatted_text,
                            "transcript": formatted_text,
                            "turn_id": self._current_turn_id,
                            "created_at": timestamp,
                            "source": "voice",
                            "metadata": {}
                        }

                        # Add client_id for multi-tenant schemas
                        if self._client_id:
                            row["client_id"] = self._client_id

                        # Add citations if available
                        if self._current_citations:
                            row["citations"] = self._current_citations
                            logger.info(f"📚 [CITATION-INSERT] Adding {len(self._current_citations)} citations to streaming INSERT")
                        else:
                            logger.info(f"📚 [CITATION-INSERT] No citations available for streaming INSERT (citations_enabled={self._citations_enabled})")

                        # Try INSERT with fallback for dedicated tenant schemas (no client_id column)
                        try:
                            result = await asyncio.to_thread(
                                lambda: self._supabase_client.table("conversation_transcripts").insert(row).execute()
                            )
                        except Exception as insert_exc:
                            # If insert failed due to client_id column, retry without it (dedicated tenant schema)
                            if self._client_id and ("client_id" in str(insert_exc).lower() or "column" in str(insert_exc).lower()):
                                logger.info(f"📝 Retrying streaming transcript insert without client_id (dedicated tenant schema)")
                                row.pop("client_id", None)
                                result = await asyncio.to_thread(
                                    lambda: self._supabase_client.table("conversation_transcripts").insert(row).execute()
                                )
                            else:
                                raise

                        if result.data and len(result.data) > 0:
                            self._streaming_transcript_row_id = result.data[0].get("id")
                            logger.info(f"📝 INSERT SUCCESS: row_id={self._streaming_transcript_row_id}, call_id={call_id}")
                    else:
                        # Subsequent chunks: UPDATE the existing row with formatted text
                        # DEBUG: Check if newlines are present in the text we're writing
                        has_newlines = '\n\n' in formatted_text
                        logger.info(f"📝 UPDATE transcript: row_id={self._streaming_transcript_row_id}, has_newlines={has_newlines}, len={len(formatted_text)}")
                        if has_newlines:
                            # Log first occurrence of newline to verify
                            nl_pos = formatted_text.find('\n\n')
                            logger.info(f"📝 UPDATE newline context: ...{repr(formatted_text[max(0,nl_pos-20):nl_pos+20])}...")

                        await asyncio.to_thread(
                            lambda: self._supabase_client.table("conversation_transcripts")
                            .update({
                                "content": formatted_text,
                                "transcript": formatted_text
                            })
                            .eq("id", self._streaming_transcript_row_id)
                            .execute()
                        )
                        logger.debug(f"📝 Updated streaming transcript ({len(formatted_text)} chars)")

                except Exception as e:
                    logger.warning(f"Failed to write streaming transcript: {e}")
            else:
                # Log why we're not writing to database
                logger.warning(f"📝 DB write SKIPPED: supabase={bool(self._supabase_client)}, conv_id={self._conversation_id}, agent_id={self._agent_id}")

            # Yield the chunk back to continue the pipeline
            yield chunk

        # At end of stream, ensure final content is formatted
        # (formatting is already applied incrementally, this is a safety net)
        final_content = self._enhance_text_for_display(accumulated_text)
        logger.info(f"📝 transcription_node FINISHED, accumulated: {len(accumulated_text)} chars, enhanced: {len(final_content)} chars")

        # Store final streamed text for deduplication, then clear streaming row ID
        # Keep _streaming_transcript_text for deduplication check, clear row_id
        self._streaming_transcript_text = final_content
        self._streaming_transcript_row_id = None
        # Also set _last_assistant_commit for content-based deduplication in store_transcript
        try:
            self._last_assistant_commit = final_content
        except Exception:
            pass
        logger.info(f"📝 transcription_node FINISHED, accumulated: {len(accumulated_text)} chars, enhanced: {len(final_content)} chars")

    @staticmethod
    def _normalize_spelled_words(text: str) -> str:
        """Collapse sequences of single-letter tokens into contiguous strings for better search."""
        if not text:
            return text

        pattern = re.compile(r"(?:(?<=^)|(?<=\s))(?:[A-Za-z](?:\s+|\s*[-]\s*)){2,}[A-Za-z](?=[\s,.;!?]|$)")

        def replacer(match: re.Match) -> str:
            chunk = match.group(0)
            letters = re.findall(r"[A-Za-z]", chunk)
            if len(letters) <= 1:
                return chunk

            joined = "".join(letters)
            if len(letters) <= 4:
                normalized = joined.upper()
            else:
                # Heuristic: split when the first letter repeats after at least two letters (spelled first/last name)
                segments: List[str] = []
                first_letter = letters[0]
                buffer: List[str] = [first_letter]
                for letter in letters[1:]:
                    if letter == first_letter and len(buffer) >= 2 and not segments:
                        segments.append("".join(buffer))
                        buffer = [letter]
                    else:
                        buffer.append(letter)
                if buffer:
                    segments.append("".join(buffer))

                if len(segments) > 1:
                    normalized = " ".join(seg[0].upper() + "".join(ch.lower() for ch in seg[1:]) for seg in segments)
                else:
                    normalized = joined[0].upper() + "".join(ch.lower() for ch in joined[1:])

            return normalized

        return pattern.sub(replacer, text)

    def setup_transcript_storage(self, room: rtc.Room) -> None:
        """Set up room reference and metadata for transcript storage."""
        # Set up room reference for transcript storage
        self._room = room
        
        # Note: The metadata (_conversation_id, _supabase_client, _agent_id) 
        # are already set directly by entrypoint.py before this method is called,
        # so we don't need to extract them here
    
    async def _handle_assistant_transcript(self, text: str) -> None:
        """Store assistant transcript with citations if available."""
        try:
            self._current_assistant_transcript = text
            
            # Skip if we already wrote this via transcription_node streaming
            if self._streaming_transcript_row_id:
                logger.debug("Skipping duplicate assistant transcript (already streamed)")
                return
            
            # Include citations if available
            citations = self._current_citations if self._citations_enabled else None
            
            if self._supabase_client and self._conversation_id:
                turn_id = self._current_turn_id or str(uuid.uuid4())
                tool_results = getattr(self, "_latest_tool_results", None) or None
                self._latest_tool_results = []
                await self._store_transcript(
                    role="assistant",
                    content=text,
                    citations=citations,
                    turn_id=turn_id,
                    tool_results=tool_results,
                )
                logger.info(f"📝 Stored assistant transcript with {len(citations) if citations else 0} citations")
                # Reset both turn IDs when assistant transcript is stored - marks turn boundary
                self._current_turn_id = None
                self._user_turn_id = None
        except Exception as e:
            logger.error(f"Failed to store assistant transcript: {e}")
    
    async def _store_transcript(
        self,
        role: str,
        content: str,
        citations: Optional[List[Dict[str, Any]]] = None,
        *,
        sequence: Optional[int] = None,
        turn_id: Optional[str] = None,
        tool_results: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[str]:
        """Store a transcript entry in the database."""
        if not self._supabase_client:
            logger.warning(f"Cannot store transcript - No Supabase client. Conv ID: {self._conversation_id}")
            return None
        
        if not self._conversation_id:
            logger.warning(f"Cannot store transcript - No conversation_id set")
            return None
        
        logger.debug(f"Storing {role} transcript for conversation {self._conversation_id}, seq={sequence}, turn={turn_id}")
        
        try:
            ts = datetime.utcnow().isoformat()

            # Normalize user identifiers to UUIDs (conversation + transcript tables expect uuid)
            original_user_id = self._user_id
            normalization_details = None
            normalized_user_id = None
            try:
                if original_user_id:
                    normalized_user_id = str(uuid.UUID(str(original_user_id)))
                else:
                    raise ValueError("empty user_id")
            except Exception as normalize_exc:
                # Deterministically derive a UUID from the identifier so we can correlate future turns
                normalized_user_id = (
                    str(uuid.uuid5(uuid.NAMESPACE_URL, str(original_user_id)))
                    if original_user_id
                    else str(uuid.uuid4())
                )
                normalization_details = {
                    "original": original_user_id,
                    "normalized": normalized_user_id,
                    "strategy": "uuid5" if original_user_id else "generated_uuid4",
                    "error": str(normalize_exc),
                }
                logger.warning(
                    "Normalizing non-UUID user_id for transcript storage",
                    extra={
                        "conversation_id": self._conversation_id,
                        "original_user_id": original_user_id,
                        "normalized_user_id": normalized_user_id,
                    },
                )

            # Persist normalized identifier for subsequent inserts in this session
            self._user_id = normalized_user_id

            # Ensure the parent conversation record exists (FK enforcement)
            try:
                def _ensure_conversation():
                    existing = (
                        self._supabase_client
                        .table("conversations")
                        .select("id")
                        .eq("id", self._conversation_id)
                        .limit(1)
                        .execute()
                    )

                    if not existing or not getattr(existing, "data", None):
                        payload = {
                            "id": self._conversation_id,
                            "agent_id": self._agent_id or self._agent_config.get("id"),
                            "user_id": normalized_user_id,
                            "channel": "voice",
                            "created_at": ts,
                            "updated_at": ts,
                        }
                        return (
                            self._supabase_client
                            .table("conversations")
                            .insert(payload)
                            .execute()
                        )
                    return existing

                await asyncio.to_thread(_ensure_conversation)
            except Exception as ensure_exc:
                logger.warning(
                    "Failed to ensure conversation exists before storing transcript",
                    extra={
                        "conversation_id": self._conversation_id,
                        "error": str(ensure_exc),
                    },
                )

            row_metadata: Dict[str, Any] = {}
            if normalization_details:
                row_metadata.setdefault("normalization", {})["user_id"] = normalization_details
            if role == "assistant" and tool_results:
                row_metadata["tool_results"] = tool_results
            client_conversation_id = getattr(self, "_client_conversation_id", None)
            if client_conversation_id:
                row_metadata.setdefault("client_context", {})["conversation_id"] = client_conversation_id

            # Strip SSML tags from assistant transcripts (emotion tags, laughter markers)
            # These are meant for TTS only and should not appear in stored/displayed text
            display_content = content
            if role == "assistant" and content:
                display_content = _strip_ssml_tags(content)
                if display_content != content:
                    logger.debug(f"Stripped SSML tags from transcript: {len(content)} -> {len(display_content)} chars")

            row = {
                "conversation_id": self._conversation_id,
                "agent_id": self._agent_id or self._agent_config.get('id'),
                "user_id": normalized_user_id,
                # Generate a stable session_id per conversation to satisfy NOT NULL constraint
                "session_id": str(self._conversation_id),
                "role": role,
                "content": display_content,
                "transcript": display_content,
                "created_at": ts,
                "source": "voice",  # Mark as voice transcript for SSE filtering
            }

            # Add client_id if available (required for multi-tenant schemas with RLS)
            if self._client_id:
                row["client_id"] = self._client_id

            if row_metadata:
                row["metadata"] = row_metadata
            
            # Add citations if available (for assistant role)
            if role == "assistant" and citations:
                row["citations"] = citations
                logger.info(f"📚 [CITATION-STORE] Adding {len(citations)} citations to {role} transcript")
            elif role == "assistant":
                logger.info(f"📚 [CITATION-STORE] No citations provided for {role} transcript")
            
            # Optional sequencing and turn grouping
            if sequence is not None:
                row["sequence"] = sequence
            if turn_id is not None:
                row["turn_id"] = turn_id
            
            # Use asyncio.to_thread to properly await the sync operation
            def _select_existing():
                return (
                    self._supabase_client
                    .table("conversation_transcripts")
                    .select("id, content")
                    .eq("turn_id", turn_id)
                    .eq("role", role)
                    .limit(1)
                    .execute()
                )

            existing = await asyncio.to_thread(_select_existing)

            if existing and getattr(existing, "data", None):
                existing_row = existing.data[0]
                existing_content = existing_row.get("content", "") or ""

                # For user transcripts, use LONGEST content to handle STT updates
                # STT often sends: partial → more complete → final, all for same turn_id
                # We should ALWAYS use the longer/more complete version, not merge
                if role == "user" and existing_content and content:
                    content_stripped = content.strip()
                    existing_stripped = existing_content.strip()

                    # Normalize for comparison (lowercase, collapse whitespace)
                    def normalize(s):
                        return ' '.join(s.lower().split())

                    content_norm = normalize(content_stripped)
                    existing_norm = normalize(existing_stripped)

                    # For user transcripts, we're now receiving pre-merged content from entrypoint.py
                    # The entrypoint concatenates STT chunks, so new content should always be >= existing
                    # Simple rule: ALWAYS use whichever is longer (the merged result)
                    if len(content_stripped) >= len(existing_stripped):
                        merged_content = content
                        logger.info(f"📝 User transcript: using new ({len(content)} chars) - longer/equal to existing ({len(existing_content)} chars)")
                    else:
                        # This shouldn't happen with proper merge logic, but handle gracefully
                        merged_content = existing_content
                        logger.info(f"📝 User transcript: keeping existing ({len(existing_content)} chars) - unexpectedly longer than new ({len(content)} chars)")

                    row["content"] = merged_content
                    row["transcript"] = merged_content

                update_payload = {k: v for k, v in row.items() if k != "created_at"}

                def _update():
                    return (
                        self._supabase_client
                        .table("conversation_transcripts")
                        .update(update_payload)
                        .eq("turn_id", turn_id)
                        .eq("role", role)
                        .execute()
                    )

                try:
                    result = await asyncio.to_thread(_update)
                except Exception as update_exc:
                    # If update failed due to client_id column, retry without it (dedicated tenant schema)
                    if self._client_id and ("client_id" in str(update_exc).lower() or "column" in str(update_exc).lower()):
                        logger.info(f"Retrying transcript update without client_id (dedicated tenant schema)")
                        update_payload.pop("client_id", None)
                        result = await asyncio.to_thread(_update)
                    else:
                        raise
                logger.info(f"🔄 Updated existing {role} transcript for turn_id={turn_id}")
            else:
                def _insert():
                    return self._supabase_client.table("conversation_transcripts").insert(row).execute()

                try:
                    result = await asyncio.to_thread(_insert)
                except Exception as insert_exc:
                    # If insert failed due to client_id column, retry without it (dedicated tenant schema)
                    if self._client_id and ("client_id" in str(insert_exc).lower() or "column" in str(insert_exc).lower()):
                        logger.info(f"Retrying transcript insert without client_id (dedicated tenant schema)")
                        row.pop("client_id", None)
                        result = await asyncio.to_thread(_insert)
                    else:
                        raise
            # Return inserted row id if available
            try:
                if result and getattr(result, 'data', None):
                    inserted_id = result.data[0].get('id')
                    logger.info(f"✅ Stored {role} transcript for conversation {self._conversation_id} (row_id={inserted_id})")
                    return inserted_id
            except Exception:
                pass
            return None

        except Exception as e:
            logger.error(f"Failed to store {role} transcript: {e}")
            return None
    
    async def store_transcript(self, role: str, content: str) -> None:
        """Public wrapper used by session event handlers."""
        try:
            logger.info(f"📝 store_transcript called: role={role}, content_len={len(content)}")
            # Skip assistant transcript if we already wrote it via transcription_node streaming
            if role == "assistant":
                # Check if currently streaming
                if self._streaming_transcript_row_id:
                    logger.info("📝 store_transcript: SKIPPING - currently streaming")
                    return
                # Check if we just finished streaming (with raw LLM text already in DB)
                # The transcription_node already wrote the final transcript with markdown-preserved text
                if self._streaming_transcript_text:
                    # Always skip for assistant if we have streaming text set
                    # The transcription_node already updated DB with raw LLM text
                    logger.info(f"📝 store_transcript: SKIPPING - transcription_node already wrote final content ({len(self._streaming_transcript_text)} chars)")
                    self._streaming_transcript_text = ""  # Clear after check
                    return
                # Content-based deduplication: skip if content matches last commit
                last_commit = getattr(self, "_last_assistant_commit", "")
                if last_commit and content and last_commit.strip() == content.strip():
                    logger.info(f"📝 store_transcript: SKIPPING - content matches last_assistant_commit ({len(content)} chars)")
                    return
            
            citations = self._current_citations if (role == "assistant" and self._citations_enabled) else None
            tool_results = None
            if role == "assistant":
                tool_results = getattr(self, "_latest_tool_results", None) or None
                self._latest_tool_results = []

            # Use persistent _user_turn_id for user messages to handle pause-mid-sentence
            # This ensures user speech chunks are merged even if user pauses and assistant starts responding
            if role == "user":
                if not self._user_turn_id:
                    self._user_turn_id = str(uuid.uuid4())
                    # CRITICAL: Also set _current_turn_id so assistant response uses the same turn_id
                    # This links the user question with the assistant's response in the UI
                    self._current_turn_id = self._user_turn_id
                    logger.info(f"📝 Generated new user_turn_id: {self._user_turn_id[:8]} (also set as current_turn_id)")
                turn_id = self._user_turn_id
            else:
                # For assistant, use the current user's turn_id to link them together
                if not self._current_turn_id:
                    self._current_turn_id = self._user_turn_id or str(uuid.uuid4())
                turn_id = self._current_turn_id
            await self._store_transcript(
                role=role,
                content=content,
                citations=citations,
                turn_id=turn_id,
                tool_results=tool_results,
            )
            if role == "assistant" and self._text_response_collector:
                try:
                    self._text_response_collector.commit_response(
                        content,
                        citations=citations or [],
                        tool_results=tool_results or [],
                    )
                except Exception as collector_err:
                    logger.debug(f"Text response collector commit failed: {collector_err}")
            if role == "assistant":
                # Reset both turn IDs when assistant completes - this marks the true turn boundary
                # Any subsequent user speech will get a new turn_id
                logger.info(f"📝 Resetting turn IDs after assistant completion (user_turn_id={self._user_turn_id[:8] if self._user_turn_id else None})")
                self._current_turn_id = None
                self._user_turn_id = None
        except Exception as e:
            logger.error(f"store_transcript failed: {e}")

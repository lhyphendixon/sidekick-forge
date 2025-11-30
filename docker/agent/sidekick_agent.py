import logging
import re
from typing import Optional, List, Dict, Any, AsyncIterable, AsyncGenerator
import uuid
import asyncio
from datetime import datetime

from livekit import rtc
from livekit.agents import llm
from livekit.agents import voice

try:
    # livekit-agents >= 1.2.18
    from livekit.agents.voice.io import TimedString
except ImportError:  # pragma: no cover - fallback for older SDKs
    from livekit.agents.voice.agent import TimedString


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
        self._client_conversation_id = None
        self._agent_id = None
        self._current_turn_id: Optional[str] = None
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
        # Text-only mode response capture
        self._text_mode_enabled: bool = False
        self._text_response_collector: Optional[Any] = None

    def attach_text_response_collector(self, collector: Any) -> None:
        """Enable text-only mode response capture."""
        self._text_mode_enabled = True
        self._text_response_collector = collector

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
                                try:
                                    msg.content = user_text
                                except Exception:
                                    logger.debug("Unable to coerce turn_ctx message content to string")
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
                            try:
                                new_message.content = user_text
                            except Exception:
                                logger.debug("Unable to coerce new_message.content to string")
                            logger.info(f"DEBUG: Extracted user text from string list: {user_text[:100]}")
                        else:
                            # Handle structured content (list of dicts)
                            for part in content:
                                if isinstance(part, dict) and part.get("type") == "text":
                                    user_text = part.get("text")
                                    break

            session_last = None
            try:
                if hasattr(self, "_agent_session"):
                    session_last = getattr(self._agent_session, "_last_committed_text", None)
            except Exception:
                session_last = None

            committed_candidates: List[str] = []
            if session_last:
                committed_candidates.append(session_last)
            if self._last_committed_text:
                committed_candidates.append(self._last_committed_text)

            if committed_candidates:
                best_candidate = max(committed_candidates, key=len)
                if not user_text or len(best_candidate) > len(user_text):
                    user_text = best_candidate
                    try:
                        if hasattr(new_message, "content") and isinstance(best_candidate, str):
                            new_message.content = best_candidate
                    except Exception:
                        logger.debug("Unable to overwrite new_message.content with committed candidate")

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
                        new_message.content = normalized_text
                except Exception:
                    logger.debug("Failed to update new_message.content with normalized text")

            self._last_committed_text = user_text
            self._current_user_transcript = user_text
            logger.info(f"Captured user text for transcript (context only): {user_text[:100]}...")

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
            
            # Collect dataset constraints if provided for this agent
            dataset_ids = []
            try:
                if isinstance(self._agent_config, dict) and self._agent_config.get('dataset_ids'):
                    if isinstance(self._agent_config['dataset_ids'], list):
                        dataset_ids = self._agent_config['dataset_ids']
            except Exception:
                dataset_ids = []

            # Perform RAG retrieval with citations
            result = await rag_citations_service.retrieve_with_citations(
                query=user_text,
                client_id=self._client_id,
                agent_slug=agent_slug,
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

    # ------------------------------------------------------------------
    # Speech output sanitization
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_tts_text(text: str) -> str:
        """Remove Markdown asterisks so TTS engines don't verbalize them."""
        if not text:
            return text

        # Collapse Markdown emphasis markers while keeping the inner content
        text = re.sub(r"\*\*(.+?)\*\*", r"\\1", text)
        text = re.sub(r"\*(.+?)\*", r"\\1", text)

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
        accumulated_text = ""
        
        async for chunk in text:
            # Extract text from chunk (TimedString or plain str)
            if isinstance(chunk, TimedString):
                chunk_text = str(chunk)
            else:
                chunk_text = chunk
            
            # Accumulate text
            accumulated_text += chunk_text
            
            # Write to database incrementally
            if self._supabase_client and self._conversation_id and self._agent_id:
                try:
                    timestamp = datetime.utcnow().isoformat()
                    
                    if not self._streaming_transcript_row_id:
                        # First chunk: INSERT a new row
                        row = {
                            "conversation_id": self._conversation_id,
                            "session_id": self._conversation_id,
                            "agent_id": self._agent_id,
                            "user_id": self._user_id,
                            "role": "assistant",
                            "content": accumulated_text,
                            "transcript": accumulated_text,
                            "turn_id": self._current_turn_id or str(uuid.uuid4()),
                            "created_at": timestamp,
                            "source": "voice",
                            "metadata": {}
                        }
                        
                        # Add citations if available
                        if self._current_citations:
                            row["citations"] = self._current_citations
                        
                        result = await asyncio.to_thread(
                            lambda: self._supabase_client.table("conversation_transcripts").insert(row).execute()
                        )
                        
                        if result.data and len(result.data) > 0:
                            self._streaming_transcript_row_id = result.data[0].get("id")
                            logger.debug(f"📝 Created streaming transcript row: {self._streaming_transcript_row_id}")
                    else:
                        # Subsequent chunks: UPDATE the existing row
                        await asyncio.to_thread(
                            lambda: self._supabase_client.table("conversation_transcripts")
                            .update({
                                "content": accumulated_text,
                                "transcript": accumulated_text
                            })
                            .eq("id", self._streaming_transcript_row_id)
                            .execute()
                        )
                        logger.debug(f"📝 Updated streaming transcript ({len(accumulated_text)} chars)")
                    
                except Exception as e:
                    logger.warning(f"Failed to write streaming transcript: {e}")
            
            # Yield the chunk back to continue the pipeline
            yield chunk
        
        # Clear streaming state at end of turn
        self._streaming_transcript_row_id = None
        self._streaming_transcript_text = ""

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
                self._current_turn_id = None
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

            row = {
                "conversation_id": self._conversation_id,
                "agent_id": self._agent_id or self._agent_config.get('id'),
                "user_id": normalized_user_id,
                # Generate a stable session_id per conversation to satisfy NOT NULL constraint
                "session_id": str(self._conversation_id),
                "role": role,
                "content": content,
                "transcript": content,
                "created_at": ts,
                "source": "voice",  # Mark as voice transcript for SSE filtering
            }

            if row_metadata:
                row["metadata"] = row_metadata
            
            # Add citations if available (for assistant role)
            if role == "assistant" and citations:
                row["citations"] = citations
            
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
                    .select("id")
                    .eq("turn_id", turn_id)
                    .eq("role", role)
                    .limit(1)
                    .execute()
                )

            existing = await asyncio.to_thread(_select_existing)

            if existing and getattr(existing, "data", None):
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

                result = await asyncio.to_thread(_update)
                logger.info(f"🔄 Updated existing {role} transcript for turn_id={turn_id}")
            else:
                def _insert():
                    return self._supabase_client.table("conversation_transcripts").insert(row).execute()

                result = await asyncio.to_thread(_insert)
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
            # Skip assistant transcript if we already wrote it via transcription_node streaming
            if role == "assistant" and self._streaming_transcript_row_id:
                logger.debug("Skipping duplicate assistant transcript (already streamed)")
                return
            
            citations = self._current_citations if (role == "assistant" and self._citations_enabled) else None
            tool_results = None
            if role == "assistant":
                tool_results = getattr(self, "_latest_tool_results", None) or None
                self._latest_tool_results = []
            if role == "user" and not self._current_turn_id:
                self._current_turn_id = str(uuid.uuid4())
            turn_id = self._current_turn_id or str(uuid.uuid4())
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
                self._current_turn_id = None
        except Exception as e:
            logger.error(f"store_transcript failed: {e}")

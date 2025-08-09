import logging
from typing import AsyncIterable, Optional

from livekit.agents import llm
from livekit.agents import ModelSettings
from livekit.agents import Agent as CoreAgent
from livekit.agents import voice


logger = logging.getLogger(__name__)


class SidekickAgent(voice.Agent):
    """
    LiveKit-compliant Agent that injects RAG context at the documented node
    `on_user_turn_completed`, and provides explicit logging around the LLM node.
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
    ) -> None:
        super().__init__(instructions=instructions, stt=stt, llm=llm, tts=tts, vad=vad)
        self._context_manager = context_manager
        self._user_id = user_id

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        try:
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

    async def llm_node(
        self,
        chat_ctx: llm.ChatContext,
        tools: list[llm.FunctionTool],
        model_settings: ModelSettings,
    ) -> AsyncIterable[llm.ChatChunk]:
        logger.info("📥 llm_node: invoked; delegating to default implementation")
        async for chunk in CoreAgent.default.llm_node(self, chat_ctx, tools, model_settings):
            yield chunk



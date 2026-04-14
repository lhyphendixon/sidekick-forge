"""
Lore Interview Wizard — Voice-guided depth-building for the personal context layer.

Uses the same LiveKit AgentTask infrastructure as the Sidekick Creation Wizard.
Each Lore category is a task that asks 2-3 targeted questions, extracts signals,
and writes them back to the Lore MCP.

Design principles:
  - Concrete before abstract (current projects first, identity last)
  - Only asks about layers the Depth Score flags as sparse/empty
  - Short sessions — each task is self-contained, progress saved between tasks
  - Conversational tone, not a form
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx
from livekit import rtc
from livekit.agents import Agent, AgentTask, function_tool, RunContext, get_job_context

logger = logging.getLogger(__name__)

LORE_MCP_BASE = os.getenv("LORE_MCP_URL", "http://lore-mcp:8082")

# Category interview order (concrete → abstract)
INTERVIEW_ORDER = [
    "current_projects",
    "tools_and_systems",
    "roles_and_responsibilities",
    "team_and_relationships",
    "communication_style",
    "goals_and_priorities",
    "preferences_and_constraints",
    "domain_knowledge",
    "identity",
    "decision_log",
]

CATEGORY_LABELS = {
    "current_projects": "Current Projects",
    "tools_and_systems": "Tools & Systems",
    "roles_and_responsibilities": "Roles & Responsibilities",
    "team_and_relationships": "Team & Relationships",
    "communication_style": "Communication Style",
    "goals_and_priorities": "Goals & Priorities",
    "preferences_and_constraints": "Preferences & Constraints",
    "domain_knowledge": "Domain Knowledge",
    "identity": "Identity",
    "decision_log": "Decision Log",
}

# 2-3 opening questions per category (the best from the question bank)
CATEGORY_QUESTIONS = {
    "current_projects": [
        "What are you working on right now that has most of your attention?",
        "What would feel like a win for you by the end of this month?",
    ],
    "tools_and_systems": [
        "Walk me through a typical workday — what tools do you open first?",
        "What does your tech stack look like right now?",
    ],
    "roles_and_responsibilities": [
        "How would you describe what you do to someone who has no context?",
        "What kind of decisions do you find yourself making over and over?",
    ],
    "team_and_relationships": [
        "Who do you work most closely with right now?",
        "Who are the most important people in your professional world right now?",
    ],
    "communication_style": [
        "How do you prefer to receive information — detailed breakdowns or the bottom line first?",
        "What's something an AI assistant has done that immediately annoyed you?",
    ],
    "goals_and_priorities": [
        "What are you most trying to make happen in the next few weeks?",
        "What are you optimizing for right now — revenue, time, creative output, something else?",
    ],
    "preferences_and_constraints": [
        "What's something an AI should never do when working with you?",
        "What's a constraint you're working within right now that shapes most of your decisions?",
    ],
    "domain_knowledge": [
        "What's a topic where you know you're genuinely ahead of most people?",
        "What do you wish people would stop explaining to you like you don't already know it?",
    ],
    "identity": [
        "What's the through-line across everything you've worked on — is there one?",
        "What kind of people do you most want to work with or build things for?",
    ],
    "decision_log": [
        "What's the biggest decision you've made in the last year — and do you still think it was right?",
        "What's a principle you keep coming back to when you're making hard calls?",
    ],
}


def _lore_target_params(
    user_id: str,
    target_supabase_url: Optional[str] = None,
    target_supabase_key: Optional[str] = None,
) -> Dict[str, str]:
    params = {"user_id": user_id}
    if target_supabase_url and target_supabase_key:
        params["target_url"] = target_supabase_url
        params["target_key"] = target_supabase_key
    return params


def _lore_internal_headers() -> Dict[str, str]:
    """Identify this process as an internal caller of the Lore MCP admin API."""
    return {"X-Lore-Internal": os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")}


async def _fetch_depth_score(
    user_id: str,
    target_supabase_url: Optional[str] = None,
    target_supabase_key: Optional[str] = None,
) -> Dict[str, str]:
    """Fetch the current Lore depth score and return {category: level}."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{LORE_MCP_BASE}/admin-api/depth-score",
                params=_lore_target_params(user_id, target_supabase_url, target_supabase_key),
                headers=_lore_internal_headers(),
            )
            if resp.status_code == 200:
                data = resp.json()
                return {layer["key"]: layer["level"] for layer in data.get("layers", [])}
    except Exception as exc:
        logger.warning(f"Failed to fetch depth score: {exc}")
    return {}


async def _fetch_category(
    user_id: str,
    category: str,
    target_supabase_url: Optional[str] = None,
    target_supabase_key: Optional[str] = None,
) -> str:
    """Fetch existing Lore content for a category."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{LORE_MCP_BASE}/admin-api/category/{category}",
                params=_lore_target_params(user_id, target_supabase_url, target_supabase_key),
                headers=_lore_internal_headers(),
            )
            if resp.status_code == 200:
                return resp.json().get("content", "")
    except Exception:
        pass
    return ""


async def _update_category(
    user_id: str,
    category: str,
    content: str,
    target_supabase_url: Optional[str] = None,
    target_supabase_key: Optional[str] = None,
) -> bool:
    """Write updated content to a Lore category."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.put(
                f"{LORE_MCP_BASE}/admin-api/category/{category}",
                params=_lore_target_params(user_id, target_supabase_url, target_supabase_key),
                headers=_lore_internal_headers(),
                json={"content": content},
            )
            return resp.status_code == 200
    except Exception as exc:
        logger.error(f"Failed to update Lore category '{category}': {exc}")
    return False


async def _merge_into_category(
    user_id: str,
    category: str,
    new_insights: str,
    cerebras_key: str = "",
    target_supabase_url: Optional[str] = None,
    target_supabase_key: Optional[str] = None,
) -> bool:
    """Merge new insights into an existing Lore category using LLM dedup.
    If no LLM key is available, falls back to naive append with a header."""
    existing = await _fetch_category(user_id, category, target_supabase_url, target_supabase_key)
    label = CATEGORY_LABELS.get(category, category)

    if not existing.strip():
        return await _update_category(user_id, category, f"# {label}\n\n{new_insights.strip()}", target_supabase_url, target_supabase_key)

    if not new_insights.strip():
        return True

    # Try LLM merge for deduplication
    if cerebras_key:
        try:
            prompt = (
                f"You are updating a Lore profile section for '{label}'.\n\n"
                f"EXISTING CONTENT:\n{existing.strip()}\n\n"
                f"NEW INSIGHTS FROM INTERVIEW:\n{new_insights.strip()}\n\n"
                f"RULES:\n"
                f"- Keep ALL existing content that is still valid\n"
                f"- Add ONLY new information that isn't already covered\n"
                f"- If new info overlaps with existing, keep the more detailed version\n"
                f"- Do NOT duplicate points that are already present\n"
                f"- Maintain the existing structure and formatting\n"
                f"- Start with a level-1 heading: # {label}\n"
                f"- Use bullet points\n"
                f"- Return ONLY the merged markdown, no explanation\n"
            )
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.cerebras.ai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {cerebras_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "zai-glm-4.7",
                        "reasoning_effort": "none",
                        "max_tokens": 3000,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                if resp.status_code == 200:
                    merged = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                    if merged.strip():
                        logger.info(f"LLM merge for '{category}': deduped successfully")
                        return await _update_category(user_id, category, merged.strip(), target_supabase_url, target_supabase_key)
        except Exception as exc:
            logger.warning(f"LLM merge failed for '{category}': {exc}")

    # Fallback: append with header
    logger.info(f"Falling back to naive append for '{category}'")
    return await _update_category(user_id, category, f"{existing.strip()}\n\n{new_insights.strip()}", target_supabase_url, target_supabase_key)


def _categories_needing_depth(depth_scores: Dict[str, str]) -> List[str]:
    """Return categories that aren't yet strong, in interview order."""
    needs_depth = []
    for cat in INTERVIEW_ORDER:
        level = depth_scores.get(cat, "not_captured")
        if level in ("not_captured", "emerging", "growing"):
            needs_depth.append(cat)
    return needs_depth


# ---------------------------------------------------------------------------
# Interview Task — one per category
# ---------------------------------------------------------------------------

@dataclass
class InterviewResult:
    category: str
    insights: str
    saved: bool


class CategoryInterviewTask(AgentTask[InterviewResult]):
    """Interview task for a single Lore category."""

    def __init__(
        self,
        category: str,
        existing_content: str,
        publisher,
        step_number: int,
        total_steps: int,
        user_id: str,
        target_supabase_url: Optional[str] = None,
        target_supabase_key: Optional[str] = None,
        chat_ctx=None,
    ):
        label = CATEGORY_LABELS.get(category, category)
        questions = CATEGORY_QUESTIONS.get(category, ["Tell me more about this area of your life."])

        question_list = "\n".join(f"  - {q}" for q in questions)
        instructions = f"""You are interviewing the user to learn about their {label}.

Your opening question pool (pick one to start, ask others as follow-ups):
{question_list}

RULES:
- Ask ONE question at a time, conversationally
- Listen carefully and ask brief follow-up questions to get specific details
- After 2-3 exchanges, call save_insights with a clean summary of what you learned
- Keep it natural — this is a conversation, not a form
- Be warm but efficient — respect their time
- Do NOT make up information — only record what the user actually said
- After save_insights, call move_to_next to proceed

{"Existing content for context (DO NOT repeat this back, just use it to avoid asking redundant questions):" + chr(10) + existing_content if existing_content.strip() else "No existing content for this category yet."}"""

        super().__init__(instructions=instructions, chat_ctx=chat_ctx)
        self.category = category
        self.existing_content = existing_content
        self.publisher = publisher
        self.step_number = step_number
        self.total_steps = total_steps
        self.user_id = user_id
        self.target_supabase_url = target_supabase_url
        self.target_supabase_key = target_supabase_key
        self._insights = ""
        self._saved = False

    async def on_enter(self) -> None:
        questions = CATEGORY_QUESTIONS.get(self.category, [])
        label = CATEGORY_LABELS.get(self.category, self.category)
        opening = questions[0] if questions else f"Tell me about your {label}."
        step_msg = f"({self.step_number} of {self.total_steps})"
        await self.session.say(
            f"Let's talk about your {label.lower()}. {step_msg} {opening}",
            allow_interruptions=False,
        )

    @function_tool
    async def save_insights(self, context: RunContext, insights: str) -> str:
        """
        Save the insights gathered from this part of the conversation to the user's Lore.
        Call this after you've gathered enough information (2-3 exchanges).

        Args:
            insights: A clean markdown summary of what the user shared. Use bullet points.
        """
        if not insights.strip():
            return "Insights cannot be empty. Summarize what the user told you."

        # Get Cerebras key for LLM merge
        cerebras_key = ""
        try:
            job_ctx = get_job_context()
            meta_str = job_ctx.room.metadata
            if meta_str:
                import json as _json
                cerebras_key = _json.loads(meta_str).get("api_keys", {}).get("cerebras_api_key", "")
        except Exception:
            pass

        success = await _merge_into_category(
            self.user_id,
            self.category,
            insights.strip(),
            cerebras_key=cerebras_key,
            target_supabase_url=self.target_supabase_url,
            target_supabase_key=self.target_supabase_key,
        )
        self._insights = insights
        self._saved = success

        if self.publisher:
            await self.publisher.field_update(
                f"lore_{self.category}", insights, step=self.step_number
            )

        status = "saved" if success else "save failed"
        logger.info(f"Lore interview: {self.category} insights {status} ({len(insights)} chars)")
        return f"Insights {status}. Now call move_to_next to continue."

    @function_tool
    async def move_to_next(self, context: RunContext) -> InterviewResult:
        """Move to the next category. Call this after save_insights."""
        return InterviewResult(
            category=self.category,
            insights=self._insights,
            saved=self._saved,
        )


# ---------------------------------------------------------------------------
# Main Interview Agent
# ---------------------------------------------------------------------------

LORE_INTERVIEW_SYSTEM_PROMPT = """You are a friendly interviewer helping a user build their personal Lore profile.

Your goal is to learn about WHO they are through natural conversation — their work, tools, goals,
relationships, communication style, and worldview.

STYLE:
- Conversational and warm, like a smart colleague getting to know them
- Ask one question at a time
- Brief follow-ups to get specifics, then move on
- Respect their time — each topic should take 2-3 minutes max
- Never lecture or explain things back to them
- Use their actual words when summarizing

You'll work through several topics. For each one, ask your questions, listen,
then save what you learned and move to the next."""


class LoreInterviewAgent(Agent):
    """Voice-guided Lore depth-building agent."""

    def __init__(
        self,
        session_id: Optional[str] = None,
        current_step: int = 1,
        form_data: Optional[Dict[str, Any]] = None,
        supabase_url: Optional[str] = None,
        supabase_service_key: Optional[str] = None,
        target_categories: Optional[List[str]] = None,
        lore_user_id: Optional[str] = None,
        lore_target_url: Optional[str] = None,
        lore_target_key: Optional[str] = None,
    ):
        super().__init__(instructions=LORE_INTERVIEW_SYSTEM_PROMPT)
        self.session_id = session_id
        self.current_step = current_step
        self.form_data = form_data or {}
        self._supabase_url = supabase_url
        self._supabase_service_key = supabase_service_key
        self.target_categories = target_categories or []
        self.lore_user_id = lore_user_id or ""
        self.lore_target_url = lore_target_url
        self.lore_target_key = lore_target_key
        self.publisher = None

    async def on_enter(self) -> None:
        try:
            job_ctx = get_job_context()
            room = job_ctx.room
        except Exception:
            room = None

        # Import publisher from wizard_tasks (shared infrastructure)
        from wizard_tasks import WizardDataPublisher
        self.publisher = WizardDataPublisher(
            room, self.session_id,
            supabase_url=self._supabase_url,
            supabase_service_key=self._supabase_service_key,
        )

        # Wait for participant
        if room:
            waited = 0
            while len(room.remote_participants) == 0 and waited < 30:
                await asyncio.sleep(1)
                waited += 1
            if room.remote_participants:
                await asyncio.sleep(0.5)

        # Determine which categories need depth
        if not self.target_categories:
            depth_scores = await _fetch_depth_score(
                self.lore_user_id, self.lore_target_url, self.lore_target_key
            )
            self.target_categories = _categories_needing_depth(depth_scores)

        if not self.target_categories:
            await self.session.say(
                "Your Lore is looking great — all categories have strong coverage. "
                "If you'd like to update anything, you can edit directly on the Lore page.",
                allow_interruptions=False,
            )
            if self.publisher:
                await self.publisher.publish("lore_interview_complete", {
                    "categories_covered": 0, "session_id": self.session_id
                })
            return

        total = len(self.target_categories)
        category_names = ", ".join(
            CATEGORY_LABELS.get(c, c) for c in self.target_categories[:3]
        )
        remaining = f" and {total - 3} more" if total > 3 else ""

        await self.session.say(
            f"Hi! I'm going to ask you a few questions to build out your Lore profile. "
            f"We'll cover {category_names}{remaining}. "
            f"This should take about {min(total * 2, 15)} minutes, and you can stop anytime. "
            f"Let's get started!",
            allow_interruptions=False,
        )

        # Run interview tasks in sequence, skipping past current_step
        categories_covered = 0
        current_category = None
        try:
            for i, category in enumerate(self.target_categories):
                step_num = i + 1
                if step_num < self.current_step:
                    continue

                current_category = category
                existing = await _fetch_category(
                    self.lore_user_id, category, self.lore_target_url, self.lore_target_key
                )

                result = await CategoryInterviewTask(
                    category=category,
                    existing_content=existing,
                    publisher=self.publisher,
                    step_number=step_num,
                    total_steps=total,
                    user_id=self.lore_user_id,
                    target_supabase_url=self.lore_target_url,
                    target_supabase_key=self.lore_target_key,
                    chat_ctx=self.chat_ctx,
                )

                categories_covered += 1
                current_category = None  # Task completed cleanly

                if self.publisher:
                    await self.publisher.step_change(step=step_num + 1, total=total)

                # Brief transition between categories
                if step_num < total:
                    await self.session.say("Great, let's move on.", allow_interruptions=False)

            # Wrap up
            await self.session.say(
                f"That's all for now! I've updated {categories_covered} "
                f"{'category' if categories_covered == 1 else 'categories'} in your Lore. "
                f"You can review and edit everything on the Lore page.",
                allow_interruptions=False,
            )

        except (asyncio.CancelledError, Exception) as exc:
            # User disconnected or task was cancelled — save what we have
            logger.info(f"Lore interview interrupted ({type(exc).__name__}), saving conversation...")
            if current_category:
                await self._save_from_chat_context(current_category, categories_covered)
                categories_covered += 1

        if self.publisher:
            await self.publisher.publish("lore_interview_complete", {
                "categories_covered": categories_covered,
                "session_id": self.session_id,
            })

    async def _save_from_chat_context(self, category: str, step: int) -> None:
        """Extract and summarize insights from the conversation, then save to Lore.
        Called when the user disconnects mid-interview."""
        try:
            # Gather full conversation from chat context
            conversation_lines = []
            if self.chat_ctx and hasattr(self.chat_ctx, 'items'):
                for item in self.chat_ctx.items:
                    role = getattr(item, 'role', None)
                    if role not in ('user', 'assistant'):
                        continue
                    text = ""
                    if hasattr(item, 'text'):
                        text = item.text or ""
                    elif hasattr(item, 'content'):
                        if isinstance(item.content, str):
                            text = item.content
                        elif isinstance(item.content, list):
                            text = " ".join(
                                b.get("text", "") if isinstance(b, dict) else str(b)
                                for b in item.content
                            )
                    if text.strip():
                        speaker = "User" if role == "user" else "Interviewer"
                        conversation_lines.append(f"{speaker}: {text.strip()}")

            if not conversation_lines:
                logger.info(f"Lore interview: no conversation to save for {category}")
                return

            label = CATEGORY_LABELS.get(category, category)
            transcript = "\n".join(conversation_lines)

            # Summarize via LLM (direct HTTP to avoid relying on session which may be closing)
            summary = await self._summarize_transcript(category, label, transcript)

            if not summary:
                logger.warning(f"Lore interview: LLM summary empty for {category}, using raw transcript")
                user_lines = [l.replace("User: ", "") for l in conversation_lines if l.startswith("User:")]
                summary = "\n".join(f"- {l}" for l in user_lines)

            # Merge with existing content, deduplicating via LLM
            cerebras_key = ""
            try:
                job_ctx = get_job_context()
                meta_str = job_ctx.room.metadata
                if meta_str:
                    import json as _json
                    cerebras_key = _json.loads(meta_str).get("api_keys", {}).get("cerebras_api_key", "")
            except Exception:
                pass

            success = await _merge_into_category(
                self.lore_user_id,
                category,
                summary,
                cerebras_key=cerebras_key,
                target_supabase_url=self.lore_target_url,
                target_supabase_key=self.lore_target_key,
            )
            logger.info(
                f"Lore interview: saved summarized insights "
                f"to '{category}' on disconnect (success={success})"
            )
        except Exception as save_exc:
            logger.error(f"Failed to save interview data on disconnect: {save_exc}")

    async def _summarize_transcript(self, category: str, label: str, transcript: str) -> str:
        """Use LLM to summarize an interview transcript into clean Lore content."""
        try:
            # Get Cerebras key from metadata passed to the agent
            job_ctx = get_job_context()
            api_keys = {}
            try:
                meta_str = job_ctx.room.metadata
                if meta_str:
                    import json as _json
                    meta = _json.loads(meta_str)
                    api_keys = meta.get("api_keys", {})
            except Exception:
                pass

            cerebras_key = api_keys.get("cerebras_api_key") or os.getenv("CEREBRAS_API_KEY", "")
            if not cerebras_key:
                logger.warning("No Cerebras key available for transcript summarization")
                return ""

            prompt = (
                f"You are summarizing an interview about a user's {label}.\n\n"
                f"Below is a transcript of the conversation. Extract the key insights "
                f"the user shared and write them as clean, concise bullet points. "
                f"Use the user's meaning but clean up the language — no verbatim speech artifacts, "
                f"no filler words, no incomplete sentences. Only include what the user actually said, "
                f"do not add information.\n\n"
                f"TRANSCRIPT:\n{transcript}\n\n"
                f"Write bullet points only, no headers or explanation:"
            )

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.cerebras.ai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {cerebras_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "zai-glm-4.7",
                        "reasoning_effort": "none",
                        "max_tokens": 2000,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                if resp.status_code == 200:
                    content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                    return content.strip()
                else:
                    logger.warning(f"Cerebras summarization failed: HTTP {resp.status_code}")
                    return ""
        except Exception as exc:
            logger.warning(f"Transcript summarization failed: {exc}")
            return ""

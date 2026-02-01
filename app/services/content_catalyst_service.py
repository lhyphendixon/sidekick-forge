"""
Content Catalyst Service

A multi-phase content generation ability that orchestrates:
1. Research Lead - Gathers sources (P1: session sources, P2: KB, P3: Perplexity, P4: LLM)
2. Content Architect - Designs structure and two different article angles
3. Ghostwriter - Drafts two article variations
4. Integrity Officer - Verifies accuracy and sources
5. Final Polisher - Refines prose, enforces word count

Produces two high-quality, research-backed article variations from a single topic.
"""

import logging
import json
import asyncio
import re
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from enum import Enum
from abc import ABC, abstractmethod

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


# ============================================================================
# LLM Provider Abstraction
# ============================================================================

class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    async def chat(self, messages: List[Dict[str, str]], max_tokens: int = 4096) -> str:
        """Send a chat completion request and return the response text."""
        pass


class GroqProvider(LLMProvider):
    """Groq LLM provider using OpenAI-compatible API."""

    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile"):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://api.groq.com/openai/v1"

    async def chat(self, messages: List[Dict[str, str]], max_tokens: int = 4096) -> str:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]


class OpenAIProvider(LLMProvider):
    """OpenAI LLM provider."""

    def __init__(self, api_key: str, model: str = "gpt-4o"):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://api.openai.com/v1"

    async def chat(self, messages: List[Dict[str, str]], max_tokens: int = 4096) -> str:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]


class AnthropicProvider(LLMProvider):
    """Anthropic Claude LLM provider."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://api.anthropic.com/v1"

    async def chat(self, messages: List[Dict[str, str]], max_tokens: int = 4096) -> str:
        # Convert OpenAI-style messages to Anthropic format
        anthropic_messages = []
        for msg in messages:
            anthropic_messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": max_tokens,
                    "messages": anthropic_messages,
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()
            return data["content"][0]["text"]


class DeepInfraProvider(LLMProvider):
    """DeepInfra LLM provider using OpenAI-compatible API."""

    def __init__(self, api_key: str, model: str = "meta-llama/Llama-3.3-70B-Instruct"):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://api.deepinfra.com/v1/openai"

    async def chat(self, messages: List[Dict[str, str]], max_tokens: int = 4096) -> str:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]


class CerebrasProvider(LLMProvider):
    """Cerebras LLM provider using OpenAI-compatible API."""

    def __init__(self, api_key: str, model: str = "zai-glm-4.7"):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://api.cerebras.ai/v1"
        logger.info(f"CerebrasProvider initialized with model: {self.model}")

    async def chat(self, messages: List[Dict[str, str]], max_tokens: int = 4096) -> str:
        logger.info(f"CerebrasProvider.chat called with model={self.model}, max_tokens={max_tokens}")
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": messages,
                        "max_tokens": max_tokens,
                    },
                    timeout=120.0,
                )
                response.raise_for_status()
                data = response.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                if not content:
                    logger.error(f"Cerebras returned empty content. Full response: {data}")
                return content or ""
            except httpx.HTTPStatusError as e:
                logger.error(f"Cerebras API error: {e.response.status_code} - {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"Cerebras request failed: {e}")
                raise


def create_llm_provider(
    provider_name: str,
    api_key: str,
    model: Optional[str] = None
) -> LLMProvider:
    """Factory function to create the appropriate LLM provider."""
    provider_name = provider_name.lower()

    if provider_name == "groq":
        return GroqProvider(api_key, model or "llama-3.3-70b-versatile")
    elif provider_name == "openai":
        return OpenAIProvider(api_key, model or "gpt-4o")
    elif provider_name == "anthropic":
        return AnthropicProvider(api_key, model or "claude-sonnet-4-20250514")
    elif provider_name == "deepinfra":
        return DeepInfraProvider(api_key, model or "meta-llama/Llama-3.3-70B-Instruct")
    elif provider_name == "cerebras":
        return CerebrasProvider(api_key, model or "zai-glm-4.7")
    else:
        raise ValueError(f"Unsupported LLM provider: {provider_name}")


class ContentCatalystPhase(str, Enum):
    """Phases in the Content Catalyst pipeline"""
    INPUT = "input"
    RESEARCH = "research"
    ARCHITECTURE = "architecture"
    DRAFTING = "drafting"
    INTEGRITY = "integrity"
    POLISHING = "polishing"
    COMPLETE = "complete"


class SourceType(str, Enum):
    """Types of source input"""
    MP3 = "mp3"
    URL = "url"
    TEXT = "text"
    DOCUMENT = "document"  # Knowledge base document


@dataclass
class PhaseOutput:
    """Output from a single phase"""
    phase: str
    completed_at: str
    output: Dict[str, Any]
    tokens_used: int = 0
    duration_ms: float = 0


@dataclass
class ContentCatalystConfig:
    """Configuration for a Content Catalyst run"""
    source_type: SourceType
    source_content: str  # URL, transcribed text, or pasted text
    target_word_count: int = 1500
    style_prompt: Optional[str] = None
    use_perplexity: bool = True
    use_knowledge_base: bool = True
    # New fields for document source and text instructions
    document_id: Optional[int] = None  # Document ID for DOCUMENT source type
    document_title: Optional[str] = None  # Document title for display
    text_instructions: Optional[str] = None  # Always-available instructions field


@dataclass
class ResearchFindings:
    """Output from Research Lead phase"""
    session_sources: List[Dict[str, Any]]  # P1: From user input
    knowledge_base_sources: List[Dict[str, Any]]  # P2: From RAG
    web_sources: List[Dict[str, Any]]  # P3: From Perplexity/Firecrawl
    key_themes: List[str]
    topic_summary: str


@dataclass
class ArticleArchitecture:
    """Output from Content Architect phase"""
    angle_1_title: str
    angle_1_hook: str
    angle_1_outline: List[str]
    angle_2_title: str
    angle_2_hook: str
    angle_2_outline: List[str]
    shared_research_points: List[str]
    internal_links: List[Dict[str, str]]  # For WordPress clients


@dataclass
class ArticleDraft:
    """A single article draft"""
    title: str
    content: str
    word_count: int
    sources_cited: List[str]


@dataclass
class IntegrityReport:
    """Output from Integrity Officer phase"""
    draft_1_issues: List[Dict[str, Any]]
    draft_2_issues: List[Dict[str, Any]]
    sources_verified: List[str]
    sources_unverifiable: List[str]
    factual_accuracy_score: float  # 0-1
    recommendations: List[str]


@dataclass
class ContentCatalystAgentConfig:
    """Per-agent configuration for Content Catalyst."""
    default_style_prompt: str = ""
    example_writing: str = ""


class ContentCatalystService:
    """
    Service for orchestrating multi-phase content generation.

    Each phase is executed by a specialized sub-agent with a focused prompt.
    Outputs are passed between phases to build the final articles.
    """

    def __init__(
        self,
        client_id: str,
        llm_provider: str = "groq",
        llm_api_key: Optional[str] = None,
        llm_model: Optional[str] = None,
        perplexity_api_key: Optional[str] = None,
        firecrawl_api_key: Optional[str] = None,
        deepgram_api_key: Optional[str] = None,
        # Legacy support for anthropic_api_key
        anthropic_api_key: Optional[str] = None,
        groq_api_key: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        deepinfra_api_key: Optional[str] = None,
        cerebras_api_key: Optional[str] = None,
        # Agent-specific configuration
        agent_config: Optional[ContentCatalystAgentConfig] = None,
    ):
        self.client_id = client_id
        self.perplexity_api_key = perplexity_api_key
        self.firecrawl_api_key = firecrawl_api_key
        self.deepgram_api_key = deepgram_api_key
        self.agent_config = agent_config or ContentCatalystAgentConfig()

        # Determine LLM provider and API key
        self.llm_provider_name = llm_provider.lower()
        self.llm_model = llm_model

        # Use provided llm_api_key, or fall back to provider-specific keys
        if llm_api_key:
            self.llm_api_key = llm_api_key
        elif self.llm_provider_name == "groq" and groq_api_key:
            self.llm_api_key = groq_api_key
        elif self.llm_provider_name == "anthropic" and anthropic_api_key:
            self.llm_api_key = anthropic_api_key
        elif self.llm_provider_name == "openai" and openai_api_key:
            self.llm_api_key = openai_api_key
        elif self.llm_provider_name == "deepinfra" and deepinfra_api_key:
            self.llm_api_key = deepinfra_api_key
        elif self.llm_provider_name == "cerebras" and cerebras_api_key:
            self.llm_api_key = cerebras_api_key
        else:
            # Try to find any available API key and use that provider
            if groq_api_key:
                self.llm_provider_name = "groq"
                self.llm_api_key = groq_api_key
            elif cerebras_api_key:
                self.llm_provider_name = "cerebras"
                self.llm_api_key = cerebras_api_key
            elif openai_api_key:
                self.llm_provider_name = "openai"
                self.llm_api_key = openai_api_key
            elif anthropic_api_key:
                self.llm_provider_name = "anthropic"
                self.llm_api_key = anthropic_api_key
            elif deepinfra_api_key:
                self.llm_provider_name = "deepinfra"
                self.llm_api_key = deepinfra_api_key
            else:
                self.llm_api_key = None

        self.max_word_count_iterations = 3
        self.word_count_tolerance_percent = 10

        self._llm_provider: Optional[LLMProvider] = None
        self._platform_sb = None
        self._client_sb = None

    @property
    def llm(self) -> LLMProvider:
        """Lazy-load LLM provider"""
        if self._llm_provider is None:
            if not self.llm_api_key:
                raise ValueError(
                    f"No API key configured for LLM provider '{self.llm_provider_name}'. "
                    "Please configure an LLM API key (groq_api_key, openai_api_key, etc.) for the client."
                )
            self._llm_provider = create_llm_provider(
                self.llm_provider_name,
                self.llm_api_key,
                self.llm_model
            )
        return self._llm_provider

    async def _llm_chat(self, prompt: str, max_tokens: int = 4096) -> str:
        """Helper to call LLM with a user prompt."""
        messages = [{"role": "user", "content": prompt}]
        return await self.llm.chat(messages, max_tokens)

    async def _transcribe_audio(self, audio_url: str) -> str:
        """Transcribe audio file using Deepgram API.

        Args:
            audio_url: Signed URL to the audio file in Supabase storage

        Returns:
            Transcribed text from the audio
        """
        if not self.deepgram_api_key:
            raise ValueError("Deepgram API key not configured. Required for audio transcription.")

        logger.info(f"Transcribing audio from URL: {audio_url[:100]}...")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.deepgram.com/v1/listen",
                headers={
                    "Authorization": f"Token {self.deepgram_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "url": audio_url,
                },
                params={
                    "model": "nova-2",
                    "smart_format": "true",
                    "punctuate": "true",
                    "paragraphs": "true",
                    "utterances": "true",
                },
                timeout=300.0,  # 5 minutes for long audio files
            )

            if response.status_code != 200:
                logger.error(f"Deepgram API error: {response.status_code} - {response.text}")
                raise ValueError(f"Deepgram transcription failed: {response.text}")

            data = response.json()

            # Extract transcript from response
            results = data.get("results", {})
            channels = results.get("channels", [])

            if not channels:
                raise ValueError("No transcription results returned from Deepgram")

            # Get the transcript from the first channel
            alternatives = channels[0].get("alternatives", [])
            if not alternatives:
                raise ValueError("No transcript alternatives in Deepgram response")

            transcript = alternatives[0].get("transcript", "")

            if not transcript:
                raise ValueError("Empty transcript returned from Deepgram")

            logger.info(f"Transcription complete: {len(transcript)} characters")
            return transcript

    async def _fetch_document_content(self, document_id: int) -> str:
        """Fetch document content from knowledge base.

        Args:
            document_id: ID of the document in the documents table

        Returns:
            Combined content from all document chunks
        """
        if not document_id:
            raise ValueError("Document ID is required")

        logger.info(f"Fetching document content for document_id: {document_id}")

        try:
            client_sb = await self.get_client_supabase()

            # First, try to get the document's main content field
            doc_result = client_sb.table('documents') \
                .select('content, title') \
                .eq('id', document_id) \
                .single() \
                .execute()

            if doc_result.data and doc_result.data.get('content'):
                content = doc_result.data.get('content', '')
                if content and len(content.strip()) > 0:
                    logger.info(f"Retrieved document content from documents table: {len(content)} chars")
                    return content
                else:
                    logger.info(f"Document {document_id} has empty content field, trying chunks")

            # If no main content, get from document_chunks
            chunks_result = client_sb.table('document_chunks') \
                .select('content, chunk_index') \
                .eq('document_id', document_id) \
                .order('chunk_index') \
                .execute()

            if not chunks_result.data:
                raise ValueError(f"No content found for document {document_id}")

            # Combine chunks in order
            combined_content = "\n\n".join(
                chunk.get('content', '') for chunk in chunks_result.data
            )

            if not combined_content or len(combined_content.strip()) == 0:
                raise ValueError(f"Document {document_id} has no text content in its chunks")

            logger.info(f"Retrieved document content from {len(chunks_result.data)} chunks: {len(combined_content)} chars")
            return combined_content

        except Exception as e:
            logger.error(f"Failed to fetch document content: {e}")
            raise ValueError(f"Failed to fetch document: {str(e)}")

    async def get_platform_supabase(self):
        """Get platform Supabase client"""
        if self._platform_sb is None:
            from supabase import create_client
            self._platform_sb = create_client(
                settings.supabase_url,
                settings.supabase_service_role_key
            )
        return self._platform_sb

    async def get_client_supabase(self):
        """Get client-specific Supabase client"""
        if self._client_sb is None:
            from app.utils.supabase_credentials import SupabaseCredentialManager
            url, _, key = await SupabaseCredentialManager.get_client_supabase_credentials(
                self.client_id
            )
            from supabase import create_client
            self._client_sb = create_client(url, key)
        return self._client_sb

    async def create_run(
        self,
        config: ContentCatalystConfig,
        agent_id: Optional[str] = None,
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """Create a new Content Catalyst run in the database"""
        try:
            platform_sb = await self.get_platform_supabase()

            result = platform_sb.rpc(
                "create_content_catalyst_run",
                {
                    "p_client_id": self.client_id,
                    "p_agent_id": agent_id,
                    "p_user_id": user_id,
                    "p_conversation_id": conversation_id,
                    "p_session_id": session_id,
                    "p_source_type": config.source_type.value,
                    "p_source_content": config.source_content,
                    "p_target_word_count": config.target_word_count,
                    "p_style_prompt": config.style_prompt,
                    "p_use_perplexity": config.use_perplexity,
                    "p_use_knowledge_base": config.use_knowledge_base,
                }
            ).execute()

            run_id = result.data
            logger.info(f"Created Content Catalyst run: {run_id}")
            return run_id

        except Exception as e:
            logger.error(f"Failed to create Content Catalyst run: {e}")
            raise

    async def update_phase(
        self,
        run_id: str,
        phase: ContentCatalystPhase,
        output: Dict[str, Any],
        status: Optional[str] = None,
        error: Optional[str] = None,
    ) -> bool:
        """Update run with phase completion"""
        try:
            platform_sb = await self.get_platform_supabase()

            result = platform_sb.rpc(
                "update_content_catalyst_phase",
                {
                    "p_run_id": run_id,
                    "p_phase": phase.value,
                    "p_phase_output": output,
                    "p_status": status,
                    "p_error": error,
                }
            ).execute()

            return result.data is True

        except Exception as e:
            logger.error(f"Failed to update phase {phase}: {e}")
            return False

    async def save_articles(
        self,
        run_id: str,
        article_1: str,
        article_2: str,
    ) -> bool:
        """Save final articles to the run"""
        try:
            platform_sb = await self.get_platform_supabase()

            result = platform_sb.rpc(
                "save_content_catalyst_articles",
                {
                    "p_run_id": run_id,
                    "p_article_1": article_1,
                    "p_article_2": article_2,
                }
            ).execute()

            return result.data is True

        except Exception as e:
            logger.error(f"Failed to save articles: {e}")
            return False

    async def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Get run details from database"""
        try:
            platform_sb = await self.get_platform_supabase()
            result = platform_sb.rpc(
                "get_content_catalyst_run",
                {"p_run_id": run_id}
            ).execute()

            if result.data:
                return result.data[0] if isinstance(result.data, list) else result.data
            return None

        except Exception as e:
            logger.error(f"Failed to get run {run_id}: {e}")
            return None

    # =========================================================================
    # PHASE 1: RESEARCH LEAD
    # =========================================================================

    async def execute_research_phase(
        self,
        config: ContentCatalystConfig,
        progress_callback: Optional[callable] = None,
    ) -> ResearchFindings:
        """
        Research Lead phase - Gathers sources with priority:
        P1: Session sources (MP3 transcript, URL content, text input)
        P2: Knowledge Base (RAG search)
        P3: Web (Perplexity/Firecrawl)
        P4: LLM training data (implicit)
        """
        start_time = datetime.now(timezone.utc)

        if progress_callback:
            progress_callback("research", "starting", "Gathering research materials...")

        session_sources = []
        kb_sources = []
        web_sources = []

        # P1: Process session source
        if config.source_type == SourceType.MP3:
            # Transcribe the audio file first
            if progress_callback:
                progress_callback("research", "transcribing", "Transcribing audio file...")

            try:
                transcript = await self._transcribe_audio(config.source_content)
                logger.info(f"Audio transcription successful: {len(transcript)} chars")
                session_sources.append({
                    "type": "mp3_transcript",
                    "content": transcript,
                    "weight": 1.0,
                })
            except Exception as e:
                logger.error(f"Audio transcription failed: {e}")
                raise ValueError(f"Failed to transcribe audio: {str(e)}")
        elif config.source_type == SourceType.URL:
            # Scrape the URL
            if progress_callback:
                progress_callback("research", "scraping", "Scraping source URL...")

            scraped = await self._scrape_url(config.source_content)
            if scraped:
                session_sources.append({
                    "type": "url",
                    "url": config.source_content,
                    "title": scraped.get("title", ""),
                    "content": scraped.get("content", ""),
                    "weight": 1.0,
                })
        elif config.source_type == SourceType.DOCUMENT:
            # Fetch document content from knowledge base
            if progress_callback:
                progress_callback("research", "fetching_document", "Fetching document content...")

            try:
                document_content = await self._fetch_document_content(config.document_id)
                logger.info(f"Document fetch successful: {len(document_content)} chars")
                session_sources.append({
                    "type": "knowledge_base_document",
                    "document_id": config.document_id,
                    "title": config.document_title or "Untitled",
                    "content": document_content,
                    "weight": 1.0,
                })
            except Exception as e:
                logger.error(f"Document fetch failed: {e}")
                raise ValueError(f"Failed to fetch document: {str(e)}")
        else:  # TEXT
            session_sources.append({
                "type": "text_input",
                "content": config.source_content,
                "weight": 1.0,
            })

        # Add text instructions as additional context if provided
        if config.text_instructions:
            session_sources.append({
                "type": "user_instructions",
                "content": config.text_instructions,
                "weight": 0.9,  # Slightly lower weight than primary source
            })

        # Determine search query - use content from session sources for searching
        search_query = config.source_content
        if session_sources:
            first_source = session_sources[0]
            source_type = first_source.get("type", "")

            if source_type == "mp3_transcript":
                # Extract first 1000 chars of transcript as search context
                transcript_excerpt = first_source.get("content", "")[:1000]
                if transcript_excerpt:
                    search_query = transcript_excerpt
                    logger.info(f"Using transcript excerpt for search query: {len(transcript_excerpt)} chars")
            elif source_type == "knowledge_base_document":
                # Extract first 1000 chars of document as search context
                doc_excerpt = first_source.get("content", "")[:1000]
                if doc_excerpt:
                    search_query = doc_excerpt
                    logger.info(f"Using document excerpt for search query: {len(doc_excerpt)} chars")

        # If user provided text instructions, include them in the search query
        if config.text_instructions:
            search_query = f"{config.text_instructions}\n\n{search_query[:500]}"
            logger.info(f"Combined text instructions with source content for search")

        # P2: Search Knowledge Base if enabled
        if config.use_knowledge_base:
            if progress_callback:
                progress_callback("research", "kb_search", "Searching knowledge base...")

            try:
                kb_results = await self._search_knowledge_base(search_query)
                kb_sources.extend(kb_results)
            except Exception as e:
                logger.warning(f"Knowledge base search failed: {e}")

        # P3: Web search if enabled
        if config.use_perplexity and self.perplexity_api_key:
            if progress_callback:
                progress_callback("research", "web_search", "Searching the web...")

            try:
                web_results = await self._perplexity_search(search_query)
                web_sources.extend(web_results)
            except Exception as e:
                logger.warning(f"Perplexity search failed: {e}")

        # Synthesize research findings
        if progress_callback:
            progress_callback("research", "synthesizing", "Synthesizing research...")

        synthesis = await self._synthesize_research(
            session_sources=session_sources,
            kb_sources=kb_sources,
            web_sources=web_sources,
        )

        duration_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
        logger.info(f"Research phase completed in {duration_ms:.1f}ms")

        return ResearchFindings(
            session_sources=session_sources,
            knowledge_base_sources=kb_sources,
            web_sources=web_sources,
            key_themes=synthesis.get("key_themes", []),
            topic_summary=synthesis.get("summary", ""),
        )

    async def _scrape_url(self, url: str) -> Optional[Dict[str, Any]]:
        """Scrape a URL using Firecrawl"""
        if not self.firecrawl_api_key:
            logger.warning("No Firecrawl API key configured")
            return None

        try:
            from app.services.firecrawl_scraper import FirecrawlScraper
            scraper = FirecrawlScraper(self.firecrawl_api_key)
            result = await scraper.scrape_url(url)
            await scraper.close()

            return {
                "url": result.get("url", url),
                "title": result.get("metadata", {}).get("title", ""),
                "content": result.get("markdown", ""),
            }
        except Exception as e:
            logger.error(f"URL scraping failed: {e}")
            return None

    async def _search_knowledge_base(
        self,
        query: str,
        max_results: int = 5,
    ) -> List[Dict[str, Any]]:
        """Search the client's knowledge base"""
        try:
            client_sb = await self.get_client_supabase()

            # Get datasets for this client
            datasets = client_sb.table("datasets").select("id").execute()
            dataset_ids = [d["id"] for d in (datasets.data or [])]

            if not dataset_ids:
                return []

            # Use RAG citations service for search
            from app.integrations.rag.citations_service import rag_citations_service

            result = await rag_citations_service.retrieve_with_citations(
                query=query,
                client_id=self.client_id,
                dataset_ids=dataset_ids,
                top_k=max_results,
                max_chunks=max_results,
            )

            sources = []
            for citation in result.citations:
                sources.append({
                    "type": "knowledge_base",
                    "title": citation.title,
                    "url": citation.source_url,
                    "content": citation.content[:1000],  # Truncate for efficiency
                    "similarity": citation.similarity,
                    "weight": 0.8,
                })

            return sources

        except Exception as e:
            logger.error(f"Knowledge base search failed: {e}")
            return []

    async def _perplexity_search(
        self,
        query: str,
    ) -> List[Dict[str, Any]]:
        """Search the web using Perplexity API"""
        if not self.perplexity_api_key:
            return []

        try:
            # Create search prompt
            search_prompt = f"""Research the following topic and provide key facts, statistics, and insights:

Topic: {query[:500]}

Focus on:
1. Key facts and statistics
2. Expert opinions
3. Recent developments
4. Multiple perspectives

Provide citations for all claims."""

            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    "https://api.perplexity.ai/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.perplexity_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "sonar-pro",
                        "messages": [
                            {"role": "user", "content": search_prompt}
                        ],
                    }
                )

                if response.status_code != 200:
                    logger.error(f"Perplexity API error: {response.status_code}")
                    return []

                data = response.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                citations = data.get("citations", [])

                sources = [{
                    "type": "web_search",
                    "content": content,
                    "citations": citations,
                    "weight": 0.6,
                }]

                return sources

        except Exception as e:
            logger.error(f"Perplexity search failed: {e}")
            return []

    async def _synthesize_research(
        self,
        session_sources: List[Dict[str, Any]],
        kb_sources: List[Dict[str, Any]],
        web_sources: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Use Claude to synthesize research findings"""

        all_sources = []
        for s in session_sources:
            # For transcripts and knowledge base documents, include much more content (up to 15000 chars)
            # These are the primary source materials for generation
            source_type = s.get('type', 'unknown')
            if source_type in ('mp3_transcript', 'knowledge_base_document'):
                # Include more of the primary source content
                content_limit = 15000
            else:
                content_limit = 3000
            all_sources.append(f"[Session Source - {source_type}]\n{s.get('content', '')[:content_limit]}")
        for s in kb_sources:
            all_sources.append(f"[Knowledge Base - {s.get('title', 'Untitled')}]\n{s['content'][:1000]}")
        for s in web_sources:
            all_sources.append(f"[Web Research]\n{s['content'][:1500]}")

        prompt = f"""You are a Research Lead synthesizing source materials for article creation.

Review the following sources and identify:
1. The main topic and focus
2. 3-5 key themes that emerge
3. Important facts, statistics, and quotes to reference

SOURCES:
{chr(10).join(all_sources)}

Respond in JSON format:
{{
    "summary": "A 2-3 sentence summary of the main topic",
    "key_themes": ["theme1", "theme2", "theme3"],
    "key_facts": ["fact1", "fact2"],
    "notable_quotes": ["quote1"],
    "recommended_angles": ["angle1", "angle2"]
}}"""

        try:
            content = await self._llm_chat(prompt, max_tokens=1500)

            # Extract JSON from response
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                return json.loads(json_match.group())

            return {"summary": content, "key_themes": []}

        except Exception as e:
            logger.error(f"Research synthesis failed: {e}")
            return {"summary": "", "key_themes": []}

    # =========================================================================
    # PHASE 2: CONTENT ARCHITECT
    # =========================================================================

    async def execute_architecture_phase(
        self,
        research: ResearchFindings,
        config: ContentCatalystConfig,
        wordpress_urls: Optional[List[str]] = None,
        progress_callback: Optional[callable] = None,
    ) -> ArticleArchitecture:
        """
        Content Architect phase - Designs structure and two different article angles.
        Also identifies internal linking opportunities for WordPress clients.
        """
        start_time = datetime.now(timezone.utc)

        if progress_callback:
            progress_callback("architecture", "starting", "Designing article structure...")

        # Build context from research - truncate long content for LLM context limits
        # Create a copy with truncated content for the prompt
        session_sources_truncated = []
        for source in research.session_sources:
            source_copy = source.copy()
            content = source_copy.get("content", "")
            source_type = source_copy.get("type", "")
            # For transcripts and knowledge base documents, include more content (up to 12000 chars)
            # These are the primary source materials for generation
            if source_type in ("mp3_transcript", "knowledge_base_document"):
                source_copy["content"] = content[:12000] + ("..." if len(content) > 12000 else "")
            else:
                source_copy["content"] = content[:3000] + ("..." if len(content) > 3000 else "")
            session_sources_truncated.append(source_copy)

        research_context = f"""
TOPIC SUMMARY: {research.topic_summary}

KEY THEMES: {', '.join(research.key_themes)}

SESSION SOURCES (PRIMARY CONTENT TO BASE ARTICLE ON):
{json.dumps(session_sources_truncated, indent=2)}

KNOWLEDGE BASE SOURCES:
{json.dumps(research.knowledge_base_sources, indent=2)}

WEB SOURCES:
{json.dumps(research.web_sources, indent=2)}
"""

        style_instruction = ""
        if config.style_prompt:
            style_instruction = f"\n\nSTYLE GUIDANCE FROM USER:\n{config.style_prompt}"

        internal_links_instruction = ""
        if wordpress_urls:
            internal_links_instruction = f"""

INTERNAL LINKING OPPORTUNITY:
The following URLs are from the client's website and can be used for internal linking:
{json.dumps(wordpress_urls[:20], indent=2)}

IMPORTANT: Use these EXACT URLs for internal links. Do NOT use sidekickforge.com or any other domain.
Select 2-3 relevant URLs and specify natural anchor text (keywords from the content, not explicit CTAs)."""

        prompt = f"""You are a Content Architect designing article structures.

You must create TWO different article angles/approaches for the same topic. Each angle should:
- Appeal to different reader interests or perspectives
- Have a unique hook and framing
- Share the same core research but present it differently

RESEARCH CONTEXT:
{research_context}

TARGET WORD COUNT: {config.target_word_count} words per article
{style_instruction}
{internal_links_instruction}

Design two article architectures in JSON format:
{{
    "angle_1": {{
        "title": "Compelling title for angle 1",
        "hook": "Opening hook that grabs attention",
        "outline": [
            "Section 1: Introduction with hook",
            "Section 2: Key point A",
            "Section 3: Key point B with examples",
            "Section 4: Counter-arguments or nuances",
            "Section 5: Conclusion with call to action"
        ],
        "target_audience": "Who this angle appeals to"
    }},
    "angle_2": {{
        "title": "Different compelling title for angle 2",
        "hook": "Different opening hook",
        "outline": [
            "Section 1: Alternative introduction",
            "Section 2: Different framing of key points",
            "..."
        ],
        "target_audience": "Different target reader"
    }},
    "shared_research_points": [
        "Key fact or stat to cite in both",
        "Important quote to reference"
    ],
    "internal_links": [
        {{"text": "anchor text", "url": "matching WordPress URL"}}
    ]
}}"""

        try:
            logger.info(f"Architecture phase prompt length: {len(prompt)} chars")
            content = await self._llm_chat(prompt, max_tokens=2000)
            logger.info(f"Architecture phase LLM response length: {len(content) if content else 0} chars")

            if not content or len(content.strip()) == 0:
                logger.error("Architecture phase received empty response from LLM")
                raise ValueError("LLM returned empty response for architecture phase")

            # Try to extract JSON - handle markdown code blocks
            json_content = content

            # Strip markdown code blocks if present
            if "```json" in content:
                json_match = re.search(r'```json\s*([\s\S]*?)\s*```', content)
                if json_match:
                    json_content = json_match.group(1)
            elif "```" in content:
                json_match = re.search(r'```\s*([\s\S]*?)\s*```', content)
                if json_match:
                    json_content = json_match.group(1)

            # Find the JSON object
            json_match = re.search(r'\{[\s\S]*\}', json_content)

            if json_match:
                try:
                    arch_data = json.loads(json_match.group())
                except json.JSONDecodeError as je:
                    logger.error(f"JSON decode error: {je}")
                    logger.error(f"Raw content (first 1000 chars): {content[:1000]}")
                    raise ValueError(f"Failed to parse architecture JSON: {je}")

                duration_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
                logger.info(f"Architecture phase completed in {duration_ms:.1f}ms")

                return ArticleArchitecture(
                    angle_1_title=arch_data.get("angle_1", {}).get("title", ""),
                    angle_1_hook=arch_data.get("angle_1", {}).get("hook", ""),
                    angle_1_outline=arch_data.get("angle_1", {}).get("outline", []),
                    angle_2_title=arch_data.get("angle_2", {}).get("title", ""),
                    angle_2_hook=arch_data.get("angle_2", {}).get("hook", ""),
                    angle_2_outline=arch_data.get("angle_2", {}).get("outline", []),
                    shared_research_points=arch_data.get("shared_research_points", []),
                    internal_links=arch_data.get("internal_links", []),
                )

            logger.error(f"No JSON found in architecture response. Raw content (first 1000 chars): {content[:1000]}")
            raise ValueError("Failed to parse architecture JSON - no JSON object found")

        except Exception as e:
            logger.error(f"Architecture phase failed: {e}")
            raise

    # =========================================================================
    # PHASE 3: GHOSTWRITER
    # =========================================================================

    async def execute_drafting_phase(
        self,
        research: ResearchFindings,
        architecture: ArticleArchitecture,
        config: ContentCatalystConfig,
        progress_callback: Optional[callable] = None,
    ) -> Tuple[ArticleDraft, ArticleDraft]:
        """
        Ghostwriter phase - Drafts two article variations based on the architecture.
        """
        start_time = datetime.now(timezone.utc)

        if progress_callback:
            progress_callback("drafting", "starting", "Writing first article draft...")

        # Draft article 1
        draft_1 = await self._write_draft(
            angle_num=1,
            title=architecture.angle_1_title,
            hook=architecture.angle_1_hook,
            outline=architecture.angle_1_outline,
            research=research,
            architecture=architecture,
            config=config,
        )

        if progress_callback:
            progress_callback("drafting", "draft_2", "Writing second article draft...")

        # Draft article 2
        draft_2 = await self._write_draft(
            angle_num=2,
            title=architecture.angle_2_title,
            hook=architecture.angle_2_hook,
            outline=architecture.angle_2_outline,
            research=research,
            architecture=architecture,
            config=config,
        )

        duration_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
        logger.info(f"Drafting phase completed in {duration_ms:.1f}ms")

        return draft_1, draft_2

    async def _write_draft(
        self,
        angle_num: int,
        title: str,
        hook: str,
        outline: List[str],
        research: ResearchFindings,
        architecture: ArticleArchitecture,
        config: ContentCatalystConfig,
    ) -> ArticleDraft:
        """Write a single article draft"""

        # Compile all sources for citation
        # For transcripts, include much more content as this is the primary source
        sources_text = []
        for i, s in enumerate(research.session_sources):
            source_type = s.get('type', 'unknown')
            if source_type == 'mp3_transcript':
                # Include more of transcript for drafting - this is the main content
                sources_text.append(f"Source {i+1} (Transcript): {s['content'][:10000]}")
            else:
                sources_text.append(f"Source {i+1}: {s['content'][:2000]}")
        for i, s in enumerate(research.knowledge_base_sources):
            sources_text.append(f"KB Source {i+1} ({s.get('title', 'Untitled')}): {s['content'][:500]}")
        for i, s in enumerate(research.web_sources):
            sources_text.append(f"Web Source {i+1}: {s['content'][:500]}")

        # SIMPLIFIED APPROACH: Trust the example, minimize instructions
        # The example IS the instruction - let it speak for itself

        # Build internal links section (keep this minimal)
        internal_links_note = ""
        if architecture.internal_links:
            links = [f"[{l['text']}]({l['url']})" for l in architecture.internal_links[:3]]
            internal_links_note = f"\n\nInclude these links naturally by linking keywords (not \"check out our article\"): {', '.join(links)}"

        # Build the voice/example section - THIS IS THE CORE
        voice_example = ""
        if self.agent_config.example_writing:
            voice_example = f"""You are ghostwriting as this author. Match their voice exactly.

EXAMPLE OF THE AUTHOR'S WRITING:
---
{self.agent_config.example_writing[:6000]}
---

Write as this author would. Not a "professional" version. THIS voice."""
        elif self.agent_config.default_style_prompt:
            voice_example = f"""Write in this voice:
{self.agent_config.default_style_prompt}"""
        else:
            voice_example = "Write in an engaging, direct voice."

        # Additional style from dropdown (secondary)
        if config.style_prompt:
            voice_example += f"\n\nAdditional note: {config.style_prompt}"

        # RADICALLY SIMPLIFIED PROMPT
        prompt = f"""{voice_example}

Write a ~{config.target_word_count} word article.

Title: {title}
Hook: {hook}

Use this outline as a loose guide (no section headers in output):
{chr(10).join(f"- {section}" for section in outline)}

Key points to include:
{chr(10).join(f"- {point}" for point in architecture.shared_research_points)}
{internal_links_note}

Source material:
{chr(10).join(sources_text)}

Format: H1 title, then flowing prose with blank lines between short paragraphs. No subheadings.

Write the article now, matching the example voice:"""

        try:
            content = await self._llm_chat(prompt, max_tokens=4000)

            word_count = len(content.split())

            # Extract sources cited (simplified - look for inline citations)
            sources_cited = re.findall(r'\[Source \d+\]|\[KB Source \d+\]|\[Web Source \d+\]', content)

            return ArticleDraft(
                title=title,
                content=content,
                word_count=word_count,
                sources_cited=list(set(sources_cited)),
            )

        except Exception as e:
            logger.error(f"Draft writing failed: {e}")
            raise

    # =========================================================================
    # PHASE 4: INTEGRITY OFFICER
    # =========================================================================

    async def execute_integrity_phase(
        self,
        draft_1: ArticleDraft,
        draft_2: ArticleDraft,
        research: ResearchFindings,
        progress_callback: Optional[callable] = None,
    ) -> IntegrityReport:
        """
        Integrity Officer phase - Verifies accuracy and sources.
        """
        start_time = datetime.now(timezone.utc)

        if progress_callback:
            progress_callback("integrity", "starting", "Verifying accuracy...")

        # Compile source materials for verification
        all_sources = []
        for s in research.session_sources:
            all_sources.append(s['content'][:1000])
        for s in research.knowledge_base_sources:
            all_sources.append(f"{s.get('title', '')}: {s['content'][:800]}")
        for s in research.web_sources:
            all_sources.append(s['content'][:1000])

        prompt = f"""You are an Integrity Officer reviewing articles for factual accuracy.

Review both article drafts against the source materials and identify:
1. Any claims not supported by the provided sources
2. Potential factual errors or misrepresentations
3. Sources that are properly cited
4. Sources that cannot be verified
5. Overall factual accuracy assessment

ARTICLE 1:
{draft_1.content[:3000]}

ARTICLE 2:
{draft_2.content[:3000]}

SOURCE MATERIALS:
{chr(10).join(all_sources)}

Respond in JSON format:
{{
    "draft_1_issues": [
        {{"claim": "the claim made", "issue": "why it's problematic", "severity": "low|medium|high"}}
    ],
    "draft_2_issues": [...],
    "sources_verified": ["list of claims that are well-sourced"],
    "sources_unverifiable": ["claims that lack clear source support"],
    "factual_accuracy_score": 0.85,
    "recommendations": [
        "Specific recommendation to improve accuracy"
    ]
}}"""

        try:
            content = await self._llm_chat(prompt, max_tokens=2000)

            json_match = re.search(r'\{[\s\S]*\}', content)

            if json_match:
                report_data = json.loads(json_match.group())

                duration_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
                logger.info(f"Integrity phase completed in {duration_ms:.1f}ms")

                return IntegrityReport(
                    draft_1_issues=report_data.get("draft_1_issues", []),
                    draft_2_issues=report_data.get("draft_2_issues", []),
                    sources_verified=report_data.get("sources_verified", []),
                    sources_unverifiable=report_data.get("sources_unverifiable", []),
                    factual_accuracy_score=report_data.get("factual_accuracy_score", 0.0),
                    recommendations=report_data.get("recommendations", []),
                )

            raise ValueError("Failed to parse integrity report JSON")

        except Exception as e:
            logger.error(f"Integrity phase failed: {e}")
            raise

    # =========================================================================
    # PHASE 5: FINAL POLISHER
    # =========================================================================

    async def execute_polishing_phase(
        self,
        draft_1: ArticleDraft,
        draft_2: ArticleDraft,
        integrity_report: IntegrityReport,
        config: ContentCatalystConfig,
        progress_callback: Optional[callable] = None,
    ) -> Tuple[str, str]:
        """
        Final Polisher phase - Refines prose and enforces word count.
        Will iterate up to 3 times to hit word count targets.
        """
        start_time = datetime.now(timezone.utc)

        if progress_callback:
            progress_callback("polishing", "starting", "Polishing article 1...")

        # Polish article 1
        article_1 = await self._polish_article(
            draft=draft_1,
            issues=integrity_report.draft_1_issues,
            config=config,
            progress_callback=progress_callback,
        )

        if progress_callback:
            progress_callback("polishing", "article_2", "Polishing article 2...")

        # Polish article 2
        article_2 = await self._polish_article(
            draft=draft_2,
            issues=integrity_report.draft_2_issues,
            config=config,
            progress_callback=progress_callback,
        )

        duration_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
        logger.info(f"Polishing phase completed in {duration_ms:.1f}ms")

        return article_1, article_2

    async def _polish_article(
        self,
        draft: ArticleDraft,
        issues: List[Dict[str, Any]],
        config: ContentCatalystConfig,
        progress_callback: Optional[callable] = None,
    ) -> str:
        """Polish a single article with word count enforcement"""

        target = config.target_word_count
        tolerance = config.target_word_count * (self.word_count_tolerance_percent / 100)
        min_words = target - tolerance
        max_words = target + tolerance

        current_content = draft.content
        current_word_count = draft.word_count

        # Build issues context
        issues_text = ""
        if issues:
            issues_text = "\nFix these issues: " + "; ".join(
                f"{i['claim']}" for i in issues[:3]
            )

        # SIMPLIFIED: Build voice example for polishing
        voice_example = ""
        if self.agent_config.example_writing:
            voice_example = f"""This article should sound like this author:

---
{self.agent_config.example_writing[:5000]}
---"""
        elif self.agent_config.default_style_prompt:
            voice_example = f"Voice: {self.agent_config.default_style_prompt}"

        for iteration in range(self.max_word_count_iterations):
            # Check if word count is within tolerance
            if min_words <= current_word_count <= max_words:
                logger.info(f"Word count {current_word_count} within target range")
                break

            word_adjustment = ""
            if current_word_count < min_words:
                word_adjustment = f"Add ~{int(min_words - current_word_count)} words."
            elif current_word_count > max_words:
                word_adjustment = f"Cut ~{int(current_word_count - max_words)} words."

            # RADICALLY SIMPLIFIED POLISHING PROMPT
            prompt = f"""{voice_example}

Polish this article to match the example voice above. {word_adjustment}{issues_text}

Current article ({current_word_count} words, target {int(min_words)}-{int(max_words)}):

{current_content}

Keep: H1 title, flowing prose, short paragraphs, blank lines between them, no subheadings.
Preserve all markdown links exactly.

Output the polished article matching the example voice:"""

            try:
                current_content = await self._llm_chat(prompt, max_tokens=4000)

                current_word_count = len(current_content.split())

                logger.info(f"Polishing iteration {iteration + 1}: {current_word_count} words")

            except Exception as e:
                logger.error(f"Polishing iteration {iteration + 1} failed: {e}")
                break

        return current_content

    # =========================================================================
    # ORCHESTRATION
    # =========================================================================

    async def run_full_pipeline(
        self,
        config: ContentCatalystConfig,
        run_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        session_id: Optional[str] = None,
        wordpress_urls: Optional[List[str]] = None,
        progress_callback: Optional[callable] = None,
    ) -> Tuple[str, str, str]:
        """
        Run the complete Content Catalyst pipeline.

        Returns:
            Tuple of (run_id, article_1, article_2)
        """
        try:
            # Create run if not provided
            if not run_id:
                run_id = await self.create_run(
                    config=config,
                    agent_id=agent_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    session_id=session_id,
                )

            # Update status to running
            await self.update_phase(
                run_id=run_id,
                phase=ContentCatalystPhase.INPUT,
                output={"source_type": config.source_type.value},
                status="running",
            )

            # Phase 1: Research
            if progress_callback:
                progress_callback("research", "starting", "Phase 1: Research Lead")

            research = await self.execute_research_phase(config, progress_callback)
            await self.update_phase(
                run_id=run_id,
                phase=ContentCatalystPhase.RESEARCH,
                output=asdict(research),
            )

            # Phase 2: Architecture
            if progress_callback:
                progress_callback("architecture", "starting", "Phase 2: Content Architect")

            # Extract source URLs from knowledge base results for internal linking
            # These are the original source website URLs (e.g., client's WordPress site)
            kb_source_urls = []
            for source in research.knowledge_base_sources:
                url = source.get("url")
                if url and url not in kb_source_urls:
                    kb_source_urls.append(url)

            # Use KB source URLs if wordpress_urls not explicitly provided
            internal_link_urls = wordpress_urls if wordpress_urls else kb_source_urls

            architecture = await self.execute_architecture_phase(
                research=research,
                config=config,
                wordpress_urls=internal_link_urls,
                progress_callback=progress_callback,
            )
            await self.update_phase(
                run_id=run_id,
                phase=ContentCatalystPhase.ARCHITECTURE,
                output=asdict(architecture),
            )

            # Phase 3: Drafting
            if progress_callback:
                progress_callback("drafting", "starting", "Phase 3: Ghostwriter")

            draft_1, draft_2 = await self.execute_drafting_phase(
                research=research,
                architecture=architecture,
                config=config,
                progress_callback=progress_callback,
            )
            await self.update_phase(
                run_id=run_id,
                phase=ContentCatalystPhase.DRAFTING,
                output={
                    "draft_1": asdict(draft_1),
                    "draft_2": asdict(draft_2),
                },
            )

            # Phase 4: Integrity
            if progress_callback:
                progress_callback("integrity", "starting", "Phase 4: Integrity Officer")

            integrity_report = await self.execute_integrity_phase(
                draft_1=draft_1,
                draft_2=draft_2,
                research=research,
                progress_callback=progress_callback,
            )
            await self.update_phase(
                run_id=run_id,
                phase=ContentCatalystPhase.INTEGRITY,
                output=asdict(integrity_report),
            )

            # Phase 5: Polishing
            if progress_callback:
                progress_callback("polishing", "starting", "Phase 5: Final Polisher")

            article_1, article_2 = await self.execute_polishing_phase(
                draft_1=draft_1,
                draft_2=draft_2,
                integrity_report=integrity_report,
                config=config,
                progress_callback=progress_callback,
            )

            # Save final articles
            await self.save_articles(run_id, article_1, article_2)

            if progress_callback:
                progress_callback("complete", "done", "Content Catalyst complete!")

            logger.info(f"Content Catalyst run {run_id} completed successfully")
            return run_id, article_1, article_2

        except Exception as e:
            logger.error(f"Content Catalyst pipeline failed: {e}")
            if run_id:
                await self.update_phase(
                    run_id=run_id,
                    phase=ContentCatalystPhase.INPUT,
                    output={"error": str(e)},
                    status="failed",
                    error=str(e),
                )
            raise


# Factory function
async def get_content_catalyst_service(
    client_id: str,
    agent_id: Optional[str] = None,
) -> ContentCatalystService:
    """
    Get a ContentCatalystService instance configured for a specific client and optionally an agent.

    If agent_id is provided, agent-specific Content Catalyst configuration will be loaded
    from the agent's tools_config.content_catalyst field.
    """
    from app.core.dependencies import get_client_service

    try:
        client_service = get_client_service()
        client = await client_service.get_client(client_id)

        if not client:
            raise ValueError(f"Client {client_id} not found")

        # Get API keys from client settings
        api_keys = {}
        if client.settings and client.settings.api_keys:
            api_keys = {
                "groq_api_key": getattr(client.settings.api_keys, "groq_api_key", None),
                "openai_api_key": getattr(client.settings.api_keys, "openai_api_key", None),
                "anthropic_api_key": getattr(client.settings.api_keys, "anthropic_api_key", None),
                "deepinfra_api_key": getattr(client.settings.api_keys, "deepinfra_api_key", None),
                "cerebras_api_key": getattr(client.settings.api_keys, "cerebras_api_key", None),
                "perplexity_api_key": getattr(client.settings.api_keys, "perplexity_api_key", None),
                "deepgram_api_key": getattr(client.settings.api_keys, "deepgram_api_key", None),
            }

        # Get Firecrawl key from client
        firecrawl_api_key = getattr(client, "firecrawl_api_key", None)

        # Get Perplexity key from client level if not in settings
        if not api_keys.get("perplexity_api_key"):
            api_keys["perplexity_api_key"] = getattr(client, "perplexity_api_key", None)

        # Determine LLM provider based on available keys (priority order)
        # Note: Agent's configured LLM takes precedence if set (see below)
        llm_provider = "groq"  # Default
        llm_model = None  # Will be set from agent config if available
        if api_keys.get("groq_api_key"):
            llm_provider = "groq"
        elif api_keys.get("cerebras_api_key"):
            llm_provider = "cerebras"
        elif api_keys.get("openai_api_key"):
            llm_provider = "openai"
        elif api_keys.get("anthropic_api_key"):
            llm_provider = "anthropic"
        elif api_keys.get("deepinfra_api_key"):
            llm_provider = "deepinfra"

        # Load agent-specific configuration if agent_id provided
        # This includes Content Catalyst style config AND the agent's LLM settings
        agent_config = ContentCatalystAgentConfig()
        if agent_id:
            try:
                # Query agent directly from client's Supabase by ID (not slug)
                from app.utils.supabase_credentials import SupabaseCredentialManager
                client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
                from supabase import create_client
                client_sb = create_client(client_url, client_key)

                # Get agent by ID - include voice_settings for LLM config
                result = client_sb.table("agents").select("tools_config, voice_settings").eq("id", agent_id).maybe_single().execute()

                if result.data:
                    # Load Content Catalyst style config
                    if result.data.get("tools_config"):
                        cc_config = result.data["tools_config"].get("content_catalyst", {})
                        if cc_config:
                            agent_config = ContentCatalystAgentConfig(
                                default_style_prompt=cc_config.get("default_style_prompt", ""),
                                example_writing=cc_config.get("example_writing", ""),
                            )
                            logger.info(f"Loaded Content Catalyst config for agent {agent_id}: style_len={len(agent_config.default_style_prompt)}, example_len={len(agent_config.example_writing)}")
                        else:
                            logger.info(f"Agent {agent_id} has no content_catalyst config in tools_config")

                    # Use agent's configured LLM provider/model if available
                    voice_settings = result.data.get("voice_settings", {})
                    if voice_settings:
                        agent_llm_provider = voice_settings.get("llm_provider")
                        agent_llm_model = voice_settings.get("llm_model")
                        if agent_llm_provider and api_keys.get(f"{agent_llm_provider}_api_key"):
                            llm_provider = agent_llm_provider
                            llm_model = agent_llm_model
                            logger.info(f"Using agent's configured LLM: {llm_provider} / {llm_model}")
                else:
                    logger.warning(f"Agent {agent_id} not found")
            except Exception as agent_err:
                logger.warning(f"Could not load agent config: {agent_err}")

        return ContentCatalystService(
            client_id=client_id,
            llm_provider=llm_provider,
            llm_model=llm_model,
            groq_api_key=api_keys.get("groq_api_key"),
            openai_api_key=api_keys.get("openai_api_key"),
            anthropic_api_key=api_keys.get("anthropic_api_key"),
            deepinfra_api_key=api_keys.get("deepinfra_api_key"),
            cerebras_api_key=api_keys.get("cerebras_api_key"),
            perplexity_api_key=api_keys.get("perplexity_api_key"),
            firecrawl_api_key=firecrawl_api_key,
            deepgram_api_key=api_keys.get("deepgram_api_key"),
            agent_config=agent_config,
        )

    except Exception as e:
        logger.error(f"Failed to create Content Catalyst service for client {client_id}: {e}")
        raise

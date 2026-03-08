#!/usr/bin/env python3
"""
Agent Context Manager
Dynamically enhances agent system prompts with user profiles, RAG searches, and conversation history
Uses remote embedding services only - no local models or vector stores
"""
import asyncio
import logging
import json
import os
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta

# ------------------------------------------------------------------
# Context truncation defaults (override via ENV when needed)
# ------------------------------------------------------------------

MAX_KNOWLEDGE_RESULTS = int(os.getenv("CONTEXT_MAX_KNOWLEDGE_RESULTS", "3"))
MAX_CONVERSATION_RESULTS = int(os.getenv("CONTEXT_MAX_CONVERSATION_RESULTS", "3"))
MAX_PROFILE_FIELD_CHARS = int(os.getenv("CONTEXT_MAX_PROFILE_FIELD_CHARS", "500"))
MAX_KNOWLEDGE_EXCERPT_CHARS = int(os.getenv("CONTEXT_MAX_KNOWLEDGE_EXCERPT_CHARS", "600"))
MAX_CONVERSATION_SNIPPET_CHARS = int(os.getenv("CONTEXT_MAX_CONVERSATION_SNIPPET_CHARS", "450"))
CONTEXT_MARKDOWN_CHAR_BUDGET = int(os.getenv("CONTEXT_MARKDOWN_CHAR_BUDGET", "20000"))
import httpx
import time

logger = logging.getLogger(__name__)


class LocalBGEEmbedder:
    """Client for local BGE-M3 embedding service (on-premise)"""

    def __init__(self, service_url: str = None):
        self.service_url = service_url or os.getenv("BGE_SERVICE_URL", "http://bge-service:8090")
        self.model = "bge-m3"
        self.client = httpx.AsyncClient(timeout=60.0)  # Longer timeout for local inference
        logger.info(f"Initialized LocalBGEEmbedder with service URL: {self.service_url}")

    async def create_embedding(self, text: str) -> List[float]:
        """Create embeddings using local BGE service"""
        try:
            response = await self.client.post(
                f"{self.service_url}/embed",
                json={"texts": [text], "model": self.model},
            )

            if response.status_code == 200:
                result = response.json()
                embeddings = result.get("embeddings", [])
                if embeddings and len(embeddings) > 0:
                    logger.debug(f"Local BGE embedding generated in {result.get('processing_time_ms', 0):.0f}ms")
                    return embeddings[0]
                raise ValueError("Empty embedding response from BGE service")
            elif response.status_code == 503:
                raise ValueError("BGE service not ready - model may still be loading")
            else:
                raise ValueError(f"BGE service error: {response.status_code} - {response.text}")
        except httpx.ConnectError as e:
            raise ValueError(f"Cannot connect to BGE service at {self.service_url}: {e}")
        except httpx.TimeoutException as e:
            raise ValueError(f"BGE service timeout: {e}")

    async def create_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """Create embeddings for multiple texts in a single batch (more efficient)"""
        if not texts:
            return []

        try:
            response = await self.client.post(
                f"{self.service_url}/embed",
                json={"texts": texts, "model": self.model},
            )

            if response.status_code == 200:
                result = response.json()
                embeddings = result.get("embeddings", [])
                logger.debug(f"Local BGE batch embedding ({len(texts)} texts) in {result.get('processing_time_ms', 0):.0f}ms")
                return embeddings
            else:
                raise ValueError(f"BGE service error: {response.status_code} - {response.text}")
        except httpx.ConnectError as e:
            raise ValueError(f"Cannot connect to BGE service at {self.service_url}: {e}")

    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()


class RemoteEmbedder:
    """Simple client for remote embedding services"""

    def __init__(self, provider: str, api_key: str, model: str = None, dimension: int = None):
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.dimension = dimension  # For models that support variable dimensions (e.g., Qwen3)
        self.client = httpx.AsyncClient(timeout=30.0)
        
    async def create_embedding(self, text: str) -> List[float]:
        """Create embeddings using the configured remote service"""
        if self.provider == 'siliconflow':
            return await self._siliconflow_embedding(text)
        elif self.provider == 'openai':
            return await self._openai_embedding(text)
        elif self.provider == 'novita':
            return await self._novita_embedding(text)
        else:
            raise ValueError(f"Unsupported embedding provider: {self.provider}")
    
    async def _siliconflow_embedding(self, text: str) -> List[float]:
        """Generate embeddings using SiliconFlow API"""
        logger.info(f"[EMBED] SiliconFlow embedding request: model={self.model}, dimension={self.dimension}, text_len={len(text)}")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        if not self.model:
            raise ValueError("No model specified for SiliconFlow embeddings")

        data = {
            "model": self.model,
            "input": text
        }

        # Add dimensions parameter for models that support variable dimensions (e.g., Qwen3)
        if self.dimension:
            data["dimensions"] = self.dimension

        response = await self.client.post(
            "https://api.siliconflow.com/v1/embeddings",
            headers=headers,
            json=data
        )

        if response.status_code == 200:
            result = response.json()
            if 'data' in result and len(result['data']) > 0:
                embedding = result['data'][0]['embedding']
                logger.debug(f"SiliconFlow embedding generated: dim={len(embedding)}")
                return embedding
            else:
                raise ValueError(f"SiliconFlow API returned unexpected response structure: {list(result.keys())}")
        else:
            raise ValueError(f"SiliconFlow API error: {response.status_code} - {response.text}")
    
    async def _openai_embedding(self, text: str) -> List[float]:
        """Generate embeddings using OpenAI API"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": self.model or "text-embedding-3-small",
            "input": text
        }
        
        response = await self.client.post(
            "https://api.openai.com/v1/embeddings",
            headers=headers,
            json=data
        )
        
        if response.status_code == 200:
            result = response.json()
            if 'data' in result and len(result['data']) > 0:
                return result['data'][0]['embedding']
        else:
            raise ValueError(f"OpenAI API error: {response.status_code} - {response.text}")
    
    async def _novita_embedding(self, text: str) -> List[float]:
        """Generate embeddings using Novita API"""
        # Implementation would go here following Novita's API structure
        raise NotImplementedError("Novita embedding provider not yet implemented")
    
    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()


class AgentContextManager:
    """Manages dynamic context generation for AI agents using remote services"""
    
    def __init__(
        self, 
        supabase_client,
        agent_config: Dict[str, Any],
        user_id: str,
        client_id: str,
        api_keys: Optional[Dict[str, str]] = None
    ):
        """
        Initialize the context manager
        
        Args:
            supabase_client: Initialized Supabase client for the specific client
            agent_config: Agent configuration including system prompt
            user_id: User identifier for profile lookup
            client_id: Client identifier for multi-tenant isolation
            api_keys: API keys for embedding services
        """
        self.supabase = supabase_client
        self.agent_config = agent_config
        self.user_id = user_id
        self.client_id = client_id
        self.api_keys = api_keys or {}
        
        # Initialize remote embedder - FAIL FAST if not configured
        self.embedder = self._initialize_embedder()
        
        logger.info(f"Initialized AgentContextManager for user {user_id}, client {client_id}")
        
        # Detect database schema
        self._detect_schema()
    
    def _initialize_embedder(self):
        """Initialize the embedding client based on configuration.

        Returns either LocalBGEEmbedder (for on-premise) or RemoteEmbedder (for cloud APIs).
        """
        # Get embedding provider from agent config - NO DEFAULTS
        embedding_config = self.agent_config.get('embedding', {})

        if not embedding_config:
            # Log available keys to help debug
            logger.warning(f"No embedding configuration found. Available keys in agent_config: {list(self.agent_config.keys())}")
            raise ValueError("No embedding configuration found. Embedding provider and model must be configured.")

        provider = embedding_config.get('provider', '').lower()
        if not provider:
            raise ValueError("No embedding provider specified in configuration. Required field: embedding.provider")

        # Check for local/on-prem provider first
        if provider in ('local', 'on-prem', 'bge-local', 'bge', 'bge-m3'):
            # Local BGE-M3 embeddings via sidecar service
            service_url = embedding_config.get('service_url') or os.getenv("BGE_SERVICE_URL")
            logger.info(f"Initializing Local BGE-M3 embedder (on-premise) at {service_url or 'default'}")
            return LocalBGEEmbedder(service_url=service_url)

        # Remote provider - needs model and API key
        model = embedding_config.get('model') or embedding_config.get('document_model') or embedding_config.get('conversation_model')
        if not model:
            raise ValueError(f"No embedding model specified for provider {provider}. Required field: embedding.model or embedding.document_model")

        # Map provider to API key name
        api_key_mapping = {
            'siliconflow': 'siliconflow_api_key',
            'openai': 'openai_api_key',
            'novita': 'novita_api_key'
        }

        # Get the appropriate API key
        api_key_name = api_key_mapping.get(provider)
        if not api_key_name:
            raise ValueError(f"Unsupported embedding provider: {provider}. Supported: local, siliconflow, openai, novita")

        api_key = self.api_keys.get(api_key_name)
        if not api_key:
            raise ValueError(f"No API key found for embedding provider {provider}. Required key: {api_key_name}")

        # Get dimension if specified (for models that support variable dimensions like Qwen3)
        dimension = embedding_config.get('dimension')

        logger.info(f"Initializing {provider} embedder with model: {model or 'default'}, dimension: {dimension or 'default'}")
        return RemoteEmbedder(provider, api_key, model, dimension)
    
    def _detect_schema(self):
        """
        NO FALLBACKS: Assume required RPC functions exist.
        If they don't, the RPC calls will fail with clear errors.
        """
        logger.info("🔍 Schema detection skipped - assuming required functions exist")
        
        # We assume these functions exist and are properly implemented:
        # - match_documents (with proper agent filtering)
        # - match_conversation_transcripts_secure (without unsafe COALESCE)
        
        # No schema detection needed - fail fast if functions don't exist
    
    async def build_initial_context(self, user_id: str) -> Dict[str, Any]:
        """
        Build initial context with only static data (user profile and base prompt).
        This is called when the agent starts, before any user messages.
        
        Args:
            user_id: The ID of the user who is in the room
            
        Returns:
            Dictionary containing:
            - enhanced_system_prompt: Original prompt + user profile context
            - context_metadata: Metadata for logging/debugging
            - raw_context_data: Gathered context data
        """
        logger.info(f"Building initial context for user {user_id}...")
        start_time = time.perf_counter()
        perf_details = {}

        try:
            # Gather user profile and user overview in parallel - no RAG searches
            start_gather = time.perf_counter()
            profile_task = asyncio.create_task(self._gather_user_profile(user_id))
            overview_task = asyncio.create_task(self._gather_user_overview(user_id))

            results = await asyncio.gather(profile_task, overview_task, return_exceptions=True)

            # Handle profile result
            if isinstance(results[0], Exception):
                logger.warning(f"Profile fetch failed: {results[0]}")
                user_profile, profile_duration = {}, 0
            else:
                user_profile, profile_duration = results[0]

            # Handle overview result
            if isinstance(results[1], Exception):
                logger.warning(f"Overview fetch failed: {results[1]}")
                user_overview, overview_duration = {}, 0
            else:
                user_overview, overview_duration = results[1]

            perf_details['gather_user_profile'] = profile_duration
            perf_details['gather_user_overview'] = overview_duration

            # Format user profile and overview as markdown (without RAG results)
            context_markdown = self._format_context_as_markdown(
                user_profile,
                [],  # No knowledge results
                [],  # No conversation results
                user_overview
            )

            # Merge with original system prompt
            original_prompt = self.agent_config.get("system_prompt", "You are a helpful AI assistant.")
            enhanced_prompt = self._merge_system_prompts(original_prompt, context_markdown)

            # Calculate timing
            duration = time.perf_counter() - start_time

            # Prepare result
            result = {
                "enhanced_system_prompt": enhanced_prompt,
                "context_metadata": {
                    "user_id": user_id,
                    "client_id": self.client_id,
                    "timestamp": datetime.now().isoformat(),
                    "duration_seconds": duration,
                    "user_profile_found": bool(user_profile),
                    "user_overview_found": bool(user_overview and any(user_overview.values())),
                    "knowledge_results_count": 0,  # No knowledge search in initial context
                    "conversation_results_count": 0,  # No conversation search in initial context
                    "context_length": len(context_markdown),
                    "total_prompt_length": len(enhanced_prompt),
                    "performance": perf_details,
                    "context_type": "initial"
                },
                "raw_context_data": {
                    "user_profile": user_profile,
                    "user_overview": user_overview,
                    "knowledge_results": [],
                    "conversation_results": [],
                    "context_markdown": context_markdown
                }
            }

            logger.info(
                f"Initial context built successfully in {duration:.2f}s - "
                f"Profile: {result['context_metadata']['user_profile_found']}, "
                f"Overview: {result['context_metadata']['user_overview_found']}"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to build initial context: {e}", exc_info=True)
            # NO FALLBACKS - re-raise the error
            raise

    async def build_complete_context(self, user_message: str, user_id: str, skip_knowledge_rag: bool = False, cached_query_embedding: Optional[List[float]] = None, top_document_intelligence: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Build dynamic context based on user message - performs RAG searches.
        This should only be called when there's an actual user message to process.

        Args:
            user_message: The user's current message/query (MUST NOT BE EMPTY)
            user_id: The ID of the user who is speaking
            skip_knowledge_rag: If True, skip knowledge RAG search (e.g., when citations_service already did it)
            cached_query_embedding: Optional pre-computed embedding to reuse (saves ~1s API call)
            top_document_intelligence: Intelligence for the #1 ranked document from RAG (DocumentSense)

        Returns:
            Dictionary containing:
            - enhanced_system_prompt: Original prompt + dynamic context
            - context_metadata: Metadata for logging/debugging
            - raw_context_data: All gathered context data
        """
        # Fail fast if no user message provided
        if not user_message or not user_message.strip():
            raise ValueError("build_complete_context requires a non-empty user_message")

        logger.info(f"Building complete context for user {user_id}, message: {user_message[:100]}... (skip_knowledge_rag={skip_knowledge_rag}, has_cached_embedding={cached_query_embedding is not None})")
        start_time = time.perf_counter()
        perf_details = {}

        try:
            # Run all context gathering operations in parallel
            start_gather = time.perf_counter()
            user_profile_task = asyncio.create_task(self._gather_user_profile(user_id))
            user_overview_task = asyncio.create_task(self._gather_user_overview(user_id))
            # Pass cached embedding to conversation RAG to skip embedding generation
            conversation_task = asyncio.create_task(self._gather_conversation_rag(user_message, user_id, cached_query_embedding))

            # Only do knowledge RAG if not already done by citations_service
            if skip_knowledge_rag:
                logger.info("Skipping knowledge RAG - already performed by citations_service")
                knowledge_task = None
            else:
                knowledge_task = asyncio.create_task(self._gather_knowledge_rag(user_message))

            # Wait for all tasks to complete - NO FALLBACKS, fail fast
            if knowledge_task:
                results = await asyncio.gather(
                    user_profile_task,
                    user_overview_task,
                    knowledge_task,
                    conversation_task,
                    return_exceptions=False  # Fail immediately if any task fails
                )
                user_profile, profile_duration = results[0]
                user_overview, overview_duration = results[1]
                knowledge_results, knowledge_duration = results[2]
                conversation_results, conversation_duration = results[3]
            else:
                results = await asyncio.gather(
                    user_profile_task,
                    user_overview_task,
                    conversation_task,
                    return_exceptions=False
                )
                user_profile, profile_duration = results[0]
                user_overview, overview_duration = results[1]
                knowledge_results, knowledge_duration = [], 0.0  # Empty - already handled by citations
                conversation_results, conversation_duration = results[2]

            perf_details['parallel_gather'] = time.perf_counter() - start_gather
            perf_details['gather_user_profile'] = profile_duration
            perf_details['gather_user_overview'] = overview_duration
            perf_details['gather_knowledge_rag'] = knowledge_duration
            perf_details['skip_knowledge_rag'] = skip_knowledge_rag
            perf_details['gather_conversation_rag'] = conversation_duration
            perf_details['has_top_document_intelligence'] = top_document_intelligence is not None

            # Format all context as markdown
            # top_document_intelligence comes from RAG result (only the #1 ranked document)
            context_markdown = self._format_context_as_markdown(
                user_profile,
                knowledge_results,
                conversation_results,
                user_overview,
                top_document_intelligence  # Single document, not a list
            )

            # Merge with original system prompt
            original_prompt = self.agent_config.get("system_prompt", "You are a helpful AI assistant.")
            enhanced_prompt = self._merge_system_prompts(original_prompt, context_markdown)

            # Calculate timing
            duration = time.perf_counter() - start_time

            # Prepare result
            result = {
                "enhanced_system_prompt": enhanced_prompt,
                "context_metadata": {
                    "user_id": user_id,  # Use the passed user_id, not self.user_id
                    "client_id": self.client_id,
                    "timestamp": datetime.now().isoformat(),
                    "duration_seconds": duration,
                    "user_profile_found": bool(user_profile),
                    "user_overview_found": bool(user_overview and any(user_overview.values())),
                    "knowledge_results_count": len(knowledge_results),
                    "conversation_results_count": len(conversation_results),
                    "has_top_document_intelligence": top_document_intelligence is not None,
                    "context_length": len(context_markdown),
                    "total_prompt_length": len(enhanced_prompt),
                    "performance": perf_details,
                    "context_type": "complete"
                },
                "raw_context_data": {
                    "user_profile": user_profile,
                    "user_overview": user_overview,
                    "knowledge_results": knowledge_results,
                    "conversation_results": conversation_results,
                    "top_document_intelligence": top_document_intelligence,
                    "context_markdown": context_markdown
                }
            }

            logger.info(
                f"Complete context built successfully in {duration:.2f}s - "
                f"Profile: {result['context_metadata']['user_profile_found']}, "
                f"Overview: {result['context_metadata']['user_overview_found']}, "
                f"Knowledge: {result['context_metadata']['knowledge_results_count']}, "
                f"Conversations: {result['context_metadata']['conversation_results_count']}, "
                f"TopDocIntel: {result['context_metadata']['has_top_document_intelligence']}"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to build complete context: {e}", exc_info=True)
            # NO FALLBACKS - re-raise the error
            raise
    
    async def _gather_user_profile(self, user_id: str) -> Tuple[Dict[str, Any], float]:
        """
        Query user profile from client's Supabase
        
        Args:
            user_id: The ID of the user to gather profile for
            
        Returns:
            Tuple of (User profile data, duration in seconds)
        """
        start_time = time.perf_counter()
        try:
            logger.info(f"Gathering user profile for {user_id}")
            
            # If profiles.user_id is UUID-typed and user_id isn't a valid UUID, skip query gracefully
            try:
                import uuid as _uuid
                _ = _uuid.UUID(str(user_id))
            except Exception:
                logger.warning(f"User id is not a UUID; skipping profile lookup: {user_id}")
                return {}, time.perf_counter() - start_time

            # Query the profiles table in client's Supabase without .single(), handle 0/1 gracefully
            result = self.supabase.table("profiles").select("*").eq("user_id", user_id).execute()
            
            if result.data and len(result.data) > 0:
                profile = result.data[0]
                # Check for various name fields
                name = profile.get('name') or profile.get('full_name') or profile.get('display_name') or profile.get('username') or 'Unknown'
                logger.info(f"Found user profile: {name}")
                return profile, time.perf_counter() - start_time
            else:
                logger.warning(f"No profile found for user {user_id}")
                return {}, time.perf_counter() - start_time
        except Exception as e:
            # For invalid input (e.g., 22P02) or any other query error, log and continue without profile
            logger.error(f"Error fetching user profile: {e}")
            return {}, time.perf_counter() - start_time

    async def _gather_user_overview(self, user_id: str) -> Tuple[Dict[str, Any], float]:
        """
        Fetch the persistent User Overview from the database.

        The User Overview is a shared, agent-maintained summary of the user
        that persists across conversations and is shared by all sidekicks
        within a client. Also includes sidekick-specific insights for this agent.

        Args:
            user_id: The ID of the user to fetch overview for

        Returns:
            Tuple of (Overview data dict with sidekick_insights, duration in seconds)
        """
        start_time = time.perf_counter()
        try:
            logger.info(f"Fetching user overview for {user_id}")

            # Validate user_id is a valid UUID
            try:
                import uuid as _uuid
                _ = _uuid.UUID(str(user_id))
            except Exception:
                logger.warning(f"User id is not a UUID; skipping overview lookup: {user_id}")
                return {}, time.perf_counter() - start_time

            # Get agent_id for sidekick-specific insights
            agent_id = self.agent_config.get("agent_id") or self.agent_config.get("id")

            # Try to use the enhanced get_user_overview_for_agent RPC if available
            # This returns both shared overview and sidekick-specific insights
            try:
                if agent_id:
                    result = self.supabase.rpc("get_user_overview_for_agent", {
                        "p_user_id": user_id,
                        "p_client_id": self.client_id,
                        "p_agent_id": agent_id
                    }).execute()

                    if result.data and isinstance(result.data, dict):
                        # RPC returns 'shared_understanding' (not 'overview') and 'my_insights' (not 'sidekick_insights')
                        overview = result.data.get("shared_understanding", {}) or result.data.get("overview", {})

                        # Extract sidekick insights from my_insights array
                        # The array contains JSON strings of insights, iterate reversed to get most recent
                        my_insights_raw = result.data.get("my_insights", [])
                        sidekick_insights = {}
                        if my_insights_raw and isinstance(my_insights_raw, list):
                            # Parse the insights - they come as JSON strings or dicts
                            # Iterate in reverse to get the most recent insight first
                            for item in reversed(my_insights_raw):
                                if isinstance(item, str) and item.strip().startswith('{'):
                                    try:
                                        parsed = json.loads(item)
                                        if isinstance(parsed, dict) and parsed.get("relationship_context"):
                                            sidekick_insights = parsed
                                            break  # Use the most recent one
                                    except json.JSONDecodeError:
                                        pass
                                elif isinstance(item, dict) and item.get("relationship_context"):
                                    sidekick_insights = item
                                    break

                        # Include sidekick insights in the overview dict for formatting
                        if sidekick_insights:
                            overview["_sidekick_insights"] = sidekick_insights

                        if overview and any(overview.values()):
                            logger.info(f"Found user overview with {len(overview)} sections for agent {agent_id}")
                            return overview, time.perf_counter() - start_time
                        else:
                            logger.info(f"User overview for agent {agent_id} was empty")
            except Exception as e:
                # Fall back to regular get_user_overview if agent-specific one doesn't exist
                logger.debug(f"get_user_overview_for_agent not available, falling back: {e}")

            # Fallback to basic get_user_overview
            result = self.supabase.rpc("get_user_overview", {
                "p_user_id": user_id,
                "p_client_id": self.client_id
            }).execute()

            if result.data:
                data = result.data
                if isinstance(data, dict) and data.get("exists"):
                    overview = data.get("overview", {})
                    logger.info(f"Found user overview with {len(overview)} sections")
                    return overview, time.perf_counter() - start_time
                elif isinstance(data, dict):
                    # Return empty/default overview
                    logger.info("No existing user overview found, using defaults")
                    return data.get("overview", {}), time.perf_counter() - start_time

            logger.info(f"No user overview found for user {user_id}")
            return {}, time.perf_counter() - start_time

        except Exception as e:
            # Log error but don't fail - overview is optional enhancement
            logger.warning(f"Error fetching user overview: {e}")
            return {}, time.perf_counter() - start_time


    async def _gather_knowledge_rag(self, user_message: str) -> Tuple[List[Dict[str, Any]], float]:
        """
        RAG search on agent's assigned documents
        
        Args:
            user_message: Query to search for
            
        Returns:
            Tuple of (List of relevant document excerpts, duration in seconds)
        """
        start_time = time.perf_counter()
        try:
            logger.info(f"Performing knowledge RAG search via match_documents RPC...")
            agent_slug = self.agent_config.get("slug") or self.agent_config.get("agent_slug")
            
            if not agent_slug:
                raise ValueError("agent_slug is required for knowledge RAG.")
                
            # Generate embeddings using remote service
            query_embedding = await self.embedder.create_embedding(user_message)

            # Use RPC signature supported by the client DB: embedding + agent slug
            rpc_params = {
                "p_query_embedding": query_embedding,
                "p_agent_slug": agent_slug,
                "p_match_threshold": 0.4,
                "p_match_count": MAX_KNOWLEDGE_RESULTS
            }
            # Shared pool match_documents requires p_client_id for tenant isolation
            hosting_type = self.agent_config.get("hosting_type", "dedicated")
            if hosting_type == "shared" and self.client_id:
                rpc_params["p_client_id"] = str(self.client_id)
            result = self.supabase.rpc("match_documents", rpc_params).execute()

            if result.data:
                logger.info(f"✅ match_documents returned {len(result.data)} results.")
                formatted_results = self._format_match_documents_results(result.data)
                formatted_results = formatted_results[:MAX_KNOWLEDGE_RESULTS]
                return formatted_results, time.perf_counter() - start_time
            
            logger.info("No relevant knowledge found via RAG.")
            return [], time.perf_counter() - start_time
        except Exception as e:
            logger.error(f"Knowledge RAG error: {e}")
            raise

    def _format_match_documents_results(self, match_results):
        """Format results from match_documents RPC function"""
        # Transform results to match expected format
        formatted_results = []
        for doc in match_results:
            formatted_results.append({
                'id': doc.get('id'),
                'title': doc.get('title', 'Untitled'),
                'excerpt': doc.get('content', ''),
                'relevance': doc.get('similarity', 0.5),  # Map similarity to relevance
                # Include any other fields that might be needed
            })
        return formatted_results
    
    async def _gather_conversation_rag(self, user_message: str, user_id: str, cached_embedding: Optional[List[float]] = None) -> Tuple[List[Dict[str, Any]], float]:
        """
        RAG search on user-agent conversation history

        Args:
            user_message: Query to search for
            user_id: The ID of the user whose conversations to search
            cached_embedding: Optional pre-computed embedding to reuse (saves ~1s API call)

        Returns:
            Tuple of (List of relevant conversation excerpts, duration in seconds)
        """
        start_time = time.perf_counter()
        try:
            logger.info(f"Performing conversation RAG via match_conversation_transcripts_secure RPC...")
            # Resolve agent slug consistently with knowledge RAG
            agent_slug = self.agent_config.get("slug") or self.agent_config.get("agent_slug")
            # Optional: also check other identifiers for diagnostics, but we require slug for this RPC
            agent_id = agent_slug or self.agent_config.get("id") or self.agent_config.get("agent_id")
            if not agent_id:
                raise ValueError("No agent identifier found in config - slug, agent_slug, or agent_id required for conversation RAG")
            if not agent_slug:
                raise ValueError("agent_slug is required for match_conversation_transcripts_secure (slug or agent_slug must be provided)")

            # Use cached embedding if available, otherwise generate new one
            if cached_embedding:
                logger.info(f"[PERF] Reusing cached embedding for conversation RAG (saved ~1000ms)")
                query_embedding = cached_embedding
            else:
                # Generate embeddings using remote service - TIMED
                embed_start = time.perf_counter()
                query_embedding = await self.embedder.create_embedding(user_message)
                embed_duration = (time.perf_counter() - embed_start) * 1000
                logger.info(f"[PERF] Conversation RAG embedding took {embed_duration:.0f}ms")

            # NO FALLBACKS: Only use the correct RPC function - TIMED
            rpc_start = time.perf_counter()
            hosting_type = self.agent_config.get("hosting_type", "dedicated")
            if hosting_type == "shared" and self.client_id:
                # Shared pool uses p_ prefixed params and requires p_client_id
                conv_rpc_params = {
                    "p_client_id": str(self.client_id),
                    "p_query_embedding": query_embedding,
                    "p_agent_slug": agent_slug,
                    "p_user_id": user_id,
                    "p_match_count": MAX_CONVERSATION_RESULTS
                }
            else:
                # Dedicated projects use legacy param names
                conv_rpc_params = {
                    "query_embeddings": query_embedding,
                    "agent_slug_param": agent_slug,
                    "user_id_param": user_id,
                    "match_count": MAX_CONVERSATION_RESULTS
                }
            result = self.supabase.rpc("match_conversation_transcripts_secure", conv_rpc_params).execute()
            rpc_duration = (time.perf_counter() - rpc_start) * 1000
            logger.info(f"[PERF] Conversation RAG RPC took {rpc_duration:.0f}ms (returned {len(result.data) if result.data else 0} results)")

            if result.data:
                logger.info(f"✅ match_conversation_transcripts_secure returned {len(result.data)} results.")
                # Format the RPC results
                conversation_results = []
                for match in result.data[:MAX_CONVERSATION_RESULTS]:
                    conversation_results.append({
                        "user_message": match.get("user_message", ""),
                        "agent_response": match.get("agent_response", ""),
                        "relevance": match.get("similarity", 0.5),
                        "timestamp": match.get("created_at", ""),
                        "conversation_id": match.get("conversation_id", "")
                    })
                return conversation_results, time.perf_counter() - start_time
            
            logger.info("No relevant conversation history found via RAG.")
            return [], time.perf_counter() - start_time
        except Exception as e:
            logger.error(f"Conversation RAG error: {e}")
            raise

    def _format_context_as_markdown(
        self,
        profile: Dict[str, Any],
        knowledge: List[Dict[str, Any]],
        conversations: List[Dict[str, Any]],
        user_overview: Optional[Dict[str, Any]] = None,
        document_intelligence: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """
        Generate clean markdown sections with proper headings

        Args:
            profile: User profile data
            knowledge: Knowledge base search results
            conversations: Conversation history search results
            user_overview: Persistent user overview (agent-maintained notes)
            document_intelligence: Document intelligence summaries (DocumentSense)

        Returns:
            Formatted markdown string
        """
        # If nothing to include, return empty string to avoid adding token noise
        if not profile and not knowledge and not conversations and not user_overview and not document_intelligence:
            return ""

        sections = []
        # Header
        sections.append("# Agent Context\n")

        # User Overview Section (comes first - most important for relationship context)
        if user_overview and any(user_overview.values()):
            sections.append("## User Overview")
            sections.append("*Your persistent notes about this user (shared across all sidekicks):*\n")

            # Identity section
            identity = user_overview.get("identity", {})
            if identity and any(identity.values()):
                sections.append("### Identity")
                if identity.get("role"):
                    sections.append(f"- **Role:** {self._truncate_text(identity['role'], MAX_PROFILE_FIELD_CHARS)}")
                if identity.get("background"):
                    sections.append(f"- **Background:** {self._truncate_text(identity['background'], MAX_PROFILE_FIELD_CHARS)}")
                if identity.get("team"):
                    sections.append(f"- **Team:** {self._truncate_text(identity['team'], MAX_PROFILE_FIELD_CHARS)}")
                # Include any other identity fields
                for key, value in identity.items():
                    if key not in ("role", "background", "team") and value:
                        sections.append(f"- **{key.replace('_', ' ').title()}:** {self._truncate_text(value, MAX_PROFILE_FIELD_CHARS)}")
                sections.append("")

            # Goals section
            goals = user_overview.get("goals", {})
            if goals and any(goals.values()):
                sections.append("### Goals")
                if goals.get("primary"):
                    sections.append(f"- **Primary:** {self._truncate_text(goals['primary'], MAX_PROFILE_FIELD_CHARS)}")
                if goals.get("secondary"):
                    secondary = goals["secondary"]
                    if isinstance(secondary, list):
                        sections.append(f"- **Secondary:** {', '.join(str(s) for s in secondary)}")
                    else:
                        sections.append(f"- **Secondary:** {self._truncate_text(secondary, MAX_PROFILE_FIELD_CHARS)}")
                if goals.get("blockers"):
                    sections.append(f"- **Blockers:** {self._truncate_text(goals['blockers'], MAX_PROFILE_FIELD_CHARS)}")
                sections.append("")

            # Working Style section
            working_style = user_overview.get("working_style", {})
            if working_style and any(working_style.values()):
                sections.append("### Working Style")
                if working_style.get("communication"):
                    sections.append(f"- **Communication:** {self._truncate_text(working_style['communication'], MAX_PROFILE_FIELD_CHARS)}")
                if working_style.get("decision_making"):
                    sections.append(f"- **Decision Making:** {self._truncate_text(working_style['decision_making'], MAX_PROFILE_FIELD_CHARS)}")
                if working_style.get("notes"):
                    sections.append(f"- **Notes:** {self._truncate_text(working_style['notes'], MAX_PROFILE_FIELD_CHARS)}")
                sections.append("")

            # Important Context section (list of items)
            important_context = user_overview.get("important_context", [])
            if important_context and isinstance(important_context, list) and len(important_context) > 0:
                sections.append("### Important Context")
                for item in important_context:
                    if item:
                        sections.append(f"- {self._truncate_text(item, MAX_PROFILE_FIELD_CHARS)}")
                sections.append("")

            # Relationship History section
            relationship = user_overview.get("relationship_history", {})
            if relationship and any(relationship.values()):
                sections.append("### Relationship History")
                if relationship.get("first_interaction"):
                    sections.append(f"- **First Interaction:** {relationship['first_interaction']}")
                if relationship.get("key_wins"):
                    wins = relationship["key_wins"]
                    if isinstance(wins, list):
                        sections.append(f"- **Key Wins:** {', '.join(str(w) for w in wins)}")
                    else:
                        sections.append(f"- **Key Wins:** {self._truncate_text(wins, MAX_PROFILE_FIELD_CHARS)}")
                if relationship.get("ongoing_threads"):
                    threads = relationship["ongoing_threads"]
                    if isinstance(threads, list):
                        sections.append(f"- **Ongoing:** {', '.join(str(t) for t in threads)}")
                    else:
                        sections.append(f"- **Ongoing:** {self._truncate_text(threads, MAX_PROFILE_FIELD_CHARS)}")
                sections.append("")

            # Biography section
            biography = user_overview.get("biography", {})
            if biography and any(biography.values()):
                sections.append("### Biography")
                if biography.get("summary"):
                    sections.append(f"- **Summary:** {self._truncate_text(biography['summary'], MAX_PROFILE_FIELD_CHARS)}")
                if biography.get("mission"):
                    sections.append(f"- **Mission:** {self._truncate_text(biography['mission'], MAX_PROFILE_FIELD_CHARS)}")
                if biography.get("ventures"):
                    ventures = biography["ventures"]
                    if isinstance(ventures, list):
                        sections.append(f"- **Ventures:** {', '.join(str(v) for v in ventures)}")
                    else:
                        sections.append(f"- **Ventures:** {self._truncate_text(ventures, MAX_PROFILE_FIELD_CHARS)}")
                if biography.get("alter_egos"):
                    alter_egos = biography["alter_egos"]
                    if isinstance(alter_egos, list):
                        sections.append(f"- **Alter Egos:** {', '.join(str(a) for a in alter_egos)}")
                    else:
                        sections.append(f"- **Alter Egos:** {self._truncate_text(alter_egos, MAX_PROFILE_FIELD_CHARS)}")
                if biography.get("philosophy"):
                    sections.append(f"- **Philosophy:** {self._truncate_text(biography['philosophy'], MAX_PROFILE_FIELD_CHARS)}")
                if biography.get("essence"):
                    sections.append(f"- **Essence:** {self._truncate_text(biography['essence'], MAX_PROFILE_FIELD_CHARS)}")
                # Include any other biography fields
                for key, value in biography.items():
                    if key not in ("summary", "mission", "ventures", "alter_egos", "philosophy", "essence") and value:
                        sections.append(f"- **{key.replace('_', ' ').title()}:** {self._truncate_text(str(value), MAX_PROFILE_FIELD_CHARS)}")
                sections.append("")

            # Sidekick-Specific Insights section (your private notes about this user)
            sidekick_insights = user_overview.get("_sidekick_insights", {})
            if sidekick_insights and any(sidekick_insights.values()):
                agent_name = self.agent_config.get("agent_name") or self.agent_config.get("name") or "You"
                sections.append(f"### Your Insights ({agent_name})")
                sections.append(f"*Your private observations about this user (specific to your relationship):*\n")

                if sidekick_insights.get("relationship_context"):
                    sections.append(f"- **Your Role:** {self._truncate_text(sidekick_insights['relationship_context'], MAX_PROFILE_FIELD_CHARS)}")

                if sidekick_insights.get("interaction_patterns"):
                    sections.append(f"- **How They Interact With You:** {self._truncate_text(sidekick_insights['interaction_patterns'], MAX_PROFILE_FIELD_CHARS)}")

                unique_obs = sidekick_insights.get("unique_observations", [])
                if unique_obs and isinstance(unique_obs, list) and len(unique_obs) > 0:
                    sections.append("- **What Only You Know:**")
                    for obs in unique_obs[:5]:  # Limit to 5 observations
                        if obs:
                            sections.append(f"  - {self._truncate_text(obs, MAX_PROFILE_FIELD_CHARS)}")

                topics = sidekick_insights.get("topics_discussed", [])
                if topics and isinstance(topics, list) and len(topics) > 0:
                    sections.append(f"- **Topics You've Discussed:** {', '.join(str(t) for t in topics[:10])}")

                sections.append("")

            sections.append("")  # Extra spacing after overview

        # Document Intelligence Section (DocumentSense - only for the #1 ranked document from RAG)
        if document_intelligence and isinstance(document_intelligence, dict):
            sections.append("## Top Document Intelligence")
            sections.append("*Deep knowledge about the most relevant document to your query:*\n")

            title = document_intelligence.get("title", "Untitled")
            sections.append(f"### {title}")

            # Document summary
            summary = document_intelligence.get("summary", "")
            if summary:
                sections.append(f"**Summary:** {self._truncate_text(summary, MAX_KNOWLEDGE_EXCERPT_CHARS)}")

            # Themes
            themes = document_intelligence.get("themes", [])
            if themes:
                themes_str = ", ".join(str(t) for t in themes[:5])
                sections.append(f"**Themes:** {themes_str}")

            # Key quotes (limited to save context)
            key_quotes = document_intelligence.get("key_quotes", [])
            if key_quotes:
                sections.append("**Notable Quotes:**")
                for quote in key_quotes[:3]:  # Limit to 3 quotes for the top doc
                    if isinstance(quote, dict):
                        quote_text = quote.get("quote", quote.get("text", str(quote)))
                    else:
                        quote_text = str(quote)
                    sections.append(f"  - \"{self._truncate_text(quote_text, 250)}\"")

            # Key entities (people, concepts)
            entities = document_intelligence.get("entities", {})
            people = entities.get("people", [])
            concepts = entities.get("concepts", [])
            if people:
                sections.append(f"**People mentioned:** {', '.join(str(p) for p in people[:5])}")
            if concepts:
                sections.append(f"**Key concepts:** {', '.join(str(c) for c in concepts[:5])}")

            # Questions this document can answer
            questions = document_intelligence.get("questions_answered", [])
            if questions:
                sections.append("**Questions this document answers:**")
                for q in questions[:3]:
                    sections.append(f"  - {self._truncate_text(str(q), 150)}")

            sections.append("")  # Extra spacing after document intelligence

        # User Profile Section
        if profile:
            sections.append("## User Profile")

            # Format profile fields
            # Prefer user_overview identity name over profile username
            name = None
            if user_overview and isinstance(user_overview, dict):
                identity = user_overview.get("identity", {})
                if isinstance(identity, dict):
                    name = identity.get("name") or identity.get("preferred_name") or identity.get("first_name")
                if not name:
                    bio = user_overview.get("biography", {})
                    if isinstance(bio, dict):
                        name = bio.get("name")
            if not name:
                _profile_name = profile.get("name") or profile.get("full_name") or profile.get("display_name") or profile.get("username")
                # Only use profile name if it looks like a real name (has spaces or mixed case)
                if _profile_name and (' ' in _profile_name or _profile_name != _profile_name.lower()):
                    name = _profile_name
            if name:
                sections.append(f"**Name:** {self._truncate_text(name, MAX_PROFILE_FIELD_CHARS)}  ")
            
            if profile.get("email"):
                sections.append(f"**Email:** {self._truncate_text(profile['email'], MAX_PROFILE_FIELD_CHARS)}  ")
            
            # Check for both 'tags' and 'Tags' fields
            tags_data = profile.get("tags") or profile.get("Tags")
            if tags_data:
                tags = tags_data if isinstance(tags_data, list) else [tags_data]
                tags_str = " ".join(f"#{tag}" for tag in tags)
                sections.append(f"**Tags:** {tags_str}  ")
            
            if profile.get("goals"):
                sections.append(f"**Goals:** {self._truncate_text(profile['goals'], MAX_PROFILE_FIELD_CHARS)}  ")
            
            if profile.get("preferences"):
                sections.append(f"**Preferences:** {self._truncate_text(profile['preferences'], MAX_PROFILE_FIELD_CHARS)}  ")
            
            sections.append("")  # Empty line
        
        # Knowledge Base Section
        if knowledge:
            sections.append("## Relevant Knowledge Base")
            
            for item in knowledge:
                sections.append(f"### Document: \"{item['title']}\" (Relevance: {item['relevance']})")
                
                # Format excerpt with proper line breaks
                raw_excerpt = (item.get('excerpt') or '').strip()
                excerpt = self._truncate_text(raw_excerpt, MAX_KNOWLEDGE_EXCERPT_CHARS)
                if excerpt:
                    # Indent excerpt lines
                    excerpt_lines = excerpt.split('\n')
                    formatted_excerpt = '\n'.join(f"- {line}" if line.strip() else "" for line in excerpt_lines)
                    sections.append(formatted_excerpt)
                
                sections.append("")  # Empty line between documents
        
        # Conversation History Section
        if conversations:
            sections.append("## Recent Conversation Context")
            
            for conv in conversations:
                # Format timestamp
                timestamp = conv.get("timestamp", "")
                if timestamp:
                    try:
                        dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                        formatted_date = dt.strftime("%Y-%m-%d")
                    except:
                        formatted_date = timestamp[:10]  # Fallback to first 10 chars
                else:
                    formatted_date = "Unknown date"
                
                sections.append(f"### Previous Discussion ({formatted_date})")
                
                # Format the exchange
                sections.append(
                    f"**User:** \"{self._truncate_text(conv.get('user_message', ''), MAX_CONVERSATION_SNIPPET_CHARS)}\"  "
                )
                sections.append(
                    f"**Agent:** \"{self._truncate_text(conv.get('agent_response', ''), MAX_CONVERSATION_SNIPPET_CHARS)}\"  "
                )
                
                # Add relevance if significant
                if conv.get("relevance", 0) > 0.7:
                    sections.append(f"*(High relevance: {conv['relevance']})*  ")
                
                sections.append("")  # Empty line between conversations
        
        # Footer
        sections.append("---")
        sections.append(f"*Context generated at: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC*")
        
        # Join all sections
        markdown = "\n".join(sections)
        
        # Clean up any triple newlines
        while "\n\n\n" in markdown:
            markdown = markdown.replace("\n\n\n", "\n\n")
        
        markdown = markdown.strip()

        if len(markdown) > CONTEXT_MARKDOWN_CHAR_BUDGET:
            logger.warning(
                "Context markdown exceeds budget (%s > %s); trimming lower-priority sections",
                len(markdown),
                CONTEXT_MARKDOWN_CHAR_BUDGET,
            )
            markdown = self._trim_markdown(markdown, CONTEXT_MARKDOWN_CHAR_BUDGET)

        return markdown

    @staticmethod
    def _truncate_text(value: Any, max_chars: int) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 1].rstrip() + "…"

    def _trim_markdown(self, markdown: str, budget: int) -> str:
        """Best-effort trimming that drops the lowest priority sections first."""
        if len(markdown) <= budget:
            return markdown

        # Split into sections separated by headings
        sections = markdown.split("\n## ")
        if len(sections) == 1:
            return markdown[:budget]

        # Keep header (first entry) and iteratively add sections until limit
        rebuilt = sections[0]
        for section in sections[1:]:
            candidate = rebuilt + "\n## " + section
            if len(candidate) > budget:
                break
            rebuilt = candidate

        return rebuilt[:budget]
    
    def _merge_system_prompts(self, original_prompt: str, context_markdown: str) -> str:
        """
        Combine user-defined system prompt with dynamic context
        
        Args:
            original_prompt: The agent's configured system prompt
            context_markdown: The dynamically generated context
            
        Returns:
            Enhanced system prompt
        """
        # Response formatting guidelines that apply to all prompts
        formatting_guidelines = """
## Response Formatting Guidelines

When speaking, structure your responses for clarity:
- Use paragraph breaks between distinct ideas or topics
- Bold key terms, important concepts, and names using **double asterisks**
- Use transition phrases to guide the listener through your explanation
- Keep individual sentences clear and conversational"""

        # User Overview tool guidance
        overview_tool_guidance = """
## User Overview Tool

You have access to an `update_user_overview` tool to maintain persistent notes about users.
These notes are shared across all sidekicks for this client - they're your collective memory.

**Use this tool when the user shares ENDURING information about:**
- **Biography:** Life story, background, personal journey, ventures, projects, origin story
- **Identity:** Their name (ALWAYS store under identity.name when shared), career/role changes, who they are
- **Goals:** Priority shifts ("My priority is now X instead of Y"), aspirations, missions
- **Working Style:** Communication preferences, decision-making patterns, neurodivergence
- **Important Context:** Personal factors affecting interactions, constraints, circumstances
- **Relationship History:** Key wins, milestones, ongoing threads worth remembering

**Do NOT use for:** Routine tasks, today-only info, already-captured details, or speculation.

**Be concise and update (don't just append).** If a goal changes, replace it. If they share biographical details, add them to the biography section."""

        if not context_markdown or context_markdown.strip() == "# Agent Context":
            # No meaningful context to add, but still include formatting and tool guidelines
            return f"{original_prompt}\n\n---\n{formatting_guidelines}\n{overview_tool_guidance}"

        # Build the enhanced prompt with formatting guidance
        enhanced_prompt = f"""{original_prompt}

---

{context_markdown}

---
{formatting_guidelines}
{overview_tool_guidance}

Remember to use this context appropriately in your responses while maintaining your core personality and instructions."""

        return enhanced_prompt
    
    async def close(self):
        """Clean up resources"""
        if hasattr(self, 'embedder') and self.embedder:
            await self.embedder.close()


# Utility function for testing
async def test_context_manager():
    """Test the context manager with mock data"""
    import os
    from supabase import create_client
    
    # This would normally come from the agent initialization
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    
    if supabase_url and supabase_key:
        supabase = create_client(supabase_url, supabase_key)
        
        # Mock agent config
        agent_config = {
            "id": "test-agent-123",
            "slug": "test-agent",
            "system_prompt": "You are a helpful AI assistant specialized in technical support.",
            "embedding": {
                "provider": "siliconflow",
                "model": "text-embedding-ada-002"
            }
        }
        
        # Mock API keys
        api_keys = {
            "siliconflow_api_key": os.getenv("SILICONFLOW_API_KEY"),
            "openai_api_key": os.getenv("OPENAI_API_KEY")
        }
        
        # Create context manager
        manager = AgentContextManager(
            supabase_client=supabase,
            agent_config=agent_config,
            user_id="test-user-123",
            client_id="test-client-123",
            api_keys=api_keys
        )
        
        try:
            # Test initial context building
            print("Testing initial context (no user message)...")
            initial_result = await manager.build_initial_context("test-user-123")
            print("Initial Context Metadata:")
            print(json.dumps(initial_result["context_metadata"], indent=2))
            
            # Test complete context building
            print("\nTesting complete context (with user message)...")
            complete_result = await manager.build_complete_context("How do I set up webhooks?", "test-user-123")
            
            print("\nEnhanced System Prompt:")
            print(complete_result["enhanced_system_prompt"])
            print("\nComplete Context Metadata:")
            print(json.dumps(complete_result["context_metadata"], indent=2))
        finally:
            await manager.close()


if __name__ == "__main__":
    # Run test if executed directly
    asyncio.run(test_context_manager())

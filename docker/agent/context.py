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


class RemoteEmbedder:
    """Simple client for remote embedding services"""
    
    def __init__(self, provider: str, api_key: str, model: str = None):
        self.provider = provider
        self.api_key = api_key
        self.model = model
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
        
        response = await self.client.post(
            "https://api.siliconflow.com/v1/embeddings",
            headers=headers,
            json=data
        )
        
        if response.status_code == 200:
            result = response.json()
            if 'data' in result and len(result['data']) > 0:
                return result['data'][0]['embedding']
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
    
    def _initialize_embedder(self) -> RemoteEmbedder:
        """Initialize the remote embedding client based on configuration"""
        # Get embedding provider from agent config - NO DEFAULTS
        embedding_config = self.agent_config.get('embedding', {})
        
        if not embedding_config:
            # Log available keys to help debug
            logger.warning(f"No embedding configuration found. Available keys in agent_config: {list(self.agent_config.keys())}")
            raise ValueError("No embedding configuration found. Embedding provider and model must be configured.")
        
        provider = embedding_config.get('provider')
        if not provider:
            raise ValueError("No embedding provider specified in configuration. Required field: embedding.provider")
            
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
            raise ValueError(f"Unsupported embedding provider: {provider}")
        
        api_key = self.api_keys.get(api_key_name)
        if not api_key:
            raise ValueError(f"No API key found for embedding provider {provider}. Required key: {api_key_name}")
        
        logger.info(f"Initializing {provider} embedder with model: {model or 'default'}")
        return RemoteEmbedder(provider, api_key, model)
    
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
            # Only gather user profile - no RAG searches
            start_gather = time.perf_counter()
            user_profile, profile_duration = await self._gather_user_profile(user_id)
            
            perf_details['gather_user_profile'] = profile_duration
            
            # Format user profile as markdown (without RAG results)
            context_markdown = self._format_context_as_markdown(
                user_profile,
                [],  # No knowledge results
                []   # No conversation results
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
                    "knowledge_results_count": 0,  # No knowledge search in initial context
                    "conversation_results_count": 0,  # No conversation search in initial context
                    "context_length": len(context_markdown),
                    "total_prompt_length": len(enhanced_prompt),
                    "performance": perf_details,
                    "context_type": "initial"
                },
                "raw_context_data": {
                    "user_profile": user_profile,
                    "knowledge_results": [],
                    "conversation_results": [],
                    "context_markdown": context_markdown
                }
            }
            
            logger.info(
                f"Initial context built successfully in {duration:.2f}s - "
                f"Profile: {result['context_metadata']['user_profile_found']}"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to build initial context: {e}", exc_info=True)
            # NO FALLBACKS - re-raise the error
            raise

    async def build_complete_context(self, user_message: str, user_id: str) -> Dict[str, Any]:
        """
        Build dynamic context based on user message - performs RAG searches.
        This should only be called when there's an actual user message to process.
        
        Args:
            user_message: The user's current message/query (MUST NOT BE EMPTY)
            user_id: The ID of the user who is speaking
            
        Returns:
            Dictionary containing:
            - enhanced_system_prompt: Original prompt + dynamic context
            - context_metadata: Metadata for logging/debugging
            - raw_context_data: All gathered context data
        """
        # Fail fast if no user message provided
        if not user_message or not user_message.strip():
            raise ValueError("build_complete_context requires a non-empty user_message")
            
        logger.info(f"Building complete context for user {user_id}, message: {user_message[:100]}...")
        start_time = time.perf_counter()
        perf_details = {}

        try:
            # Run all context gathering operations in parallel
            start_gather = time.perf_counter()
            user_profile_task = asyncio.create_task(self._gather_user_profile(user_id))
            knowledge_task = asyncio.create_task(self._gather_knowledge_rag(user_message))
            conversation_task = asyncio.create_task(self._gather_conversation_rag(user_message, user_id))
            
            # Wait for all tasks to complete - NO FALLBACKS, fail fast
            results = await asyncio.gather(
                user_profile_task,
                knowledge_task,
                conversation_task,
                return_exceptions=False  # Fail immediately if any task fails
            )
            
            # Unpack results with performance data
            user_profile, profile_duration = results[0]
            knowledge_results, knowledge_duration = results[1]
            conversation_results, conversation_duration = results[2]
            
            perf_details['parallel_gather'] = time.perf_counter() - start_gather
            perf_details['gather_user_profile'] = profile_duration
            perf_details['gather_knowledge_rag'] = knowledge_duration
            perf_details['gather_conversation_rag'] = conversation_duration
            
            # Format all context as markdown
            context_markdown = self._format_context_as_markdown(
                user_profile,
                knowledge_results,
                conversation_results
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
                    "knowledge_results_count": len(knowledge_results),
                    "conversation_results_count": len(conversation_results),
                    "context_length": len(context_markdown),
                    "total_prompt_length": len(enhanced_prompt),
                    "performance": perf_details,
                    "context_type": "complete"
                },
                "raw_context_data": {
                    "user_profile": user_profile,
                    "knowledge_results": knowledge_results,
                    "conversation_results": conversation_results,
                    "context_markdown": context_markdown
                }
            }
            
            logger.info(
                f"Complete context built successfully in {duration:.2f}s - "
                f"Profile: {result['context_metadata']['user_profile_found']}, "
                f"Knowledge: {result['context_metadata']['knowledge_results_count']}, "
                f"Conversations: {result['context_metadata']['conversation_results_count']}"
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
            result = self.supabase.rpc("match_documents", {
                "p_query_embedding": query_embedding,
                "p_agent_slug": agent_slug,
                "p_match_threshold": 0.4,
                "p_match_count": MAX_KNOWLEDGE_RESULTS
            }).execute()
            
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
    
    async def _gather_conversation_rag(self, user_message: str, user_id: str) -> Tuple[List[Dict[str, Any]], float]:
        """
        RAG search on user-agent conversation history
        
        Args:
            user_message: Query to search for
            user_id: The ID of the user whose conversations to search
            
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
                
            # Generate embeddings using remote service
            query_embedding = await self.embedder.create_embedding(user_message)

            # NO FALLBACKS: Only use the correct RPC function
            result = self.supabase.rpc("match_conversation_transcripts_secure", {
                "query_embeddings": query_embedding,
                "agent_slug_param": agent_slug,
                "user_id_param": user_id,  # Use the passed user_id, not self.user_id
                "match_count": MAX_CONVERSATION_RESULTS
            }).execute()

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
        conversations: List[Dict[str, Any]]
    ) -> str:
        """
        Generate clean markdown sections with proper headings
        
        Args:
            profile: User profile data
            knowledge: Knowledge base search results
            conversations: Conversation history search results
            
        Returns:
            Formatted markdown string
        """
        # If nothing to include, return empty string to avoid adding token noise
        if not profile and not knowledge and not conversations:
            return ""

        sections = []
        # Header
        sections.append("# Agent Context\n")
        
        # User Profile Section
        if profile:
            sections.append("## User Profile")
            
            # Format profile fields
            # Check for various name fields
            name = profile.get("name") or profile.get("full_name") or profile.get("display_name") or profile.get("username")
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
        if not context_markdown or context_markdown.strip() == "# Agent Context":
            # No meaningful context to add
            return original_prompt
        
        # Build the enhanced prompt
        enhanced_prompt = f"""{original_prompt}

---

{context_markdown}

---

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

#!/usr/bin/env python3
"""
Agent Context Manager
Dynamically enhances agent system prompts with user profiles, RAG searches, and conversation history
Uses remote embedding services only - no local models or vector stores
"""
import asyncio
import logging
import json
import math
from ast import literal_eval
from typing import Dict, Any, List, Optional, Tuple, Set
from datetime import datetime, timedelta
import httpx
import time
from types import SimpleNamespace

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
            
            # Query the profiles table in client's Supabase
            # NO WORKAROUNDS: Let the database query run naturally
            # Use regular select without .single() to handle missing profiles gracefully
            result = self.supabase.table("profiles").select("*").eq("user_id", user_id).execute()
            
            if result.data and len(result.data) > 0:
                profile = result.data[0]  # Take first result
                # Check for various name fields
                name = profile.get('name') or profile.get('full_name') or profile.get('display_name') or profile.get('username') or 'Unknown'
                logger.info(f"Found user profile: {name}")
                return profile, time.perf_counter() - start_time
            else:
                logger.warning(f"No profile found for user {user_id}")
                return {}, time.perf_counter() - start_time
        except Exception as e:
            logger.error(f"Error fetching user profile: {e}")
            # NO FALLBACKS - re-raise the error
            raise
    
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
            # Convert to pgvector string format
            emb_vec = "[" + ",".join(map(str, query_embedding)) + "]"

            # NO FALLBACKS: Only use the correct RPC function
            result = self.supabase.rpc("match_documents", {
                "p_query_embedding": emb_vec,          # Pass as pgvector string
                "p_agent_slug": agent_slug,            # Database function expects p_ prefix
                "p_match_threshold": 0.3,              # Lowered from 0.5 for better recall
                "p_match_count": 5
            }).execute()
            
            match_rows = list(result.data or [])

            dataset_ids = []
            try:
                raw_ids = self.agent_config.get("dataset_ids")
                if isinstance(raw_ids, (list, tuple, set)):
                    dataset_ids = [int(str(did)) for did in raw_ids if str(did).strip()]
            except Exception:
                dataset_ids = []

            if dataset_ids:
                seen_ids: Set[int] = set()
                for row in match_rows:
                    try:
                        seen_ids.add(int(str(row.get("id"))))
                    except Exception:
                        continue

                missing_ids = [doc_id for doc_id in dataset_ids if doc_id not in seen_ids]

                if missing_ids:
                    logger.info(
                        "⚠️ match_documents missing %s assigned documents; performing fallback similarity search.",
                        len(missing_ids),
                    )
                    fallback_rows = await self._fallback_document_similarity(
                        missing_ids,
                        query_embedding,
                    )
                    if fallback_rows:
                        match_rows.extend(fallback_rows)

            if match_rows:
                # Sort combined rows by similarity (desc) and cap to RPC limit (default 5)
                match_rows.sort(key=lambda row: row.get("similarity", 0.0), reverse=True)
                match_rows = match_rows[:5]

                logger.info(f"✅ match_documents returned {len(match_rows)} results (after fallback).")

                # Expose citations for trigger.py (_last_rag_results.citations)
                self._last_rag_results = SimpleNamespace(
                    citations=[
                        SimpleNamespace(
                            doc_id=row.get("id"),
                            title=row.get("title"),
                            source_url=None,             # not available from this RPC
                            chunk_text=row.get("content"),
                            score=row.get("similarity", 0.0),
                        )
                        for row in match_rows
                    ]
                )
                formatted_results = self._format_match_documents_results(match_rows)
                return formatted_results, time.perf_counter() - start_time
            
            logger.info("No relevant knowledge found via RAG.")
            return [], time.perf_counter() - start_time
        except Exception as e:
            logger.error(f"Knowledge RAG error: {e}")
            raise

    async def _fallback_document_similarity(
        self,
        document_ids: List[int],
        query_embedding: List[float],
    ) -> List[Dict[str, Any]]:
        """
        Compute document similarity client-side for documents missing from match_documents RPC.
        """
        try:
            if not document_ids:
                return []

            # Normalize query embedding once
            query_norm = math.sqrt(sum(val * val for val in query_embedding)) or 1.0

            doc_info = (
                self.supabase
                .table("documents")
                .select("id,title")
                .in_("id", document_ids)
                .execute()
            )
            doc_title_map = {int(row["id"]): row.get("title", "Untitled") for row in doc_info.data or []}

            chunk_response = (
                self.supabase
                .table("document_chunks")
                .select("document_id, chunk_index, content, embeddings")
                .in_("document_id", document_ids)
                .execute()
            )

            best_per_doc: Dict[int, Dict[str, Any]] = {}
            for row in chunk_response.data or []:
                doc_id = row.get("document_id")
                embeddings = row.get("embeddings")
                content = row.get("content")
                if doc_id is None or embeddings is None or content is None:
                    continue
                try:
                    vector = literal_eval(embeddings)
                    chunk_norm = math.sqrt(sum(val * val for val in vector)) or 1.0
                    similarity = sum(a * b for a, b in zip(query_embedding, vector)) / (query_norm * chunk_norm)
                except Exception as exc:
                    logger.debug(f"Failed to compute similarity for fallback chunk: {exc}")
                    continue

                current_best = best_per_doc.get(doc_id)
                if current_best is None or similarity > current_best["similarity"]:
                    best_per_doc[doc_id] = {
                        "id": doc_id,
                        "title": doc_title_map.get(doc_id, "Untitled"),
                        "content": content,
                        "similarity": similarity,
                    }

            return list(best_per_doc.values())

        except Exception as exc:
            logger.warning(f"Fallback similarity computation failed: {exc}")
            return []

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
            # Try to get agent identifier - could be id, agent_id, or slug
            agent_id = self.agent_config.get("id") or self.agent_config.get("agent_id") or self.agent_config.get("agent_slug")
            
            if not agent_id:
                raise ValueError("No agent identifier found in config - agent_id or agent_slug required for conversation RAG")
                
            # Generate embeddings using remote service
            query_embedding = await self.embedder.create_embedding(user_message)
            # Convert to pgvector string format
            emb_vec = "[" + ",".join(map(str, query_embedding)) + "]"

            # NO FALLBACKS: Only use the correct RPC function
            # Cast user_id to UUID to disambiguate the overloaded function
            import uuid
            try:
                user_id_uuid = str(uuid.UUID(user_id))  # Ensure it's a valid UUID string
            except ValueError:
                logger.error(f"Invalid UUID format for user_id: {user_id}")
                raise ValueError(f"user_id must be a valid UUID, got: {user_id}")
                
            result = self.supabase.rpc("match_conversation_transcripts_secure", {
                "query_embeddings": emb_vec,  # Pass as pgvector string
                "match_count": 5,  # Put match_count before other params to match function signature
                "agent_slug_param": self.agent_config.get("slug"),
                "user_id_param": user_id_uuid  # Pass as UUID string
            }).execute()

            if result.data:
                logger.info(f"✅ match_conversation_transcripts_secure returned {len(result.data)} results.")
                # Format the RPC results
                conversation_results = []
                for match in result.data:
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
                sections.append(f"**Name:** {name}  ")
            
            if profile.get("email"):
                sections.append(f"**Email:** {profile['email']}  ")
            
            # Check for both 'tags' and 'Tags' fields
            tags_data = profile.get("tags") or profile.get("Tags")
            if tags_data:
                tags = tags_data if isinstance(tags_data, list) else [tags_data]
                tags_str = " ".join(f"#{tag}" for tag in tags)
                sections.append(f"**Tags:** {tags_str}  ")
            
            if profile.get("goals"):
                sections.append(f"**Goals:** {profile['goals']}  ")
            
            if profile.get("preferences"):
                sections.append(f"**Preferences:** {profile['preferences']}  ")
            
            sections.append("")  # Empty line
        
        # Knowledge Base Section
        if knowledge:
            sections.append("## Relevant Knowledge Base")
            
            for item in knowledge:
                sections.append(f"### Document: \"{item['title']}\" (Relevance: {item['relevance']})")
                
                # Format excerpt with proper line breaks
                excerpt = item['excerpt'].strip()
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
                sections.append(f"**User:** \"{conv['user_message']}\"  ")
                sections.append(f"**Agent:** \"{conv['agent_response']}\"  ")
                
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
        
        return markdown.strip()
    
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

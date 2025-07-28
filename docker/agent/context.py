#!/usr/bin/env python3
"""
Agent Context Manager
Dynamically enhances agent system prompts with user profiles, RAG searches, and conversation history
"""
import asyncio
import logging
import json
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import numpy as np
from supabase import Client
import hashlib

logger = logging.getLogger(__name__)

# Try to import embedding libraries
try:
    from sentence_transformers import SentenceTransformer
    import chromadb
    from chromadb.config import Settings
    HAS_EMBEDDINGS = True
except ImportError:
    logger.warning("sentence-transformers and chromadb not available - RAG features will be limited")
    HAS_EMBEDDINGS = False


class AgentContextManager:
    """Manages dynamic context generation for AI agents"""
    
    def __init__(
        self, 
        supabase_client: Client,
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
        
        # Initialize embedding model
        self.embedder = self._initialize_embedder() if HAS_EMBEDDINGS else None
        
        # Initialize ChromaDB for local vector storage
        if HAS_EMBEDDINGS:
            self.chroma_client = chromadb.Client(Settings(
                is_persistent=False,
                anonymized_telemetry=False
            ))
            
            # Create collections for different context types
            self.knowledge_collection = None
            self.conversation_collection = None
            self._initialize_collections()
        else:
            self.chroma_client = None
            self.knowledge_collection = None
            self.conversation_collection = None
        
        logger.info(f"Initialized AgentContextManager for user {user_id}, client {client_id}")
    
    def _initialize_embedder(self):
        """Initialize the embedding model based on available API keys"""
        # For now, use a local sentence-transformers model
        # Future: Support Novita, SiliconFlow, Jina APIs
        model_name = "all-MiniLM-L6-v2"  # Fast, good quality embeddings
        logger.info(f"Loading embedding model: {model_name}")
        return SentenceTransformer(model_name)
    
    def _initialize_collections(self):
        """Initialize ChromaDB collections for vector storage"""
        try:
            # Create unique collection names using client_id and timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            self.knowledge_collection = self.chroma_client.create_collection(
                name=f"knowledge_{self.client_id}_{timestamp}",
                metadata={"type": "knowledge_base"}
            )
            
            self.conversation_collection = self.chroma_client.create_collection(
                name=f"conversations_{self.client_id}_{timestamp}",
                metadata={"type": "conversation_history"}
            )
            
            logger.info("Initialized ChromaDB collections for vector storage")
        except Exception as e:
            logger.error(f"Failed to initialize ChromaDB collections: {e}")
            # Continue without vector storage - fallback to basic search
    
    async def build_complete_context(self, user_message: str) -> Dict[str, Any]:
        """
        Main orchestrator - builds all context components in parallel
        
        Args:
            user_message: The user's current message/query
            
        Returns:
            Dictionary containing:
            - enhanced_system_prompt: Original prompt + dynamic context
            - context_metadata: Metadata for logging/debugging
            - raw_context_data: All gathered context data
        """
        logger.info(f"Building context for message: {user_message[:100]}...")
        start_time = datetime.now()
        
        try:
            # Run all context gathering operations in parallel
            user_profile_task = asyncio.create_task(self._gather_user_profile())
            knowledge_task = asyncio.create_task(self._gather_knowledge_rag(user_message))
            conversation_task = asyncio.create_task(self._gather_conversation_rag(user_message))
            
            # Wait for all tasks to complete
            user_profile, knowledge_results, conversation_results = await asyncio.gather(
                user_profile_task,
                knowledge_task,
                conversation_task,
                return_exceptions=True  # Don't fail if one task fails
            )
            
            # Handle any errors gracefully
            if isinstance(user_profile, Exception):
                logger.error(f"Failed to gather user profile: {user_profile}")
                user_profile = {}
            
            if isinstance(knowledge_results, Exception):
                logger.error(f"Failed to gather knowledge: {knowledge_results}")
                knowledge_results = []
            
            if isinstance(conversation_results, Exception):
                logger.error(f"Failed to gather conversations: {conversation_results}")
                conversation_results = []
            
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
            duration = (datetime.now() - start_time).total_seconds()
            
            # Prepare result
            result = {
                "enhanced_system_prompt": enhanced_prompt,
                "context_metadata": {
                    "user_id": self.user_id,
                    "client_id": self.client_id,
                    "timestamp": datetime.now().isoformat(),
                    "duration_seconds": duration,
                    "user_profile_found": bool(user_profile),
                    "knowledge_results_count": len(knowledge_results),
                    "conversation_results_count": len(conversation_results),
                    "context_length": len(context_markdown),
                    "total_prompt_length": len(enhanced_prompt)
                },
                "raw_context_data": {
                    "user_profile": user_profile,
                    "knowledge_results": knowledge_results,
                    "conversation_results": conversation_results,
                    "context_markdown": context_markdown
                }
            }
            
            logger.info(
                f"Context built successfully in {duration:.2f}s - "
                f"Profile: {result['context_metadata']['user_profile_found']}, "
                f"Knowledge: {result['context_metadata']['knowledge_results_count']}, "
                f"Conversations: {result['context_metadata']['conversation_results_count']}"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Failed to build context: {e}", exc_info=True)
            # Return minimal context on error
            return {
                "enhanced_system_prompt": self.agent_config.get("system_prompt", "You are a helpful AI assistant."),
                "context_metadata": {
                    "error": str(e),
                    "timestamp": datetime.now().isoformat()
                },
                "raw_context_data": {}
            }
    
    async def _gather_user_profile(self) -> Dict[str, Any]:
        """
        Query user profile from client's Supabase
        
        Returns:
            User profile data including name, email, tags, preferences
        """
        try:
            logger.info(f"Gathering user profile for {self.user_id}")
            
            # Query the profiles table in client's Supabase
            result = self.supabase.table("profiles").select("*").eq("user_id", self.user_id).single().execute()
            
            if result.data:
                profile = result.data
                logger.info(f"Found user profile: {profile.get('name', 'Unknown')}")
                return profile
            else:
                logger.warning(f"No profile found for user {self.user_id}")
                return {}
                
        except Exception as e:
            logger.error(f"Error gathering user profile: {e}")
            return {}
    
    async def _gather_knowledge_rag(self, user_message: str) -> List[Dict[str, Any]]:
        """
        RAG search on agent's assigned documents
        
        Args:
            user_message: Query to search for
            
        Returns:
            List of relevant document excerpts with metadata
        """
        try:
            logger.info(f"Performing knowledge RAG search for: {user_message[:50]}...")
            
            # Get agent's assigned documents
            agent_id = self.agent_config.get("id") or self.agent_config.get("agent_id")
            if not agent_id:
                logger.warning("No agent ID found, skipping knowledge search")
                return []
            
            # Query documents assigned to this agent
            docs_result = self.supabase.table("agent_documents").select(
                "document_id, documents(id, title, content, metadata)"
            ).eq("agent_id", agent_id).execute()
            
            if not docs_result.data:
                logger.info("No documents assigned to agent")
                return []
            
            # Generate embedding for user query
            if self.embedder:
                query_embedding = self.embedder.encode(user_message).tolist()
            else:
                query_embedding = None
            
            # Store documents in ChromaDB and search
            if self.knowledge_collection and query_embedding:
                try:
                    # Add documents to collection
                    for doc_rel in docs_result.data:
                        doc = doc_rel.get("documents", {})
                        if doc and doc.get("content"):
                            # Split content into chunks (simple paragraph split for now)
                            chunks = self._chunk_document(doc["content"])
                            
                            for i, chunk in enumerate(chunks):
                                chunk_id = f"{doc['id']}_{i}"
                                self.knowledge_collection.add(
                                    ids=[chunk_id],
                                    documents=[chunk],
                                    metadatas=[{
                                        "document_id": doc["id"],
                                        "title": doc.get("title", "Untitled"),
                                        "chunk_index": i
                                    }]
                                )
                    
                    # Search for similar chunks
                    results = self.knowledge_collection.query(
                        query_embeddings=[query_embedding],
                        n_results=5  # Top 5 most relevant chunks
                    )
                    
                    # Format results
                    knowledge_results = []
                    for i, (doc, metadata, distance) in enumerate(zip(
                        results["documents"][0],
                        results["metadatas"][0],
                        results["distances"][0]
                    )):
                        # Convert distance to similarity score (1 - normalized distance)
                        similarity = 1 - (distance / 2)  # Cosine distance is 0-2
                        
                        knowledge_results.append({
                            "title": metadata["title"],
                            "excerpt": doc,
                            "relevance": round(similarity, 2),
                            "document_id": metadata["document_id"],
                            "chunk_index": metadata["chunk_index"]
                        })
                    
                    logger.info(f"Found {len(knowledge_results)} relevant knowledge chunks")
                    return knowledge_results
                    
                except Exception as e:
                    logger.error(f"ChromaDB search failed: {e}")
                    # Fall back to basic search
            
            # Fallback: Return first few documents without similarity scoring
            knowledge_results = []
            for doc_rel in docs_result.data[:3]:  # Limit to 3 documents
                doc = doc_rel.get("documents", {})
                if doc:
                    knowledge_results.append({
                        "title": doc.get("title", "Untitled"),
                        "excerpt": doc.get("content", "")[:500] + "...",
                        "relevance": 0.5,  # Default relevance
                        "document_id": doc.get("id")
                    })
            
            return knowledge_results
            
        except Exception as e:
            logger.error(f"Error in knowledge RAG search: {e}")
            return []
    
    async def _gather_conversation_rag(self, user_message: str) -> List[Dict[str, Any]]:
        """
        RAG search on user-agent conversation history
        
        Args:
            user_message: Query to search for
            
        Returns:
            List of relevant conversation excerpts
        """
        try:
            logger.info(f"Performing conversation RAG search for: {user_message[:50]}...")
            
            # Query recent conversations for this user and agent
            agent_id = self.agent_config.get("id") or self.agent_config.get("agent_id")
            
            # Get conversations from the last 30 days
            cutoff_date = (datetime.now() - timedelta(days=30)).isoformat()
            
            conversations_result = self.supabase.table("conversations").select(
                "id, created_at, messages(role, content, created_at)"
            ).eq("user_id", self.user_id).gte("created_at", cutoff_date).order(
                "created_at", desc=True
            ).limit(10).execute()
            
            if not conversations_result.data:
                logger.info("No recent conversations found")
                return []
            
            # Generate embedding for user query
            if self.embedder:
                query_embedding = self.embedder.encode(user_message).tolist()
            else:
                query_embedding = None
            
            # Process conversations for RAG
            if self.conversation_collection and query_embedding:
                try:
                    # Add conversation turns to collection
                    for conv in conversations_result.data:
                        messages = conv.get("messages", [])
                        
                        # Group messages into conversation turns (user + agent response)
                        turns = []
                        for i in range(0, len(messages) - 1, 2):
                            if i + 1 < len(messages):
                                user_msg = messages[i] if messages[i]["role"] == "user" else messages[i + 1]
                                agent_msg = messages[i + 1] if messages[i + 1]["role"] == "assistant" else messages[i]
                                
                                if user_msg["role"] == "user" and agent_msg["role"] == "assistant":
                                    turn_text = f"User: {user_msg['content']}\nAgent: {agent_msg['content']}"
                                    turns.append({
                                        "text": turn_text,
                                        "user_content": user_msg["content"],
                                        "agent_content": agent_msg["content"],
                                        "timestamp": user_msg["created_at"]
                                    })
                        
                        # Add turns to collection
                        for i, turn in enumerate(turns):
                            turn_id = f"{conv['id']}_{i}"
                            self.conversation_collection.add(
                                ids=[turn_id],
                                documents=[turn["text"]],
                                metadatas=[{
                                    "conversation_id": conv["id"],
                                    "turn_index": i,
                                    "timestamp": turn["timestamp"],
                                    "created_at": conv["created_at"]
                                }]
                            )
                    
                    # Search for similar conversation turns
                    results = self.conversation_collection.query(
                        query_embeddings=[query_embedding],
                        n_results=3  # Top 3 most relevant turns
                    )
                    
                    # Format results
                    conversation_results = []
                    for i, (doc, metadata, distance) in enumerate(zip(
                        results["documents"][0],
                        results["metadatas"][0],
                        results["distances"][0]
                    )):
                        # Convert distance to similarity score
                        similarity = 1 - (distance / 2)
                        
                        # Parse the turn text back into user/agent parts
                        lines = doc.split("\n")
                        user_part = lines[0].replace("User: ", "") if lines else ""
                        agent_part = lines[1].replace("Agent: ", "") if len(lines) > 1 else ""
                        
                        conversation_results.append({
                            "user_message": user_part,
                            "agent_response": agent_part,
                            "relevance": round(similarity, 2),
                            "timestamp": metadata.get("timestamp", metadata.get("created_at")),
                            "conversation_id": metadata["conversation_id"]
                        })
                    
                    logger.info(f"Found {len(conversation_results)} relevant conversation turns")
                    return conversation_results
                    
                except Exception as e:
                    logger.error(f"ChromaDB conversation search failed: {e}")
                    # Fall back to basic search
            
            # Fallback: Return most recent conversations
            conversation_results = []
            for conv in conversations_result.data[:2]:  # Last 2 conversations
                messages = conv.get("messages", [])
                if len(messages) >= 2:
                    # Get last exchange
                    user_msg = next((m for m in reversed(messages) if m["role"] == "user"), None)
                    agent_msg = next((m for m in reversed(messages) if m["role"] == "assistant"), None)
                    
                    if user_msg and agent_msg:
                        conversation_results.append({
                            "user_message": user_msg["content"],
                            "agent_response": agent_msg["content"],
                            "relevance": 0.5,  # Default relevance
                            "timestamp": conv["created_at"],
                            "conversation_id": conv["id"]
                        })
            
            return conversation_results
            
        except Exception as e:
            logger.error(f"Error in conversation RAG search: {e}")
            return []
    
    def _chunk_document(self, content: str, chunk_size: int = 500) -> List[str]:
        """
        Split document content into chunks for embedding
        
        Args:
            content: Document content to chunk
            chunk_size: Approximate size of each chunk
            
        Returns:
            List of text chunks
        """
        # Simple paragraph-based chunking
        paragraphs = content.split("\n\n")
        
        chunks = []
        current_chunk = ""
        
        for para in paragraphs:
            if len(current_chunk) + len(para) > chunk_size and current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = para
            else:
                current_chunk += "\n\n" + para if current_chunk else para
        
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        return chunks
    
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
            if profile.get("name"):
                sections.append(f"**Name:** {profile['name']}  ")
            
            if profile.get("email"):
                sections.append(f"**Email:** {profile['email']}  ")
            
            if profile.get("tags"):
                tags = profile["tags"] if isinstance(profile["tags"], list) else [profile["tags"]]
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
            "system_prompt": "You are a helpful AI assistant specialized in technical support."
        }
        
        # Create context manager
        manager = AgentContextManager(
            supabase_client=supabase,
            agent_config=agent_config,
            user_id="test-user-123",
            client_id="test-client-123"
        )
        
        # Test context building
        result = await manager.build_complete_context("How do I set up webhooks?")
        
        print("Enhanced System Prompt:")
        print(result["enhanced_system_prompt"])
        print("\nContext Metadata:")
        print(json.dumps(result["context_metadata"], indent=2))


if __name__ == "__main__":
    # Run test if executed directly
    asyncio.run(test_context_manager())
"""
DocumentSense Executor - extracts intelligence from documents using LLM.
Uses the Sidekick's configured LLM via the trigger API.
"""

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Dict, Any, List, Optional

import httpx

from app.config import settings
from app.utils.supabase_credentials import SupabaseCredentialManager

logger = logging.getLogger(__name__)


@dataclass
class DocumentIntelligence:
    """Extracted intelligence from a document."""
    summary: str
    key_quotes: List[str]
    themes: List[str]
    entities: Dict[str, List[str]]
    questions_answered: List[str]
    document_type_inferred: Optional[str]


@dataclass
class DocumentSenseResult:
    """Result from DocumentSense extraction."""
    intelligence: Optional[DocumentIntelligence]
    success: bool
    error: Optional[str] = None
    chunks_analyzed: int = 0
    extraction_model: Optional[str] = None


# DocumentSense extraction prompt
DOCUMENTSENSE_EXTRACTION_PROMPT = """You are DocumentSense, an AI that extracts structured intelligence from documents.

Your task is to analyze the document content and extract key information that will help users ask questions about this specific document.

## Document Title
{document_title}

## Document Content
{document_content}

## Instructions

Extract the following information from this document:

1. **summary** - A concise 2-3 sentence summary of the document's main content and purpose
2. **key_quotes** - Up to 10 notable, quotable passages (EXACT text from document). Choose quotes that are:
   - Insightful or memorable
   - Representative of key ideas
   - Useful for someone asking "What are the best quotes from this document?"
3. **themes** - Main topics/themes discussed (array of 3-8 strings)
4. **entities** - Named entities organized by type:
   - people: Names of people mentioned
   - organizations: Companies, institutions, groups
   - locations: Places, cities, countries mentioned
   - dates: Specific dates or time periods referenced
   - concepts: Key concepts, ideas, or technical terms
5. **questions_answered** - What questions could this document help answer? (3-6 example questions)
6. **document_type_inferred** - What type of document is this? (e.g., "transcript", "article", "report", "interview", "meeting notes", "essay", etc.)

## Response Format

Return a JSON object with this exact structure:
```json
{{
  "summary": "A 2-3 sentence summary of the document...",
  "key_quotes": [
    "Exact quote from document 1...",
    "Exact quote from document 2..."
  ],
  "themes": ["theme1", "theme2", "theme3"],
  "entities": {{
    "people": ["Person Name 1", "Person Name 2"],
    "organizations": ["Org Name"],
    "locations": ["Location"],
    "dates": ["January 2024"],
    "concepts": ["Concept 1", "Concept 2"]
  }},
  "questions_answered": [
    "What is the main topic discussed?",
    "Who are the key people involved?"
  ],
  "document_type_inferred": "transcript"
}}
```

IMPORTANT:
- key_quotes must be EXACT text from the document, not paraphrased
- Keep the summary concise but informative
- Only include entities that are actually present in the document
- Leave arrays empty [] if no relevant items are found

Only return the JSON object, no other text."""


class DocumentSenseExecutor:
    """Executes DocumentSense extraction on documents using the Sidekick API."""

    def __init__(self):
        self._http_client = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client for API calls."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=180.0)  # Longer timeout for document processing
        return self._http_client

    async def _get_documentsense_agent(self, client_id: str) -> Optional[Dict[str, Any]]:
        """Find an agent that has DocumentSense ability or an agent with LLM config."""
        try:
            client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
            from supabase import create_client
            client_sb = create_client(client_url, client_key)

            # Get all agents for this client
            agents_result = client_sb.table('agents').select('id, slug, name, voice_settings').execute()

            if not agents_result.data:
                logger.warning(f"No agents found for client {client_id}")
                return None

            # First, try to find an agent with DocumentSense tool assigned
            tools_result = client_sb.table('tools').select('id, slug').eq('slug', 'documentsense').execute()
            documentsense_tool_id = None
            if tools_result.data:
                documentsense_tool_id = tools_result.data[0]['id']

            if documentsense_tool_id:
                for agent in agents_result.data:
                    agent_tools_result = client_sb.table('agent_tools').select(
                        'tool_id'
                    ).eq('agent_id', agent['id']).eq('tool_id', documentsense_tool_id).execute()

                    if agent_tools_result.data:
                        logger.info(f"Found DocumentSense agent: {agent['slug']} ({agent['name']})")
                        return agent

            # Fallback: use any agent with LLM config
            for agent in agents_result.data:
                if agent.get('voice_settings') and agent['voice_settings'].get('llm_provider'):
                    logger.info(f"Using fallback agent for DocumentSense: {agent['slug']}")
                    return agent

            logger.warning(f"No suitable agent found for DocumentSense in client {client_id}")
            return None

        except Exception as e:
            logger.error(f"Failed to get DocumentSense agent: {e}")
            return None

    async def _call_sidekick_api(
        self,
        agent_slug: str,
        message: str,
        client_id: str
    ) -> Optional[str]:
        """Call the Sidekick trigger API to get an LLM response."""
        try:
            client = await self._get_http_client()

            payload = {
                "agent_slug": agent_slug,
                "message": message,
                "mode": "text",
                "user_id": f"documentsense-extraction-{uuid.uuid4().hex[:8]}",
                "session_id": f"extraction-{uuid.uuid4().hex[:8]}",
                "conversation_id": str(uuid.uuid4()),
                "context": {
                    "internal_extraction": True,
                    "skip_transcript_storage": True,
                    "skip_rag": True  # Don't need RAG for extraction
                }
            }

            api_url = "http://localhost:8000/api/v1/trigger-agent"

            logger.info(f"Calling Sidekick API for DocumentSense extraction: {agent_slug}")

            response = await client.post(
                api_url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Client-ID": client_id,
                    "X-Internal-Request": "true"
                }
            )

            if response.status_code == 200:
                result = response.json()
                if result.get('success') and result.get('data', {}).get('response'):
                    return result['data']['response']
                else:
                    logger.warning(f"API call succeeded but no response text: {result}")
                    return None
            else:
                logger.error(f"Sidekick API call failed: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            logger.error(f"Failed to call Sidekick API: {e}", exc_info=True)
            return None

    async def extract_intelligence(
        self,
        client_id: str,
        document_id: int,
        document_title: str,
        document_content: str,
        max_content_chars: int = 50000
    ) -> DocumentSenseResult:
        """
        Extract intelligence from a document.

        Args:
            client_id: Client ID for multi-tenant isolation
            document_id: Document ID in the database
            document_title: Title of the document
            document_content: Text content of the document
            max_content_chars: Maximum characters to analyze (truncate if larger)

        Returns:
            DocumentSenseResult with extracted intelligence
        """
        try:
            # Truncate content if too long
            if len(document_content) > max_content_chars:
                document_content = document_content[:max_content_chars] + "\n\n[... content truncated for analysis ...]"
                logger.info(f"Truncated document {document_id} content to {max_content_chars} chars for analysis")

            # Build the extraction prompt
            prompt = DOCUMENTSENSE_EXTRACTION_PROMPT.format(
                document_title=document_title or "Untitled Document",
                document_content=document_content
            )

            # Get the agent to use for LLM call
            agent = await self._get_documentsense_agent(client_id)
            if not agent:
                logger.error(f"No suitable agent found for DocumentSense extraction in client {client_id}")
                return DocumentSenseResult(
                    intelligence=None,
                    success=False,
                    error="No agent available for extraction"
                )

            # Call LLM via Sidekick API
            logger.info(f"DocumentSense extracting intelligence from document {document_id} ({document_title}) using agent {agent['slug']}")

            response_text = await self._call_sidekick_api(
                agent['slug'],
                prompt,
                client_id
            )

            if not response_text:
                logger.warning(f"No response from Sidekick API for DocumentSense extraction")
                return DocumentSenseResult(
                    intelligence=None,
                    success=False,
                    error="Failed to get response from agent"
                )

            # Parse the response
            intelligence = self._parse_response(response_text)

            if intelligence:
                # Store the result in the database
                await self._store_intelligence(
                    client_id=client_id,
                    document_id=document_id,
                    document_title=document_title,
                    intelligence=intelligence,
                    extraction_model=agent.get('voice_settings', {}).get('llm_model', 'unknown'),
                    chunks_analyzed=1  # For now, treating whole doc as 1 chunk
                )

                return DocumentSenseResult(
                    intelligence=intelligence,
                    success=True,
                    chunks_analyzed=1,
                    extraction_model=agent.get('voice_settings', {}).get('llm_model')
                )
            else:
                return DocumentSenseResult(
                    intelligence=None,
                    success=False,
                    error="Failed to parse extraction response"
                )

        except Exception as e:
            logger.error(f"DocumentSense extraction failed for document {document_id}: {e}", exc_info=True)
            return DocumentSenseResult(
                intelligence=None,
                success=False,
                error=str(e)
            )

    def _parse_response(self, response_text: str) -> Optional[DocumentIntelligence]:
        """Parse LLM response into DocumentIntelligence."""
        try:
            # Extract JSON from response
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1

            if json_start >= 0 and json_end > json_start:
                json_str = response_text[json_start:json_end]
                data = json.loads(json_str)

                return DocumentIntelligence(
                    summary=data.get("summary", ""),
                    key_quotes=data.get("key_quotes", []),
                    themes=data.get("themes", []),
                    entities=data.get("entities", {
                        "people": [],
                        "organizations": [],
                        "locations": [],
                        "dates": [],
                        "concepts": []
                    }),
                    questions_answered=data.get("questions_answered", []),
                    document_type_inferred=data.get("document_type_inferred")
                )

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse DocumentSense response as JSON: {e}")

        return None

    async def _store_intelligence(
        self,
        client_id: str,
        document_id: int,
        document_title: str,
        intelligence: DocumentIntelligence,
        extraction_model: str,
        chunks_analyzed: int
    ) -> bool:
        """Store extracted intelligence in the database."""
        try:
            client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
            from supabase import create_client
            client_sb = create_client(client_url, client_key)

            # Convert intelligence to JSONB-compatible dict
            intelligence_dict = {
                "summary": intelligence.summary,
                "key_quotes": intelligence.key_quotes,
                "themes": intelligence.themes,
                "entities": intelligence.entities,
                "questions_answered": intelligence.questions_answered,
                "document_type_inferred": intelligence.document_type_inferred
            }

            # Call the upsert RPC function
            result = client_sb.rpc(
                "upsert_document_intelligence",
                {
                    "p_document_id": document_id,
                    "p_client_id": client_id,
                    "p_document_title": document_title,
                    "p_intelligence": json.dumps(intelligence_dict),
                    "p_extraction_model": extraction_model,
                    "p_chunks_analyzed": chunks_analyzed
                }
            ).execute()

            if result.data and result.data.get("success"):
                logger.info(f"Stored document intelligence for document {document_id}")
                return True
            else:
                logger.warning(f"Failed to store document intelligence: {result.data}")
                return False

        except Exception as e:
            logger.error(f"Failed to store document intelligence: {e}")
            return False

    async def get_document_intelligence(
        self,
        client_id: str,
        document_id: int
    ) -> Optional[Dict[str, Any]]:
        """Retrieve stored intelligence for a document."""
        try:
            client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
            from supabase import create_client
            client_sb = create_client(client_url, client_key)

            result = client_sb.rpc(
                "get_document_intelligence",
                {
                    "p_document_id": document_id,
                    "p_client_id": client_id
                }
            ).execute()

            if result.data and result.data.get("exists"):
                return result.data
            return None

        except Exception as e:
            logger.error(f"Failed to get document intelligence: {e}")
            return None

    async def search_documents(
        self,
        client_id: str,
        query: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Search documents by title/content using DocumentSense data."""
        try:
            client_url, _, client_key = await SupabaseCredentialManager.get_client_supabase_credentials(client_id)
            from supabase import create_client
            client_sb = create_client(client_url, client_key)

            result = client_sb.rpc(
                "search_document_intelligence",
                {
                    "p_client_id": client_id,
                    "p_query": query,
                    "p_limit": limit
                }
            ).execute()

            return result.data or []

        except Exception as e:
            logger.error(f"Failed to search document intelligence: {e}")
            return []


# Singleton instance
documentsense_executor = DocumentSenseExecutor()

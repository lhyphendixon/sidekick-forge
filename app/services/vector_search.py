"""
Vector search service using Supabase pgvector
"""
import logging
from typing import List, Dict, Any, Optional
import numpy as np
from supabase import Client
import json

logger = logging.getLogger(__name__)


class VectorSearchService:
    """Service for vector search operations using Supabase pgvector"""
    
    def __init__(self, supabase_client: Client):
        self.supabase = supabase_client
        self.embedding_dimension = 1024  # Default for SiliconFlow
    
    async def search_documents(
        self,
        client_id: str,
        query_embedding: List[float],
        agent_slug: Optional[str] = None,
        limit: int = 5,
        threshold: float = 0.7
    ) -> List[Dict[str, Any]]:
        """
        Search for similar document chunks using vector similarity
        
        Args:
            client_id: The client ID to search within
            query_embedding: The embedding vector of the search query
            agent_slug: Optional agent slug to filter results
            limit: Maximum number of results to return
            threshold: Minimum similarity threshold (0-1)
            
        Returns:
            List of document chunks with similarity scores
        """
        try:
            # Convert embedding to string format for pgvector
            embedding_str = f"[{','.join(map(str, query_embedding))}]"
            
            # Build the query
            query = self.supabase.table("document_chunks").select(
                "id, document_id, content, chunk_index, metadata, embedding"
            ).eq("client_id", client_id)
            
            # Add agent filter if specified
            if agent_slug:
                query = query.eq("agent_slug", agent_slug)
            
            # Execute similarity search using pgvector
            # Note: This assumes you have a function in Supabase that handles vector similarity
            response = self.supabase.rpc(
                "search_similar_chunks",
                {
                    "query_embedding": embedding_str,
                    "match_count": limit,
                    "filter": {
                        "client_id": client_id,
                        "agent_slug": agent_slug
                    } if agent_slug else {"client_id": client_id}
                }
            ).execute()
            
            if not response.data:
                return []
            
            # Filter by threshold and format results
            results = []
            for chunk in response.data:
                similarity = chunk.get("similarity", 0)
                if similarity >= threshold:
                    results.append({
                        "chunk_id": chunk["id"],
                        "document_id": chunk["document_id"],
                        "content": chunk["content"],
                        "score": similarity,
                        "chunk_index": chunk["chunk_index"],
                        "metadata": chunk.get("metadata", {})
                    })
            
            return results
            
        except Exception as e:
            logger.error(f"Error searching documents: {e}")
            return []
    
    async def store_document_chunks(
        self,
        client_id: str,
        document_id: str,
        chunks: List[Dict[str, Any]],
        agent_slug: Optional[str] = None
    ) -> bool:
        """
        Store document chunks with their embeddings
        
        Args:
            client_id: The client ID
            document_id: The document ID
            chunks: List of chunks with content and embeddings
            agent_slug: Optional agent slug
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Prepare chunk records for insertion
            records = []
            for i, chunk in enumerate(chunks):
                # Convert embedding to string format for pgvector
                embedding_str = f"[{','.join(map(str, chunk['embedding']))}]"
                
                record = {
                    "client_id": client_id,
                    "document_id": document_id,
                    "agent_slug": agent_slug,
                    "chunk_index": i,
                    "content": chunk["content"],
                    "embedding": embedding_str,
                    "metadata": json.dumps(chunk.get("metadata", {}))
                }
                records.append(record)
            
            # Insert chunks into Supabase
            response = self.supabase.table("document_chunks").insert(records).execute()
            
            return bool(response.data)
            
        except Exception as e:
            logger.error(f"Error storing document chunks: {e}")
            return False
    
    async def delete_document_chunks(
        self,
        client_id: str,
        document_id: str
    ) -> bool:
        """
        Delete all chunks for a document
        
        Args:
            client_id: The client ID
            document_id: The document ID
            
        Returns:
            True if successful, False otherwise
        """
        try:
            response = self.supabase.table("document_chunks")\
                .delete()\
                .eq("client_id", client_id)\
                .eq("document_id", document_id)\
                .execute()
            
            return True
            
        except Exception as e:
            logger.error(f"Error deleting document chunks: {e}")
            return False
    
    async def get_document_info(
        self,
        client_id: str,
        document_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get document information from Supabase
        
        Args:
            client_id: The client ID
            document_id: The document ID
            
        Returns:
            Document information or None if not found
        """
        try:
            response = self.supabase.table("documents")\
                .select("*")\
                .eq("client_id", client_id)\
                .eq("id", document_id)\
                .execute()
            
            if response.data:
                return response.data[0]
            return None
            
        except Exception as e:
            logger.error(f"Error getting document info: {e}")
            return None


# SQL function to create in Supabase for vector similarity search:
"""
CREATE OR REPLACE FUNCTION search_similar_chunks(
    query_embedding vector(1024),
    match_count int,
    filter jsonb
)
RETURNS TABLE (
    id uuid,
    document_id uuid,
    content text,
    chunk_index int,
    metadata jsonb,
    similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT 
        dc.id,
        dc.document_id,
        dc.content,
        dc.chunk_index,
        dc.metadata,
        1 - (dc.embedding <=> query_embedding) as similarity
    FROM document_chunks dc
    WHERE 
        dc.client_id = (filter->>'client_id')::uuid
        AND (filter->>'agent_slug' IS NULL OR dc.agent_slug = filter->>'agent_slug')
    ORDER BY dc.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;
"""
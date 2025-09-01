"""
Citations Analytics Service

Tracks and logs RAG citation usage for observability and analytics.
Stores minimal PII-free data for performance monitoring and user insights.
"""
import logging
import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from uuid import uuid4

from app.integrations.supabase_client import supabase_manager

logger = logging.getLogger(__name__)


class CitationAnalytics:
    """Service for tracking citation usage analytics"""
    
    def __init__(self):
        self.table_name = "agent_message_citations"
    
    async def log_citation_usage(
        self,
        client_id: str,
        agent_slug: str,
        session_id: str,
        message_id: str,
        citations: List[Dict[str, Any]],
        user_query_length: Optional[int] = None,
        retrieval_time_ms: Optional[float] = None,
        total_chunks_found: Optional[int] = None
    ) -> None:
        """
        Log citation usage for analytics.
        
        Args:
            client_id: Client identifier
            agent_slug: Agent identifier
            session_id: Conversation session ID
            message_id: Unique message ID
            citations: List of citation objects
            user_query_length: Length of user query in characters
            retrieval_time_ms: RAG retrieval time in milliseconds
            total_chunks_found: Total chunks found before filtering
        """
        try:
            if not citations:
                return
            
            # Create analytics records (one per citation)
            analytics_records = []
            timestamp = datetime.now(timezone.utc).isoformat()
            
            for citation in citations:
                record = {
                    'id': str(uuid4()),
                    'client_id': client_id,
                    'agent_slug': agent_slug,
                    'session_id': session_id,
                    'message_id': message_id,
                    'chunk_id': citation.get('chunk_id'),
                    'doc_id': citation.get('doc_id'),
                    'source_type': citation.get('source_type', 'unknown'),
                    'similarity_score': float(citation.get('similarity', 0.0)),
                    'chunk_index': citation.get('chunk_index', 0),
                    'page_number': citation.get('page_number'),
                    'created_at': timestamp
                }
                analytics_records.append(record)
            
            # Insert records into platform Supabase (not client-specific)
            await supabase_manager.execute_query(
                supabase_manager.admin_client.table(self.table_name).insert(analytics_records)
            )
            
            # Log summary for observability
            unique_docs = len(set(c.get('doc_id') for c in citations if c.get('doc_id')))
            avg_similarity = sum(float(c.get('similarity', 0)) for c in citations) / len(citations)
            
            logger.info(f"Citations analytics logged: {len(citations)} citations from "
                       f"{unique_docs} documents, avg similarity: {avg_similarity:.3f}, "
                       f"retrieval time: {retrieval_time_ms}ms")
                       
        except Exception as e:
            logger.error(f"Failed to log citation analytics: {e}")
            # Don't raise - analytics failure shouldn't break the main flow
    
    async def log_citation_interaction(
        self,
        client_id: str,
        session_id: str,
        message_id: str,
        interaction_type: str,  # 'click', 'expand', 'tooltip'
        citation_chunk_id: str,
        citation_doc_id: str
    ) -> None:
        """
        Log user interactions with citations.
        
        Args:
            client_id: Client identifier
            session_id: Session identifier
            message_id: Message identifier
            interaction_type: Type of interaction
            citation_chunk_id: Chunk ID that was interacted with
            citation_doc_id: Document ID that was interacted with
        """
        try:
            interaction_record = {
                'id': str(uuid4()),
                'client_id': client_id,
                'session_id': session_id,
                'message_id': message_id,
                'interaction_type': interaction_type,
                'chunk_id': citation_chunk_id,
                'doc_id': citation_doc_id,
                'created_at': datetime.now(timezone.utc).isoformat()
            }
            
            # Log to citation_interactions table (would need to be created)
            await supabase_manager.execute_query(
                supabase_manager.admin_client.table("agent_citation_interactions").insert(interaction_record)
            )
            
            logger.debug(f"Citation interaction logged: {interaction_type} on {citation_doc_id}")
            
        except Exception as e:
            logger.error(f"Failed to log citation interaction: {e}")
    
    async def get_citation_stats(
        self,
        client_id: Optional[str] = None,
        agent_slug: Optional[str] = None,
        days: int = 7
    ) -> Dict[str, Any]:
        """
        Get citation usage statistics.
        
        Args:
            client_id: Optional client filter
            agent_slug: Optional agent filter
            days: Number of days to look back
            
        Returns:
            Dictionary with citation statistics
        """
        try:
            # Build query with filters
            query = supabase_manager.admin_client.table(self.table_name).select("*")
            
            # Add date filter
            from datetime import timedelta
            cutoff_date = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            query = query.gte("created_at", cutoff_date)
            
            if client_id:
                query = query.eq("client_id", client_id)
            if agent_slug:
                query = query.eq("agent_slug", agent_slug)
            
            results = await supabase_manager.execute_query(query)
            
            if not results:
                return {
                    'total_citations': 0,
                    'unique_messages': 0,
                    'unique_documents': 0,
                    'avg_similarity': 0.0,
                    'source_type_breakdown': {},
                    'top_documents': []
                }
            
            # Calculate statistics
            total_citations = len(results)
            unique_messages = len(set(r['message_id'] for r in results))
            unique_documents = len(set(r['doc_id'] for r in results if r['doc_id']))
            
            # Average similarity
            similarities = [float(r.get('similarity_score', 0)) for r in results]
            avg_similarity = sum(similarities) / len(similarities) if similarities else 0.0
            
            # Source type breakdown
            source_types = {}
            for result in results:
                source_type = result.get('source_type', 'unknown')
                source_types[source_type] = source_types.get(source_type, 0) + 1
            
            # Top documents by citation count
            doc_counts = {}
            for result in results:
                doc_id = result.get('doc_id')
                if doc_id:
                    if doc_id not in doc_counts:
                        doc_counts[doc_id] = {'count': 0, 'avg_similarity': 0, 'similarities': []}
                    doc_counts[doc_id]['count'] += 1
                    doc_counts[doc_id]['similarities'].append(float(result.get('similarity_score', 0)))
            
            # Calculate average similarities and sort
            for doc_data in doc_counts.values():
                doc_data['avg_similarity'] = sum(doc_data['similarities']) / len(doc_data['similarities'])
                del doc_data['similarities']  # Clean up
            
            top_documents = sorted(doc_counts.items(), key=lambda x: x[1]['count'], reverse=True)[:10]
            
            return {
                'total_citations': total_citations,
                'unique_messages': unique_messages,
                'unique_documents': unique_documents,
                'avg_similarity': round(avg_similarity, 3),
                'source_type_breakdown': source_types,
                'top_documents': [
                    {'doc_id': doc_id, **stats} 
                    for doc_id, stats in top_documents
                ],
                'period_days': days
            }
            
        except Exception as e:
            logger.error(f"Failed to get citation stats: {e}")
            raise


# Singleton instance
citation_analytics = CitationAnalytics()
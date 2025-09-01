"""
Tests for RAG Citations functionality
"""
import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock
from typing import List, Dict, Any

from app.integrations.rag.citations_service import RAGCitationsService, CitationChunk, RAGRetrievalResult
from app.models.citation import Citation, CitationResponse
from app.integrations.analytics.citations_analytics import CitationAnalytics


class TestRAGCitationsService:
    """Test cases for RAGCitationsService"""
    
    @pytest.fixture
    def service(self):
        return RAGCitationsService()
    
    @pytest.fixture
    def mock_client_config(self):
        return {
            'url': 'https://test.supabase.co',
            'service_key': 'test_key',
            'client_id': 'test_client'
        }
    
    @pytest.fixture
    def mock_raw_chunks(self):
        return [
            {
                'id': 'chunk1',
                'doc_id': 'doc1',
                'title': 'Test Document 1',
                'source_url': 'https://example.com/doc1',
                'source_type': 'web',
                'chunk_index': 0,
                'page_number': 1,
                'char_start': 0,
                'char_end': 100,
                'content': 'This is the first chunk of content.',
                'similarity': 0.85
            },
            {
                'id': 'chunk2',
                'doc_id': 'doc1',
                'title': 'Test Document 1',
                'source_url': 'https://example.com/doc1',
                'source_type': 'web',
                'chunk_index': 1,
                'page_number': 1,
                'char_start': 100,
                'char_end': 200,
                'content': 'This is the second chunk of content.',
                'similarity': 0.75
            },
            {
                'id': 'chunk3',
                'doc_id': 'doc2',
                'title': 'Test Document 2',
                'source_url': 'https://example.com/doc2',
                'source_type': 'pdf',
                'chunk_index': 0,
                'page_number': 2,
                'char_start': 0,
                'char_end': 120,
                'content': 'This is content from a different document.',
                'similarity': 0.80
            }
        ]
    
    @pytest.mark.asyncio
    async def test_consolidate_and_rerank(self, service, mock_raw_chunks):
        """Test chunk consolidation and reranking"""
        result = await service._consolidate_and_rerank(
            raw_chunks=mock_raw_chunks,
            max_documents=2,
            max_chunks=3
        )
        
        # Should return chunks ordered by document relevance then similarity
        assert len(result) == 3
        
        # First chunk should be from doc1 (highest avg similarity: (0.85+0.75)/2 = 0.8)
        # But doc2 has 0.80 similarity, so order depends on implementation
        # At minimum, we should have chunks from both documents
        doc_ids = set(chunk['doc_id'] for chunk in result)
        assert 'doc1' in doc_ids
        assert 'doc2' in doc_ids
    
    def test_build_context_text(self, service, mock_raw_chunks):
        """Test context text building"""
        context = service._build_context_text(mock_raw_chunks)
        
        assert context is not None
        assert len(context) > 0
        
        # Should contain source references
        assert '[Source 1:' in context
        assert '[Source 2:' in context
        
        # Should contain content from chunks
        assert 'This is the first chunk' in context
        assert 'This is content from a different document' in context
    
    def test_build_citations(self, service, mock_raw_chunks):
        """Test citation object building"""
        citations = service._build_citations(mock_raw_chunks)
        
        assert len(citations) == 3
        
        # Check first citation
        citation = citations[0]
        assert isinstance(citation, CitationChunk)
        assert citation.chunk_id == 'chunk1'
        assert citation.doc_id == 'doc1'
        assert citation.title == 'Test Document 1'
        assert citation.source_url == 'https://example.com/doc1'
        assert citation.similarity == 0.85
    
    @pytest.mark.asyncio
    async def test_retrieve_with_citations_no_results(self, service):
        """Test retrieval when no results found (should raise RAGError)"""
        with patch.object(service, '_get_client_supabase_config') as mock_config, \
             patch.object(service, '_create_client_supabase') as mock_client, \
             patch.object(service, '_generate_embedding') as mock_embedding, \
             patch.object(service, '_search_document_chunks') as mock_search:
            
            mock_config.return_value = {'url': 'test', 'service_key': 'test', 'client_id': 'test'}
            mock_client.return_value = Mock()
            mock_embedding.return_value = [0.0] * 1024
            mock_search.return_value = []  # No results
            
            from app.utils.exceptions import RAGError
            with pytest.raises(RAGError, match="No relevant documents found"):
                await service.retrieve_with_citations(
                    query="test query",
                    client_id="test_client",
                    dataset_ids=["dataset1"]
                )
    
    @pytest.mark.asyncio
    async def test_retrieve_with_citations_success(self, service, mock_raw_chunks):
        """Test successful retrieval with citations"""
        with patch.object(service, '_get_client_supabase_config') as mock_config, \
             patch.object(service, '_create_client_supabase') as mock_client, \
             patch.object(service, '_generate_embedding') as mock_embedding, \
             patch.object(service, '_search_document_chunks') as mock_search, \
             patch.object(service, '_consolidate_and_rerank') as mock_consolidate:
            
            mock_config.return_value = {'url': 'test', 'service_key': 'test', 'client_id': 'test'}
            mock_client.return_value = Mock()
            mock_embedding.return_value = [0.0] * 1024
            mock_search.return_value = mock_raw_chunks
            mock_consolidate.return_value = mock_raw_chunks
            
            result = await service.retrieve_with_citations(
                query="test query",
                client_id="test_client",
                dataset_ids=["dataset1"]
            )
            
            assert isinstance(result, RAGRetrievalResult)
            assert len(result.citations) == 3
            assert result.context_for_llm is not None
            assert result.total_chunks_found == 3
            assert result.processing_time_ms > 0


class TestCitationModels:
    """Test cases for Citation models"""
    
    def test_citation_model(self):
        """Test Citation model validation"""
        citation = Citation(
            chunk_id="test_chunk",
            doc_id="test_doc",
            title="Test Document",
            source_url="https://example.com",
            source_type="web",
            chunk_index=0,
            similarity=0.85
        )
        
        assert citation.chunk_id == "test_chunk"
        assert citation.doc_id == "test_doc"
        assert citation.similarity == 0.85
    
    def test_citation_response_from_citations(self):
        """Test CitationResponse creation from citations"""
        citations = [
            Citation(
                chunk_id="chunk1", doc_id="doc1", title="Doc 1", 
                source_url="https://example.com/1", source_type="web",
                chunk_index=0, similarity=0.85
            ),
            Citation(
                chunk_id="chunk2", doc_id="doc1", title="Doc 1",
                source_url="https://example.com/1", source_type="web", 
                chunk_index=1, similarity=0.75
            ),
            Citation(
                chunk_id="chunk3", doc_id="doc2", title="Doc 2",
                source_url="https://example.com/2", source_type="pdf",
                chunk_index=0, similarity=0.90
            )
        ]
        
        response = CitationResponse.from_citations("test_message", citations)
        
        assert response.message_id == "test_message"
        assert len(response.citations) == 3
        assert response.total_chunks == 3
        assert response.total_documents == 2
        
        # Should group by document, sorted by best similarity
        assert len(response.document_groups) == 2
        
        # Doc2 should be first (higher similarity: 0.90 vs 0.85)
        first_group = response.document_groups[0]
        assert first_group.doc_id == "doc2"
        assert first_group.best_similarity == 0.90
        assert first_group.chunk_count == 1


class TestCitationAnalytics:
    """Test cases for CitationAnalytics"""
    
    @pytest.fixture
    def analytics(self):
        return CitationAnalytics()
    
    @pytest.fixture
    def mock_citations(self):
        return [
            {
                'chunk_id': 'chunk1',
                'doc_id': 'doc1',
                'source_type': 'web',
                'similarity': 0.85,
                'chunk_index': 0,
                'page_number': 1
            },
            {
                'chunk_id': 'chunk2', 
                'doc_id': 'doc2',
                'source_type': 'pdf',
                'similarity': 0.75,
                'chunk_index': 0,
                'page_number': 2
            }
        ]
    
    @pytest.mark.asyncio
    async def test_log_citation_usage_empty_citations(self, analytics):
        """Test logging with no citations (should return without error)"""
        # Should not raise any errors
        await analytics.log_citation_usage(
            client_id="test_client",
            agent_slug="test_agent",
            session_id="test_session",
            message_id="test_message",
            citations=[]
        )
    
    @pytest.mark.asyncio
    async def test_log_citation_usage_success(self, analytics, mock_citations):
        """Test successful citation usage logging"""
        with patch('app.integrations.supabase_client.supabase_manager') as mock_manager:
            mock_manager.execute_query = AsyncMock()
            mock_manager.admin_client.table.return_value.insert.return_value = Mock()
            
            await analytics.log_citation_usage(
                client_id="test_client",
                agent_slug="test_agent", 
                session_id="test_session",
                message_id="test_message",
                citations=mock_citations,
                retrieval_time_ms=150.5
            )
            
            # Should have called insert with 2 records
            mock_manager.execute_query.assert_called_once()
            call_args = mock_manager.execute_query.call_args[0][0]
            # The insert call should have been made
            assert mock_manager.admin_client.table.called
    
    @pytest.mark.asyncio
    async def test_get_citation_stats(self, analytics):
        """Test citation statistics retrieval"""
        mock_results = [
            {
                'message_id': 'msg1',
                'doc_id': 'doc1', 
                'similarity_score': 0.85,
                'source_type': 'web'
            },
            {
                'message_id': 'msg1',
                'doc_id': 'doc2',
                'similarity_score': 0.75, 
                'source_type': 'pdf'
            },
            {
                'message_id': 'msg2',
                'doc_id': 'doc1',
                'similarity_score': 0.90,
                'source_type': 'web'
            }
        ]
        
        with patch('app.integrations.supabase_client.supabase_manager') as mock_manager:
            mock_manager.execute_query = AsyncMock(return_value=mock_results)
            mock_manager.admin_client.table.return_value.select.return_value.gte.return_value = Mock()
            
            stats = await analytics.get_citation_stats(client_id="test_client", days=7)
            
            assert stats['total_citations'] == 3
            assert stats['unique_messages'] == 2
            assert stats['unique_documents'] == 2
            assert stats['avg_similarity'] == 0.833  # (0.85 + 0.75 + 0.90) / 3
            assert stats['source_type_breakdown'] == {'web': 2, 'pdf': 1}


class TestIntegrationCitations:
    """Integration tests for citations functionality"""
    
    @pytest.mark.asyncio
    async def test_end_to_end_citation_flow(self):
        """Test the complete citation flow from retrieval to response"""
        # This would be a more comprehensive integration test
        # that tests the entire flow from user query to citation display
        pass
    
    def test_citation_ui_component_initialization(self):
        """Test that citation UI component can be initialized"""
        # This would test the JavaScript component if we had a JS test framework
        pass


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v"])
"""
Citation models for RAG responses
"""
from typing import Optional, List
from pydantic import BaseModel, Field
from uuid import UUID


class Citation(BaseModel):
    """Citation metadata for RAG responses"""
    chunk_id: str = Field(..., description="Unique chunk identifier")
    doc_id: str = Field(..., description="Document identifier") 
    title: str = Field(..., description="Document title")
    source_url: str = Field(..., description="Clickable URL to source")
    source_type: str = Field(default="unknown", description="Source type (web, pdf, md, etc.)")
    chunk_index: int = Field(..., ge=0, description="Chunk position in document")
    page_number: Optional[int] = Field(None, description="Page number for PDFs")
    char_start: Optional[int] = Field(None, description="Character start position")
    char_end: Optional[int] = Field(None, description="Character end position")
    similarity: float = Field(..., ge=0.0, le=1.0, description="Similarity score")
    
    class Config:
        schema_extra = {
            "example": {
                "chunk_id": "123e4567-e89b-12d3-a456-426614174000",
                "doc_id": "123e4567-e89b-12d3-a456-426614174001",
                "title": "Product Documentation",
                "source_url": "https://example.com/docs/product",
                "source_type": "web",
                "chunk_index": 3,
                "page_number": 5,
                "char_start": 120,
                "char_end": 340,
                "similarity": 0.82
            }
        }


class DocumentGroup(BaseModel):
    """Grouped citations by document for UI display"""
    doc_id: str
    title: str
    source_url: str
    source_type: str
    chunk_count: int = Field(..., ge=1, description="Number of chunks from this document")
    best_similarity: float = Field(..., ge=0.0, le=1.0, description="Highest similarity score")
    chunks: List[Citation] = Field(..., description="Citations from this document")


class CitationResponse(BaseModel):
    """Response containing citations for a message"""
    message_id: str = Field(..., description="Message identifier")
    citations: List[Citation] = Field(default_factory=list, description="Individual citations")
    document_groups: List[DocumentGroup] = Field(default_factory=list, description="Citations grouped by document")
    total_chunks: int = Field(default=0, ge=0, description="Total chunks used")
    total_documents: int = Field(default=0, ge=0, description="Total unique documents")
    
    @classmethod
    def from_citations(cls, message_id: str, citations: List[Citation]) -> 'CitationResponse':
        """Create response with automatic document grouping"""
        # Group citations by document
        doc_groups_map = {}
        
        for citation in citations:
            doc_id = citation.doc_id
            if doc_id not in doc_groups_map:
                doc_groups_map[doc_id] = {
                    'doc_id': doc_id,
                    'title': citation.title,
                    'source_url': citation.source_url,
                    'source_type': citation.source_type,
                    'chunks': [],
                    'best_similarity': citation.similarity
                }
            
            doc_groups_map[doc_id]['chunks'].append(citation)
            doc_groups_map[doc_id]['best_similarity'] = max(
                doc_groups_map[doc_id]['best_similarity'],
                citation.similarity
            )
        
        # Convert to DocumentGroup objects
        document_groups = []
        for group_data in doc_groups_map.values():
            document_groups.append(DocumentGroup(
                doc_id=group_data['doc_id'],
                title=group_data['title'],
                source_url=group_data['source_url'],
                source_type=group_data['source_type'],
                chunk_count=len(group_data['chunks']),
                best_similarity=group_data['best_similarity'],
                chunks=group_data['chunks']
            ))
        
        # Sort by best similarity
        document_groups.sort(key=lambda x: x.best_similarity, reverse=True)
        
        return cls(
            message_id=message_id,
            citations=citations,
            document_groups=document_groups,
            total_chunks=len(citations),
            total_documents=len(document_groups)
        )
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID

class DocumentBase(BaseModel):
    """Base document model"""
    filename: str = Field(..., min_length=1, max_length=255)
    file_type: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

class Document(DocumentBase):
    """Document model matching production documents table"""
    id: Optional[UUID] = None
    user_id: UUID
    file_size: Optional[int] = None
    content: Optional[str] = None
    upload_status: str = Field(default="pending", pattern="^(pending|uploading|completed|failed)$")
    processing_status: str = Field(default="pending", pattern="^(pending|processing|completed|failed)$")
    word_count: Optional[int] = None
    chunk_count: int = 0
    created_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True

class DocumentChunk(BaseModel):
    """Document chunk model for RAG system"""
    id: Optional[UUID] = None
    document_id: UUID
    chunk_index: int = Field(..., ge=0)
    content: str = Field(..., min_length=1)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    
    # Vector embedding will be handled by Supabase
    # embeddings field is not exposed in API
    
    class Config:
        from_attributes = True

class DocumentUploadRequest(BaseModel):
    """Request model for document upload"""
    filename: str = Field(..., min_length=1, max_length=255)
    content_type: str
    file_size: int = Field(..., gt=0, le=104857600)  # Max 100MB
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

class DocumentUploadResponse(BaseModel):
    """Response model for document upload"""
    document_id: UUID
    upload_url: str
    upload_method: str = "PUT"
    expires_at: datetime
    
    class Config:
        schema_extra = {
            "example": {
                "document_id": "123e4567-e89b-12d3-a456-426614174000",
                "upload_url": "https://storage.supabase.co/upload/...",
                "upload_method": "PUT",
                "expires_at": "2024-01-01T01:00:00Z"
            }
        }

class DocumentProcessingStatus(BaseModel):
    """Document processing status"""
    document_id: UUID
    upload_status: str
    processing_status: str
    word_count: Optional[int] = None
    chunk_count: int = 0
    error_message: Optional[str] = None
    
class DocumentSearchRequest(BaseModel):
    """Request model for document search (RAG)"""
    query: str = Field(..., min_length=1, max_length=1000)
    limit: int = Field(default=10, ge=1, le=100)
    threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    document_ids: Optional[List[UUID]] = None
    metadata_filters: Optional[Dict[str, Any]] = None

class DocumentSearchResult(BaseModel):
    """Search result from RAG system"""
    chunk_id: UUID
    document_id: UUID
    document_name: str
    content: str
    score: float
    metadata: Dict[str, Any] = Field(default_factory=dict)

class DocumentSearchResponse(BaseModel):
    """Response model for document search"""
    query: str
    results: List[DocumentSearchResult]
    total_results: int
    processing_time_ms: float

class DocumentListResponse(BaseModel):
    """Response model for document list"""
    documents: List[Document]
    total: int
    page: int = 1
    per_page: int = 20
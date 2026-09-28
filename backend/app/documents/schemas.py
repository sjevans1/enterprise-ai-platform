"""Pydantic schemas for the document ingestion and retrieval API.

Some schemas (DocumentUploadResponse, Citation) already exist in
app.api.schemas.common and are re-exported here to avoid duplication.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Re-export existing schemas to provide a single import surface for documents
from app.api.schemas.common import Citation, DocumentUploadResponse  # noqa: F401


class DocumentChunkResponse(BaseModel):
    """A single chunk returned in retrieval results."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str
    chunk_index: int
    content: str
    page_ref: int | None = None
    section_ref: str | None = None
    row_ref: int | None = None
    relevance_score: float = Field(description="Cosine similarity to the query (0–1)")
    mmr_score: float | None = Field(default=None, description="MMR-adjusted score")
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentResponse(BaseModel):
    """Full document metadata returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    original_name: str
    source_type: str
    file_size: int
    mime_type: str
    content_hash: str
    version: int
    status: str
    embedding_model: str | None = None
    embedding_version: str | None = None
    is_indexed: bool
    index_version: int
    ingestion_timestamp: datetime
    created_at: datetime
    updated_at: datetime
    chunk_count: int | None = None


class RetrievalRequest(BaseModel):
    """Request for the MMR retrieval endpoint."""

    query: str = Field(..., description="Search query text", min_length=1, max_length=2000)
    top_k: int = Field(default=10, ge=1, le=100, description="Number of results to return")
    fetch_k: int = Field(
        default=50, ge=10, le=500,
        description="Number of vector candidates to retrieve before MMR reranking",
    )
    lambda_param: float = Field(
        default=0.7, ge=0.0, le=1.0,
        description="MMR relevance/diversity trade-off. 1.0=pure relevance, 0.0=pure diversity",
    )
    chunk_size: int = Field(
        default=512, ge=64, le=8192,
        description="Chunk size used during ingestion (for reference only)",
    )
    chunk_overlap: int = Field(
        default=128, ge=0, le=1024,
        description="Chunk overlap used during ingestion (for reference only)",
    )
    document_ids: list[str] | None = Field(
        default=None,
        description="Optional list of document IDs to restrict the search scope",
    )

    @field_validator("lambda_param")
    @classmethod
    def validate_lambda(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("lambda_param must be between 0.0 and 1.0")
        return v


class RetrievalResult(BaseModel):
    """A single retrieval result with citation metadata."""

    chunk_id: str
    document_id: str
    relevance_score: float = Field(description="Cosine similarity (0–1)")
    mmr_score: float = Field(description="Final MMR score")
    content: str = Field(description="The text passage from the chunk")
    page_ref: int | None = None
    section_ref: str | None = None
    row_ref: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    citation: Citation


class RetrievalResponse(BaseModel):
    """Response from the retrieval endpoint."""

    query: str
    top_k: int
    lambda_param: float
    candidate_count: int
    results: list[RetrievalResult]
    query_embedding_dims: int = 384


class DocumentIngestRequest(BaseModel):
    """Request body for ingestion with explicit parameters (alternative to raw upload)."""

    chunk_size: int = Field(default=512, ge=64, le=8192)
    chunk_overlap: int = Field(default=128, ge=0, le=1024)
    embedding_model: str | None = Field(
        default=None,
        description="Override the default embedding model (defaults to settings)",
    )

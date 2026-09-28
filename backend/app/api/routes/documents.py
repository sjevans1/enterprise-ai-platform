"""Document management and retrieval API routes.

Endpoints:
  POST   /documents/upload     — upload a file, extract, chunk, embed, index
  GET    /documents/           — list documents
  GET    /documents/{id}       — get document metadata + chunk count
  DELETE /documents/{id}       — soft-delete a document
  POST   /documents/retrieve   — MMR-enhanced vector search → ranked citations
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.connection import get_db
from app.db.models import Document, DocumentIndexVersion
from app.documents.ingestion import DocumentIngestionService, DocumentIngestionError
from app.documents.schemas import RetrievalRequest
from app.retrieval.service import RetrievalService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["documents"])


def _result_to_dict(r) -> dict:
    """Convert a RetrievalResult to a JSON-serializable dict."""
    return {
        "chunk_id": r.chunk_id,
        "document_id": r.document_id,
        "relevance_score": r.relevance_score,
        "mmr_score": r.mmr_score,
        "content": r.content,
        "page_ref": r.page_ref,
        "section_ref": r.section_ref,
        "row_ref": r.row_ref,
        "citation": {
            "source_type": r.citation.source_type,
            "source_id": r.citation.source_id,
            "title": r.citation.title,
            "passage": r.citation.passage,
            "citation": r.citation.citation,
            "confidence": r.citation.confidence,
        },
        "metadata": r.metadata,
    }


# ─── Upload ────────────────────────────────────────────────────────

@router.post("/documents/upload", response_model=None)
async def upload_document(
    request_file: UploadFile = File(...),
    chunk_size: int = Query(default=512, ge=64, le=8192),
    chunk_overlap: int = Query(default=128, ge=0, le=1024),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a document for ingestion.

    Accepts PDF, DOCX, PPTX, XLSX, CSV, TXT, MD, and HTML files.
    The file is saved, text is extracted, split into chunks, embeddings
    are generated locally (CPU-only), and all chunks are stored with
    vector embeddings in PostgreSQL/pgvector.
    """
    content = await request_file.read()
    filename = request_file.filename or "upload"

    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    supported = {".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".txt", ".md", ".html"}
    if ext not in supported:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type: '{ext}'. Supported: {', '.join(sorted(supported))}",
        )

    logger.info(f"Upload: user={current_user.id}, file={filename}, size={len(content)}")

    try:
        service = DocumentIngestionService(db_session=db)
        doc = await service.ingest_file(
            file_content=content,
            filename=filename,
            mime_type=request_file.content_type,
            source_type="upload",
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
    except DocumentIngestionError as e:
        logger.error(f"Ingestion error for {filename}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )

    return {
        "document_id": doc.id,
        "filename": doc.original_name,
        "file_size": doc.file_size,
        "mime_type": doc.mime_type,
        "status": doc.status.value,
        "content_hash": doc.content_hash,
        "extraction_method": doc.extraction_method,
        "embedding_model": doc.embedding_model,
        "is_indexed": doc.is_indexed,
        "ingestion_timestamp": doc.ingestion_timestamp.isoformat(),
        "created_at": doc.created_at.isoformat(),
    }


# ─── List Documents ────────────────────────────────────────────────

@router.get("/documents/")
async def list_documents(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List uploaded documents (READY status only)."""
    service = DocumentIngestionService(db_session=db)
    docs = await service.list_documents(limit=limit, offset=offset)

    results = []
    for doc in docs:
        chunk_count = await service.get_chunk_count(doc.id)
        results.append({
            "id": doc.id,
            "filename": doc.original_name,
            "file_size": doc.file_size,
            "mime_type": doc.mime_type,
            "status": doc.status.value,
            "content_hash": doc.content_hash,
            "extraction_method": doc.extraction_method,
            "embedding_model": doc.embedding_model,
            "is_indexed": doc.is_indexed,
            "chunk_count": chunk_count,
            "created_at": doc.created_at.isoformat(),
            "updated_at": doc.updated_at.isoformat(),
        })

    return {"documents": results, "count": len(results), "limit": limit, "offset": offset}


# ─── Get Document ──────────────────────────────────────────────────

@router.get("/documents/{document_id}")
async def get_document(
    document_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get document metadata, chunk count, and index version info."""
    service = DocumentIngestionService(db_session=db)
    doc = await service.get_document(document_id)

    if not doc or doc.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Document not found")

    chunk_count = await service.get_chunk_count(doc.id)

    result = await db.execute(
        select(DocumentIndexVersion)
        .where(DocumentIndexVersion.document_id == doc.id)
        .order_by(DocumentIndexVersion.version.desc())
        .limit(1)
    )
    index_version = result.scalars().first()

    return {
        "id": doc.id,
        "filename": doc.original_name,
        "file_size": doc.file_size,
        "mime_type": doc.mime_type,
        "content_hash": doc.content_hash,
        "status": doc.status.value,
        "extraction_method": doc.extraction_method,
        "embedding_model": doc.embedding_model,
        "embedding_version": doc.embedding_version,
        "is_indexed": doc.is_indexed,
        "chunk_count": chunk_count,
        "index_version": {
            "version": index_version.version if index_version else None,
            "embedding_model": index_version.embedding_model if index_version else None,
            "embedding_dimensions": index_version.embedding_dimensions if index_version else None,
            "status": index_version.status if index_version else None,
            "built_at": index_version.built_at.isoformat() if index_version else None,
        } if index_version else None,
        "created_at": doc.created_at.isoformat(),
        "updated_at": doc.updated_at.isoformat(),
    }


# ─── Delete Document ───────────────────────────────────────────────

@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a document and its chunks."""
    service = DocumentIngestionService(db_session=db)
    success = await service.delete_document(document_id)

    if not success:
        raise HTTPException(status_code=404, detail="Document not found")

    return {"message": "Document deleted", "document_id": document_id}


# ─── Retrieve with MMR ─────────────────────────────────────────────

@router.post("/documents/retrieve", response_model=None)
async def retrieve_documents(
    retrieval_request: RetrievalRequest,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve documents using MMR-enhanced vector search.

    1. Embeds the query with all-MiniLM-L6-v2 (CPU-only, local).
    2. Searches the pgvector IVFFlat index for top-K cosine-distance candidates.
    3. Reranks with Maximal Marginal Relevance (MMR) for diversity.
    4. Returns ranked results with citation metadata.

    **MMR Parameters:**
    - `lambda_param` (default 0.7): Relevance/diversity trade-off.
      - 1.0 = pure relevance (no diversity)
      - 0.0 = pure diversity (maximally dissimilar results)
    - `fetch_k` (default 50): Number of vector candidates retrieved
      before MMR reranking.
    - `top_k` (default 10): Final number of results returned.
    """
    service = RetrievalService(db_session=db)
    results = await service.retrieve(
        query=retrieval_request.query,
        top_k=retrieval_request.top_k,
        fetch_k=retrieval_request.fetch_k,
        lambda_param=retrieval_request.lambda_param,
        document_ids=retrieval_request.document_ids,
    )

    return {
        "query": retrieval_request.query,
        "top_k": retrieval_request.top_k,
        "lambda_param": retrieval_request.lambda_param,
        "candidate_count": len(results),
        "results": [_result_to_dict(r) for r in results],
    }

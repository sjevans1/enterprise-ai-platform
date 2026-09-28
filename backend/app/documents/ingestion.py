"""Document ingestion service: orchestrates extraction, chunking, embedding, and storage.

Workflow per document:
  1. Save uploaded file to the configured upload directory.
  2. Compute content hash for deduplication.
  3. Create a Document record (status = UPLOADED).
  4. Extract text → structured PageContent blocks.
  5. Chunk text with configurable size/overlap.
  6. Generate embeddings for each chunk (local CPU model).
  7. Bulk-insert DocumentChunk records with vectors.
  8. Create a DocumentIndexVersion record.
  9. Mark Document as indexed (status = READY, is_indexed = True).

The service is async to cooperate with FastAPI's event loop, but the
heavy text/embedding work runs in threads via asyncio.to_thread().
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import (
    Document,
    DocumentChunk,
    DocumentIndexVersion,
    DocumentStatus,
)
from app.documents.chunker import TextChunker
from app.documents.extractor import DocumentExtractor

if TYPE_CHECKING:
    from app.embeddings.provider import LocalEmbeddingProvider

logger = logging.getLogger(__name__)


def _sha256(text: str | bytes) -> str:
    """Compute SHA-256 hex digest of a string or bytes."""
    if isinstance(text, str):
        text = text.encode("utf-8")
    return hashlib.sha256(text).hexdigest()


class DocumentIngestionError(Exception):
    """Raised when document ingestion fails."""


class DocumentIngestionService:
    """Orchestrates the full document ingestion pipeline.

    Uses the existing async session and pgvector-backed models.
    All embedding computation is CPU-only via the local embedding provider.
    """

    SUPPORTED_EXTENSIONS: set[str] = {
        ".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".txt", ".md", ".html",
    }

    def __init__(
        self,
        db_session: AsyncSession,
        embedding_provider=None,
        chunker: TextChunker | None = None,
        extractor: DocumentExtractor | None = None,
    ):
        self._db = db_session
        self._embedding = embedding_provider
        self._extractor = extractor or DocumentExtractor()
        self._chunker = chunker or TextChunker(
            chunk_size=settings.embedding_dimensions * 2,  # ~512 chars for 384-dim model
            chunk_overlap=128,
        )

    # ── Lazy embedding provider ────────────────────────────────────

    def _get_embedding_provider(self):
        """Lazily load the embedding provider (avoids import-time model load)."""
        if self._embedding is None:
            from app.embeddings.provider import get_embedding_provider
            self._embedding = get_embedding_provider()
        return self._embedding

    # ── Public API ─────────────────────────────────────────────────

    async def ingest_file(
        self,
        file_content: bytes,
        filename: str,
        mime_type: str | None = None,
        source_type: str = "upload",
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> Document:
        """Ingest a single file end-to-end.

        Args:
            file_content: Raw bytes of the uploaded file.
            filename: Original filename (used for extension detection and display).
            mime_type: MIME type from the upload.
            source_type: "upload", "connector", etc.
            chunk_size: Override default chunk size.
            chunk_overlap: Override default chunk overlap.

        Returns:
            The created/updated Document model instance.

        Raises:
            DocumentIngestionError: If extraction or embedding fails.
        """
        # Validate file extension
        ext = self._get_extension(filename)
        if ext not in self.SUPPORTED_EXTENSIONS:
            raise DocumentIngestionError(
                f"Unsupported file type: '{ext}'. Supported: {', '.join(sorted(self.SUPPORTED_EXTENSIONS))}"
            )

        # Validate file size
        max_bytes = settings.max_upload_size_mb * 1024 * 1024
        if len(file_content) > max_bytes:
            raise DocumentIngestionError(
                f"File too large: {len(file_content)} bytes (max {max_bytes})"
            )

        # Compute content hash for deduplication
        content_hash = _sha256(file_content)

        # Check for existing document with same hash
        existing = await self._db.execute(
            select(Document).where(Document.content_hash == content_hash, Document.deleted_at.is_(None))
        )
        if existing_row := existing.scalars().first():
            logger.info(f"Document already exists: {existing_row.id} (hash={content_hash[:16]}...)")
            return existing_row

        # Ensure upload directory exists
        upload_dir = settings.upload_dir
        os.makedirs(upload_dir, exist_ok=True)

        # Generate a safe storage filename
        stored_filename = f"{uuid.uuid4().hex}{ext}"
        file_path = os.path.join(upload_dir, stored_filename)

        # Save file to disk
        with open(file_path, "wb") as f:
            f.write(file_content)
        logger.info(f"Saved file to {file_path} ({len(file_content)} bytes)")

        # Create Document record
        doc = Document(
            id=str(uuid.uuid4()),
            filename=stored_filename,
            original_name=filename,
            source_type=source_type,
            file_size=len(file_content),
            mime_type=mime_type or self._guess_mime_type(filename),
            content_hash=content_hash,
            version=1,
            status=DocumentStatus.UPLOADED,
            ingestion_timestamp=datetime.now(timezone.utc),
        )
        self._db.add(doc)
        await self._db.flush()  # Get the ID assigned

        try:
            # ── Step 1: Extract text ──
            logger.info(f"Extracting text from {filename}")
            doc.status = DocumentStatus.EXTRACTING
            await self._db.flush()

            extraction_result = await self._extract_async(file_content, doc.mime_type, filename)
            doc.extraction_method = extraction_result.extraction_method

            # ── Step 2: Chunk text ──
            logger.info(f"Chunking {len(extraction_result.pages)} pages for doc {doc.id}")
            doc.status = DocumentStatus.CHUNKING
            await self._db.flush()

            chunker = self._build_chunker(chunk_size, chunk_overlap)
            chunks = chunker.chunk_pages(extraction_result.pages)

            if not chunks:
                raise DocumentIngestionError("Text extraction produced no chunks")

            # ── Step 3: Generate embeddings ──
            logger.info(f"Generating embeddings for {len(chunks)} chunks (doc {doc.id})")
            doc.status = DocumentStatus.EMBEDDING
            await self._db.flush()

            embedding_provider = self._get_embedding_provider()
            chunk_texts = [chunk.content for chunk in chunks]
            embeddings = await self._embed_texts_async(embedding_provider, chunk_texts)

            if len(embeddings) != len(chunks):
                raise DocumentIngestionError(
                    f"Embedding count mismatch: got {len(embeddings)}, expected {len(chunks)}"
                )

            # ── Step 4: Store chunks ──
            logger.info(f"Storing {len(chunks)} chunks for doc {doc.id}")
            doc.status = DocumentStatus.INDEXING
            await self._db.flush()

            doc_chunks = []
            for chunk, embedding in zip(chunks, embeddings):
                chunk_record = DocumentChunk(
                    id=str(uuid.uuid4()),
                    document_id=doc.id,
                    chunk_index=chunk.chunk_index,
                    content=chunk.content,
                    content_hash=_sha256(chunk.content),
                    page_ref=chunk.source_page,
                    section_ref=chunk.source_section,
                    row_ref=chunk.source_row,
                    embedding_vector=embedding,
                    created_at=datetime.now(timezone.utc),
                )
                doc_chunks.append(chunk_record)

            self._db.add_all(doc_chunks)
            await self._db.flush()

            # ── Step 5: Create index version ──
            model_info = embedding_provider.get_model_info()
            index_version = DocumentIndexVersion(
                id=str(uuid.uuid4()),
                document_id=doc.id,
                version=1,
                status="active",
                embedding_model=model_info.get("model", settings.embedding_model),
                embedding_dimensions=model_info.get("dimensions", settings.embedding_dimensions),
                is_active=True,
                built_at=datetime.now(timezone.utc),
            )
            self._db.add(index_version)

            # ── Step 6: Finalize document ──
            doc.embedding_model = model_info.get("model", settings.embedding_model)
            doc.embedding_version = model_info.get("revision", "unknown")
            doc.index_version = 1
            doc.is_indexed = True
            doc.status = DocumentStatus.READY

            await self._db.commit()
            logger.info(f"Document {doc.id} ingested successfully: {len(chunks)} chunks, status=READY")

            return doc

        except Exception as e:
            # Rollback and mark as failed
            await self._db.rollback()
            doc.status = DocumentStatus.FAILED
            try:
                await self._db.commit()
            except Exception:
                pass
            logger.error(f"Ingestion failed for doc {doc.id}: {e}", exc_info=True)
            raise DocumentIngestionError(f"Ingestion failed: {e}") from e

    async def delete_document(self, document_id: str) -> bool:
        """Soft-delete a document and its chunks.

        Returns True if the document was found and deleted, False otherwise.
        """
        result = await self._db.execute(
            select(Document).where(Document.id == document_id)
        )
        doc = result.scalars().first()
        if not doc:
            return False

        doc.status = DocumentStatus.DELETED
        doc.deleted_at = datetime.now(timezone.utc)
        doc.is_indexed = False
        await self._db.commit()
        logger.info(f"Document {document_id} soft-deleted")
        return True

    async def get_document(self, document_id: str) -> Document | None:
        """Retrieve a document by ID."""
        result = await self._db.execute(
            select(Document).where(Document.id == document_id)
        )
        return result.scalars().first()

    async def list_documents(self, limit: int = 50, offset: int = 0) -> list[Document]:
        """List documents (optionally filtering by user permissions)."""
        stmt = (
            select(Document)
            .where(Document.deleted_at.is_(None), Document.status == DocumentStatus.READY)
            .order_by(Document.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._db.execute(stmt)
        return result.scalars().all()

    async def get_chunk_count(self, document_id: str) -> int:
        """Count chunks for a document."""
        result = await self._db.execute(
            select(func.count(DocumentChunk.id)).where(DocumentChunk.document_id == document_id)
        )
        return result.scalar_one()

    # ── Internal helpers ───────────────────────────────────────────

    def _get_extension(self, filename: str) -> str:
        """Get the lowercase file extension including the dot."""
        if "." not in filename:
            return ""
        return "." + filename.rsplit(".", 1)[-1].lower()

    def _guess_mime_type(self, filename: str) -> str:
        """Guess MIME type from filename extension."""
        ext = self._get_extension(filename)
        mime_map = {
            ".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".csv": "text/csv",
            ".txt": "text/plain",
            ".md": "text/markdown",
            ".html": "text/html",
        }
        return mime_map.get(ext, "application/octet-stream")

    def _build_chunker(self, chunk_size: int | None, chunk_overlap: int | None) -> TextChunker:
        """Build a chunker with the specified or default settings."""
        size = chunk_size or 512
        overlap = chunk_overlap or 128
        return TextChunker(chunk_size=size, chunk_overlap=overlap)

    async def _extract_async(self, content: bytes, mime_type: str | None, filename: str):
        """Run extraction in a thread to avoid blocking the event loop."""
        return await asyncio.to_thread(
            self._extractor.extract, content, mime_type, filename
        )

    async def _embed_texts_async(self, provider, texts: list[str]):
        """Run embedding generation in a thread to avoid blocking."""
        return await asyncio.to_thread(provider.embed_texts, texts)

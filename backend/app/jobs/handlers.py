"""Job type handlers for background job processing.

Each handler implements a simple ``execute(db, job)`` protocol and is
registered in ``HANDLER_REGISTRY`` keyed by job-type string.

The handlers in this module are **stubs** — they validate payloads,
load related ORM records, simulate work, and return a result dict.
Real extraction, embedding, and report-generation logic can be wired
in later without touching the worker or service layers.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, DocumentStatus, JobQueue

logger = logging.getLogger(__name__)


class JobHandler(Protocol):
    """Protocol every job handler must satisfy."""

    async def execute(
        self,
        db: AsyncSession,
        job: JobQueue,
    ) -> dict[str, Any]:
        """Execute the job and return a result dictionary.

        Raise an exception on failure; the worker records the error
        and handles retry/requeue.
        """
        ...


# ── document_ingest ──────────────────────────────────────────────


class DocumentIngestHandler:
    """Handler for ``document_ingest`` jobs.

    Expected payload:
        document_id (str):  ID of the Document record to ingest.
        file_path (str|None): Local path of the uploaded file.
        embedding_model (str|None): Override the embedding model name.

    The handler loads the document, simulates extraction/chunking/
    embedding, and updates the document status to ``READY``.
    """

    async def execute(
        self,
        db: AsyncSession,
        job: JobQueue,
    ) -> dict[str, Any]:
        payload = json.loads(job.payload) if job.payload else {}
        document_id = payload.get("document_id")
        file_path = payload.get("file_path")

        if not document_id:
            raise ValueError(
                "document_id is required in payload for document_ingest jobs"
            )

        logger.info(
            "document_ingest: loading document_id=%s, file_path=%s",
            document_id,
            file_path,
        )

        # ── Load the document record ──────────────────────────
        result = await db.execute(
            select(Document).where(Document.id == document_id)
        )
        doc = result.scalars().first()
        if doc is None:
            raise ValueError(f"Document not found: {document_id}")

        if doc.status == DocumentStatus.READY:
            logger.info("document_ingest: document %s already READY — skipping", document_id)
            return {
                "document_id": document_id,
                "filename": doc.filename,
                "status": DocumentStatus.READY.value,
                "message": f"Document {document_id} already ingested (no-op)",
                "was_skipped": True,
            }

        # ── Stub: simulate extraction + chunking + embedding ────
        # Replace with real logic:
        #   1. Extract text via PyPDF2 / python-docx / openpyxl / bs4
        #   2. Split into chunks (sentence or token-based)
        #   3. Generate embeddings via sentence-transformers
        #   4. Store DocumentChunk rows with embedding vectors
        #   5. Update Document.index_version, .is_indexed
        logger.info(
            "document_ingest: simulating extraction for %s (%s, %d bytes)",
            doc.filename, doc.mime_type, doc.file_size,
        )
        await _simulate_processing(doc)

        # ── Update document status ────────────────────────────
        doc.status = DocumentStatus.READY
        await db.commit()

        return {
            "document_id": document_id,
            "filename": doc.filename,
            "mime_type": doc.mime_type,
            "file_size_bytes": doc.file_size,
            "status": DocumentStatus.READY.value,
            "message": f"Document {document_id} ingested successfully",
        }


async def _simulate_processing(doc: Document) -> None:
    """Placeholder for real extraction / chunking / embedding work."""
    import asyncio as _aio
    await _aio.sleep(0.1)  # simulate I/O


# ── report_generation ───────────────────────────────────────────


class ReportGenerationHandler:
    """Handler for ``report_generation`` jobs.

    Expected payload:
        name (str):           Report name / title.
        description (str|None): Optional description.
        query_ids (list[str]):  IDs of queries to include.
        evidence_ids (list[str]): IDs of evidence to include.
        format (str):         Output format: "markdown", "html", or "pdf".

    The handler simulates report generation and returns metadata
    about the generated report.
    """

    async def execute(
        self,
        db: AsyncSession,
        job: JobQueue,
    ) -> dict[str, Any]:
        payload = json.loads(job.payload) if job.payload else {}
        report_name = payload.get("name", "Untitled Report")
        report_format = payload.get("format", "markdown")
        query_ids = payload.get("query_ids", [])
        evidence_ids = payload.get("evidence_ids", [])
        description = payload.get("description")

        logger.info(
            "report_generation: generating '%s' (format=%s, queries=%d, evidence=%d)",
            report_name,
            report_format,
            len(query_ids),
            len(evidence_ids),
        )

        # ── Stub: simulate report generation ────────────────────
        # Replace with real logic:
        #   1. Fetch query results from the database
        #   2. Collect evidence passages
        #   3. Assemble the report in the requested format
        #   4. Store the report or return a download URL
        import asyncio as _aio
        await _aio.sleep(0.2)

        result_data = {
            "report_name": report_name,
            "description": description,
            "format": report_format,
            "query_ids": query_ids,
            "evidence_ids": evidence_ids,
            "status": "generated",
            "message": f"Report '{report_name}' generated successfully",
        }

        logger.info("report_generation: report '%s' generated", report_name)
        return result_data


# ── Registry ─────────────────────────────────────────────────────

HANDLER_REGISTRY: dict[str, JobHandler] = {
    "document_ingest": DocumentIngestHandler(),
    "report_generation": ReportGenerationHandler(),
}


def get_handler(job_type: str) -> JobHandler | None:
    """Look up a handler for the given job type."""
    return HANDLER_REGISTRY.get(job_type)


def list_supported_job_types() -> list[str]:
    """Return a list of all registered job types."""
    return list(HANDLER_REGISTRY.keys())

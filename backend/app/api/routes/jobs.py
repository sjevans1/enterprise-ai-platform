"""Job queue API routes.

Endpoints:
    POST   /api/v1/jobs/          Submit a new background job
    GET    /api/v1/jobs/{id}     Get job status (and result if complete)
    GET    /api/v1/jobs/         List jobs (filter by status / job_type)
    DELETE /api/v1/jobs/{id}     Cancel a queued or running job

The routes are intentionally auth-free for MVP parity with the health
endpoint.  Layering on ``get_current_user`` is a one-line change once
RBAC requirements are finalised.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.jobs import (
    JobStatusResponse,
    JobSubmitRequest,
    JobSubmitResponse,
)
from app.db.connection import get_db
from app.jobs.service import JobService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["jobs"])


@router.post(
    "/jobs/",
    response_model=JobSubmitResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_job(
    request: JobSubmitRequest,
    db: AsyncSession = Depends(get_db),
) -> JobSubmitResponse:
    """Enqueue a new background job.

    Supported job types and their payload schemas:

    **document_ingest**
      ``{"document_id": "<doc-uuid>", "file_path": "/path/to/file.pdf"}`

    **report_generation**
      ``{"name": "Sales Report", "query_ids": ["q1"], "evidence_ids": ["e1"], "format": "markdown"}`
    """
    try:
        job = await JobService(db).submit_job(
            job_type=request.job_type.value,
            payload=request.payload,
            priority=request.priority,
            max_retries=request.max_retries,
            expires_at=request.expires_at,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    return JobSubmitResponse(
        job_id=job.id,
        job_type=job.job_type,
        status=_orm_status_to_schema(job.status),
        submitted_at=job.created_at,
    )


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    db: AsyncSession = Depends(get_db),
) -> JobStatusResponse:
    """Retrieve the status (and result, if complete) of a job by ID."""
    job = await JobService(db).get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job not found: {job_id}",
        )
    return JobStatusResponse.from_orm_job(job)


@router.get("/jobs/", response_model=list[JobStatusResponse])
async def list_jobs(
    status: str | None = None,
    job_type: str | None = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
) -> list[JobStatusResponse]:
    """List jobs, optionally filtered by *status* or *job_type*."""
    jobs = await JobService(db).list_jobs(
        status=status, job_type=job_type, limit=limit
    )
    return [JobStatusResponse.from_orm_job(j) for j in jobs]


@router.delete("/jobs/{job_id}", response_model=dict[str, str])
async def cancel_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Cancel a queued or running job."""
    cancelled = await JobService(db).cancel_job(
        job_id, reason="cancelled_by_api"
    )
    if not cancelled:
        # Distinguish "not found" from "not cancellable"
        job = await JobService(db).get_job(job_id)
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job not found: {job_id}",
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job {job_id} cannot be cancelled (status={job.status})",
        )
    return {"job_id": job_id, "status": "cancelled"}


# ── Helpers ──────────────────────────────────────────────────────


def _orm_status_to_schema(status: Any) -> "JobStatusResponse.model_fields['status'].annotation":  # type: ignore[name-defined]
    """Normalise an ORM JobStatus value to the API schema enum."""
    from app.api.schemas.jobs import JobStatus as SchemaJobStatus

    if hasattr(status, "name"):
        return SchemaJobStatus[status.name]
    if isinstance(status, str):
        try:
            return SchemaJobStatus[status]
        except KeyError:
            return SchemaJobStatus(status)
    return SchemaJobStatus(str(status))
